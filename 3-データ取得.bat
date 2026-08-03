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
echo If the universe step is refused (HTTP 403), that endpoint is not in
echo your J-Quants plan. Prices and statements are separate endpoints and
echo may still work - name the codes you want instead:
echo   powershell -ExecutionPolicy Bypass -File scripts\3-load-data.ps1 -Symbols 7203,6758,9984
echo.

powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\3-load-data.ps1" -Segment growth -Limit 20
echo.
pause
