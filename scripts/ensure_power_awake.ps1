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

$SUB_SLEEP     = "238c9fa8-0aad-41ed-83f4-97be242c8f20"
$STANDBYIDLE   = "29f6c1db-86da-48c5-9fdb-f2b67b1f44da"
$HYBRIDSLEEP   = "94ac6d29-73ce-41a6-809f-6363ba21b47e"
$HIBERNATEIDLE = "9d7815a6-7ee4-497e-8888-515a05f02364"
$RTCWAKE       = "bd3b718a-0680-4d9d-8ab2-e1d2b4ac806d"
$SUB_BUTTONS   = "4f971e89-eebd-4455-a8de-9e59040e7347"
$LIDACTION     = "5ca83367-6e45-459f-a27b-476b1d01c936"

function Set-Ac([string]$sub, [string]$setting, [string]$value) {
    if ($WhatIf) {
        Write-Host "WHATIF setac $setting $value"
        return $true
    }
    $out = & powercfg /setacvalueindex SCHEME_CURRENT $sub $setting $value 2>&1 | Out-String
    if ($LASTEXITCODE -ne 0) {
        Write-Host ("WARN setac {0}: {1}" -f $setting, $out.Trim())
        return $false
    }
    return $true
}

function Set-Dc([string]$sub, [string]$setting, [string]$value) {
    if ($WhatIf) {
        Write-Host "WHATIF setdc $setting $value"
        return $true
    }
    $out = & powercfg /setdcvalueindex SCHEME_CURRENT $sub $setting $value 2>&1 | Out-String
    if ($LASTEXITCODE -ne 0) {
        Write-Host ("WARN setdc {0}: {1}" -f $setting, $out.Trim())
        return $false
    }
    return $true
}

Write-Host "[power] AC: sleep=never, hibernate=never, lid=do-nothing, wake-timers=on"

# 0 = never for idle timeouts; lid 0 = Do nothing
[void](Set-Ac $SUB_SLEEP $STANDBYIDLE "0")
[void](Set-Ac $SUB_SLEEP $HIBERNATEIDLE "0")
[void](Set-Ac $SUB_SLEEP $HYBRIDSLEEP "0")
[void](Set-Ac $SUB_BUTTONS $LIDACTION "0")
[void](Set-Ac $SUB_SLEEP $RTCWAKE "1")

if ($AlsoBattery) {
    Write-Host "[power] AlsoBattery: applying same to DC"
    [void](Set-Dc $SUB_SLEEP $STANDBYIDLE "0")
    [void](Set-Dc $SUB_SLEEP $HIBERNATEIDLE "0")
    [void](Set-Dc $SUB_SLEEP $HYBRIDSLEEP "0")
    [void](Set-Dc $SUB_BUTTONS $LIDACTION "0")
    [void](Set-Dc $SUB_SLEEP $RTCWAKE "1")
}

if (-not $WhatIf) {
    & powercfg /setactive SCHEME_CURRENT | Out-Null
}

Write-Host "[power] verify STANDBYIDLE:"
& powercfg /q SCHEME_CURRENT $SUB_SLEEP $STANDBYIDLE 2>&1 | Select-String "0x"
Write-Host "[power] verify LIDACTION:"
& powercfg /q SCHEME_CURRENT $SUB_BUTTONS $LIDACTION 2>&1 | Select-String "0x"
Write-Host "[power] keep AC plugged. Display-off is OK; sleep is not."
if (-not $AlsoBattery) {
    Write-Host "[power] DC battery profile left unchanged (use -AlsoBattery to force)."
}
