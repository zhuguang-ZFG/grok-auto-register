# Enable full unattended automation for D:\Users\grok-auto-register
param(
    [int]$MaintainEveryHours = 2,
    [int]$HealthEveryMinutes = 45
)

$ErrorActionPreference = "Stop"
$root = "D:\Users\grok-auto-register"
Set-Location -LiteralPath $root

Write-Host "============================================================"
Write-Host " Enable autonomy for REAL project"
Write-Host " root: $root"
Write-Host "============================================================"

# 1) maintain: health + optional register topup via quota/register
$maintainName = "GrokPoolMaintain"
try { Unregister-ScheduledTask -TaskName $maintainName -Confirm:$false -ErrorAction SilentlyContinue } catch {}
$maintainBat = Join-Path $root "run_maintain.bat"
if (-not (Test-Path $maintainBat)) {
  @"
@echo off
chcp 65001 >nul
cd /d "$root"
python pool_maintain.py %*
"@ | Set-Content -LiteralPath $maintainBat -Encoding ASCII
}
$principal = New-ScheduledTaskPrincipal -UserId $env:USERNAME -LogonType Interactive -RunLevel Limited
$actionM = New-ScheduledTaskAction -Execute "cmd.exe" -Argument "/c `"$maintainBat`"" -WorkingDirectory $root
$startM = (Get-Date).AddMinutes(5)
$triggerM = New-ScheduledTaskTrigger -Once -At $startM -RepetitionInterval (New-TimeSpan -Hours $MaintainEveryHours) -RepetitionDuration (New-TimeSpan -Days 3650)
$settingsM = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Hours 4)
Register-ScheduledTask -TaskName $maintainName -Action $actionM -Trigger $triggerM -Settings $settingsM -Principal $principal -Force | Out-Null
Write-Host "[+] $maintainName every ${MaintainEveryHours}h"

# 2) health-only
$healthName = "GrokPoolHealth"
try { Unregister-ScheduledTask -TaskName $healthName -Confirm:$false -ErrorAction SilentlyContinue } catch {}
$healthBat = Join-Path $root "run_health_only.bat"
@"
@echo off
chcp 65001 >nul
cd /d "$root"
python pool_health.py
python auto_link_cli.py
"@ | Set-Content -LiteralPath $healthBat -Encoding ASCII
$actionH = New-ScheduledTaskAction -Execute "cmd.exe" -Argument "/c `"$healthBat`"" -WorkingDirectory $root
$startH = (Get-Date).AddMinutes(3)
$triggerH = New-ScheduledTaskTrigger -Once -At $startH -RepetitionInterval (New-TimeSpan -Minutes $HealthEveryMinutes) -RepetitionDuration (New-TimeSpan -Days 3650)
$settingsH = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Hours 1)
Register-ScheduledTask -TaskName $healthName -Action $actionH -Trigger $triggerH -Settings $settingsH -Principal $principal -Force | Out-Null
Write-Host "[+] $healthName every ${HealthEveryMinutes}m"

# 3) boot
$bootName = "GrokPoolBoot"
try { Unregister-ScheduledTask -TaskName $bootName -Confirm:$false -ErrorAction SilentlyContinue } catch {}
$actionB = New-ScheduledTaskAction -Execute "cmd.exe" -Argument "/c `"$maintainBat`"" -WorkingDirectory $root
$triggerB = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
$settingsB = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Hours 4)
Register-ScheduledTask -TaskName $bootName -Action $actionB -Trigger $triggerB -Settings $settingsB -Principal $principal -Force | Out-Null
Write-Host "[+] $bootName AtLogOn"

# 4) CLIProxy autostart (already may exist)
$cpaName = "CLIProxyAPI-Local"
try { Unregister-ScheduledTask -TaskName $cpaName -Confirm:$false -ErrorAction SilentlyContinue } catch {}
$actionC = New-ScheduledTaskAction -Execute "D:\cli-proxy-api\cli-proxy-api.exe" -Argument "-config D:\cli-proxy-api\config.yaml" -WorkingDirectory "D:\cli-proxy-api"
$triggerC = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
$settingsC = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1)
Register-ScheduledTask -TaskName $cpaName -Action $actionC -Trigger $triggerC -Settings $settingsC -Principal $principal -Force | Out-Null
Write-Host "[+] $cpaName AtLogOn"

# 5) immediate
python "$root\pool_health.py"
python "$root\auto_link_cli.py"
try { Start-ScheduledTask -TaskName $maintainName } catch {}
try { Start-ScheduledTask -TaskName $healthName } catch {}

Write-Host "[OK] autonomy bound to $root"
Write-Host " Kimi: kimi -m local-cpa/grok-4.5"
Write-Host " CPA:  http://127.0.0.1:8317  auth-dir=cpa_auths"
