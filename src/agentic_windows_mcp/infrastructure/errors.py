"""Typed error envelope + delivery disposition (issue #13).

Tool failures used to be plain text with a few ad-hoc prefixes
(``STALE_REF:``, ``CONTROL_*``). An agent could not tell whether a failed
action had reached the app, so it could not decide whether retrying was
safe — the classic double-submit / toggle-undoes-itself failure.

This module adds the missing contract without taking the human text away:

- A repo-wide error-code vocabulary (below). Codes are *derived* from the
  existing message prefixes and exception types, so current clients keep
  working and the original message — prefix included — stays in front.
- Every action-tool result carries a **disposition**: whether the input
  reached the application (``delivery``) and, derived from that, whether
  repeating the call is safe (``retry``). Only ``not_delivered`` is
  ``safe`` to retry; anything delivered or uncertain is ``unsafe`` (observe
  first, then decide), and an unknowable outcome stays ``unknown``.
- Where a safe recovery exists, the envelope names it (``recovery=``).

On the wire the envelope is a single bracketed suffix appended to the
tool's text, on success

    [disposition delivery=delivered_unverified retry=unsafe]

and on failure

    [error code=STALE_REF delivery=not_delivered retry=safe recovery=refresh_snapshot_then_retry_original]

so agents branch on the tokens, never on the prose. Read-only tool errors
are always not_delivered/safe by construction: a read that failed sent no
input.
"""

import functools
import inspect
from dataclasses import dataclass


@dataclass(frozen=True)
class Disposition:
    delivery: str
    retry: str


NOT_DELIVERED = Disposition("not_delivered", "safe")
DELIVERED_UNVERIFIED = Disposition("delivered_unverified", "unsafe")
DELIVERED_VERIFIED = Disposition("delivered_verified", "unsafe")
DELIVERY_UNCERTAIN = Disposition("delivery_uncertain", "unsafe")
UNKNOWN = Disposition("unknown", "unknown")


def retry_for(delivery: str) -> str:
    """The retry rule: only not_delivered is safe; unknown stays unknown."""
    if delivery == "not_delivered":
        return "safe"
    if delivery == "unknown":
        return "unknown"
    return "unsafe"


# Message-prefix -> (code, disposition, recovery strategy or None). Order
# matters: the first matching prefix wins.
_PREFIX_RULES = (
    ("STALE_REF:", "STALE_REF", NOT_DELIVERED, "refresh_snapshot_then_retry_original"),
    ("PERM_DENIED:", "PERM_DENIED", NOT_DELIVERED, None),
    ("UNSUPPORTED_PATTERN:", "ACTION_NOT_SUPPORTED", NOT_DELIVERED, None),
    ("NOT_EDITABLE:", "ACTION_NOT_SUPPORTED", NOT_DELIVERED, None),
    ("AMBIGUOUS_TARGET:", "AMBIGUOUS_TARGET", NOT_DELIVERED, None),
    # A multi-event sequence delivered its input but the release of a
    # held button/key could not be confirmed (#23): the action landed,
    # the desktop may be poisoned — never retry blind.
    (
        "RELEASE_UNCONFIRMED:",
        "RELEASE_UNCONFIRMED",
        DELIVERED_UNVERIFIED,
        "check_pending_input_via_control_status_before_retrying",
    ),
)


def classify_error(exc: BaseException) -> tuple[str, Disposition, str | None]:
    """Map an exception to (code, disposition, recovery).

    The control gate's blocks carry a ``code`` attribute (CONTROL_*): the
    action was refused before dispatch, so nothing was delivered and a
    retry is safe once the gate is available again.
    """
    gate_code = getattr(exc, "code", None)
    if isinstance(gate_code, str) and gate_code.startswith("CONTROL_"):
        return "POLICY_DENIED", NOT_DELIVERED, "inspect_state_then_retry_original"
    message = str(exc)
    for prefix, code, disposition, recovery in _PREFIX_RULES:
        if message.startswith(prefix):
            return code, disposition, recovery
    if isinstance(exc, TimeoutError):
        return "TIMEOUT", UNKNOWN, None
    if isinstance(exc, PermissionError):
        return "PERM_DENIED", NOT_DELIVERED, None
    if isinstance(exc, (ValueError, TypeError, KeyError, IndexError)):
        # Argument/shape errors are raised before dispatch in this codebase.
        return "INVALID_ARGS", NOT_DELIVERED, None
    if isinstance(exc, RuntimeError):
        # A runtime failure from an action is mid-flight by default: the
        # input may have landed, so a blind retry is unsafe.
        return "ACTION_FAILED", DELIVERY_UNCERTAIN, None
    return "INTERNAL", UNKNOWN, None


def success_suffix(disposition: Disposition) -> str:
    return f"\n[disposition delivery={disposition.delivery} retry={disposition.retry}]"


def error_suffix(code: str, disposition: Disposition, recovery: str | None) -> str:
    suffix = (
        f"\n[error code={code} delivery={disposition.delivery} "
        f"retry={disposition.retry}"
    )
    if recovery:
        suffix += f" recovery={recovery}"
    return suffix + "]"


def _augment(exc: Exception, suffix: str) -> Exception:
    """Rebuild exc with the envelope appended, preserving its type so
    existing isinstance/message-prefix behaviour survives."""
    try:
        return type(exc)(str(exc) + suffix)
    except Exception:
        fallback = ValueError if isinstance(exc, ValueError) else RuntimeError
        return fallback(str(exc) + suffix)


def with_disposition(fn=None, *, success: Disposition = DELIVERED_UNVERIFIED):
    """Wrap an action tool: append the disposition to its text result, and
    convert any raised error into the same exception type whose message
    gains the typed envelope (original human text kept, prefix first)."""

    def decorate(func):
        if inspect.iscoroutinefunction(func):

            @functools.wraps(func)
            async def async_wrapper(*args, **kwargs):
                try:
                    result = await func(*args, **kwargs)
                except Exception as exc:
                    code, disposition, recovery = classify_error(exc)
                    raise _augment(exc, error_suffix(code, disposition, recovery)) from exc
                if isinstance(result, str):
                    return result + success_suffix(success)
                return result

            return async_wrapper

        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            try:
                result = func(*args, **kwargs)
            except Exception as exc:
                code, disposition, recovery = classify_error(exc)
                raise _augment(exc, error_suffix(code, disposition, recovery)) from exc
            if isinstance(result, str):
                return result + success_suffix(success)
            return result

        return wrapper

    if fn is not None:
        return decorate(fn)
    return decorate
