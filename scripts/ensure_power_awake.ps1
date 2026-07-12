# Ensure laptop stays usable for unattended pool work when on AC power.
# Uses powercfg GUIDs (locale-safe).
#
# Usage:
#   powershell -ExecutionPolicy Bypass -File .\scripts\ensure_power_awake.ps1
#   powershell -ExecutionPolicy Bypass -File .\scripts\ensure_power_awake.ps1 -AlsoBattery

param(
    [switch]$AlsoBattery,
    [switch]$WhatIf
)

$ErrorActionPreference = "Continue"

# Well-known GUIDs
$SUB_SLEEP   = "238c9fa8-0aad-41ed-83f4-97be242c8f20"
$STANDBYIDLE = "29f6c1db-86da-48c5-9fdb-f2b67b1f44da"
$HYBRIDSLEEP = "94ac6d29-73ce-41a6-809f-6363ba21b47e"
$HIBERNATEIDLE = "9d7815a6-7ee4-497e-8888-515a05f02364"
$RTCWAKE     = "bd3b718a-0680-4d9d-8ab2-e1d2b4ac806d"
$SUB_BUTTONS = "4f971e89-eebd-4455-a8de-9e59040e7347"
$LIDACTION   = "5ca83367-6e45-459f-a27b-476b1d01c936"  # Lid close action

function Apply-AcDc([string]$sub, [string]$setting, [string]$acHex, [string]$dcHex) {
    if ($WhatIf) {
        Write-Host "WHATIF setac $sub $setting $acHex"
        if ($null -ne $dcHex) { Write-Host "WHATIF setdc $sub $setting $dcHex" }
        return
    }
    $r1 = & powercfg /setacvalueindex SCHEME_CURRENT $sub $setting $acHex 2>&1
    if ($LASTEXITCODE -ne 0) { Write-Host "WARN setac $setting : $r1" }
    if ($null -ne $dcHex) {
        $r2 = & powercfg /setdcvalueindex SCHEME_CURRENT $sub $setting $dcHex 2>&1
        if ($LASTEXITCODE -ne 0) { Write-Host "WARN setdc $setting : $r2" }
    }
}

Write-Host "[power] AC: sleep=never, hibernate=never, lid=do-nothing, wake-timers=on"

# 0 minutes = never for idle timeouts
Apply-AcDc $SUB_SLEEP $STANDBYIDLE "0" $(if ($AlsoBattery) { "0" } else { $null })
Apply-AcDc $SUB_SLEEP $HIBERNATEIDLE "0" $(if ($AlsoBattery) { "0" } else { $null })
# hybrid sleep off
Apply-AcDc $SUB_SLEEP $HYBRIDSLEEP "0" $(if ($AlsoBattery) { "0" } else { $null })
# lid: 0 = Do nothing
Apply-AcDc $SUB_BUTTONS $LIDACTION "0" $(if ($AlsoBattery) { "0" } else { $null })
# RTC wake enable
Apply-AcDc $SUB_SLEEP $RTCWAKE "1" $(if ($AlsoBattery) { "1" } else { $null })

if (-not $WhatIf) {
    & powercfg /setactive SCHEME_CURRENT | Out-Null
}

Write-Host "[power] verify STANDBYIDLE (AC should be 0x0):"
& powercfg /q SCHEME_CURRENT $SUB_SLEEP $STANDBYIDLE 2>&1 | Select-String "0x"
Write-Host "[power] verify LIDACTION (AC should be 0x0):"
& powercfg /q SCHEME_CURRENT $SUB_BUTTONS $LIDACTION 2>&1 | Select-String "0x"
Write-Host "[power] keep AC plugged. Display-off is OK; sleep is not."
if (-not $AlsoBattery) {
    Write-Host "[power] DC battery profile left unchanged (use -AlsoBattery to force)."
}
