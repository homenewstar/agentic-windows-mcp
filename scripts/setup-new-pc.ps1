# ============================================================
#  Agentic Windows MCP - FULL setup for a NEW Windows PC
#  One paste in PowerShell does everything:
#  uv -> Python 3.12 -> package from PyPI -> auth key ->
#  Scheduled Task (server on 0.0.0.0:8000) -> Tailscale ->
#  tailscale serve. You only: approve UAC if asked, sign in
#  to Tailscale in the browser, read back the machine name.
# ============================================================
$ErrorActionPreference = "Stop"
function Step($msg) { Write-Host "`n=== $msg ===" -ForegroundColor Cyan }
function Refresh-Path {
    $env:Path = [System.Environment]::GetEnvironmentVariable("Path","User") + ";" +
                [System.Environment]::GetEnvironmentVariable("Path","Machine")
}

# --- 1. uv ---
Step "1/6  uv"
if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
    powershell -ExecutionPolicy Bypass -c "irm https://astral.sh/uv/install.ps1 | iex"
    Refresh-Path
}
uv --version

# --- 2. Python 3.12 (managed by uv) ---
Step "2/6  Python 3.12"
uv python install 3.12

# --- 3. Agentic Windows MCP from PyPI ---
Step "3/6  agentic-windows-mcp (PyPI)"
uv tool install agentic-windows-mcp
uv tool update-shell
Refresh-Path
if (-not (Get-Command agentic-windows-mcp -ErrorAction SilentlyContinue)) {
    throw "agentic-windows-mcp not on PATH - open a NEW PowerShell window and re-run."
}

# --- 4. Auth key ---
Step "4/6  Auth key"
Write-Host "Keep the key PRIVATE - never paste it into any chat." -ForegroundColor Yellow
Write-Host "You will type it later into a secure form."
agentic-windows-mcp auth

# --- 5. Scheduled Task + start server (same config as the main PC) ---
Step "5/6  Scheduled Task + server on 0.0.0.0:8000"
agentic-windows-mcp install --transport streamable-http --host 0.0.0.0 --port 8000 --force
Start-Sleep -Seconds 3
netstat -ano | findstr ":8000"

# --- 6. Tailscale: install, sign in, expose the server in your tailnet ---
Step "6/6  Tailscale"
$ts = "$env:ProgramFiles\Tailscale\tailscale.exe"
if (-not (Test-Path $ts)) {
    Write-Host "Installing Tailscale via winget (click YES if Windows asks)..."
    winget install --id Tailscale.Tailscale -e --accept-source-agreements --accept-package-agreements
}
if (-not (Test-Path $ts)) { throw "Tailscale install failed - install it from https://tailscale.com/download then re-run." }
Write-Host "A browser window will open - sign in with the SAME Tailscale account as your main PC." -ForegroundColor Yellow
& $ts up
& $ts serve --bg --https=443 http://127.0.0.1:8000
$dns = (& $ts status --json | ConvertFrom-Json).Self.DNSName.TrimEnd('.')
Write-Host "`n============================================================" -ForegroundColor Green
Write-Host " ALL DONE. Send ONLY this machine name back (never the key):" -ForegroundColor Green
Write-Host "   $dns" -ForegroundColor Green
Write-Host "============================================================" -ForegroundColor Green
