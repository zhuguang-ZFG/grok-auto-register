@echo off
chcp 65001 >nul
cd /d "%~dp0"

REM Grok 批量注册启动器
REM 用法:
REM   run_batch.bat                 按 config.json 的 register_count/concurrency
REM   run_batch.bat 50 3            注册 50 个，并发 3
REM   run_batch.bat --retry-push    只重推本地 CPA auth

setlocal
set COUNT=
set CONC=
set EXTRA=

if "%~1"=="--retry-push" (
  python grok_register_ttk.py --retry-push
  goto :eof
)

if not "%~1"=="" set COUNT=-n %~1
if not "%~2"=="" set CONC=-c %~2

echo [*] 工作目录: %cd%
echo [*] 启动: python grok_register_ttk.py %COUNT% %CONC% -y
python grok_register_ttk.py %COUNT% %CONC% -y
set ERR=%ERRORLEVEL%
echo [*] 退出码: %ERR%
exit /b %ERR%
