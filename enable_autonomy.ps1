# Hidden autonomy installer (no CMD black window)
# Usage: powershell -ExecutionPolicy Bypass -File .\enable_autonomy.ps1

$ErrorActionPreference = "Stop"
$root = "D:\Users\grok-auto-register"
$MaintainEveryHours = 2
$HealthEveryMinutes = 45

Set-Location -LiteralPath $root
$principal = New-ScheduledTaskPrincipal -UserId $env:USERNAME -LogonType Interactive -RunLevel Limited

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

$startM = (Get-Date).AddMinutes(5)
$triggerM = New-ScheduledTaskTrigger -Once -At $startM -RepetitionInterval (New-TimeSpan -Hours $MaintainEveryHours) -RepetitionDuration (New-TimeSpan -Days 3650)
Install-Task "GrokPoolMaintain" "wscript.exe" ("//B //Nologo `"$root\run_hidden.vbs`" pool_maintain.py") $root $triggerM 4

$startH = (Get-Date).AddMinutes(3)
$triggerH = New-ScheduledTaskTrigger -Once -At $startH -RepetitionInterval (New-TimeSpan -Minutes $HealthEveryMinutes) -RepetitionDuration (New-TimeSpan -Days 3650)
Install-Task "GrokPoolHealth" "wscript.exe" ("//B //Nologo `"$root\run_hidden.vbs`" run_health_only.bat") $root $triggerH 1

$triggerB = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
Install-Task "GrokPoolBoot" "wscript.exe" ("//B //Nologo `"$root\run_hidden.vbs`" pool_maintain.py") $root $triggerB 4

$triggerC = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
Install-Task "CLIProxyAPI-Local" "wscript.exe" ("//B //Nologo `"$root\start_cliproxy_hidden.vbs`"") "D:\cli-proxy-api" $triggerC 0

Start-Process -FilePath "wscript.exe" -ArgumentList @("//B","//Nologo","$root\start_cliproxy_hidden.vbs") -WindowStyle Hidden

$triggerQ = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
Install-Task "GrokQuotaWatch" "wscript.exe" ("//B //Nologo `"$root\start_quota_watch_hidden.vbs`"") $root $triggerQ 0
Start-Process -FilePath "wscript.exe" -ArgumentList @("//B","//Nologo","$root\start_quota_watch_hidden.vbs") -WindowStyle Hidden

Write-Host "[OK] tasks use wscript hidden launch"
