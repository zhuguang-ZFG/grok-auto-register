@echo off
REM 兼容入口：实际由 VBS 隐藏启动
cd /d "%~dp0"
wscript.exe //B //Nologo "%~dp0run_hidden.vbs" "pool_maintain.py"
