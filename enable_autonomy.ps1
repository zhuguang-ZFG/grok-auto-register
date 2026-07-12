# Hidden autonomy installer (no CMD black window)
# Usage:
#   powershell -ExecutionPolicy Bypass -File .\enable_autonomy.ps1
#   powershell -ExecutionPolicy Bypass -File .\enable_autonomy.ps1 -Unregister

param(
    [switch]$Unregister
)

$ErrorActionPreference = "Stop"
$root = $PSScriptRoot
if (-not $root) { $root = "D:\Users\grok-auto-register" }
$MaintainEveryHours = 2
$RefreshEveryHours = 2
$HealthEveryMinutes = 45
$cliproxyDir = "D:\cli-proxy-api"

Set-Location -LiteralPath $root
$principal = New-ScheduledTaskPrincipal -UserId $env:USERNAME -LogonType Interactive -RunLevel Limited

$taskNames = @(
    "GrokPoolMaintain",
    "GrokPoolRefresh",
    "GrokPoolHealth",
    "GrokPoolBoot",
    "GrokRegisterAuto",
    "CLIProxyAPI-Local",
    "GrokQuotaWatch"
)

if ($Unregister) {
    foreach ($n in $taskNames) {
        try { Unregister-ScheduledTask -TaskName $n -Confirm:$false -ErrorAction SilentlyContinue } catch {}
        Write-Host "[-] removed $n"
    }
    exit 0
}

function Install-Task([string]$Name, [string]$Execute, [string]$Arguments, [string]$WorkDir, $Trigger, [int]$HoursLimit) {
    try { Unregister-ScheduledTask -TaskName $Name -Confirm:$false -ErrorAction SilentlyContinue } catch {}
    $action = New-ScheduledTaskAction -Execute $Execute -Argument $Arguments -WorkingDirectory $WorkDir
    if ($HoursLimit -le 0) {
        $settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable -MultipleInstances IgnoreNew -Hidden
    } else {
        $settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Hours $HoursLimit) -Hidden
    }
    Register-ScheduledTask -TaskName $Name -Action $action -Trigger $Trigger -Settings $settings -Principal $principal -Force | Out-Null
    Write-Host "[+] $Name"
}

Write-Host "Hidden autonomy @ $root"

# Full maintain (refresh+health+conditional refill) every N hours
$startM = (Get-Date).AddMinutes(5)
$triggerM = New-ScheduledTaskTrigger -Once -At $startM -RepetitionInterval (New-TimeSpan -Hours $MaintainEveryHours) -RepetitionDuration (New-TimeSpan -Days 3650)
Install-Task "GrokPoolMaintain" "wscript.exe" ("//B //Nologo `"$root\run_hidden.vbs`" pool_maintain.py") $root $triggerM 4

# Lightweight token refresh every N hours (overlap ok; maintain also refreshes)
$startR = (Get-Date).AddMinutes(8)
$triggerR = New-ScheduledTaskTrigger -Once -At $startR -RepetitionInterval (New-TimeSpan -Hours $RefreshEveryHours) -RepetitionDuration (New-TimeSpan -Days 3650)
Install-Task "GrokPoolRefresh" "wscript.exe" ("//B //Nologo `"$root\run_hidden.vbs`" refresh_pool.py --within-hours 2 --max 400 --workers 3 --purge-dead") $root $triggerR 2

# Health-only more frequent
$startH = (Get-Date).AddMinutes(3)
$triggerH = New-ScheduledTaskTrigger -Once -At $startH -RepetitionInterval (New-TimeSpan -Minutes $HealthEveryMinutes) -RepetitionDuration (New-TimeSpan -Days 3650)
Install-Task "GrokPoolHealth" "wscript.exe" ("//B //Nologo `"$root\run_hidden.vbs`" run_health_only.bat") $root $triggerH 1

# At logon: maintain once + long-running services
$triggerB = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
Install-Task "GrokPoolBoot" "wscript.exe" ("//B //Nologo `"$root\run_hidden.vbs`" pool_maintain.py") $root $triggerB 4

$triggerReg = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
Install-Task "GrokRegisterAuto" "wscript.exe" ("//B //Nologo `"$root\start_register_hidden.vbs`"") $root $triggerReg 0

$triggerC = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
Install-Task "CLIProxyAPI-Local" "wscript.exe" ("//B //Nologo `"$root\start_cliproxy_hidden.vbs`"") $cliproxyDir $triggerC 0

$triggerQ = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
Install-Task "GrokQuotaWatch" "wscript.exe" ("//B //Nologo `"$root\start_quota_watch_hidden.vbs`"") $root $triggerQ 0

# Start services now (idempotent VBS)
Start-Process -FilePath "wscript.exe" -ArgumentList @("//B","//Nologo","$root\start_cliproxy_hidden.vbs") -WindowStyle Hidden
Start-Process -FilePath "wscript.exe" -ArgumentList @("//B","//Nologo","$root\start_quota_watch_hidden.vbs") -WindowStyle Hidden
Start-Process -FilePath "wscript.exe" -ArgumentList @("//B","//Nologo","$root\start_register_hidden.vbs") -WindowStyle Hidden

Write-Host "[OK] tasks: $($taskNames -join ', ')"
Write-Host "    maintain every ${MaintainEveryHours}h | refresh every ${RefreshEveryHours}h | health every ${HealthEveryMinutes}m"
Write-Host "    remove: powershell -ExecutionPolicy Bypass -File .\enable_autonomy.ps1 -Unregister"
Write-Host "    list:   Get-ScheduledTask | ? TaskName -like 'Grok*' "
