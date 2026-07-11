@echo off
chcp 65001 >nul
cd /d "%~dp0"

REM ============================================================
REM  号池入口
REM    run_pool.bat                 -> 走 maintain（健康+条件补号）
REM    run_pool.bat refill [n] [c]  -> 强制补号
REM    run_pool.bat health          -> 只健康检查/同步 CLI
REM    run_pool.bat --status        -> 概况
REM    run_pool.bat --retry-push    -> 重推 CPA
REM ============================================================

setlocal EnableExtensions

if /I "%~1"=="--retry-push" (
  python grok_register_ttk.py --retry-push
  exit /b %ERRORLEVEL%
)
if /I "%~1"=="--status" (
  python pool_status.py
  exit /b %ERRORLEVEL%
)
if /I "%~1"=="status" (
  python pool_status.py
  exit /b %ERRORLEVEL%
)
if /I "%~1"=="health" (
  python pool_health.py
  exit /b %ERRORLEVEL%
)
if /I "%~1"=="maintain" (
  python pool_maintain.py
  exit /b %ERRORLEVEL%
)
if /I "%~1"=="refill" (
  set N=%~2
  if "%N%"=="" set N=6
  set C=%~3
  if "%C%"=="" set C=1
  echo [*] force refill n=%N% c=%C%
  python pool_maintain.py --force-refill %N%
  exit /b %ERRORLEVEL%
)

REM 默认：完整维持
python pool_maintain.py %*
exit /b %ERRORLEVEL%
