"""
Test suite for PyWinUI's pure-Python behavior — the tree model, context-manager
building, attach/detach, property queueing, event handling, and the async/
thread-marshalling routing.

None of this needs Windows or the winui3 packages: tests that touch the native
layer swap in a fake (see the `fake_native` fixture), so the whole suite runs
anywhere. The `# VERIFY` native calls in pywinui.py are the only thing these
tests can't cover — those still need a real Windows box.

    pip install pytest
    pytest test_pywinui.py -v
"""

import types
import asyncio
import pytest

import pywinui as ui


# ---------------------------------------------------------------------------
# Fake native layer: stand-ins for the winui3 controls so we can exercise
# _realize(), _apply(), event binding, and value conversions without Windows.
# ---------------------------------------------------------------------------
class FakeControl:
    """Mimics a WinUI control: settable properties, a children vector, a content
    slot, and add_<event> subscription methods."""

    def __init__(self, kind: str) -> None:
        object.__setattr__(self, "_kind", kind)
        object.__setattr__(self, "children", [])   # WinRT vector exposes .append
        object.__setattr__(self, "content", None)
        object.__setattr__(self, "events", {})

    def __getattr__(self, name):
        # Dynamically provide add_<event>(handler) subscription methods.
        if name.startswith("add_"):
            event = name[4:]
            def _subscribe(handler, _event=event):
                self.events.setdefault(_event, []).append(handler)
            return _subscribe
        raise AttributeError(name)

    def activate(self):
        object.__setattr__(self, "activated", True)


class FakeNative:
    """Stands in for pywinui._native."""

    Visibility = types.SimpleNamespace(VISIBLE="VISIBLE", COLLAPSED="COLLAPSED")

    def load(self):
        pass

    # control factories (called as _native.Button(), etc.)
    def Button(self):     return FakeControl("Button")
    def TextBlock(self):  return FakeControl("TextBlock")
    def TextBox(self):    return FakeControl("TextBox")
    def StackPanel(self): return FakeControl("StackPanel")
    def Grid(self):       return FakeControl("Grid")
    def Window(self):     return FakeControl("Window")

    # value converters
    def thickness(self, value):
        return ("Thickness", value)

    def visibility(self, value):
        return self.Visibility.VISIBLE if value else self.Visibility.COLLAPSED

    def box(self, value):
        # Mirrors the real layer: primitives get boxed, controls pass through.
        return ("Boxed", value) if isinstance(value, (str, bool, int, float)) else value


@pytest.fixture
def fake_native(monkeypatch):
    fn = FakeNative()
    monkeypatch.setattr(ui, "_native", fn)
    # Make sure the dispatcher is unbound so writes go direct during realize.
    ui._dispatcher._queue = None
    ui._dispatcher._ui_thread_id = None
    yield fn
    ui._dispatcher._queue = None
    ui._dispatcher._ui_thread_id = None


@pytest.fixture(autouse=True)
def clean_context():
    """Guarantee the open-parent context is clear before/after every test."""
    assert ui._open_parent.get() is None
    yield
    # If a test left something open, reset by exhausting is not possible; assert.
    assert ui._open_parent.get() is None


# ---------------------------------------------------------------------------
# Tree building — list style
# ---------------------------------------------------------------------------
def test_list_style_children():
    panel = ui.StackPanel(children=[ui.TextBlock("a"), ui.TextBlock("b")])
    assert len(panel._child_widgets) == 2


def test_list_children_are_not_auto_attached_elsewhere():
    # Constructing children as args happens with no open parent.
    a = ui.TextBlock("a")
    panel = ui.StackPanel(children=[a])
    assert panel._child_widgets == [a]


# ---------------------------------------------------------------------------
# Tree building — with style
# ---------------------------------------------------------------------------
def test_with_style_auto_attaches():
    with ui.StackPanel() as panel:
        ui.TextBlock("a")
        ui.Button("b")
    assert len(panel._child_widgets) == 2


def test_nested_with_attaches_to_innermost():
    with ui.StackPanel() as outer:
        ui.TextBlock("a")
        with ui.StackPanel() as inner:
            ui.Button("b")
    assert len(outer._child_widgets) == 2
    assert outer._child_widgets[1] is inner
    assert len(inner._child_widgets) == 1


def test_context_cleared_after_block():
    with ui.StackPanel():
        pass
    assert ui._open_parent.get() is None


def test_context_cleared_even_on_exception():
    with pytest.raises(ValueError):
        with ui.StackPanel():
            raise ValueError("boom")
    assert ui._open_parent.get() is None


def test_list_and_with_compose():
    seed = ui.TextBlock("seed")  # built outside the block -> not auto-attached
    with ui.StackPanel(children=[seed]) as panel:
        ui.TextBlock("added")
    assert len(panel._child_widgets) == 2
    assert panel._child_widgets[0] is seed


# ---------------------------------------------------------------------------
# Slot semantics: Window single content, leaves reject children
# ---------------------------------------------------------------------------
def test_window_single_content_slot():
    with ui.Window() as win:
        ui.TextBlock("only")
    assert win._content is not None


def test_window_rejects_second_child():
    with ui.Window() as win:
        ui.TextBlock("first")
    with pytest.raises(TypeError):
        with win:
            ui.TextBlock("second")


def test_window_content_kwarg():
    win = ui.Window(content=ui.TextBlock("via kwarg"))
    assert win._content is not None


def test_leaf_rejects_children():
    with pytest.raises(TypeError):
        with ui.TextBlock("leaf"):
            ui.Button("nope")


# ---------------------------------------------------------------------------
# Detach / attach
# ---------------------------------------------------------------------------
def test_detached_context_suppresses_attach():
    with ui.StackPanel() as panel:
        with ui.detached():
            loose = ui.Button("later")
    assert loose not in panel._child_widgets


def test_attached_false_flag_suppresses_attach():
    with ui.StackPanel() as panel:
        loose = ui.Button("later", attached=False)
    assert loose not in panel._child_widgets


def test_attached_kwarg_is_consumed_not_forwarded():
    b = ui.Button("x", attached=False)
    assert "attached" not in b._props


def test_default_attached_true():
    with ui.StackPanel() as panel:
        b = ui.Button("x")
    assert b in panel._child_widgets


def test_manual_add_and_chaining():
    panel = ui.StackPanel()
    a, b = ui.TextBlock("a", attached=False), ui.TextBlock("b", attached=False)
    assert panel.add(a) is panel          # returns self
    panel.add(b)
    assert panel._child_widgets == [a, b]


# ---------------------------------------------------------------------------
# Property queueing + attribute forwarding
# ---------------------------------------------------------------------------
def test_event_handlers_split_from_props():
    b = ui.Button("x", on_click=lambda s, a: None)
    assert "click" in b._events
    assert "on_click" not in b._props
    assert "content" in b._props           # positional label mapped to content


def test_mutate_before_realize_queues():
    tb = ui.TextBlock("0")
    tb.text = "queued"
    assert tb._props["text"] == "queued"
    assert tb.text == "queued"             # __getattr__ reads from queued props


# ---------------------------------------------------------------------------
# Realization against the fake native layer
# ---------------------------------------------------------------------------
def test_realize_builds_native_tree(fake_native):
    root = ui.StackPanel(spacing=10, children=[
        ui.TextBlock("hi", font_size=20),
        ui.Button("go", on_click=lambda s, a: None),
    ])
    native = root._realize()
    assert native._kind == "StackPanel"
    assert native.spacing == 10
    assert len(native.children) == 2
    assert native.children[0].text == "hi"
    assert native.children[0].font_size == 20
    # Button.Content is object-typed, so a str must arrive boxed (verified
    # against the real bindings: a bare str raises "not a System.Object").
    assert native.children[1].content == ("Boxed", "go")
    assert "click" in native.children[1].events


def test_realize_is_idempotent(fake_native):
    b = ui.Button("x")
    assert b._realize() is b._realize()


def test_native_property_realizes(fake_native):
    b = ui.Button("x")
    assert b.native is b._native
    assert b.native._kind == "Button"


def test_mutate_after_realize_forwards(fake_native):
    tb = ui.TextBlock("0")
    tb._realize()
    tb.text = "5"
    assert tb._native.text == "5"


def test_padding_converts_to_thickness(fake_native):
    panel = ui.StackPanel(padding=24)
    native = panel._realize()
    assert native.padding == ("Thickness", 24)


def test_visible_maps_to_visibility(fake_native):
    hidden = ui.TextBlock("x", visible=False)
    native = hidden._realize()
    assert native.visibility == "COLLAPSED"
    assert not hasattr(native, "visible")   # renamed, no stray attr

    shown = ui.TextBlock("y", visible=True)
    assert shown._realize().visibility == "VISIBLE"


def test_window_content_realized(fake_native):
    win = ui.Window(title="t", content=ui.TextBlock("body"))
    native = win._realize()
    assert native.title == "t"
    assert native.content._kind == "TextBlock"


# ---------------------------------------------------------------------------
# Async / dispatcher routing
# ---------------------------------------------------------------------------
def test_async_handler_wrapped_to_sync_shim():
    async def ahandler(sender, args):
        return None
    wrapped = ui._wrap_async(ahandler)
    assert not asyncio.iscoroutinefunction(wrapped)


def test_sync_handler_passed_through():
    def shandler(sender, args):
        return None
    assert ui._wrap_async(shandler) is shandler


def test_as_future_adapts_an_awaitable():
    """as_future delegates to the projection's own __await__ (verified on
    Windows: PyWinRT 3.2 async ops are directly awaitable). A stand-in
    awaitable stands in for IAsyncOperation here."""
    class FakeAsyncOp:
        def __await__(self):
            async def _inner():
                return "done"
            return _inner().__await__()

    async def use_it():
        fut = ui.as_future(FakeAsyncOp())
        assert isinstance(fut, asyncio.Future)
        return await fut

    assert asyncio.run(use_it()) == "done"


def test_as_future_propagates_errors():
    """WinRT failures surface as ordinary Python exceptions (verified on
    Windows: a missing path raises FileNotFoundError through the await)."""
    class FailingOp:
        def __await__(self):
            async def _inner():
                raise FileNotFoundError("nope")
            return _inner().__await__()

    async def use_it():
        await ui.as_future(FailingOp())

    with pytest.raises(FileNotFoundError):
        asyncio.run(use_it())


def test_offthread_write_is_marshalled(fake_native):
    tb = ui.TextBlock("x")
    tb._realize()                      # dispatcher unbound -> direct

    calls = []
    class FakeQueue:
        def try_enqueue(self, fn):
            calls.append(1)
            fn()
    ui._dispatcher._queue = FakeQueue()
    ui._dispatcher._ui_thread_id = -1  # pretend the UI thread is some other one

    tb.text = "hop"
    assert calls == [1]
    assert tb._native.text == "hop"


def test_onthread_write_is_direct(fake_native):
    import threading
    tb = ui.TextBlock("x")
    tb._realize()

    calls = []
    class FakeQueue:
        def try_enqueue(self, fn):
            calls.append(1)
            fn()
    ui._dispatcher._queue = FakeQueue()
    ui._dispatcher._ui_thread_id = threading.get_ident()  # we ARE the UI thread

    tb.text = "direct"
    assert calls == []                 # no marshalling
    assert tb._native.text == "direct"
