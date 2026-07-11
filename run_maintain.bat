@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo ============================================================
echo  号池维持  %DATE% %TIME%
echo ============================================================
python pool_maintain.py %*
set ERR=%ERRORLEVEL%
echo [*] maintain exit=%ERR%
exit /b %ERR%
