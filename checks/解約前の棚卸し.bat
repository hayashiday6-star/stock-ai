@echo off
setlocal
cd /d "%~dp0.."
rem This .bat pauses at the end; stop the script pausing too.
set STOCK_AI_NO_PAUSE=1

rem ASCII only - cmd.exe reads a .bat in the console codepage, not UTF-8.
rem The Japanese belongs in the .ps1, which carries a UTF-8 BOM.

echo ============================================
echo   stock-ai : what the cancellation takes
echo ============================================
echo.
echo The J-Quants plan ends 2026-09-22. Anything refetchable
echo afterwards is not urgent - Tachibana still serves prices,
echo EDINET still serves annual reports.
echo.
echo Three things cannot be rebuilt from anywhere:
echo   1. dated listing rosters
echo   2. prices for delisted symbols
echo   3. company forecasts and disclosure times
echo.
echo The five-year rolling window bites before the cancellation
echo does. Nothing here can wait for the deadline.
echo.
echo Counts only. Fetches nothing.
echo.

powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0..\scripts\jquants-inventory.ps1" %*
set CODE=%ERRORLEVEL%

echo.
if not "%CODE%"=="0" (
  echo Did not finish. Paste the output above.
) else (
  echo Done. Paste the output above.
)
echo.
pause
exit /b %CODE%
