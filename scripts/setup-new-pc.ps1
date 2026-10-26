# ============================================================
#  Agentic Windows MCP - FULL setup for a NEW Windows PC (v3)
#  Forces Python 3.13 explicitly (a machine may already have a
#  beta/newer Python whose packages must compile from source -
#  and this PC's Application Control blocks build-time DLLs).
#  On 3.13 every dependency ships as a ready wheel: no builds.
# ============================================================
$ErrorActionPreference = "Stop"
function Step($msg) { Write-Host "`n=== $msg ===" -ForegroundColor Cyan }
function Refresh-Path {
    $env:Path = [System.Environment]::GetEnvironmentVariable("Path","User") + ";" +
                [System.Environment]::GetEnvironmentVariable("Path","Machine")
}

# --- 1. Python 3.13, explicitly selected (ignore any other Python) ---
Step "1/6  Python 3.13 (python.org)"
$have313 = $false
try { $v = & py -3.13 --version 2>$null; if ($v -match "3\.13") { $have313 = $true } } catch {}
if (-not $have313) {
    $installer = "$env:TEMP\python-3.13.7-amd64.exe"
    Invoke-WebRequest "https://www.python.org/ftp/python/3.13.7/python-3.13.7-amd64.exe" -OutFile $installer
    Start-Process $installer -ArgumentList "/quiet InstallAllUsers=0 PrependPath=1 Include_launcher=1" -Wait
    Refresh-Path
}
py -3.13 --version

# --- 2. Agentic Windows MCP from PyPI (all wheels, nothing compiles) ---
Step "2/6  agentic-windows-mcp (PyPI via pip)"
py -3.13 -m pip install --upgrade pip
py -3.13 -m pip install agentic-windows-mcp

# --- 3. Auth key ---
Step "3/6  Auth key"
Write-Host "Keep the key PRIVATE - never paste it into any chat." -ForegroundColor Yellow
Write-Host "You will type it later into a secure form."
py -3.13 -m agentic_windows_mcp auth

# --- 4. Scheduled Task + start server (same config as the main PC) ---
Step "4/6  Scheduled Task + server on 0.0.0.0:8000"
py -3.13 -m agentic_windows_mcp install --transport streamable-http --host 0.0.0.0 --port 8000 --force
Start-Sleep -Seconds 3
netstat -ano | findstr ":8000"

# --- 5. Tailscale serve ---
Step "5/6  Tailscale"
$ts = "$env:ProgramFiles\Tailscale\tailscale.exe"
if (-not (Test-Path $ts)) {
    winget install --id Tailscale.Tailscale -e --accept-source-agreements --accept-package-agreements
}
& $ts up
& $ts serve --bg --https=443 http://127.0.0.1:8000
if ($LASTEXITCODE -ne 0) {
    Write-Host "`nTailscale Serve is not enabled yet for this tailnet." -ForegroundColor Yellow
    Write-Host "Open the link Tailscale showed above (login.tailscale.com/f/serve?node=...),"
    Write-Host "enable Serve, then run these two commands:"
    Write-Host "   & `"$ts`" serve --bg --https=443 http://127.0.0.1:8000"
    Write-Host "   & `"$ts`" status"
}

# --- 6. Report the machine name ---
Step "6/6  Done"
try {
    $dns = (& $ts status --json | ConvertFrom-Json).Self.DNSName.TrimEnd('.')
    Write-Host "`n============================================================" -ForegroundColor Green
    Write-Host " Send ONLY this machine name back (never the key):" -ForegroundColor Green
    Write-Host "   $dns" -ForegroundColor Green
    Write-Host "============================================================" -ForegroundColor Green
} catch {
    Write-Host "Run 'tailscale status' and send back this machine's name (first column)." -ForegroundColor Yellow
}
