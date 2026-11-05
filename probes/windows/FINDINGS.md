# Windows platform findings ledger

Every measurement this project relies on about real Windows / UIA / input
behaviour lives here as one row with a **stable id** (`W-NNN`). Code and tests
cite a row id the way they would cite a CVE: the id stays true however the
code moves. A row records the environment it was measured in — a number
measured on one machine is a fact about that machine, not about the design.

Verdicts:

- **CONFIRMS** — the measurement supports the assumption the code was written on.
- **CONTRADICTS** — the assumption was wrong. A contradicting row is a defect
  in whatever assumed otherwise and must land together with the fix.
- **NEW-EDGE** — behaviour nobody had assumed either way; now documented.
- **DEFERRED** — the measurement has not actually been run yet. A deferred row
  is a placeholder, not evidence; never cite it as proof.

| Id | Area | Environment | Observed behaviour | Assumption tested | Verdict |
|----|------|-------------|--------------------|-------------------|---------|
| W-001 | PowerShell output encoding | desktop-ddpatmg, Windows PowerShell 5.1, server v0.8.13 | Non-ASCII stdout corruption was **latin-1** bytes reinterpreted, not cp1252: the first #11 fix modelled cp1252 and its live verification failed; capturing the real codepoints on the target machine showed latin-1. Fix corrected in v0.8.14 and verified live (147 characters round-tripped byte-exact through UIA). | "The mojibake is cp1252 double-encoding." | **CONTRADICTS** (the cp1252 model; lesson: capture real codepoints on the target before writing the encoding fix or its test) |
| W-002 | UIPI / SendInput delivery | Measured by vercel-labs/agent-desktop on a split-integrity rig (their finding A9-3); local re-measurement pending | `SendInput` reports the injected-event count as success in **both** the delivered arm and the UIPI-blocked arm (Medium → High integrity). The return value is never delivery evidence. | "SendInput returning success means the input landed." | **NEW-EDGE** for this repo (drives issue #14's token-integrity pre-check; local probe `probe_integrity_level.py` re-run still **DEFERRED**) |
| W-003 | Chromium UIA tree settling | agent-desktop Windows adapter documentation, Chromium 138+ | The first UIA read of a freshly launched Chromium/Electron app starts the renderer's accessibility build and returns a small fraction of the tree; a cold launch takes roughly 10–25 s to settle. An early snapshot that looks "empty" is a timing artefact, not an empty app. | "A snapshot taken right after launch reflects the app's real tree." | **NEW-EDGE** (consumers must not conclude an empty tree from the first read; allow a settle window) |
| W-004 | Screenshot scale on the test PC | desktop-ddpatmg, 1920×1200 panel, measured 2026-11-03 | Captures come back downscaled (~1707×1067) versus the real 1920×1200 screen. Coordinates read from a capture must be scaled by ~1.125 (or taken from the UIA Snapshot) — raw image coordinates miss small targets and the bottom of the screen. | "Screenshot pixels are screen pixels." | **CONTRADICTS** (on that machine/profile; re-measure per machine before trusting pixel coordinates) |
| W-005 | Scheduled Task kill semantics | desktop-ddpatmg, measured 2026-10-27 and again 2026-11-03 | `schtasks /End` does **not** reliably kill the server python: the launcher `cmd` keeps the task in "Running", so a following `schtasks /Run` no-ops and RestartOnFailure never fires. The reliable kill is by CIM command-line match (`agentic_windows_mcp`) plus confirming port 8000 is free before Run. | "`schtasks /End` stops the server." | **CONTRADICTS** |
| W-006 | Pipe read semantics (jobs) | Local + Windows CI, issue #2 follow-up | `BufferedReader.read(n)` blocks until *n* bytes arrive or EOF, which silently stalled incremental background-job output; `read1()` returns what is available. The #2 test suite caught this as a real product bug before release. | "`read(n)` returns available bytes promptly." | **CONTRADICTS** |

## Rules

- Before changing a role/pattern mapping, an encoding assumption, or an
  input-delivery assumption, check this ledger for the row that covers it. If
  no row covers it and the behaviour matters, measure it (see the probe
  corpus README in this directory) and add the row **before** the fix.
- Captures committed beside probes pass through redaction: user names,
  machine names and profile paths are replaced with stable placeholders.
  Redaction covers operator identity only — content that happens to be on
  screen is the probe author's responsibility at the call site.
- A benchmark number recorded from `benchmarks/locator-resolution/` names
  the machine and the commit it was taken at.
