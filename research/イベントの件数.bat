@echo off
setlocal
cd /d "%~dp0.."
rem This .bat pauses at the end; stop the script pausing too.
set STOCK_AI_NO_PAUSE=1

rem ASCII only - cmd.exe reads a .bat in the console codepage, not UTF-8.
rem The Japanese belongs in the .ps1, which carries a UTF-8 BOM.

echo ============================================================
echo   stock-ai : how many limit-up days and halt resumptions
echo ============================================================
echo.
echo Counting before registering anything. In hypothesis #1, 97.5%%
echo of qualifying names vanished at the liquidity filter, and we
echo only found that out after building the test.
echo.
echo No returns are computed, so this spends no verdict.
echo.
echo Four things to read off the output:
echo   1. count after the filter - does it reach 1,000
echo   2. the move histogram - limit widths come from a step table,
echo      so real limit days should pile up on a few values
echo   3. turnover - still small-cap even after the filter?
echo   4. execution - how often the next day is limit-locked too
echo      (you cannot buy at all), and the open-gap you pay
echo.
echo Reads every daily bar. Takes several minutes.
echo.

powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0..\scripts\event-census.ps1" %*
set CODE=%ERRORLEVEL%

echo.
if not "%CODE%"=="0" (
  echo Did not finish. Paste the output above.
) else (
  echo Done. Paste the tables only.
)
echo.
pause
exit /b %CODE%
