# Ensure Dahl local proxy is listening; restart via VBS if down.
# Install: schtasks /Create /TN "DahlProxyWatchdog" /SC MINUTE /MO 5 /TR "powershell -NoProfile -ExecutionPolicy Bypass -File D:\Users\grok-auto-register\scripts\dahl_proxy_watchdog.ps1" /F
$ErrorActionPreference = "SilentlyContinue"
$root = Split-Path (Split-Path $PSScriptRoot -Parent) -Parent
if (-not (Test-Path "$PSScriptRoot\..\start_dahl_proxy_hidden.vbs")) {
  $root = "D:\Users\grok-auto-register"
}
$root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$port = 8330
$ok = $false
try {
  $r = Invoke-WebRequest -Uri "http://127.0.0.1:$port/health" -Headers @{ Authorization = "Bearer sk-local-dahl" } -TimeoutSec 3 -UseBasicParsing
  if ($r.StatusCode -eq 200) { $ok = $true }
} catch {}
$log = Join-Path $root "logs\dahl_proxy_watchdog.log"
$ts = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
if ($ok) {
  Add-Content -Path $log -Value "$ts ok"
  exit 0
}
Add-Content -Path $log -Value "$ts down -> restart"
$vbs = Join-Path $root "start_dahl_proxy_hidden.vbs"
Start-Process -FilePath "wscript.exe" -ArgumentList "`"$vbs`"" -WindowStyle Hidden
exit 0
