@echo off
setlocal
cd /d D:\Users\grok-auto-register
echo ===== retest_quarantine %DATE% %TIME% =====>> logs\retest_quarantine.log
python scripts\retest_quarantine.py --max-retests 3 >> logs\retest_quarantine.log 2>&1
echo ===== retest_quarantine done %DATE% %TIME% =====>> logs\retest_quarantine.log
endlocal
