from typing import Literal


__all__ = ["list_processes", "kill_process", "PROTECTED_PROCESSES", "protected_process_refusal"]


# Session-critical Windows images that must never be terminated through
# this tooling (issue #22). Killing explorer.exe breaks the user's
# desktop session outright; the rest are core session/system processes.
# Most are additionally shielded by the OS (privilege/PPL), but the
# guard's job is a clear refusal at the point of action rather than a
# bare AccessDenied after an attempt. Matching is the EXACT image name,
# case-insensitive — a program whose name merely contains a protected
# name (explorer-notes.exe) is a different, closable process.
PROTECTED_PROCESSES = frozenset(
    {
        "explorer.exe",
        "csrss.exe",
        "smss.exe",
        "winlogon.exe",
        "wininit.exe",
        "dwm.exe",
    }
)


def protected_process_refusal(image_name: str | None) -> str | None:
    """The refusal message when image_name is protected, else None."""
    if image_name and image_name.lower() in PROTECTED_PROCESSES:
        return (
            f"Error: PROTECTED_PROCESS: {image_name} is a protected "
            "session-critical process and must not be terminated — "
            "killing it would break the user's desktop session."
        )
    return None


def list_processes(
    name: str | None = None,
    sort_by: Literal["memory", "cpu", "name"] = "memory",
    limit: int = 20,
) -> str:
    import psutil
    from tabulate import tabulate

    procs = []
    for p in psutil.process_iter(["pid", "name", "cpu_percent", "memory_info"]):
        try:
            info = p.info
            mem_mb = info["memory_info"].rss / (1024 * 1024) if info["memory_info"] else 0
            procs.append(
                {
                    "pid": info["pid"],
                    "name": info["name"] or "Unknown",
                    "cpu": info["cpu_percent"] or 0,
                    "mem_mb": round(mem_mb, 1),
                }
            )
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    if name:
        from thefuzz import fuzz

        procs = [p for p in procs if fuzz.partial_ratio(name.lower(), p["name"].lower()) > 60]
    sort_key = {
        "memory": lambda x: x["mem_mb"],
        "cpu": lambda x: x["cpu"],
        "name": lambda x: x["name"].lower(),
    }
    procs.sort(key=sort_key.get(sort_by, sort_key["memory"]), reverse=(sort_by != "name"))
    procs = procs[:limit]
    if not procs:
        return f"No processes found{f' matching {name}' if name else ''}."
    table = tabulate(
        [[p["pid"], p["name"], f"{p['cpu']:.1f}%", f"{p['mem_mb']:.1f} MB"] for p in procs],
        headers=["PID", "Name", "CPU%", "Memory"],
        tablefmt="simple",
    )
    return f"Processes ({len(procs)} shown):\n{table}"


def kill_process(
    name: str | None = None, pid: int | None = None, force: bool = False
) -> str:
    import psutil

    if pid is None and name is None:
        return "Error: Provide either pid or name parameter for kill mode."
    if name is not None:
        # Refuse a protected target before enumerating anything (#22).
        refusal = protected_process_refusal(name)
        if refusal is not None:
            return refusal
    killed = []
    if pid is not None:
        try:
            p = psutil.Process(pid)
            pname = p.name()
            refusal = protected_process_refusal(pname)
            if refusal is not None:
                return refusal
            if force:
                p.kill()
            else:
                p.terminate()
            killed.append(f"{pname} (PID {pid})")
        except psutil.NoSuchProcess:
            return f"No process with PID {pid} found."
        except psutil.AccessDenied:
            return f"Access denied to kill PID {pid}. Try running as administrator."
    else:
        for p in psutil.process_iter(["pid", "name"]):
            try:
                if p.info["name"] and p.info["name"].lower() == name.lower():
                    if force:
                        p.kill()
                    else:
                        p.terminate()
                    killed.append(f"{p.info['name']} (PID {p.info['pid']})")
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue
    if not killed:
        return f'No process matching "{name}" found or access denied.'
    return f"{'Force killed' if force else 'Terminated'}: {', '.join(killed)}"
