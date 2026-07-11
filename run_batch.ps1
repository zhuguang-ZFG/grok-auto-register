# Grok 批量注册启动器 (PowerShell)
# 用法:
#   .\run_batch.ps1
#   .\run_batch.ps1 -Count 50 -Concurrency 3
#   .\run_batch.ps1 -RetryPush

param(
    [int]$Count = 0,
    [int]$Concurrency = 0,
    [switch]$RetryPush,
    [switch]$NoPush
)

$ErrorActionPreference = "Stop"
Set-Location -Path $PSScriptRoot

$pyArgs = @()
if ($RetryPush) {
    $pyArgs += "--retry-push"
} else {
    if ($Count -gt 0) { $pyArgs += @("-n", "$Count") }
    if ($Concurrency -gt 0) { $pyArgs += @("-c", "$Concurrency") }
    if ($NoPush) { $pyArgs += "--no-push" }
    $pyArgs += "-y"
}

Write-Host "[*] cwd: $PWD"
Write-Host "[*] python grok_register_ttk.py $($pyArgs -join ' ')"
python grok_register_ttk.py @pyArgs
exit $LASTEXITCODE
