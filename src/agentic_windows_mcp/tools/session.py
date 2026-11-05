"""Session tool (#20): named ref namespaces with on-disk manifests.

Starting a session scopes every Snapshot registration and ref
resolution to it: refs issued under one session never resolve under
another (a cross-session attempt fails with a STALE_REF that names
the owning session). Ending a session returns to the default
namespace; the session's refmap is kept, so starting it again resumes
where it left off. A manifest under <config dir>/sessions/<id>/
records created/ended for incident analysis.
"""

from fastmcp import Context
from mcp.types import ToolAnnotations

from agentic_windows_mcp.infrastructure import with_analytics
from agentic_windows_mcp.infrastructure.sessions import SessionStore
from agentic_windows_mcp.infrastructure.trace import ensure_trace, record_event


def register(mcp, *, get_desktop, get_analytics) -> None:
    @mcp.tool(
        name="Session",
        description=(
            "Manage snapshot sessions: named namespaces that own the refs a "
            "Snapshot issues. mode='start' with a session_id activates that "
            "namespace (creating it if needed) — from then on, refs from "
            "other sessions no longer resolve, and new Snapshots register "
            "into this one. mode='status' reports the active session and "
            "every session's generation/ref counts. mode='end' returns to "
            "the default session; the ended session's refs are kept and "
            "resume if it is started again. Use sessions to keep parallel "
            "lines of work (or a long investigation) from clobbering each "
            "other's refs. mode='start' also accepts trace=True to start "
            "the JSONL trace for this session even when the server-wide "
            "trace switches are off, and artifacts=True to additionally "
            "store pre/post-action replay screenshots beside the trace — "
            "those PNGs are unredacted screen content, treat them as "
            "sensitive."
        ),
        annotations=ToolAnnotations(
            title="Session",
            readOnlyHint=False,
            destructiveHint=False,
            idempotentHint=False,
            openWorldHint=False,
        ),
    )
    @with_analytics(get_analytics(), "Session-Tool", run_sync_in_thread=False)
    def session_tool(
        mode: str = "status",
        session_id: str = "",
        trace: bool = False,
        artifacts: bool = False,
        ctx: Context = None,
    ) -> str:
        desktop = get_desktop()
        store = SessionStore()
        mode = mode.strip().lower()
        if mode == "start":
            if not session_id.strip():
                return "Error: mode='start' requires a session_id."
            try:
                info = desktop.activate_session(session_id)
            except ValueError as e:
                return f"Error: {e}"
            store.create(
                info["session"],
                options={
                    "via": "Session tool",
                    "trace": bool(trace),
                    "artifacts": bool(artifacts),
                },
            )
            trace_note = ""
            if trace or artifacts:
                # Explicit per-session opt-in (#29): start the writer
                # even when the server-wide switches are off.
                writer = ensure_trace(capture_screenshots=bool(artifacts))
                if writer is None:
                    trace_note = (
                        " Trace was requested but could not be started; "
                        "the session itself is unaffected."
                    )
                elif artifacts:
                    trace_note = (
                        f" Trace is recording to {writer.path} with replay "
                        "screenshots ON — those PNGs are unredacted screen "
                        "content; treat them as sensitive."
                    )
                else:
                    trace_note = f" Trace is recording to {writer.path}."
            record_event(
                "session_started", session=info["session"], created=info["created"]
            )
            state = "created" if info["created"] else "resumed"
            return (
                f"Session {info['session']!r} is now active ({state}; "
                f"generation {info['generation']}, {info['refs']} refs). "
                "Snapshots now register into this session, and refs from "
                "other sessions will not resolve here."
                + trace_note
            )
        if mode == "end":
            previous = desktop.end_session()
            if previous == "default":
                return "No named session was active; the default session remains active."
            store.mark_ended(previous)
            record_event("session_ended", session=previous)
            return (
                f"Session {previous!r} ended; the default session is active "
                "again. Its refs are kept and resume if the session is "
                "started again."
            )
        if mode == "status":
            overview = desktop.session_overview()
            lines = [f"Active session: {overview['active']!r}"]
            for sid, info in sorted(overview["sessions"].items()):
                marker = " (active)" if sid == overview["active"] else ""
                manifest = store.read(sid)
                span = ""
                if manifest:
                    span = f", created {manifest.get('created_at')}"
                    if manifest.get("ended_at"):
                        span += f", ended {manifest['ended_at']}"
                lines.append(
                    f"- {sid!r}: generation {info['generation']}, "
                    f"{info['refs']} refs{marker}{span}"
                )
            return "\n".join(lines)
        return (
            f"Error: unknown mode {mode!r}. Use 'start', 'status' or 'end'."
        )
