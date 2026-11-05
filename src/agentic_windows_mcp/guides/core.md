# Core playbook: observe → act → verify

## The loop

1. **Observe first.** Take a `Snapshot` before acting on a window you have
   not just observed. The snapshot is your map: elements carry refs
   (`@e0`, `@e1`, …) and a generation number.
2. **Act on refs, not coordinates.** `Invoke`, `SetValue`, `Toggle`, and
   `Get`/`Is` take a ref plus the snapshot generation it came from. Refs
   carry the element's full identity (role, name, AutomationId, window
   process token), so a moved or restyled element still resolves — and a
   recycled one fails loudly instead of hitting the wrong target.
3. **Verify after acting.** Re-observe (`Snapshot`, `Is checked`,
   `Get value`, `Screenshot`) before claiming success. An action result
   says how far delivery was confirmed — believe the disposition, not
   the fact that the call returned.

## Ref discipline

- A ref is valid only with its snapshot generation. Passing a stale
  generation fails with `STALE_REF:` — take a fresh Snapshot and pick
  the element again. Never reuse a ref from an older snapshot, and
  never invent a ref.
- If resolution reports `AMBIGUOUS_TARGET:`, the identity matched more
  than one live element. Re-snapshot (possibly scoped with `window=`
  or a `root_ref` drill-down) and choose again — do not guess.
- Use `Find` to locate elements by text/role/AutomationId across the
  desktop instead of paging whole Snapshots when you know what you
  want.

## Retry discipline

Every action result ends with a disposition
(`[disposition delivery=… retry=…]`) and every failure with a typed
envelope (`[error code=… delivery=… retry=… recovery=…]`).

- **Retry only when `retry=safe`** (delivery `not_delivered`: the input
  provably never reached the app — a stale ref, a denied permission, a
  gate refusal).
- When `retry=unsafe` or `unknown`, the input may have landed. Retrying
  blind can double-submit a form or undo a toggle. Observe first, then
  decide.
- `Invoke`/`SetValue`/`Toggle` wait for the target to become
  actionable (visible, enabled, stable) before dispatching; an
  `ACTIONABILITY_TIMEOUT:` error names the check that never passed —
  the app is busy or the element is genuinely not ready, not a cue to
  hammer it.

## Typing and secrets

- `Type` handles long text reliably (paste for long plain text, paced
  chunks otherwise); trust its read-back verdict when it reports one.
- Password fields are never read back or echoed anywhere — capture,
  snapshots and `SetValue` results all withhold secure values by
  design. Do not try to verify a password by reading it; verify by the
  app's response to submitting it.

## Sequences

- Prefer `Batch` for a planned sequence of actions: one call, one gate
  lease, per-step dispositions, stops at the first failure. A human
  takeover still interrupts a batch at the next step boundary.
- Multi-event sequences (drag, Ctrl-select, chords) release whatever
  they hold on any abort. A `RELEASE_UNCONFIRMED` result means the
  action landed but a release could not be confirmed — check
  `ControlStatus` pending input before continuing.

## The human is in charge

The user can take over at any moment (takeover chord
Ctrl+Alt+Shift+Space) and the gate gives them priority. A `CONTROL_*`
refusal means: stop, re-observe, and only retry when the state says
the way is clear.
