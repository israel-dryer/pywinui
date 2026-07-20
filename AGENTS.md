# PyWinUI — Agent Briefing

You are picking up **PyWinUI**, a Pythonic wrapper library that lets developers
build **native WinUI 3 (Windows App SDK) desktop applications in Python**. This
document explains the goal, the design decisions already made (and *why*), the
current state of the code, the constraints you must respect, and what remains.
Read it fully before changing anything.

---

## 1. The goal

Let someone write a modern Windows 11 desktop app — real native WinUI 3 controls,
not a themed look-alike — using ordinary, idiomatic Python.

The guiding principle: **a user should not know they are using WinUI/WinRT
underneath, except when they reach for advanced features.** The common path feels
like a normal Python UI library (Flet/Flutter/nicegui-flavored). The WinRT
machinery only surfaces when someone deliberately goes past the curated surface.

This is a wrapper, not a reimplementation. Underneath sit the **PyWinRT `winui3`
projection packages** (auto-generated Python bindings for the Windows App SDK,
e.g. `winui3-Microsoft.UI.Xaml`). We wrap those to make them pleasant.

### Why this is feasible now (and wasn't before)
The `winui3` namespace projections were only added to PyWinRT around **March
2025**. An earlier same-named attempt (github.com/bornacheck/PyWinUI) was archived
in **May 2023** with essentially no code — at that time no WinUI projections
existed, so it would have meant hand-rolling raw WinRT. That foundation now exists;
the remaining work is the ergonomics layer, which is what this project is.

---

## 2. Non-negotiable design principles

These are the spine of the project. Preserve them unless you have a strong,
explicit reason and you update this document.

### 2.1 Isolate all native calls behind one glue layer
Every `winui3` import and every WinRT-specific call lives in the `_Native` class
(and a couple of clearly marked helpers). The rest of the library is pure Python
and never mentions WinRT. This is what makes the code testable off-Windows and
lets us fix binding changes in one place.

### 2.2 The `# VERIFY` convention
Any line that mirrors the C#/WinRT object model but has **not** been confirmed
against the real packages on Windows is tagged `# VERIFY`. These are the calls an
agent on a Windows machine must validate. Do not silently remove the tags; remove
one only after confirming that specific call works, and say so in the commit.

### 2.3 Declaration is separate from realization
Constructing widgets builds a cheap pure-Python tree. A later `_realize()` pass
walks that tree and creates the native controls. Native objects can only be
created after the runtime is bootstrapped and on the UI thread, so this split is
deliberate — keep it. Never create native controls in `__init__`.

### 2.4 Curated surface + escape hatch
Common properties/events get explicit, Pythonic, validated wrappers. Anything not
wrapped still works via attribute forwarding, and `widget.native` is the blessed
way to reach the raw WinUI control for advanced use. The curated layer is where
type hints, validation, and value conversions live; forwarding covers the long
tail. Keep both tiers.

### 2.5 Two tree-building styles, both first-class
- **Flutter style:** `Panel(children=[A(), B()])` — the tree is a value.
- **With style:** `with Panel(): A(); B()` — widgets constructed inside a block
  auto-attach to it (nicegui-style, via a `contextvars` parent stack).

They compose (a panel can be seeded with `children=[...]` and extended inside a
`with`). **With-style is the idiomatic default** (it handles loops, conditionals,
and local child references far better); **Flutter style is preferred for reusable
components**, where a function returning a widget is referentially transparent.
Do not remove either.

### 2.6 Structure vs rendering are different axes
- **Attachment** (structural: "is this widget in the tree?") is a one-time,
  construct-time decision. Controlled by auto-parenting, `attached=False`,
  `detached()`, and manual `panel.add(...)`.
- **Visibility** (rendering: "is this widget shown?") is a live property,
  `visible: bool`, mapped to WinUI's `Visibility` enum.

Never conflate them. Critically: **WinUI enforces one parent per element** —
adding an already-parented control elsewhere throws. So "hide it and move it
later" does not work; you must detach/re-add. This constraint is why `attached`
and `visible` are separate and must stay separate.

### 2.7 Pythonic naming, curated conversions
PyWinRT already projects properties as `snake_case`, so forwarding is mostly a
`setattr`. A small number of properties need conversion; these live in one place
(a `_Native` helper + a branch in `_set_native`). Current examples:
`padding`/`margin` → `Thickness`, `visible` → `Visibility`. New conversions
(`enabled`, `opacity`, colors, alignment enums) follow the identical pattern.

### 2.8 Threads and async are hidden
WinUI has one UI thread; touching a control from another thread throws, and WinRT
I/O returns `IAsyncOperation` objects. The library hides this:
- Event handlers may be `async def`; they are auto-scheduled.
- Widget writes made off the UI thread are auto-marshalled onto it.
- WinRT async ops are awaited directly — **PyWinRT 3.2 projects `__await__`**, so
  `await picker.pick_single_file_async()` just works and WinRT failures arrive as
  ordinary Python exceptions (a missing path raises `FileNotFoundError`).
  `as_future(op)` is now a thin `ensure_future` wrapper, kept for when you need a
  real Future (`gather`, `wait_for`, cancellation) — not a hand-rolled bridge.
- `run_on_ui(fn)` is the explicit escape hatch.

Current architecture (v1, simple + robust): asyncio runs on a **separate daemon
thread** because `Application.start` owns the UI thread's message pump; UI
mutations hop back via the `DispatcherQueue`. Known limitation: only *writes* are
auto-marshalled, not *reads*.

---

## 3. Current state

Files:

| File | What it is |
|------|-----------|
| `src/pywinui/__init__.py` | The library. Sole source file. |
| `examples/counter.py` | Minimal counter app — runs for real on Windows. |
| `examples/async_file_read.py` | Async handler awaiting real WinRT I/O — also runs. |
| `tests/test_pywinui.py` | Pytest suite — 31 tests, all passing. |
| `pyproject.toml` | Packaging + the full winui3 dependency list. |
| `README.md` / `LICENSE` | Public-facing docs; MIT. |
| `AGENTS.md` | This document (project root). |

It's a package directory holding one module so that splitting `_Native` or the
controls into submodules later doesn't change the import path or the layout.

Both examples have been executed against the real bindings, not just written.

What exists in `pywinui.py`: the `_Native` glue layer; a `Widget` base with
lazy realization, attribute forwarding, the `.native` escape hatch, and the
container/context-manager protocol; controls `TextBlock`, `TextBox`, `Button`,
`StackPanel`, `Grid`; `Window`; an `App` lifecycle with bootstrap + background
asyncio loop; and the dispatcher/async bridge (`run_on_ui`, `as_future`,
`detached`).

### What the tests cover vs. don't
The suite runs **anywhere** (no Windows needed) by swapping a fake native layer
in via a fixture. It locks in: both tree-building styles and their composition,
nesting and context cleanup (including on exceptions), the Window single-content
slot and leaf rejection, attach/detach/`add`, property queueing and forwarding,
the `padding→Thickness`, `visible→Visibility` and `content→boxed` conversions,
`as_future` adaptation and error propagation, and async/thread marshalling in
both directions.

The tests still **cannot** cover the native calls themselves — the fake layer
mirrors the binding contract, it doesn't prove it. That split is correct, but it
does mean the fake must be kept honest: when a real binding turns out to behave
differently (as boxing did), update `FakeNative` to match, or the suite will
happily keep certifying the wrong contract. Re-run the two examples on Windows
after any change to `_Native`.

---

## 4. Hard constraints (read before running or editing)

1. **Native code only runs on Windows** with the **Windows App Runtime**
   installed. The library imports `winui3` lazily so it can be imported (and
   tested) elsewhere, but anything that realizes controls needs Windows.
2. **Use python.org Python, not the Microsoft Store build.** The Store build is a
   packaged app and fails the runtime bootstrap with `ERROR_NOT_SUPPORTED`.
3. **Import paths (resolved).** `winui3.microsoft.ui.xaml` is correct — the docs'
   `winui3.microsoft.windows.ui.xaml` was a typo. **Every WinRT namespace is a
   separate wheel**, and parents do not pull in children: `...Xaml.Controls` and
   `...DynamicDependency.Bootstrap` must be installed explicitly, and
   `winrt-Windows.Foundation` is required for value boxing and event delegates.
   The full list is in the install hint inside `_Native.load()`.
4. **Keep the pure-Python / native split.** Do not scatter `winui3` imports
   through the codebase. New native touchpoints go in `_Native` and get a
   `# VERIFY` tag.
5. **Respect the one-parent rule** (see 2.6) in any layout/reparenting code.
6. **Don't break the two marshalling tests** without intent — they encode the
   current threading contract.

---

## 5. Open work (roughly prioritized)

### 5.1 Verify the `# VERIFY` calls on Windows — mostly DONE
Verified on Windows 11, winui3 3.2.1, python.org Python 3.13, by launching a
real window (counter app: native controls realized, click handler mutated them,
off-thread write marshalled back). Confirmed as originally guessed:
- **App lifecycle:** `Application.start(callback)` is the right unpackaged entry
  point — no `Application` subclass needed. `Window.activate()`, `.title`,
  `.content` all correct.
- **Bootstrap:** `bootstrap.initialize(options=...)` as a context manager, with
  `InitializeOptions.ON_NO_MATCH_SHOW_UI`.
- **Dispatcher:** `get_for_current_thread()` and `try_enqueue(fn)` both work, and
  a plain Python callable *does* satisfy the delegate.
- **Layout:** `Panel.children.append(...)` works.
- **Values:** `Thickness(l,t,r,b)` correct; `Visibility.VISIBLE`/`COLLAPSED`
  correct. Ints are coerced to double automatically (`font_size=20` → `20.0`).
- **Events:** `add_<event>` / `remove_<event>` with `(sender, args)` is correct.

Two things were **not** as assumed, and are now fixed in `_Native`:
- **Object-typed properties need boxing.** `Button.content = "Go"` raises
  `TypeError: not a System.Object`. Strings/numbers must go through
  `PropertyValue.create_*`; this is the new `_Native.box()` helper, wired into
  `_set_native` for `content`. Any future object-typed property (`Tag`,
  `ContentControl.Content`, ...) needs the same treatment.
- **Namespaces are per-wheel** (see §4.3) — the missing `winrt-Windows.Foundation`
  package was what made event subscription look broken.

The **async bridge is now verified too** (`example_async.py`, driven end-to-end):
an `async def` handler returned in 0.1ms without blocking the UI thread, awaited
two chained WinRT async ops, and its result landed in a native `TextBlock` via
the DispatcherQueue. The originally-assumed mechanism (settable `Completed` +
`get_results()`) does work — it was verified directly — but it turned out to be
unnecessary, hence the `as_future` simplification in §2.8.

**No `# VERIFY` tags remain.** `TextBox` and `Grid` are the only controls never
instantiated; they follow the same pattern as verified ones.

Also observed: queued `run_on_ui` work cannot run while a handler is blocking the
UI thread — it only drains once the handler returns and the pump resumes. Obvious
in hindsight, but it makes any blocking wait inside a handler a deadlock risk.

### 5.2 DispatcherQueue-driven single event loop
Retire the two-thread split by running one event loop driven by the
`DispatcherQueue`, so handlers run *on* the UI thread and marshalling largely
disappears (this also fixes the read-marshalling gap). Bigger job. When you do it,
the two marshalling tests are the contract to preserve or deliberately update.

### 5.3 More controls + attached properties
Add the common controls. **Grid** needs a helper for attached properties
(`Grid.Row`/`Grid.Column`) — they don't fit the plain-property model and are
currently stubbed. Design that helper before adding grid-heavy layouts.

### 5.4 More curated conversions
`enabled`, `opacity`, colors/brushes, alignment enums — each follows the
`padding`/`visible` template.

### 5.5 Packaging
Shipping a double-clickable app means bundling Python + the Windows App Runtime,
ideally as MSIX, and reconciling that with the dynamic-dependency bootstrap. This
is the least-trodden path and worth prototyping early.

### 5.6 No declarative XAML (by design, for now)
The tree is built imperatively. There is no XAML markup path, hot-reload, or
designer. A declarative DSL over the bindings would be future work, not a current
goal.

### 5.7 Naming / PyPI
`pywinui` is currently unclaimed on PyPI — register a placeholder to hold the name.
Note the archived GPL-3.0 repo of the same name exists (no code); pick a license
deliberately.

---

## 6. Reference notes for translating WinUI docs

There are no Python API docs for the bindings (they're generated). Use Microsoft's
Windows App SDK / WinRT reference and translate names with PyWinRT conventions:
- Namespaces → lowercase, no underscores.
- Type names → stay `CapitalizedWords`.
- Methods/properties/fields/events → `snake_case`.
- Enum members → `UPPER_CASE`.
- Many methods are async and return `IAsyncOperation`-family types (see 2.8).

---

## 7. How to run the tests

```
pip install -e ".[dev]"
pytest
```

`pythonpath = ["src"]` in `pyproject.toml` means the suite also runs straight
from a checkout without installing. To exercise the native layer you must be on
Windows and run the examples by hand:

```
pip install -e ".[examples,dev]"
python examples/counter.py
python examples/async_file_read.py
```

They pass on any OS. If you touch the tree model, attach/detach, property
forwarding, conversions, or marshalling, keep them green — that suite is the
guardrail protecting the design decisions above.
