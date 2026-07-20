# PyWinUI

Build **native WinUI 3** (Windows App SDK) desktop applications in idiomatic Python.

Not a themed look-alike and not a reimplementation — these are real WinUI 3
controls, wrapped so that you never have to touch WinRT unless you want to.

```python
import pywinui as ui


class CounterApp(ui.App):
    def build(self):
        count = ui.TextBlock("0", font_size=32)

        def bump(sender, args):
            count.text = str(int(count.text) + 1)

        return ui.Window(
            title="PyWinUI Counter",
            content=ui.StackPanel(
                spacing=12, padding=24,
                children=[
                    ui.TextBlock("Counter", font_size=20),
                    count,
                    ui.Button("Increment", on_click=bump),
                ],
            ),
        )


CounterApp().run()
```

> **Status: early alpha.** The architecture is verified end-to-end against the
> real bindings on Windows 11, but only five controls are wrapped so far. The API
> may change. See [Current scope](#current-scope) before depending on it.

## Why now

The WinUI 3 projections for Python ([PyWinRT](https://github.com/pywinrt/pywinrt))
only landed in March 2025. Before that, a Python WinUI 3 app meant hand-rolling
raw WinRT. The bindings now exist and work; PyWinUI is the ergonomics layer on
top of them.

## Requirements

- **Windows 10/11** with the [Windows App Runtime](https://aka.ms/windowsappsdk/runtime)
- **python.org Python 3.10+** — *not* the Microsoft Store build, which is a
  packaged app and fails the runtime bootstrap with `ERROR_NOT_SUPPORTED`

## Install

```
pip install pywinui
```

The `winui3-*` dependencies are Windows-only and install automatically there.
On other platforms the package still installs and imports (the native layer is
loaded lazily), so the test suite and editor tooling work anywhere — but
anything that realizes a control needs Windows.

## Two ways to build a tree

Both are first-class and they compose.

```python
# Flutter style — the tree is a value. Best for reusable components.
ui.StackPanel(children=[ui.TextBlock("a"), ui.TextBlock("b")])

# With style — handles loops, conditionals and local references far better.
with ui.StackPanel() as panel:
    ui.TextBlock("Items")
    for name in items:
        ui.Button(name, on_click=make_handler(name))
```

## Async without the ceremony

Handlers may be `async def`. They're scheduled off the UI thread automatically,
WinRT async operations are awaited like any coroutine, and writes back to
widgets are marshalled onto the UI thread for you.

```python
async def read_file(sender, args):
    status.text = "Reading..."
    file = await StorageFile.get_file_from_path_async(path)
    text = await FileIO.read_text_async(file)      # real WinRT I/O
    status.text = f"{len(text)} chars"             # marshalled back to the UI
```

Failures arrive as ordinary Python exceptions — a missing path raises
`FileNotFoundError`.

## The escape hatch

The curated surface covers the common path with type hints, validation and value
conversions. Anything not wrapped still forwards by name, and `widget.native`
gives you the raw WinUI control:

```python
btn = ui.Button("Save")
btn.native.background = some_brush    # raw WinUI, fully supported
```

## Current scope

| Working | Not yet |
|---|---|
| `TextBlock`, `TextBox`, `Button`, `StackPanel`, `Grid`, `Window` | The other ~100 WinUI controls |
| Both tree-building styles, attach/detach | Data binding, styles, resource dictionaries |
| `async def` handlers, awaiting WinRT ops | Control templates, virtualized lists |
| Off-thread writes auto-marshalled | Off-thread *reads* (wrap in `run_on_ui`) |
| `padding`/`margin`, `visible`, content boxing | `Grid.Row`/`Grid.Column` attached properties |
| Running from a Python environment | Packaging a double-clickable app (MSIX) |

**Packaging is unproven.** Shipping an app to end users means bundling Python and
the Windows App Runtime, likely as MSIX, and that path has not been prototyped.
Today this is a library for developers who already have Python installed.

## Examples

```
pip install -e ".[examples,dev]"
python examples/counter.py
python examples/async_file_read.py
```

## Tests

```
pytest
```

The suite runs **anywhere** — no Windows required. It swaps in a fake native
layer to lock down the tree model, both building styles, attach/detach, property
forwarding, value conversions and thread marshalling. What it deliberately
cannot cover is the binding boundary itself; that is verified by running the
examples on Windows.

## License

MIT
