#!/usr/bin/env python3
"""Probe: token integrity levels (ledger W-002).

Prints and captures the current process's integrity level and, when a pid is
given, that process's level too -- the exact comparison the UIPI pre-check
(issue #14) performs before sending input. Run on the target Windows machine:

    py -3.13 probe_integrity_level.py [pid]

Writes probe_integrity_level.capture.json beside this script with operator
identity redacted. Windows-only; on other platforms it reports that and exits.
"""
import json
import os
import sys

LEVELS = {
    0x0000: "untrusted",
    0x1000: "low",
    0x2000: "medium",
    0x2100: "medium-plus",
    0x3000: "high",
    0x4000: "system",
    0x5000: "protected-process",
}


def integrity_of(pid):
    """Return (rid, label) for a process's token integrity level."""
    import ctypes
    from ctypes import wintypes

    PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    TOKEN_QUERY = 0x0008
    TokenIntegrityLevel = 25

    advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

    handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        token = wintypes.HANDLE()
        if not advapi32.OpenProcessToken(handle, TOKEN_QUERY, ctypes.byref(token)):
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            size = wintypes.DWORD(0)
            advapi32.GetTokenInformation(token, TokenIntegrityLevel, None, 0, ctypes.byref(size))
            buf = ctypes.create_string_buffer(size.value)
            if not advapi32.GetTokenInformation(token, TokenIntegrityLevel, buf, size, ctypes.byref(size)):
                raise ctypes.WinError(ctypes.get_last_error())
            # TOKEN_MANDATORY_LABEL: first field is a SID_AND_ATTRIBUTES
            # whose Sid pointer is followed by the Attributes DWORD.
            sid_ptr = ctypes.cast(buf, ctypes.POINTER(ctypes.c_void_p))[0]
            sid = ctypes.c_void_p(sid_ptr)
            count = ctypes.cast(sid.value + 1, ctypes.POINTER(ctypes.c_ubyte))[0]
            rid = ctypes.cast(sid.value + 4 + 4 * (count - 1), ctypes.POINTER(ctypes.c_ulong))[0]
            return rid, LEVELS.get(rid, "unknown")
        finally:
            kernel32.CloseHandle(token)
    finally:
        kernel32.CloseHandle(handle)


def redact(value):
    """Replace operator identity with stable placeholders."""
    text = json.dumps(value)
    for secret in (os.environ.get("USERNAME", ""), os.environ.get("COMPUTERNAME", ""),
                   os.path.expanduser("~")):
        if secret:
            text = text.replace(secret, "<redacted>")
    return json.loads(text)


def main():
    if os.name != "nt":
        print("This probe only runs on Windows.")
        return 2
    result = {"probe": "integrity_level", "self": {}}
    rid, label = integrity_of(os.getpid())
    result["self"] = {"integrity_rid": rid, "integrity": label}
    if len(sys.argv) > 1:
        pid = int(sys.argv[1])
        rid, label = integrity_of(pid)
        result["target"] = {"pid": pid, "integrity_rid": rid, "integrity": label}
        result["target_higher_than_self"] = result["target"]["integrity_rid"] > result["self"]["integrity_rid"]
    result = redact(result)
    out = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                       "probe_integrity_level.capture.json")
    with open(out, "w", encoding="utf-8") as fh:
        json.dump(result, fh, indent=2)
    print(json.dumps(result, indent=2))
    print("capture written:", out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
