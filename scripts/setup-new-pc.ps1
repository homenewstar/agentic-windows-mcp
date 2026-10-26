# ============================================================
#  Agentic Windows MCP - FULL setup for a NEW Windows PC (v2)
#  Route: signed Python.org installer + pip (no uv - uv.exe is
#  blocked by Application Control / Smart App Control on some
#  new machines). The scheduled task runs through python.exe
#  itself, so everything stays inside trusted, signed code.
# ============================================================
$ErrorActionPreference = "Stop"
function Step($msg) { Write-Host "`n=== $msg ===" -ForegroundColor Cyan }
function Refresh-Path {
    $env:Path = [System.Environment]::GetEnvironmentVariable("Path","User") + ";" +
                [System.Environment]::GetEnvironmentVariable("Path","Machine")
}

# --- 1. Python 3.13 from python.org (signed by the PSF) ---
Step "1/6  Python 3.13 (python.org)"
if (-not (Get-Command py -ErrorAction SilentlyContinue)) {
    $installer = "$env:TEMP\python-3.13.7-amd64.exe"
    Invoke-WebRequest "https://www.python.org/ftp/python/3.13.7/python-3.13.7-amd64.exe" -OutFile $installer
    Start-Process $installer -ArgumentList "/quiet InstallAllUsers=0 PrependPath=1 Include_launcher=1" -Wait
    Refresh-Path
}
py --version

# --- 2. Agentic Windows MCP from PyPI ---
Step "2/6  agentic-windows-mcp (PyPI via pip)"
py -m pip install --upgrade pip
py -m pip install agentic-windows-mcp

# --- 3. Auth key ---
Step "3/6  Auth key"
Write-Host "Keep the key PRIVATE - never paste it into any chat." -ForegroundColor Yellow
Write-Host "You will type it later into a secure form."
py -m agentic_windows_mcp auth

# --- 4. Scheduled Task + start server (same config as the main PC) ---
Step "4/6  Scheduled Task + server on 0.0.0.0:8000"
py -m agentic_windows_mcp install --transport streamable-http --host 0.0.0.0 --port 8000 --force
Start-Sleep -Seconds 3
netstat -ano | findstr ":8000"

# --- 5. Tailscale: install, sign in, expose the server in your tailnet ---
Step "5/6  Tailscale"
$ts = "$env:ProgramFiles\Tailscale\tailscale.exe"
if (-not (Test-Path $ts)) {
    Write-Host "Installing Tailscale via winget (click YES if Windows asks)..."
    winget install --id Tailscale.Tailscale -e --accept-source-agreements --accept-package-agreements
}
if (-not (Test-Path $ts)) { throw "Tailscale install failed - install it from https://tailscale.com/download then re-run steps 5-6 manually." }
Write-Host "A browser window will open - sign in with the SAME Tailscale account as your main PC." -ForegroundColor Yellow
& $ts up
& $ts serve --bg --https=443 http://127.0.0.1:8000

# --- 6. Report the machine name ---
Step "6/6  Done"
$dns = (& $ts status --json | ConvertFrom-Json).Self.DNSName.TrimEnd('.')
Write-Host "`n============================================================" -ForegroundColor Green
Write-Host " ALL DONE. Send ONLY this machine name back (never the key):" -ForegroundColor Green
Write-Host "   $dns" -ForegroundColor Green
Write-Host "============================================================" -ForegroundColor Green
