@echo off
chcp 65001 >nul
cd /d "%~dp0"
REM Proxy first: rotate Clash if accounts.x.ai TLS/path is bad
wscript.exe //B //Nologo "%~dp0run_hidden.vbs" "proxy_health.py --rotate-if-bad"
wscript.exe //B //Nologo "%~dp0run_hidden.vbs" "pool_health.py"
wscript.exe //B //Nologo "%~dp0run_hidden.vbs" "auto_link_cli.py"
exit /b 0
