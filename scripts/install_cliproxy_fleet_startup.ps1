#Requires -Version 5.1
<#
.SYNOPSIS
  Install CLIProxy fleet watchdog to current user's Startup folder (no admin).

  Creates:
    %APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup\CLIProxyFleetWatchdog.cmd
#>
$ErrorActionPreference = "Stop"
$startup = [Environment]::GetFolderPath("Startup")
$ps1 = "D:\Users\grok-auto-register\scripts\cliproxy_fleet_watchdog.ps1"
$cmdPath = Join-Path $startup "CLIProxyFleetWatchdog.cmd"
$cmd = @"
@echo off
rem CLIProxy Grok/Codex/Claude fleet watchdog (hidden)
start "" /min powershell.exe -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File "$ps1"
"@
Set-Content -Path $cmdPath -Value $cmd -Encoding ASCII
Write-Host "Installed: $cmdPath"
# ensure now
& powershell.exe -NoProfile -ExecutionPolicy Bypass -File $ps1 -Once
Write-Host "Done. Watchdog will start at next logon; instances ensured now."
