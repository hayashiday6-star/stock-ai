@echo off
setlocal
cd /d "%~dp0.."
rem This .bat pauses at the end; stop the script pausing too.
set STOCK_AI_NO_PAUSE=1

rem ASCII only - cmd.exe reads a .bat in the console codepage, not UTF-8.
rem The Japanese belongs in the .ps1, which carries a UTF-8 BOM.

echo ============================================
echo   stock-ai : low volatility, counting first
echo ============================================
echo.
echo No returns are computed. Counts and distributions only.
echo.
echo Two registrations were sealed before anyone knew whether
echo the test had the power to decide. Reversal reversed that
echo order and it worked. Same order here.
echo.
echo This output settles three things before sealing:
echo   1. which measurement window - 60, 120 or 250 sessions
echo   2. whether the quantiles tilt by size
echo   3. whether the quantiles tilt by sector
echo.
echo Monthly rebalancing, so observations are symbol-months.
echo That is what removes the 2.95x overlap inflation that
echo reversal carried.
echo.
echo Takes a few minutes - it scans every symbol.
echo.

powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0..\scripts\lowvol-census.ps1" %*
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
