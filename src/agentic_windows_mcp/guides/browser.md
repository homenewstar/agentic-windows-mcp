# Browser tools (Browser_*) — attach-only automation

The Browser_* tools drive the user's ALREADY-OPEN browser (Chrome, Edge,
Brave) over the DevTools protocol. They never launch a browser: if no
debuggable browser is reachable they fail with a typed error and stop.

## Precondition: remote debugging must be on

The browser must have remote debugging enabled
(chrome://inspect/#remote-debugging). When it is on, the browser writes a
DevToolsActivePort file and answers a short probe on its debug port. On
connect, the server probes every candidate endpoint before trusting it —
a port file can outlive its browser, and its recorded ws path dies with
that session — and reports exactly what discovery found when nothing
answers.

## The consent dialog is the user's click

The first attach may raise Chrome's remote-debugging consent dialog. That
Allow click belongs to the user. When the dialog is up you get
BROWSER_CONSENT_PENDING: ask the user in chat to click Allow, then retry
after they confirm. Do not try to click the dialog yourself, and do not
retry in a loop — consent-pending results are not cached, but blind
retries only stall.

## Ask before relaunching the user's browser

If debugging is off, the fix (relaunching the browser with debugging
enabled, or toggling the setting) changes the user's running browser and
can cost them open tabs and unsaved page state. ASK the user first, state
what will happen, and wait for their go-ahead. Never close or restart
their browser on your own initiative; if they decline, fall back to a
temporary profile flow if one is available, or report the blocker.

## Typed errors — what to do with each

- BROWSER_NOT_DEBUGGABLE (retry=manual): no endpoint answered. The error
  text carries the discovery report — port files seen, probe outcomes —
  so read it before concluding: "no file + fallback refused" means the
  browser is running without debugging (or no browser is open). Only a
  human can fix this; ask, do not retry blindly. A failed discovery is
  negative-cached for ~45 seconds, so calls inside that window fail fast
  with the same error; the cache clears on the next successful connect.
- BROWSER_CONSENT_PENDING: the consent dialog awaits the user (above).
- ELEMENT_NOT_FOUND: the selector never appeared within timeout_ms.
  Re-observe (Browser_GetPageSnapshot) and fix the selector; do not just
  raise the timeout.
- ELEMENT_NOT_ACTIONABLE: the element is attached but never became
  clickable/fillable/hoverable within timeout_ms (covered, disabled,
  still animating). Re-observe, then retry once.
- DRIVER_DEAD: the automation driver process died and the one automatic
  reset did not revive it. The action's outcome is unknown — verify the
  page state before retrying anything.

## Interact timeouts

Browser_Interact's timeout_ms bounds click, fill and hover (default
10s), replacing the driver's old silent 30s default. Presence is
pre-checked with the same budget, which is what separates
ELEMENT_NOT_FOUND from ELEMENT_NOT_ACTIONABLE.
