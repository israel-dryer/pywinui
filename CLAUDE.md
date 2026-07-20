# CLAUDE.md

@AGENTS.md

The project briefing above is the single source of truth and is deliberately
tool-neutral. Keep new project knowledge in `AGENTS.md`, not here — this file
exists only because Claude Code auto-loads `CLAUDE.md` rather than `AGENTS.md`.

## Claude Code specifics

- **Verifying native behavior.** The test suite runs anywhere, but it cannot
  prove the binding contract. When you change `_Native`, actually launch the
  examples on Windows (§9). A GUI app blocks, so run it with a timeout and treat
  exit code 124 as success:
  `timeout 8 python examples/counter.py` → 124 means the window stayed up.
  To drive an app headlessly, subclass it and override `_on_start` to fire
  handlers directly, then exit via `Application.current.exit()`.
- **Long-running probes.** When checking an unknown binding, prefer a small
  probe script that tries each call in a `try/except` and prints OK/FAIL per
  line, rather than one script that dies on the first surprise.
- **`python -u`.** Output from inside `Application.start` can be lost when the
  process exits; run probes unbuffered or you will debug phantom failures.
- **Scratchpad depth.** The session scratchpad path is deep enough to trip the
  `MAX_PATH` limit described in §4.7 — create throwaway venvs somewhere short
  (e.g. `%TEMP%\pv`) when installing the winui3 wheels.
