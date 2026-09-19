@echo off
setlocal
cd /d "%~dp0.."
rem This .bat pauses at the end; stop the script pausing too.
set STOCK_AI_NO_PAUSE=1

rem ASCII only - cmd.exe reads a .bat in the console codepage, not UTF-8.
rem The Japanese belongs in the .ps1, which carries a UTF-8 BOM.

echo ==================================================
echo   stock-ai : calibrate the CALENDAR pipe
echo ==================================================
echo.
echo Hypothesis 13 - the turn of the month - is neither
echo the monthly panel nor the event window. It compares
echo days against other days inside a single series, so
echo it is a third pipe, and it has no line yet.
echo.
echo The monthly control measured a t spread of 1.12 and
echo the event control 1.09. Neither of those was
echo measured on this estimator, so neither carries over.
echo.
echo This keeps the real returns and randomises only
echo WHICH four days count as the window. The fake window
echo is placed outside the real one - overlapping it
echo would let the real effect leak into what is supposed
echo to be the null.
echo.
echo   spread near 1.00   the line is the plain 3.02
echo   spread above it    the line has to rise
echo.
echo Writing the result into MEASURED_INFLATION_CALENDAR
echo is a job for the other side. Paste the output.
echo Hypothesis 13 cannot be sealed until that is done.
echo.
echo Not a claim, not counted against the budget, and no
echo judgement is spent.
echo.
echo No API calls. Reads one daily series 400 times over.
echo.

powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0..\scripts\rehearsal-calendar.ps1" %*
set CODE=%ERRORLEVEL%

echo.
if not "%CODE%"=="0" (
  echo Did not finish. Paste the output above.
) else (
  echo Done. Paste the table and the lines below it.
)
echo.
pause
exit /b %CODE%
