# Operations: the control gate, health, and recovery

## Gate semantics

- The control gate serializes physical control of the machine. Under
  the default **strict** policy, every tool except `ControlStatus`
  takes the lease; read-only tools contend for it like action tools.
- The user has priority. The takeover chord
  **Ctrl+Alt+Shift+Space** preempts the agent between steps: held
  input is released, the in-flight call fails with a `CONTROL_*`
  code, and the channel latches unavailable until the user releases
  control. This is by design — never fight it; wait and re-observe.
- The on-screen overlay (border + banner) is shown while the gate is
  active so the user always knows the agent is driving.

## Reading ControlStatus health

`ControlStatus` is gate-exempt and always answers. Fields that matter:

- `wedged` — the coordinator considers the channel stuck.
- `watchdog_alive`, `monitor_beat_age_seconds` — the recovery threads'
  pulse; a frozen beat age with a dead watchdog is the wedge
  signature.
- `last_fault` — the most recent fail-open fault: `site` (which call
  site failed open), `error` (exception type and message), `at`
  (monotonic timestamp). This is the first place to look after any
  spontaneous latch.
- `recovery_events` — the audit trail of in-process recoveries and
  escalations, newest last.
- `escalation_armed` / `escalation_pending` — whether repeated failed
  recoveries have earned a process self-exit, and whether it is
  deferred because a call is in flight.

## Recovery discipline

- In-process recovery (monitor/watchdog thread restarts) is **opt-in**
  via the `health_recovery` setting and rate-limited to one attempt
  per 120 seconds. It can only fix a wedged channel inside a living
  process.
- When repeated recoveries fail to clear the latch, the coordinator
  escalates: it records a `process_restart_escalation` recovery event,
  logs CRITICAL, and exits the process (code 3) so the external
  supervision (Scheduled Task / port watchdog) starts a fresh one.
  Escalation never fires while a call is in flight.
- A dead process or a whole-PC crash is outside in-process recovery
  entirely — that is the supervisor's job, and no client-side retry
  loop will fix it.

## Sessions and the trace

- The JSONL trace is switched from `config.toml` (`[trace] enabled =
  true`, optional `directory`); `AGENTIC_WINDOWS_MCP_TRACE` overrides
  the enabled flag only. A session can request tracing for itself
  (`Session` start with `trace=True`) even when the server-wide
  switch is off. Trace events carry tool name, outcome and duration —
  never arguments.
- Replay screenshots are a separate, stronger opt-in (`[trace]
  capture_screenshots = true`, or `artifacts=True` on a session
  start). When on, a frame from just before and just after each
  action call is stored beside the trace segment. Those PNGs are
  **unredacted screen content** — everything visible on the desktop
  is in them. Treat them as sensitive, share them like screenshots,
  and leave the flag off unless an investigation needs them. No
  environment variable can enable them.

## When the channel wedges

1. Stop issuing action calls; hammering a latched channel only queues
   failures.
2. Poll `ControlStatus` and read `last_fault` + `recovery_events` —
   they say what failed, where, and what recovery already tried.
3. Wait for recovery or escalation to play out (the cooldown is
   120 s). If `escalation_pending` is set, it fires as soon as the
   channel is idle.
4. After the channel recovers, re-observe (Snapshot) before resuming:
   generations and refs from before the wedge are not trustworthy.
