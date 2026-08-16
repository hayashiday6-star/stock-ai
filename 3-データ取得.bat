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
echo The version this runs is printed below. If it says the copy is
echo behind origin, run "git pull" first - a fix that is pushed but not
echo pulled looks exactly like a fix that does not work.
echo.
echo To load specific codes instead of a whole segment:
echo   powershell -ExecutionPolicy Bypass -File scripts\3-load-data.ps1 -Symbols 7203,6758,9984
echo.

powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\3-load-data.ps1" -Segment growth -Limit 20
echo.
pause
