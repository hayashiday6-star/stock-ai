@echo off
setlocal
cd /d "%~dp0.."
rem This .bat pauses at the end; stop the script pausing too.
set STOCK_AI_NO_PAUSE=1

rem ASCII only - cmd.exe reads a .bat in the console codepage, not UTF-8.
rem The Japanese belongs in the .ps1, which carries a UTF-8 BOM.

echo ==================================================
echo   stock-ai : materials for candidates 6 and 7
echo ==================================================
echo.
echo Both were written up as impossible to measure.
echo Candidate 6 said we hold no sentiment gauge at
echo all; candidate 7 said the design was undecided.
echo Neither is true: the option settlement file
echo carries an implied volatility, and the investor
echo breakdown is already one row per week.
echo.
echo The implied volatility column is empty in the
echo older originals, so this counts it year by year.
echo Where a column starts is not something a total
echo can show - absence never prints itself.
echo.
echo How each series is folded was decided before
echo any wall was measured, and is written in the
echo source. Trying several and keeping the best is
echo the mistake hypothesis 10 died of.
echo.
echo NOTHING IS FETCHED - this only reads what is
echo already saved.
echo.

powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0..\scripts\material-coverage.ps1" %*
set CODE=%ERRORLEVEL%

echo.
if not "%CODE%"=="0" (
  echo No materials. Paste the output above.
) else (
  echo Done. Paste the tables and the warnings.
)
echo.
pause
exit /b %CODE%
