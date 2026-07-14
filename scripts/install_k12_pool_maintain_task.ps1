# Install Windows scheduled task: K12-Pool-Maintain (daily 04:30)
# Headless: powershell -WindowStyle Hidden (no console flash).
# Run elevated or as current user.
$ErrorActionPreference = "Stop"
$taskName = "K12-Pool-Maintain"
$script = "D:\Users\grok-auto-register\scripts\run_k12_pool_maintain.ps1"
if (!(Test-Path $script)) { throw "missing $script" }

# Hidden window — pure CLI maintain, no browser, no interactive UI
$action = New-ScheduledTaskAction -Execute "powershell.exe" `
  -Argument "-NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File `"$script`""
$trigger = New-ScheduledTaskTrigger -Daily -At 4:30am
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
  -StartWhenAvailable -ExecutionTimeLimit (New-TimeSpan -Hours 2) `
  -Hidden
# S4U / Interactive: prefer Interactive so existing user session works without storing password
$principal = New-ScheduledTaskPrincipal -UserId $env:USERNAME -LogonType Interactive -RunLevel Limited

Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $trigger `
  -Settings $settings -Principal $principal -Force | Out-Null
Write-Host "registered task $taskName (Hidden) -> $script"
Get-ScheduledTask -TaskName $taskName | Format-List TaskName, State
Get-ScheduledTaskInfo -TaskName $taskName | Format-List LastRunTime, NextRunTime, LastTaskResult
