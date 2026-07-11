# 安装 Windows 计划任务：号池定时健康检查 + 条件补号
# 管理员/当前用户 PowerShell:
#   Set-ExecutionPolicy -Scope Process Bypass
#   .\install_pool_task.ps1
#   .\install_pool_task.ps1 -EveryHours 2
#   .\install_pool_task.ps1 -Unregister

param(
    [string]$TaskName = "GrokPoolMaintain",
    [int]$EveryHours = 3,
    [switch]$Unregister
)

$ErrorActionPreference = "Stop"
$root = $PSScriptRoot
$bat = Join-Path $root "run_maintain.bat"

if ($Unregister) {
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false -ErrorAction SilentlyContinue
    Write-Host "[+] removed task $TaskName"
    exit 0
}

if (-not (Test-Path $bat)) {
    throw "missing $bat"
}

# 若已存在先删再装，保证参数更新
Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false -ErrorAction SilentlyContinue

$action = New-ScheduledTaskAction -Execute "cmd.exe" -Argument "/c `"$bat`"" -WorkingDirectory $root
# 从 5 分钟后开始，每 N 小时重复
$start = (Get-Date).AddMinutes(5)
$trigger = New-ScheduledTaskTrigger -Once -At $start -RepetitionInterval (New-TimeSpan -Hours $EveryHours) -RepetitionDuration (New-TimeSpan -Days 3650)
$settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -StartWhenAvailable `
    -MultipleInstances IgnoreNew `
    -ExecutionTimeLimit (New-TimeSpan -Hours 4)
$principal = New-ScheduledTaskPrincipal -UserId $env:USERNAME -LogonType Interactive -RunLevel Limited

Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Settings $settings -Principal $principal -Force | Out-Null
Write-Host "[+] ScheduledTask installed: $TaskName"
Write-Host "    every ${EveryHours}h from $start"
Write-Host "    action: $bat"
Write-Host "    check:  Get-ScheduledTask -TaskName $TaskName"
Write-Host "    run now: Start-ScheduledTask -TaskName $TaskName"
Write-Host "    remove:  .\install_pool_task.ps1 -Unregister"
