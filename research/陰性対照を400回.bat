@echo off
setlocal
cd /d "%~dp0.."
rem This .bat pauses at the end; stop the script pausing too.
set STOCK_AI_NO_PAUSE=1

rem ASCII only - cmd.exe reads a .bat in the console codepage, not UTF-8.
rem The Japanese belongs in the .ps1, which carries a UTF-8 BOM.

echo ==================================================
echo   stock-ai : is the t statistic calibrated?
echo ==================================================
echo.
echo The negative control again, but 400 times. One draw
echo says nothing: a pass has a 0.125 percent chance, so
echo 400 runs expect half of one.
echo.
echo What 400 runs CAN show is the shape of t under the
echo null. It should have a spread of 1.00.
echo.
echo   spread 1.00   the verdict line means what it says
echo   spread 1.15   t >= 3.02 is really about 0.6 percent,
echo                 not the 0.25 percent it was designed
echo                 to be - and every detectable-difference
echo                 figure so far sits on top of that
echo.
echo Hypotheses 5, 8, 9 and 11 were all closed for being
echo undetectable. If the line itself is off, so is the
echo meaning of those closures.
echo.
echo Same panels as the single run, redrawing only the
echo signal, so this takes about as long.
echo.
echo No API calls.
echo.

powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0..\scripts\rehearsal.ps1" -Repeat 400
set CODE=%ERRORLEVEL%

echo.
if not "%CODE%"=="0" (
  echo Did not finish. Paste the output above.
) else (
  echo Done. Paste the shape table and the lines below it.
)
echo.
pause
exit /b %CODE%
