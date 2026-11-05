# Windows platform notes

## Integrity levels (UIPI)

Windows blocks input from a lower-integrity process to a
higher-integrity window (an app running as administrator), and
`SendInput` reports success anyway. This server pre-checks integrity
before physical input and UIA pattern writes and fails closed with
`PERM_DENIED:` when the target outranks it. To drive an elevated app,
the server itself must run at the same integrity level. Observing
elevated windows (Snapshot, Screenshot) always works.

## Screenshots vs coordinates

Returned screenshots may be downscaled relative to the real screen
(measured: a 1920×1200 desktop served at ~1707×1067, a factor of
~1.125). Coordinates read off the image are therefore approximate —
always prefer element refs, whose bounds come from UIA and are exact.
If you must click by coordinates, take them from the Snapshot's UIA
geometry, not from pixel positions in the image.

## Chromium and Electron trees settle slowly

After a Chromium/Electron window opens or navigates, its UIA tree
keeps changing for 10–25 seconds before it stabilizes. A Snapshot
taken immediately can miss controls or carry refs that go stale at
once. If a web app looks half-empty, wait and snapshot again.

## PowerShell pitfalls

- Prefer script files or `-EncodedCommand` for anything complex;
  inline quoting across the MCP → PowerShell boundary eats backslashes
  and dollar signs.
- Console encoding is not UTF-8 by default, and text can be mangled
  through a Latin-1 (not cp1252) decode — when output looks like
  mojibake, inspect the actual codepoints before assuming a fix.
  Write files with an explicit UTF-8 encoding instead of redirecting
  console output when the content matters.
- Long-running commands belong in `Job` (start/status/output/stop),
  not in a blocking PowerShell call.

## Delivery is never the injection count

`SendInput` returning success only means the events were queued. The
server's dispositions and read-backs exist because delivery has to be
verified at the app, not at the API. When a result says
`delivered_unverified`, confirm with a Snapshot or a state query
before building on the action.

## Smart App Control

On machines with Smart App Control enabled, unsigned binaries can be
blocked from running at all. If a freshly installed executable will
not start and no error surfaces, check Smart App Control before
debugging the binary.

## Windows recycle handles

Window handles (HWNDs) are recycled by the OS. Refs therefore carry
the owning process's creation token as part of the window identity —
a ref whose window was closed and whose handle was reused by another
process fails as `STALE_REF:` rather than acting on the stranger.
