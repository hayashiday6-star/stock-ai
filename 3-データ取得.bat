@echo off
setlocal
cd /d "%~dp0"
rem This .bat pauses at the end; stop the script pausing too.
set STOCK_AI_NO_PAUSE=1

echo ============================================
echo   stock-ai : load data (step 3 of 3)
echo ============================================
echo.
echo Starting with a 20-symbol trial run.
echo Once that works, load the full market with:
echo   powershell -ExecutionPolicy Bypass -File scripts\3-load-data.ps1 -Segment prime
echo.

powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\3-load-data.ps1" -Segment growth -Limit 20
echo.
pause
