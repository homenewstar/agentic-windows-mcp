# ============================================================
#  awmcp-revive - port-watch watchdog for the server task.
#  Run every few minutes from a scheduled task of its own
#  (e.g. task name 'awmcp-revive'). When nothing listens on the
#  server port, the server task is Ended and Run again so the
#  self-healing launcher (issue #32) gets a fresh start.
#
#  The interpreter check (issue #32, point 4) runs BEFORE the
#  restart: if no Python >= 3.12 can even start, an End+Run loop
#  would spin forever against a launcher that can only rebuild
#  from a working interpreter - so that condition is logged
#  loudly in revive.log instead of being rediscovered by hand
#  in server.error.log hours later (incident #31).
# ============================================================
param([int]$Port = 8000, [string]$TaskName = 'agentic-windows-mcp-server')
$dir = "$env:USERPROFILE\.agentic-windows-mcp"
$log = Join-Path $dir 'revive.log'
function Revive-Log($message) {
    Add-Content -Path $log -Value "[revive] $(Get-Date -Format o) $message"
}

$listening = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
if ($listening) { exit 0 }

Revive-Log "nothing listening on port $Port; checking interpreters before restart"

# Interpreter existence: the install venv, else any py tag that runs.
$venvPy = Join-Path $dir 'venv\Scripts\python.exe'
$interpreterOk = $false
$interpreterDesc = 'none'
if (Test-Path $venvPy) {
    $null = & $venvPy -c "import sys" 2>$null
    if ($LASTEXITCODE -eq 0) { $interpreterOk = $true; $interpreterDesc = "venv ($venvPy)" }
}
if (-not $interpreterOk) {
    foreach ($tag in '3.14','3.13','3.12') {
        $null = & py "-$tag" -c "import sys" 2>$null
        if ($LASTEXITCODE -eq 0) { $interpreterOk = $true; $interpreterDesc = "py -$tag"; break }
    }
}
if ($interpreterOk) {
    Revive-Log "working interpreter found: $interpreterDesc - the launcher will select or rebuild at task start"
} else {
    Revive-Log "ERROR: no working Python >= 3.12 on this machine - restarting the task cannot revive the server until one is installed"
}

schtasks /End /TN $TaskName 2>$null | Out-Null
Start-Sleep -Seconds 2
schtasks /Run /TN $TaskName 2>$null | Out-Null
Revive-Log "task $TaskName Ended and Run again"
