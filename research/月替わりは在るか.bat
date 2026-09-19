@echo off
setlocal
cd /d "%~dp0.."
rem This .bat pauses at the end; stop the script pausing too.
set STOCK_AI_NO_PAUSE=1

rem ASCII only - cmd.exe reads a .bat in the console codepage, not UTF-8.
rem The Japanese belongs in the .ps1, which carries a UTF-8 BOM.

echo ==================================================
echo   stock-ai : #13 turn of the month (IS only)
echo ==================================================
echo.
echo Are the last day of the month and the first three
echo of the next better than the other days? The window
echo is fixed by the preregistration at four trading
echo days and this command has no knob for it.
echo.
echo THIS IS NOT THE VERDICT. It measures the in-sample
echo window so the gate table can be filled. Out of
echo sample - 2018 onward - is not touched.
echo.
echo Nothing is bought or sold. One observation is:
echo.
echo   the sum of the daily returns inside the window
echo   minus
echo   the same number of days of the outside average
echo.
echo So no costs are deducted. Instead the line in the
echo preregistration was set from what a tradeable
echo version would cost later - 2.4%% a year.
echo.
echo One observation per month turn, not per day.
echo Counting days would inflate the sample the way
echo hypothesis 5 did when 1,827 events turned out to
echo sit on 831 days.
echo.
echo Both the index and the equal weighted universe are
echo printed. The line applies to the index.
echo.
echo The detectable difference in the table is
echo PROVISIONAL: this pipe has no measured line yet, so
echo the monthly one stands in. Run the calendar control
echo first.
echo.
echo No API calls. Reading the equal weighted universe
echo means reading every symbol once, so allow a while.
echo.

powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0..\scripts\turn-of-month-power.ps1" %*
set CODE=%ERRORLEVEL%

echo.
if not "%CODE%"=="0" (
  echo Did not finish. Paste the output above.
) else (
  echo Done. Paste both tables and the lines below them.
)
echo.
pause
exit /b %CODE%
