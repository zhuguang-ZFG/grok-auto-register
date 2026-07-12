@echo off
setlocal
cd /d "%~dp0"
python -u status.py %*
endlocal
