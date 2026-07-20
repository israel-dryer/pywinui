# PyWinUI — Agent Briefing

You are picking up **PyWinUI**, a Pythonic wrapper library that lets developers
build **native WinUI 3 (Windows App SDK) desktop applications in Python**. This
document explains the goal, the design decisions already made (and *why*), the
current state of the code, the constraints you must respect, and what remains.
Read it fully before changing anything.

> Claude Code loads this file via an `@AGENTS.md` import in `CLAUDE.md`. Keep the
> project knowledge **here** — `CLAUDE.md` is only for Claude-specific notes, so
> that other agents reading `AGENTS.md` get the whole picture.

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

This principle has already paid off: both binding surprises found during
verification (§5) were absorbed inside `_Native` in a few lines, with the rest of
the library untouched.

### 2.2 The `# VERIFY` convention
Any line that mirrors the C#/WinRT object model but has **not** been confirmed
against the real packages on Windows is tagged `# VERIFY`. Remove a tag only after
confirming that specific call works, and say so in the commit.

**There are currently no `# VERIFY` tags** — the original set was all confirmed
(§5). The convention stays in force for *new* native touchpoints: if you add a
call to `_Native` that you have not personally run on Windows, tag it.

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
`padding`/`margin` → `Thickness`, `visible` → `Visibility`, and `content` →
boxed `IInspectable` (§5). New conversions (`enabled`, `opacity`, colors,
alignment enums) follow the identical pattern.

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
auto-marshalled, not *reads* (§6.1).

---

## 3. Current state

Published as an **early alpha**: `pywinui 0.1.0a1` on PyPI, source at
github.com/israel-dryer/pywinui, MIT licensed. See §7 for release mechanics.

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
| `CLAUDE.md` | Thin `@AGENTS.md` import; Claude-specific notes only. |
| `.github/workflows/tests.yml` | Test matrix + build check. |

It's a package directory holding one module so that splitting `_Native` or the
controls into submodules later doesn't change the import path or the layout.

Both examples have been executed against the real bindings, not just written.

What exists in `src/pywinui/__init__.py`: the `_Native` glue layer; a `Widget`
base with lazy realization, attribute forwarding, the `.native` escape hatch, and
the container/context-manager protocol; controls `TextBlock`, `TextBox`,
`Button`, `StackPanel`, `Grid`; `Window`; an `App` lifecycle with bootstrap +
background asyncio loop (exposing the built window as `app.window`); and the
dispatcher/async bridge (`run_on_ui`, `as_future`, `detached`).

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
   `# VERIFY` tag (§2.2).
5. **Respect the one-parent rule** (see §2.6) in any layout/reparenting code.
6. **Don't break the two marshalling tests** without intent — they encode the
   current threading contract.
7. **Beware `MAX_PATH` when creating test environments.** The winui3 extension
   module names are very long (e.g.
   `_winui3_microsoft_windows_applicationmodel_dynamicdependency_bootstrap`), so a
   venv in a deeply nested directory fails at import with `DLL load failed ...
   The filename or extension is too long`. That is a path-length problem, **not**
   a packaging bug — retest in a short path before chasing it.

---

## 5. Verified against the real bindings (reference)

Done on Windows 11, winui3 3.2.1, python.org Python 3.13, by launching real
windows. **This section is history, not a task** — it exists so nobody re-derives
it. Confirmed as originally guessed:

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
- **Async:** an `async def` handler returned in ~0.2ms without blocking the UI
  thread, awaited two chained WinRT ops, and its result landed in a native
  `TextBlock` via the DispatcherQueue.

Three things were **not** as assumed, and are now handled in `_Native`:

- **Object-typed properties need boxing.** `Button.content = "Go"` raises
  `TypeError: not a System.Object`. Strings/numbers must go through
  `PropertyValue.create_*`; this is the `_Native.box()` helper, wired into
  `_set_native` for `content`. Any future object-typed property (`Tag`,
  `ContentControl.Content`, ...) needs the same treatment.
- **Namespaces are per-wheel** (see §4.3) — the missing `winrt-Windows.Foundation`
  package was what made event subscription look broken.
- **`as_future` was unnecessary.** The assumed mechanism (settable `Completed` +
  `get_results()`) does work and was verified directly, but PyWinRT 3.2 already
  projects `__await__`, so plain `await op` is the idiomatic path (§2.8).

`TextBox` and `Grid` are the only controls never instantiated; they follow the
same pattern as verified ones.

Also observed: queued `run_on_ui` work cannot run while a handler is blocking the
UI thread — it only drains once the handler returns and the pump resumes. Obvious
in hindsight, but it makes any blocking wait inside a handler a deadlock risk.

---

## 6. Open work (roughly prioritized)

### 6.1 DispatcherQueue-driven single event loop
Retire the two-thread split by running one event loop driven by the
`DispatcherQueue`, so handlers run *on* the UI thread and marshalling largely
disappears. **This is the top priority**: it closes the read-marshalling gap,
which is currently the sharpest edge in the library — reading `widget.text` from
an async handler is silently wrong, and the only workaround is wrapping every
read in `run_on_ui`. Bigger job. The two marshalling tests are the contract to
preserve or deliberately update.

### 6.2 More controls + attached properties
Add the common controls. **Grid** needs a helper for attached properties
(`Grid.Row`/`Grid.Column`) — they don't fit the plain-property model and are
currently stubbed. Design that helper before adding grid-heavy layouts. Beyond
that lies the genuinely hard part of the surface: data binding, styles and
resource dictionaries, control templates, and virtualized lists.

### 6.3 More curated conversions
`enabled`, `opacity`, colors/brushes, alignment enums — each follows the
`padding`/`visible` template. Watch for object-typed properties, which need
`_Native.box()` rather than a plain `setattr` (§5).

### 6.4 Packaging
Shipping a double-clickable app means bundling Python + the Windows App Runtime,
ideally as MSIX, and reconciling that with the dynamic-dependency bootstrap. This
is the least-trodden path and worth prototyping early. Until it exists, PyWinUI
is a library for developers who already have Python — the README says so, and it
should keep saying so.

### 6.5 No declarative XAML (by design, for now)
The tree is built imperatively. There is no XAML markup path, hot-reload, or
designer. A declarative DSL over the bindings would be future work, not a current
goal.

---

## 7. Release process

Published to PyPI as `pywinui`; the name is claimed on both PyPI and GitHub. The
archived GPL-3.0 repo of the same name (no code) constrains nothing — this
project is MIT.

Versions on PyPI are **permanent and non-reusable**, so verify before uploading:

1. Green CI, or equivalent local verification across the supported Python
   versions if CI is unavailable.
2. Tag: `git tag -a vX.Y.Z -m "..."` and push the tag.
3. Build from a **clean clone of the tag**, not the working tree, so the
   artifacts are traceable to a commit: `git clone --branch vX.Y.Z . <tmp>` then
   `python -m build`.
4. `twine check dist/*`.
5. Install the built wheel into a fresh venv **in a short path** (§4.7) and run
   `examples/counter.py` from it. This is the step that catches packaging bugs
   the test suite structurally cannot.
6. `twine upload` (credentials in `.pypirc`, which is gitignored — never commit
   it), then create the GitHub release against the tag.

---

## 8. Reference notes for translating WinUI docs

There are no Python API docs for the bindings (they're generated). Use Microsoft's
Windows App SDK / WinRT reference and translate names with PyWinRT conventions:
- Namespaces → lowercase, no underscores.
- Type names → stay `CapitalizedWords`.
- Methods/properties/fields/events → `snake_case`.
- Enum members → `UPPER_CASE`.
- Many methods are async and return `IAsyncOperation`-family types (see §2.8).

---

## 9. How to run the tests and examples

```
pip install -e ".[dev]"
pytest
```

`pythonpath = ["src"]` in `pyproject.toml` means the suite also runs straight
from a checkout without installing. It passes on any OS. If you touch the tree
model, attach/detach, property forwarding, conversions, or marshalling, keep it
green — that suite is the guardrail protecting the design decisions above.

To exercise the native layer you must be on Windows and run the examples by hand
(CI cannot: GitHub runners have no UI thread or Windows App Runtime):

```
pip install -e ".[examples,dev]"
python examples/counter.py
python examples/async_file_read.py
```

**Run both after any change to `_Native`.** A green suite alone does not prove
the binding contract (§3).
