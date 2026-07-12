# Ensure laptop stays usable for unattended pool work when on AC power.
# - Sleep never (AC)
# - Lid close = Do nothing (AC)
# - Display off allowed (saves panel; does not stop Python/tasks)
# Battery (DC) left mostly alone so undocked drain is not catastrophic.
#
# Usage (Admin recommended for some policies; usually works without):
#   powershell -ExecutionPolicy Bypass -File .\scripts\ensure_power_awake.ps1
#   powershell -ExecutionPolicy Bypass -File .\scripts\ensure_power_awake.ps1 -AlsoBattery

param(
    [switch]$AlsoBattery,
    [switch]$WhatIf
)

$ErrorActionPreference = "Continue"

function Set-Idx([string]$sub, [string]$set, [string]$ac, [string]$dc) {
    if ($WhatIf) {
        Write-Host "WHATIF powercfg /setacvalueindex SCHEME_CURRENT $sub $set $ac"
        if ($null -ne $dc) { Write-Host "WHATIF powercfg /setdcvalueindex SCHEME_CURRENT $sub $set $dc" }
        return
    }
    & powercfg /setacvalueindex SCHEME_CURRENT $sub $set $ac | Out-Null
    if ($null -ne $dc) {
        & powercfg /setdcvalueindex SCHEME_CURRENT $sub $set $dc | Out-Null
    }
}

Write-Host "[power] scheme current -> AC sleep never, lid do-nothing (AC)"

# Sleep after: 0 = never
Set-Idx "SUB_SLEEP" "STANDBYIDLE" "0" $(if ($AlsoBattery) { "0" } else { $null })
# Hibernate after: 0 = never (AC)
Set-Idx "SUB_SLEEP" "HIBERNATEIDLE" "0" $(if ($AlsoBattery) { "0" } else { $null })
# Hybrid sleep off (AC)
Set-Idx "SUB_SLEEP" "HYBRIDSLEEP" "0" $(if ($AlsoBattery) { "0" } else { $null })

# Lid close action: 0=Do nothing, 1=Sleep, 2=Hibernate, 3=Shutdown
Set-Idx "SUB_BUTTONS" "LIDACTION" "0" $(if ($AlsoBattery) { "0" } else { $null })

# Allow wake timers (AC) so scheduled tasks can still fire if something naps
Set-Idx "SUB_SLEEP" "RTCWAKE" "1" $(if ($AlsoBattery) { "1" } else { $null })

if (-not $WhatIf) {
    & powercfg /setactive SCHEME_CURRENT | Out-Null
}

Write-Host "[power] active scheme applied"
& powercfg /q SCHEME_CURRENT SUB_SLEEP STANDBYIDLE 2>&1 | Select-String -Pattern "当前|Current|0x" | Select-Object -First 8
Write-Host "[power] tip: keep AC plugged for 7x24; lid-close will not sleep on AC."
if (-not $AlsoBattery) {
    Write-Host "[power] battery profile unchanged (pass -AlsoBattery to also disable DC sleep)."
}
