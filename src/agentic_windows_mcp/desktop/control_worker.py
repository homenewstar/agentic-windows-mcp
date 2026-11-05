"""State coordination and hold cleanup, never on the Win32 hook pump."""

import logging
import queue
import time
from typing import Any, Callable


logger = logging.getLogger(__name__)


def refresh_deadline(owner: Any, now: float, fault=None) -> None:
    """Renew suppression only from a live, healthy coordinator proof."""
    fault = owner._fault_token if fault is None else fault

    def current():
        return (
            fault is owner._fault_token
            and not owner._emergency
            and not owner._stop.is_set()
            and time.monotonic() - now < 0.25
        )

    if not owner._lock.acquire(blocking=False):
        owner._fail_open(
            site="refresh_deadline.lock_busy",
            detail="coordinator lock held by another thread",
        )
        return
    try:
        if current() and owner._state == "ai" and owner._visual_armed and not owner._fast_takeover:
            owner._deadline = now + 1.0
            # A dialog yield (consent dialog on screen) wins over the
            # heartbeat: keep suppression OFF so the user can answer the
            # dialog. Without this, the yield was overridden within
            # ~0.25s and the user stayed locked out.
            owner._suppress = not owner._dialog_yield
            if not current():
                owner._suppress = False
                owner._deadline = 0.0
                owner.input_ledger.block_new()
        else:
            owner._suppress = False
    finally:
        owner._lock.release()


def run_control_worker(owner: Any, interactive_desktop: Callable[[], bool]) -> None:
    """Reuse the watchdog for ordered events; throttle expensive health probes."""
    next_health = 0.0
    while True:
        owner._release_requested.wait(0.01)
        owner._release_requested.clear()
        if owner._stop.is_set():
            return
        try:
            # Fault cleanup takes precedence over queued work and slow UI probes.
            if owner._emergency:
                owner._mark_unavailable()
            for _ in range(128):
                if owner._stop.is_set():
                    return
                try:
                    event = owner._events.get_nowait()
                except queue.Empty:
                    break
                owner._handle(event)
            owner._handle(("tick",))
            fault = owner._fault_token
            can_recover = owner._rehook_completed is fault and not owner._rotating
            if owner._emergency and not can_recover:
                continue
            if time.monotonic() < next_health:
                continue
            try:
                probe_ok = owner._health_probe is None or owner._health_probe()
                interactive = interactive_desktop()
            finally:
                next_health = time.monotonic() + 0.25
            healthy = probe_ok and interactive
            now = time.monotonic()
            if (
                not healthy
                or not owner._thread
                or not owner._thread.is_alive()
                or now - owner._thread_beat > 1.0
            ):
                # Issue #26: this site fired live with error null twice —
                # it is a condition fault, so record the measured
                # conditions (and the probe's own explanation, which can
                # carry the exception the overlay thread stored) instead
                # of a bare site name.
                reasons: list[str] = []
                if not probe_ok:
                    probe_detail = getattr(owner, "_probe_detail", None)
                    reasons.append(
                        (probe_detail() if callable(probe_detail) else None)
                        or "health probe reported unhealthy"
                    )
                if not interactive:
                    reasons.append("desktop not interactive")
                if not owner._thread:
                    reasons.append("monitor thread missing")
                elif not owner._thread.is_alive():
                    reasons.append("monitor thread not alive")
                beat_age = now - owner._thread_beat
                if beat_age > 1.0:
                    reasons.append(f"monitor beat stale ({beat_age:.1f}s old)")
                owner._fail_open(
                    site="worker.health_check", detail="; ".join(reasons)
                )
                owner._mark_unavailable()
                continue
            if owner._emergency and can_recover:
                owner._recover_after_rehook(fault, healthy=True, sampled_at=now)
            if not owner._emergency:
                refresh_deadline(owner, now, fault)
        except Exception as exc:
            # Issue #25: never swallow the fault that tripped the latch.
            # Log it with its traceback and record it on the coordinator
            # so ControlStatus (health.last_fault) names the exact site
            # and exception instead of an anonymous emergency.
            logger.exception("Control worker loop fault; failing open")
            owner._fail_open(site="worker.loop_exception", error=exc)
            owner._mark_unavailable()
