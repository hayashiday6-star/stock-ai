@echo off
setlocal
cd /d "%~dp0"
rem This .bat pauses at the end; stop the script pausing too.
set STOCK_AI_NO_PAUSE=1

echo ============================================
echo   stock-ai : setup (step 1 of 3)
echo ============================================
echo.

powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\1-setup.ps1"
if errorlevel 1 (
  echo.
  echo [ERROR] setup did not finish. See the messages above.
)
echo.
pause
