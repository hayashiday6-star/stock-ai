@echo off
setlocal
cd /d "%~dp0.."
rem This .bat pauses at the end; stop the script pausing too.
set STOCK_AI_NO_PAUSE=1

rem ASCII only - cmd.exe reads a .bat in the console codepage, not UTF-8.
rem The Japanese belongs in the .ps1, which carries a UTF-8 BOM.

echo ==================================================
echo   stock-ai : why is there no roster on that day?
echo ==================================================
echo.
echo Two things look identical from a distance and need
echo fixing in different places:
echo.
echo   the source has no rows for that day
echo     -^> J-Quants does not publish one. NOT ours to fix
echo   rows exist, the filter rejected every one
echo     -^> that one IS ours
echo.
echo Two trading days came back with no roster: 2008-12-30
echo and 2009-01-05. Both are half-day sessions, and they
echo are the only two missing out of 4,493. That is not a
echo coincidence, but a count alone cannot say which of the
echo two cases it is. Naming the rows settles it.
echo.
echo Defaults to those two days. Pass other dates to look
echo at them instead.
echo.
echo Hits no API. Reads the roster originals, a few minutes.
echo.

powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0..\scripts\roster-day.ps1" %*
set CODE=%ERRORLEVEL%

echo.
if not "%CODE%"=="0" (
  echo Did not finish. Paste the output above.
) else (
  echo Done. Paste the tables and the verdict lines.
)
echo.
pause
exit /b %CODE%
