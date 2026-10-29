""" PowerShell command executor service """

import base64
import ctypes
import ctypes.wintypes
import logging
import os
import winreg
import shutil
import subprocess

from agentic_windows_mcp.desktop.utils import is_elevated
from agentic_windows_mcp.powershell.utils import run_with_graceful_timeout, strip_clixml

__all__ = ["PowerShellExecutor"]

logger = logging.getLogger(__name__)


def _read_reg_env(hkey: int, subkey: str) -> tuple[dict[str, str], str, str]:
    """Read all environment variables from a registry key.

    Returns (vars, path, pathext) where *vars* is a dict of name->value
    (excluding PATH/PATHEXT), and *path*/*pathext* are the raw expanded
    values for those two special keys (empty string if absent).
    """
    variables: dict[str, str] = {}
    path = ""
    pathext = ""

    try:
        with winreg.OpenKey(hkey, subkey) as key:
            i = 0
            while True:
                try:
                    name, value, reg_type = winreg.EnumValue(key, i)
                    if reg_type not in (winreg.REG_SZ, winreg.REG_EXPAND_SZ):
                        i += 1
                        continue
                    if reg_type == winreg.REG_EXPAND_SZ:
                        # Note: ExpandEnvironmentStrings uses the current process
                        # environment, which may be stripped by the MCP host. References
                        # to missing vars (e.g. %COMPUTERNAME%) won't expand correctly.
                        # In practice this is acceptable: most REG_EXPAND_SZ values under
                        # HKLM/HKCU\Environment reference %SystemRoot% or %USERPROFILE%
                        # which are almost always present.
                        value = winreg.ExpandEnvironmentStrings(value)
                    upper = name.upper()
                    if upper == "PATH":
                        path = value
                    elif upper == "PATHEXT":
                        pathext = value
                    else:
                        variables[name] = value
                    i += 1
                except OSError:
                    break
    except OSError:
        pass

    return variables, path, pathext


def _dedup_path(*segments: str) -> str:
    """Join PATH segments and deduplicate entries (case-insensitive)."""
    seen = set()
    deduped = []
    for p in ";".join(filter(None, segments)).split(";"):
        norm = p.lower().rstrip("\\")
        if norm and norm not in seen:
            seen.add(norm)
            deduped.append(p)
    return ";".join(deduped)


def _win32_name(dll: str, func: str) -> str:
    """Call a Win32 GetXxxNameW(LPWSTR, LPDWORD) function.

    Only suitable for APIs with the (buffer, &size) calling convention,
    e.g. kernel32.GetComputerNameW and advapi32.GetUserNameW.
    """
    buf = ctypes.create_unicode_buffer(257)
    size = ctypes.wintypes.DWORD(257)
    fn = getattr(ctypes.windll, dll)
    if getattr(fn, func)(buf, ctypes.byref(size)):
        return buf.value
    return ""


_FALLBACK_PATHEXT = ".COM;.EXE;.BAT;.CMD;.VBS;.VBE;.JS;.JSE;.WSF;.WSH;.MSC;.CPL;.PY;.PYW"


def _prepare_env() -> dict[str, str]:
    """Prepare a complete environment block for the PowerShell subprocess.

    MCP hosts (e.g. Claude Desktop) may launch this server with a stripped
    environment block missing session-level variables. This function starts
    from os.environ and fills in missing variables from:
      1. System-level env vars from the registry (HKLM)
      2. User-level env vars from the registry (HKCU)
      3. Dynamic vars (COMPUTERNAME, USERNAME, etc.) via Win32 API
    Existing values in os.environ are never overwritten, only missing ones
    are supplemented. PATH is special-cased: inherited entries keep their
    priority and registry entries are appended to fill in missing paths.
    """
    env = os.environ.copy()

    # PYTHONHOME is set by uv to pin its own managed interpreter for this
    # server process. Leaking it into a spawned shell makes any Python found
    # on PATH there resolve its standard library against the server's
    # interpreter instead of its own, breaking unrelated installs (#350).
    env.pop("PYTHONHOME", None)

    try:
        machine_vars, machine_path, machine_pathext = _read_reg_env(
            winreg.HKEY_LOCAL_MACHINE,
            r"SYSTEM\CurrentControlSet\Control\Session Manager\Environment",
        )
        user_vars, user_path, user_pathext = _read_reg_env(winreg.HKEY_CURRENT_USER, r"Environment")

        # Supplement missing vars. User-level (HKCU) takes precedence over
        # system-level (HKLM) for same-named keys, matching Windows' resolution order.
        for name, value in {**machine_vars, **user_vars}.items():
            if not env.get(name):
                env[name] = value

        # PATH: inherited entries keep their priority; registry entries are
        # appended to fill in anything missing (e.g. stripped MCP host env).
        if machine_path or user_path:
            env["PATH"] = _dedup_path(env.get("PATH", ""), machine_path, user_path)

        # PATHEXT: use registry value if the inherited one looks incomplete
        effective_pathext = user_pathext or machine_pathext
        if effective_pathext and ".EXE" not in env.get("PATHEXT", "").upper():
            env["PATHEXT"] = effective_pathext

    except Exception:
        logger.debug("Failed to read environment from registry", exc_info=True)
        if ".EXE" not in env.get("PATHEXT", "").upper():
            env["PATHEXT"] = _FALLBACK_PATHEXT

    # Dynamic variables not stored in registry — only fill if missing
    if not env.get("COMPUTERNAME"):
        try:
            computer_name = _win32_name("kernel32", "GetComputerNameW")
            if computer_name:
                env["COMPUTERNAME"] = computer_name
        except Exception as e:
            logger.debug("Failed to get COMPUTERNAME via Win32 API: %s", e)

    if not env.get("USERNAME"):
        try:
            user_name = _win32_name("advapi32", "GetUserNameW")
            if user_name:
                env["USERNAME"] = user_name
        except Exception as e:
            logger.debug("Failed to get USERNAME via Win32 API: %s", e)

    user_profile = os.path.expanduser("~")
    env.setdefault("USERPROFILE", user_profile)
    drive, tail = os.path.splitdrive(user_profile)
    env.setdefault("HOMEDRIVE", drive)
    env.setdefault("HOMEPATH", tail)
    # On domain-joined machines USERDOMAIN is the NetBIOS domain name (e.g.
    # "CORP"), not the computer name. This fallback is only correct for workgroup
    # machines. USERDOMAIN is a session variable set at logon and is unlikely to
    # be missing on domain-joined hosts, so this is an acceptable last resort.
    env.setdefault("USERDOMAIN", env.get("COMPUTERNAME", ""))

    return env


_WINDOWS_POWERSHELL = os.path.join("System32", "WindowsPowerShell", "v1.0", "powershell.exe")


def _resolve_shell(shell: str | None, env: dict[str, str]) -> str:
    """Resolve the PowerShell executable to launch against the child's PATH.

    Windows looks a bare program name up on the *server's* PATH, not on the
    environment handed to the child, so the PATH repaired by ``_prepare_env()``
    does not decide which executable is found. ``System32\\WindowsPowerShell\\v1.0``
    is not on the default search list either, so when the MCP host strips the
    server's PATH a bare ``powershell`` fails with WinError 2 (#411).

    Args:
        shell: The shell the caller asked for, or ``None`` to prefer ``pwsh`` and
            fall back to Windows PowerShell.
        env: The environment the child will run with.

    Returns:
        An absolute path when the shell can be found; otherwise Windows
        PowerShell's well-known location for a PowerShell request, or the name
        unchanged for any other shell so the launch error names it.
    """
    search_path = env.get("PATH")
    for name in (shell,) if shell else ("pwsh", "powershell"):
        found = shutil.which(name, path=search_path)
        if found:
            return found

    if shell and os.path.basename(shell).lower().removesuffix(".exe") != "powershell":
        return shell
    system_root = env.get("SystemRoot") or os.environ.get("SystemRoot") or r"C:\Windows"
    return os.path.join(system_root, _WINDOWS_POWERSHELL)


def wrap_command(command: str) -> str:
    """Prefix a user command with the session settings every call needs.

    - ``$OutputEncoding``: controls how PS5.1 encodes output written to its
      stdout pipe. Without UTF-8, PS5.1 uses the system codepage and native
      process stdout is silently lost when Python reads the pipe.
    - ``[Console]::OutputEncoding``: controls how PS decodes bytes from
      native exe stdout.
    - ``$ProgressPreference``: progress records must never reach the result;
      in some hosting modes they are serialized as CLIXML and pollute the
      output (and any string matching done on it).
    """
    return (
        "$OutputEncoding = [System.Text.Encoding]::UTF8; "
        "[Console]::OutputEncoding = [System.Text.Encoding]::UTF8; "
        "$ProgressPreference = 'SilentlyContinue'; "
        f"{command}"
    )


def encode_command(command: str) -> str:
    """Base64 (UTF-16LE) encode a command for ``-EncodedCommand``."""
    return base64.b64encode(wrap_command(command).encode("utf-16le")).decode("ascii")


def build_invocation(
        command: str, shell: str | None = None
) -> tuple[list[str], dict[str, str]]:
    """Build the ``(argv, env)`` used to launch a PowerShell command.

    Shared by the synchronous executor and the background job runner so both
    paths resolve the shell, rebuild the environment and wrap the command
    (UTF-8, no progress records) identically.
    """
    encoded = encode_command(command)
    env = _prepare_env()
    # NO_COLOR suppresses ANSI escape sequences in pwsh 7.2+ (and many other CLI tools).
    # PS5.1 has no ANSI output, so this is harmlessly ignored there.
    # https://learn.microsoft.com/en-us/powershell/module/microsoft.powershell.core/about/about_ansi_terminals#disabling-ansi-output
    env["NO_COLOR"] = "1"

    shell = _resolve_shell(shell, env)

    args = [shell, "-NoProfile"]
    # Only older Windows PowerShell (5.1) uses -OutputFormat Text successfully here
    shell_name = os.path.basename(shell).lower().replace(".exe", "")
    if shell_name == "powershell":
        args.extend(["-OutputFormat", "Text"])
    args.extend(["-EncodedCommand", encoded])
    return args, env


class PowerShellExecutor:
    """Static utility class for executing PowerShell commands."""

    @staticmethod
    def execute_command_detailed(
            command: str, timeout: int = 10, shell: str | None = None
    ) -> tuple[str, str, int]:
        """Execute a command and return ``(stdout, stderr, status_code)``.

        Both streams are decoded as UTF-8 and stripped of CLIXML noise, and
        are returned separately so callers (the PowerShell tool) can present
        them as distinct fields instead of one merged blob.
        """
        try:
            args, env = build_invocation(command, shell)
            shell = args[0]

            result = run_with_graceful_timeout(
                args,
                stdin=subprocess.DEVNULL,  # Prevent child processes from inheriting the MCP pipe stdin
                capture_output=True,  # No errors='ignore' - let subprocess return bytes
                timeout=timeout,
                cwd=os.path.expanduser(path="~"),
                env=env,
            )
            # Handle both bytes and str output (subprocess behavior varies by environment)
            stdout = result.stdout
            stderr = result.stderr
            if isinstance(stdout, bytes):
                stdout = stdout.decode("utf-8", errors="replace")
            if isinstance(stderr, bytes):
                stderr = stderr.decode("utf-8", errors="replace")
            return strip_clixml(stdout), strip_clixml(stderr), result.returncode
        except subprocess.TimeoutExpired:
            return "Command execution timed out", "", 1
        except Exception as e:
            # WinError 2 does not say which file was missing, so name the shell.
            logger.warning("PowerShell command failed to run (shell=%s)", shell, exc_info=True)
            return f"Command execution failed: {type(e).__name__}: {e} (shell: {shell})", "", 1

    @staticmethod
    def execute_command(
            command: str, timeout: int = 10, shell: str | None = None
    ) -> tuple[str, int]:
        """Execute a command and return ``(output, status_code)``.

        Kept for the many internal callers that expect a single output
        string: stdout wins, stderr is the fallback, matching the historical
        behaviour.
        """
        stdout, stderr, status_code = PowerShellExecutor.execute_command_detailed(
            command, timeout, shell
        )
        output = stdout or stderr
        # If the command failed with "Access is denied" and we aren't elevated, add a helpful hint
        if status_code != 0 and "Access is denied" in output and not is_elevated():
            output += (
                "\n\nHINT: This command may require an elevated (Administrator) terminal. "
                "The Agentic Agentic Windows MCP server is currently running at a lower integrity level."
            )
        return output, status_code

    @staticmethod
    def start_background(command: str, shell: str | None = None):
        """Start a command as a background job and return the job record.

        The job's state follows the PowerShell process itself, so a command
        that spawns a detached child (a dev server started with
        ``Start-Process``, ``cmd /c start /b`` …) no longer blocks the caller
        until a timeout the way the synchronous path does: the job reports
        ``exited`` as soon as PowerShell exits, while any output a detached
        grandchild still writes into the inherited pipes keeps landing in the
        job's buffers.
        """
        from agentic_windows_mcp.powershell.jobs import JOBS

        args, env = build_invocation(command, shell)
        return JOBS.start(command, args, env, cwd=os.path.expanduser(path="~"))
