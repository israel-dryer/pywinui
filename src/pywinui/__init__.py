"""
PyWinUI — a small, Pythonic wrapper over PyWinRT's ``winui3`` (Windows App SDK)
bindings.

Goal: declare native WinUI 3 UIs the way you'd write Flet/Flutter-style Python,
while all the verbose WinRT glue stays in ONE place (the `_Native` layer below).

    import pywinui as ui

    class Demo(ui.App):
        def build(self):
            count = ui.TextBlock("0", font_size=32)

            def bump(sender, args):
                count.text = str(int(count.text) + 1)

            return ui.Window(
                title="PyWinUI Demo",
                content=ui.StackPanel(
                    spacing=12, padding=24,
                    children=[
                        ui.TextBlock("Counter", font_size=20),
                        count,
                        ui.Button("Increment", on_click=bump),
                    ],
                ),
            )

    Demo().run()

--------------------------------------------------------------------------------
STATUS: working proof of concept — verified end-to-end on Windows 11 against
winui3 3.2.1 / Python 3.13. A real window opens, native controls realize, click
handlers mutate them, and off-thread writes marshal back via the DispatcherQueue.

The import paths are confirmed: `winui3.microsoft.ui.xaml` is correct (the
PyWinRT docs' `winui3.microsoft.windows.ui.xaml` was indeed a typo).

The async bridge is verified too: `async def` handlers, awaiting WinRT async
operations, and off-thread writes marshalling back to the UI. No `# VERIFY`
tags remain — `TextBox` and `Grid` are the only controls not yet instantiated,
and they follow the same pattern as the verified ones.
--------------------------------------------------------------------------------
"""

from __future__ import annotations
import asyncio
import contextlib
import contextvars
import inspect
import threading
from typing import Any, Callable, Optional


# =============================================================================
# NATIVE GLUE — the only place that imports winui3.
# Swap/verify these against the installed packages; the rest of the file is
# plain Python and never mentions WinRT directly.
# =============================================================================
class _Native:
    """Lazy holder for winui3 modules. Imported on first use so that importing
    `pywinui` on a non-Windows box (e.g. for tests of the pure-Python parts)
    doesn't explode."""

    _loaded = False

    def load(self) -> None:
        if self._loaded:
            return
        try:
            # Import paths confirmed against winui3 3.2.1 on Windows 11.
            from winui3.microsoft.ui.xaml import Application, Window, Thickness
            from winui3.microsoft.ui.xaml import Visibility
            from winui3.microsoft.ui.xaml.controls import (
                Button, TextBlock, TextBox, StackPanel, Grid,
            )
            # Separate package from the Xaml one — the dispatcher lives in
            # Microsoft.UI.Dispatching, not under Xaml.
            from winui3.microsoft.ui.dispatching import DispatcherQueue
            from winui3.microsoft.windows.applicationmodel.dynamicdependency import (
                bootstrap,
            )
            # WinRT object-typed properties (Button.Content etc.) won't accept a
            # bare Python str — it has to be boxed into an IInspectable first.
            from winrt.windows.foundation import PropertyValue
        except ImportError as exc:  # give a useful message, not a raw traceback
            raise RuntimeError(
                "PyWinUI requires the winui3 bindings and the Windows App "
                "Runtime.\n"
                # ASCII only: this prints to consoles still using cp1252, where
                # non-ASCII punctuation shows up as mojibake.
                "  1) pip install the namespace packages (each namespace is its\n"
                "     own wheel - .Controls and .Bootstrap are NOT included by\n"
                "     their parent):\n"
                "       winui3-Microsoft.UI.Xaml\n"
                "       winui3-Microsoft.UI.Xaml.Controls\n"
                "       winui3-Microsoft.UI.Dispatching\n"
                "       winui3-Microsoft.Windows.ApplicationModel.DynamicDependency\n"
                "       winui3-Microsoft.Windows.ApplicationModel."
                "DynamicDependency.Bootstrap\n"
                "       winrt-Windows.Foundation   (boxing + event delegates)\n"
                "  2) Install the Windows App Runtime: "
                "https://aka.ms/windowsappsdk/runtime\n"
                "  3) Use python.org Python, NOT the Microsoft Store build "
                "(the Store build fails bootstrap with ERROR_NOT_SUPPORTED).\n"
                "  4) If the cause above is 'DLL load failed ... filename or "
                "extension is too long', your environment sits too deep: the\n"
                "     winui3 DLL names are long enough to exceed Windows'"
                " 260-char MAX_PATH. Use a shorter venv path or enable long\n"
                "     paths (LongPathsEnabled)."
            ) from exc

        # Stash the pieces the wrapper needs.
        self.Application = Application
        self.Window = Window
        self.Thickness = Thickness
        self.Visibility = Visibility
        self.Button = Button
        self.TextBlock = TextBlock
        self.TextBox = TextBox
        self.StackPanel = StackPanel
        self.Grid = Grid
        self.DispatcherQueue = DispatcherQueue
        self.bootstrap = bootstrap
        self.PropertyValue = PropertyValue
        self._loaded = True

    def box(self, value: Any):
        """Box a Python primitive into an IInspectable for object-typed
        properties (``Button.Content``, ``ContentControl.Content``, ...).
        WinRT objects and realized controls pass through untouched."""
        if isinstance(value, str):
            return self.PropertyValue.create_string(value)
        if isinstance(value, bool):
            return self.PropertyValue.create_boolean(value)
        if isinstance(value, int):
            return self.PropertyValue.create_int32(value)
        if isinstance(value, float):
            return self.PropertyValue.create_double(value)
        return value

    def thickness(self, value: Any):
        """Turn a Pythonic padding/margin (number or 4-tuple) into a WinRT
        Thickness struct."""
        if isinstance(value, (int, float)):
            l = t = r = b = float(value)
        else:
            l, t, r, b = (float(v) for v in value)
        return self.Thickness(l, t, r, b)

    def visibility(self, value: bool):
        """Map a Pythonic bool to WinUI's Visibility enum (Visible/Collapsed —
        note WinUI has no 'Hidden'; Collapsed removes it from layout)."""
        return self.Visibility.VISIBLE if value else self.Visibility.COLLAPSED


_native = _Native()


# =============================================================================
# CONTEXT-MANAGER TREE BUILDING
# `with SomeContainer():` opens a scope; any Widget constructed inside it
# auto-attaches to that container. Implemented with a ContextVar (async-safe)
# instead of threading.local, so it survives an asyncio/dispatcher layer later.
# Nesting works via the token stack: each __enter__ set()s and each __exit__
# reset()s, so `_open_parent.get()` is always the innermost open container.
# =============================================================================
_open_parent: contextvars.ContextVar[Optional["Widget"]] = contextvars.ContextVar(
    "pywinui_open_parent", default=None
)


@contextlib.contextmanager
def detached():
    """Suppress auto-attach inside a `with` block. Widgets constructed in this
    scope are NOT added to the enclosing container, so you can build one for
    manual placement later:

        with ui.StackPanel() as panel:
            ui.TextBlock("in tree")            # attaches
            with ui.detached():
                loose = ui.Button("later")      # does NOT attach
        panel.add(loose)                        # place it explicitly
    """
    token = _open_parent.set(None)
    try:
        yield
    finally:
        _open_parent.reset(token)


# =============================================================================
# DISPATCHER + ASYNC BRIDGE
# WinUI has ONE UI thread; touching a control from another thread throws, and
# WinRT I/O returns IAsyncOperation objects instead of plain values. This
# section hides both so handlers can be ordinary `async def`:
#   * run_on_ui(fn)   -> marshal a call back onto the UI thread (any thread ok)
#   * as_future(op)   -> await a WinRT async op like a normal coroutine
#   * async handlers  -> auto-scheduled on a background loop
#   * off-thread widget writes -> auto-marshalled (see Widget._set_native)
#
# ARCHITECTURE (v1 — simple + robust): Application.start owns the UI thread with
# the WinUI message pump, so we can't also run asyncio there. Instead asyncio
# runs on a separate daemon thread; async handlers run there, and anything that
# mutates UI hops back via the DispatcherQueue. The nicer alternative — one
# event loop *driven by* the DispatcherQueue so handlers run on the UI thread —
# is noted as future work; it removes the marshalling but is far more code.
# =============================================================================
class _Dispatcher:
    def __init__(self) -> None:
        self._queue: Any = None
        self._ui_thread_id: Optional[int] = None

    @property
    def bound(self) -> bool:
        return self._queue is not None

    def bind_current_thread(self) -> None:
        """Call once from the UI thread during app startup."""
        self._queue = _native.DispatcherQueue.get_for_current_thread()
        self._ui_thread_id = threading.get_ident()

    def on_ui_thread(self) -> bool:
        return threading.get_ident() == self._ui_thread_id

    def post(self, fn: Callable[[], Any]) -> None:
        if self._queue is None:
            fn()  # no UI yet (e.g. building before start) — just run inline
            return
        self._queue.try_enqueue(fn)  # a plain Python callable satisfies the delegate


_dispatcher = _Dispatcher()
_loop: Optional[asyncio.AbstractEventLoop] = None


def run_on_ui(fn: Callable[..., Any], *args: Any, **kwargs: Any) -> None:
    """Run `fn` on the UI thread. Safe from any thread."""
    _dispatcher.post(lambda: fn(*args, **kwargs))


def as_future(winrt_async_op: Any) -> "asyncio.Future":
    """Adapt a WinRT IAsyncOperation / IAsyncAction into an asyncio Future.

        text = await ui.as_future(FileIO.read_text_async(file))

    NOTE: as of PyWinRT 3.2 the projected async types implement ``__await__``
    already, so plain ``await op`` works and is the idiomatic path (errors
    surface as normal Python exceptions either way). This wrapper is kept for
    the cases where you want a real Future — to pass to ``asyncio.gather``,
    ``wait_for``, or to hold and cancel later — and as a stable seam if the
    projection ever changes. It no longer hand-rolls the Completed delegate.
    """
    return asyncio.ensure_future(winrt_async_op)


def _schedule_coro(coro: Any) -> None:
    """Schedule an async-handler coroutine on the background loop."""
    if _loop is None:
        raise RuntimeError("async handler fired before the app loop started")
    asyncio.run_coroutine_threadsafe(coro, _loop)


def _wrap_async(handler: Callable[..., Any]) -> Callable[..., Any]:
    """If a handler is `async def`, return a sync shim that schedules it and
    returns immediately, so the WinRT event callback doesn't block the UI."""
    # inspect, not asyncio: asyncio.iscoroutinefunction is deprecated in 3.14.
    if inspect.iscoroutinefunction(handler):
        def _fire(sender: Any, args: Any, _h: Callable[..., Any] = handler) -> None:
            _schedule_coro(_h(sender, args))
        return _fire
    return handler


# =============================================================================
# BASE WIDGET
# Declaration and native realization are deliberately separate: you build a
# cheap Python tree first, then `_realize()` walks it and creates native
# objects (which must happen after bootstrap, on the UI thread).
# =============================================================================
class Widget:
    # Attribute names that live on the Python object, never forwarded to native.
    _INTERNAL = {"_native", "_props", "_events"}

    def __init__(self, **props: Any) -> None:
        # Set internals via object.__setattr__ so our custom __setattr__ below
        # doesn't try to forward them to a native object that doesn't exist yet.
        object.__setattr__(self, "_native", None)
        # `attached` is a construct-time-only flag (NOT a live property): it
        # controls the one-shot auto-parenting below. Consume it so it never
        # gets forwarded to native.
        attached = props.pop("attached", True)
        # Split "on_*" handlers out from ordinary properties.
        events = {k[3:]: props.pop(k) for k in list(props) if k.startswith("on_")}
        object.__setattr__(self, "_events", events)
        object.__setattr__(self, "_props", props)

        # If we're being constructed inside a `with container:` block, attach
        # ourselves to that container. Merely constructing the widget is the
        # side effect that adds it to the tree (nicegui-style). `attached=False`
        # (or a surrounding ui.detached()) opts out for manual placement.
        if attached:
            parent = _open_parent.get()
            if parent is not None:
                parent._adopt(self)

    # ---- container protocol -------------------------------------------------
    def _adopt(self, child: "Widget") -> None:
        """Attach a child constructed inside this widget's `with` block.
        Leaf controls reject children; containers override this."""
        raise TypeError(f"{type(self).__name__} cannot contain children")

    def __enter__(self) -> "Widget":
        # Push self as the current parent; remember the token so nested blocks
        # unwind correctly on exit.
        object.__setattr__(self, "_ctx_token", _open_parent.set(self))
        return self

    def __exit__(self, *exc: Any) -> bool:
        token = self.__dict__.pop("_ctx_token", None)
        if token is not None:
            _open_parent.reset(token)
        return False  # never swallow exceptions

    # ---- override points for subclasses -------------------------------------
    def _construct(self) -> Any:
        """Return a freshly created native WinRT control."""
        raise NotImplementedError

    def _apply(self) -> None:
        """Apply queued properties to the native control. Default: forward each
        kwarg by name, since PyWinRT already exposes properties as snake_case."""
        for name, value in self._props.items():
            self._set_native(name, value)

    # ---- property forwarding ------------------------------------------------
    def _set_native(self, name: str, value: Any) -> None:
        # Map Pythonic property names/values onto the WinRT surface.
        if name in ("padding", "margin"):
            value = _native.thickness(value)
        elif name == "visible":
            name, value = "visibility", _native.visibility(value)
        elif name == "content":
            # Object-typed slot: primitives must be boxed, controls must not.
            value = _native.box(value)
        if _dispatcher.bound and not _dispatcher.on_ui_thread():
            # Write coming from an async handler on the background loop: hop it
            # onto the UI thread so the caller never has to think about threads.
            _dispatcher.post(lambda: setattr(self._native, name, value))
        else:
            setattr(self._native, name, value)

    # ---- realization machinery ----------------------------------------------
    def _realize(self) -> Any:
        if self._native is not None:
            return self._native
        self._native = self._construct()
        self._apply()
        for event, handler in self._events.items():
            # PyWinRT projects WinRT events as add_<event>/remove_<event>.
            # _wrap_async lets handlers be `async def` transparently.
            getattr(self._native, f"add_{event}")(_wrap_async(handler))
        return self._native

    # ---- the escape hatch ---------------------------------------------------
    @property
    def native(self) -> Any:
        """The underlying WinUI 3 control — the blessed way to reach anything
        the wrapper doesn't cover. Realizes on access (idempotent), so advanced
        users can grab and tweak the real object even mid-build:

            btn = ui.Button("Save")
            btn.native.background = some_brush   # raw WinUI, fully supported
        """
        return self._realize()

    # ---- Pythonic attribute access ------------------------------------------
    # Lets you write `label.text = "hi"` before OR after realization and read
    # `label.text` back, transparently forwarding to the native control.
    def __setattr__(self, name: str, value: Any) -> None:
        if name in self._INTERNAL or name.startswith("_"):
            object.__setattr__(self, name, value)
            return
        native = self.__dict__.get("_native")
        if native is not None:
            self._set_native(name, value)
        else:
            self._props[name] = value

    def __getattr__(self, name: str) -> Any:
        # __getattr__ only fires when normal lookup fails, so methods/internals
        # are unaffected. Forward reads to the native control once realized,
        # otherwise return whatever was queued.
        native = self.__dict__.get("_native")
        if native is not None:
            return getattr(native, name)
        props = self.__dict__.get("_props", {})
        if name in props:
            return props[name]
        raise AttributeError(name)


# =============================================================================
# CONTROLS
# Adding a new control is the whole point of the design: subclass Widget,
# implement `_construct`, and (optionally) map a positional arg to a property.
# =============================================================================
class TextBlock(Widget):
    def __init__(self, text: str = "", **props: Any) -> None:
        props.setdefault("text", text)
        super().__init__(**props)

    def _construct(self):
        return _native.TextBlock()


class TextBox(Widget):
    def __init__(self, text: str = "", **props: Any) -> None:
        props.setdefault("text", text)
        super().__init__(**props)

    def _construct(self):
        return _native.TextBox()  # not yet exercised on Windows (same pattern)


class Button(Widget):
    def __init__(self, label: Optional[str] = None, **props: Any) -> None:
        if label is not None:
            props.setdefault("content", label)  # WinUI Button uses Content
        super().__init__(**props)

    def _construct(self):
        return _native.Button()


class _Panel(Widget):
    """Base for layout containers that hold a list of child Widgets."""

    def __init__(self, children: Optional[list["Widget"]] = None, **props: Any) -> None:
        super().__init__(**props)
        object.__setattr__(self, "_child_widgets", children or [])

    def _adopt(self, child: "Widget") -> None:
        # Panels hold many children; `with`-adopted ones append to whatever the
        # `children=[...]` kwarg already seeded, so the two styles compose.
        self._child_widgets.append(child)

    def add(self, *children: "Widget") -> "_Panel":
        """Attach children manually — e.g. ones built inside ui.detached().
        Returns self for chaining."""
        self._child_widgets.extend(children)
        return self

    def _apply(self) -> None:
        super()._apply()  # spacing, padding, orientation, ...
        container = self._native.children
        for child in self._child_widgets:
            container.append(child._realize())


class StackPanel(_Panel):
    def _construct(self):
        return _native.StackPanel()


class Grid(_Panel):
    # Row/column definitions and Grid.Row/Grid.Column attached properties are
    # left as an exercise — attached properties need their own helper. Kept here
    # so the container hierarchy is visible.
    def _construct(self):
        return _native.Grid()  # not yet exercised on Windows (same pattern)


# =============================================================================
# WINDOW + APP LIFECYCLE
# This is the least-trodden part of the binding; treat _on_start as the piece
# most likely to need adjustment against the real packages.
# =============================================================================
class Window(Widget):
    def __init__(self, content: Optional[Widget] = None, **props: Any) -> None:
        super().__init__(**props)
        object.__setattr__(self, "_content", content)

    def _adopt(self, child: "Widget") -> None:
        # A Window has a single content slot, so a second child is an error —
        # the "slot" semantics (single vs list) are what let one protocol serve
        # both container kinds.
        if self._content is not None:
            raise TypeError("Window already has content; it holds a single child")
        object.__setattr__(self, "_content", child)

    def _construct(self):
        return _native.Window()

    def _apply(self) -> None:
        super()._apply()  # title, etc.
        if self._content is not None:
            self._native.content = self._content._realize()


class App:
    """Subclass and implement `build()` returning a Window, then call `run()`."""

    def build(self) -> Window:
        raise NotImplementedError("App subclasses must implement build() -> Window")

    def run(self) -> None:
        _native.load()
        self._start_background_loop()
        boot = _native.bootstrap
        # Start the Windows App Runtime; prompt the user to install it if missing.
        opts = boot.InitializeOptions.ON_NO_MATCH_SHOW_UI
        try:
            with boot.initialize(options=opts):
                # Application.start blocks and pumps the UI message loop until exit.
                # The callback runs on the freshly created UI thread.
                _native.Application.start(self._on_start)
        finally:
            self._stop_background_loop()

    def _on_start(self, _params: Any) -> None:
        # We're on the UI thread now: capture it + the dispatcher queue so
        # off-thread widget writes and run_on_ui() can marshal back here.
        _dispatcher.bind_current_thread()
        # Keep the window on the app: it's the root of the live tree, and both
        # user code and tooling need a handle on it after startup.
        self.window = self.build()
        self.window._realize()
        self.window._native.activate()

    # -- background asyncio loop (see DISPATCHER + ASYNC BRIDGE) ---------------
    def _start_background_loop(self) -> None:
        global _loop
        _loop = asyncio.new_event_loop()
        self._loop_thread = threading.Thread(
            target=_loop.run_forever, name="pywinui-async", daemon=True
        )
        self._loop_thread.start()

    def _stop_background_loop(self) -> None:
        global _loop
        if _loop is not None:
            _loop.call_soon_threadsafe(_loop.stop)
            _loop = None


__all__ = [
    "App", "Window",
    "TextBlock", "TextBox", "Button",
    "StackPanel", "Grid",
    "Widget",
    "detached", "run_on_ui", "as_future",
]
