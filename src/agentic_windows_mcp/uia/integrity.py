"""UIPI integrity pre-check (issue #14, findings ledger W-002).

Windows User Interface Privilege Isolation silently blocks input from a
lower-integrity process to a higher-integrity window (for example an app
running as administrator), while ``SendInput`` still reports the events as
injected. The injected-event count is therefore never delivery evidence: a
click can "succeed" while nothing landed, and an agent proceeds on a false
premise. The countermeasure is to compare token integrity levels *before*
dispatch and fail closed with ``PERM_DENIED`` when the target process runs
strictly higher than this server.

Only the comparison is enforced. When a level cannot be read (the target's
token is not queryable, the pid is gone, or the platform is not Windows) the
check allows the action: an unreadable level is unknown, not proof of a
higher one. Reading another process's integrity uses
``PROCESS_QUERY_LIMITED_INFORMATION``, the handle right that exists exactly
for cross-integrity queries.
"""

import ctypes
import os

# Integrity RIDs (from winnt.h SECURITY_MANDATORY_*_RID).
LEVEL_LABELS = {
    0x0000: "untrusted",
    0x1000: "low",
    0x2000: "medium",
    0x2100: "medium-plus",
    0x3000: "high",
    0x4000: "system",
    0x5000: "protected-process",
}


class UipiBlockedError(PermissionError):
    """The target process outranks this server; input would not land."""


def level_label(rid: int | None) -> str:
    if rid is None:
        return "unknown"
    return LEVEL_LABELS.get(rid, f"rid-{rid:#x}")


def is_strictly_higher(target: int | None, own: int | None) -> bool:
    """True only when both levels are known and target outranks own."""
    return target is not None and own is not None and target > own


def _read_integrity_rid(pid: int) -> int | None:
    """Token integrity RID for a process, or None when it cannot be read."""
    if os.name != "nt" or not pid or pid <= 0:
        return None
    from ctypes import wintypes

    PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    TOKEN_QUERY = 0x0008
    TokenIntegrityLevel = 25

    class SID_AND_ATTRIBUTES(ctypes.Structure):
        _fields_ = [("Sid", ctypes.c_void_p), ("Attributes", wintypes.DWORD)]

    class TOKEN_MANDATORY_LABEL(ctypes.Structure):
        _fields_ = [("Label", SID_AND_ATTRIBUTES)]

    advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        return None
    try:
        token = wintypes.HANDLE()
        if not advapi32.OpenProcessToken(handle, TOKEN_QUERY, ctypes.byref(token)):
            return None
        try:
            size = wintypes.DWORD(0)
            advapi32.GetTokenInformation(
                token, TokenIntegrityLevel, None, 0, ctypes.byref(size)
            )
            if not size.value:
                return None
            buf = ctypes.create_string_buffer(size.value)
            if not advapi32.GetTokenInformation(
                token, TokenIntegrityLevel, buf, size, ctypes.byref(size)
            ):
                return None
            label = ctypes.cast(buf, ctypes.POINTER(TOKEN_MANDATORY_LABEL)).contents
            sid = label.Label.Sid
            count_ptr = advapi32.GetSidSubAuthorityCount(ctypes.c_void_p(sid))
            if not count_ptr:
                return None
            count = ctypes.cast(count_ptr, ctypes.POINTER(ctypes.c_ubyte)).contents.value
            rid_ptr = advapi32.GetSidSubAuthority(ctypes.c_void_p(sid), count - 1)
            if not rid_ptr:
                return None
            return int(ctypes.cast(rid_ptr, ctypes.POINTER(wintypes.DWORD)).contents.value)
        finally:
            kernel32.CloseHandle(token)
    except Exception:
        return None
    finally:
        kernel32.CloseHandle(handle)


def integrity_level_of(pid: int | None) -> int | None:
    """Public seam (patched in tests) over the raw token read."""
    if pid is None:
        return None
    return _read_integrity_rid(int(pid))


_own_level_cache: int | None = None
_own_level_read = False


def own_integrity_level() -> int | None:
    """This server process's integrity level (it never changes; cached)."""
    global _own_level_cache, _own_level_read
    if not _own_level_read:
        _own_level_cache = _read_integrity_rid(os.getpid())
        _own_level_read = True
    return _own_level_cache


def check_target_process(pid: int | None, action: str = "input") -> None:
    """Raise UipiBlockedError when pid's process outranks this server.

    Unknown pids and unreadable levels are allowed through: only a proven
    strictly-higher target is denied, and it is denied *before* any input or
    pattern write is attempted, so the failure is not_delivered by
    construction.
    """
    if pid is None:
        return
    target = integrity_level_of(pid)
    own = own_integrity_level()
    if is_strictly_higher(target, own):
        raise UipiBlockedError(
            f"PERM_DENIED: the {action} target process (pid {pid}) runs at "
            f"integrity level {level_label(target)}, higher than this server "
            f"({level_label(own)}). Windows UIPI would silently block the "
            "input while reporting success (findings W-002), so no input was "
            "sent. Run the server elevated to the target's level to drive it."
        )
