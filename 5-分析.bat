@echo off
setlocal
cd /d "%~dp0"
rem This .bat pauses at the end; stop the script pausing too.
set STOCK_AI_NO_PAUSE=1

echo ============================================
echo   stock-ai : analyse the loaded data
echo ============================================
echo.
echo Run this after the data load. It fills in any missing
echo valuation snapshots, screens, then tests whether the
echo score is worth anything on real data.
echo.

powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\5-analyze.ps1"
if errorlevel 1 (
  echo.
  echo [ERROR] Analysis did not finish. See the messages above.
)
echo.
pause
