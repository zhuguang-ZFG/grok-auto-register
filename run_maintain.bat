@echo off
chcp 65001 >nul
cd /d "%~dp0"
REM 计划任务/手动都走隐藏启动；需要看输出时用: python pool_maintain.py
wscript.exe //B //Nologo "%~dp0run_hidden.vbs" "pool_maintain.py" %*
exit /b 0
