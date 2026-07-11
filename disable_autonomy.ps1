$names = @("GrokPoolMaintain", "GrokPoolHealth", "GrokPoolBoot", "CLIProxyAPI-Local")
foreach ($n in $names) {
  try { Unregister-ScheduledTask -TaskName $n -Confirm:$false -ErrorAction SilentlyContinue } catch {}
  Write-Host "[+] removed $n"
}
Write-Host "[OK] tasks removed (files kept)"
