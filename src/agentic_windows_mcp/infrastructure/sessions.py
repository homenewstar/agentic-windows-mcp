"""On-disk session manifests (#20).

A session is a namespace that owns a snapshot refmap (held in the
Desktop, keyed by session id) plus a manifest directory under
``<config dir>/sessions/<session_id>/`` recording created/ended and
the options the session was started with. The manifest is bookkeeping
for incident analysis; the refmap itself is process state.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path

_SESSION_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")


def validate_session_id(session_id: str) -> str:
    session_id = str(session_id).strip()
    if not _SESSION_ID.match(session_id):
        raise ValueError(
            "session id must be 1-64 characters of letters, digits, '.', '_' "
            "or '-', starting with a letter or digit"
        )
    return session_id


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class SessionStore:
    def __init__(self, root: Path | None = None) -> None:
        if root is None:
            from agentic_windows_mcp.infrastructure.config import CONFIG_DIR

            root = CONFIG_DIR / "sessions"
        self.root = Path(root)

    def _manifest_path(self, session_id: str) -> Path:
        return self.root / session_id / "manifest.json"

    def create(self, session_id: str, options: dict | None = None) -> dict:
        session_id = validate_session_id(session_id)
        path = self._manifest_path(session_id)
        if path.exists():
            return self.read(session_id) or {}
        path.parent.mkdir(parents=True, exist_ok=True)
        manifest = {
            "session_id": session_id,
            "created_at": _now_iso(),
            "ended_at": None,
            "options": dict(options or {}),
        }
        path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
        return manifest

    def mark_ended(self, session_id: str) -> dict | None:
        manifest = self.read(session_id)
        if manifest is None:
            return None
        manifest["ended_at"] = _now_iso()
        self._manifest_path(session_id).write_text(
            json.dumps(manifest, indent=2), encoding="utf-8"
        )
        return manifest

    def read(self, session_id: str) -> dict | None:
        try:
            session_id = validate_session_id(session_id)
        except ValueError:
            return None
        path = self._manifest_path(session_id)
        if not path.exists():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (ValueError, OSError):
            return None
        return data if isinstance(data, dict) else None

    def list_sessions(self) -> list[dict]:
        if not self.root.exists():
            return []
        manifests = []
        for child in sorted(self.root.iterdir()):
            if child.is_dir():
                manifest = self.read(child.name)
                if manifest is not None:
                    manifests.append(manifest)
        return manifests
