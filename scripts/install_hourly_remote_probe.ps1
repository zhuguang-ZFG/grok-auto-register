#Requires -Version 5.1
<#
.SYNOPSIS
  Install hourly remote-only probe (charity quota / slow source temp-out + recover).
#>
$ErrorActionPreference = "Stop"
$taskName = "GrokHourlyRemoteProbe"
$cmd = "D:\Users\grok-auto-register\scripts\run_hourly_remote_probe.cmd"
$action = New-ScheduledTaskAction -Execute $cmd
# every hour at :23 (avoid :00 herd with other tasks)
$trigger = New-ScheduledTaskTrigger -Once -At (Get-Date).Date.AddHours((Get-Date).Hour + 1).AddMinutes(23) `
    -RepetitionInterval (New-TimeSpan -Hours 1) -RepetitionDuration (New-TimeSpan -Days 9999)
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
    -StartWhenAvailable -ExecutionTimeLimit (New-TimeSpan -Minutes 45)
Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $trigger `
    -Settings $settings -Force | Out-Null
Write-Host "Installed scheduled task $taskName -> $cmd"
Get-ScheduledTask -TaskName $taskName | Get-ScheduledTaskInfo | Format-List TaskName, NextRunTime
