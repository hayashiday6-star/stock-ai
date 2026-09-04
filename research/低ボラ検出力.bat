@echo off
setlocal
cd /d "%~dp0.."
rem This .bat pauses at the end; stop the script pausing too.
set STOCK_AI_NO_PAUSE=1

rem ASCII only - cmd.exe reads a .bat in the console codepage, not UTF-8.
rem The Japanese belongs in the .ps1, which carries a UTF-8 BOM.

echo ============================================
echo   stock-ai : low volatility, power first
echo ============================================
echo.
echo Variance only. The mean is never computed or printed,
echo and the judged period is refused as an input.
echo.
echo This hypothesis was chosen partly because monthly windows
echo do not overlap. Reversal entered daily and held 20 days,
echo which inflated its standard error 2.95x. Expect near 1.0
echo here - and check it rather than assume it.
echo.
echo The cost threshold is measured, not assumed: 88.5%% of
echo quintile 1 survives month to month, so 11.5%% turns over,
echo giving 0.046%% a month against reversal's 0.40%% per turn.
echo.
echo Takes a few minutes - it scans every symbol.
echo.

powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0..\scripts\lowvol-power.ps1" %*
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
