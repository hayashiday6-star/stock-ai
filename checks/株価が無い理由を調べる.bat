@echo off
setlocal
cd /d "%~dp0.."
rem This .bat pauses at the end; stop the script pausing too.
set STOCK_AI_NO_PAUSE=1

rem ASCII only - cmd.exe reads a .bat in the console codepage, not UTF-8.
rem The Japanese belongs in the .ps1, which carries a UTF-8 BOM.

echo ==================================================
echo   stock-ai : why do these names have no prices?
echo ==================================================
echo.
echo Sixteen symbols sit in the roster with no price bar at all.
echo None of them turned out to be a difference between the two
echo roster sources - the bulk master lists every one. Whatever
echo explains it is inside the originals.
echo.
echo "No prices" has at least three causes, and a count cannot
echo tell them apart:
echo.
echo   no rows in the bars   not carried in the bulk price file
echo   rows but no close     listed, but nothing ever traded
echo   a close is there      our own ingest is dropping it - a bug
echo.
echo The first two will never fill in, however many times you
echo run the fetch. The third is worth fixing.
echo.
echo Reads the originals. Fetches nothing. Takes a few minutes.
echo.

powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0..\scripts\symbol-probe.ps1" %*
set CODE=%ERRORLEVEL%

echo.
if not "%CODE%"=="0" (
  echo Did not finish. Paste the output above.
) else (
  echo Done. Paste the table and the last line.
)
echo.
pause
exit /b %CODE%
