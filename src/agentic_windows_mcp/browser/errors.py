"""Typed errors for the Browser cluster (issue #37).

Until #37 every Browser_* failure surfaced as a bare RuntimeError whose
text an agent could not branch on: "the user's browser is not
debuggable — a human must act" looked exactly like "the element is not
there yet — re-observe and retry". These types carry the #13 message
prefixes that ``infrastructure.errors.classify_error`` maps to codes
and dispositions, so the manager raises intentions, not strings, and
the tool layer only has to append the envelope.

The constructor is prefix-idempotent: the #13 envelope wrapper rebuilds
an exception as ``type(exc)(str(exc) + suffix)``, so a message that
already starts with the class prefix is kept as-is instead of gaining
the prefix twice.
"""

from __future__ import annotations


class BrowserError(RuntimeError):
    """Base: the wire message is composed as ``<PREFIX>: <detail>``."""

    PREFIX = "BROWSER_ERROR"

    def __init__(self, detail: str) -> None:
        self.detail = detail
        if detail.startswith(self.PREFIX + ":"):
            super().__init__(detail)
        else:
            super().__init__(f"{self.PREFIX}: {detail}")


class NotDebuggableError(BrowserError):
    """No debuggable browser endpoint could be attached (D1/D2/D3).

    Carries the discovery report in its detail: which port files were
    found, which endpoints were probed, and how each probe ended.
    """

    PREFIX = "BROWSER_NOT_DEBUGGABLE"


class ConsentPendingError(BrowserError):
    """Chrome's remote-debugging consent dialog awaits the user (D1)."""

    PREFIX = "BROWSER_CONSENT_PENDING"


class ElementNotFoundError(BrowserError):
    """The locator never appeared within the caller's timeout (D4)."""

    PREFIX = "ELEMENT_NOT_FOUND"


class ElementNotActionableError(BrowserError):
    """The locator appeared but never became actionable (D4)."""

    PREFIX = "ELEMENT_NOT_ACTIONABLE"


class DriverDeadError(BrowserError):
    """The Playwright driver died and the one reset did not revive it (D5)."""

    PREFIX = "DRIVER_DEAD"


_DRIVER_DEATH_MARKERS = (
    "reading from the driver",
    "driver has been closed",
)


def is_driver_death(exc: BaseException) -> bool:
    """True when *exc* looks like the Playwright driver process dying.

    The census case (2026-10-29) read "Connection closed while reading
    from the driver". Matching is on the message because Playwright
    surfaces driver loss as a plain Error with no dedicated type.
    """
    message = str(exc).lower()
    return any(marker in message for marker in _DRIVER_DEATH_MARKERS)
