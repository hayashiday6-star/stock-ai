@echo off
setlocal
cd /d "%~dp0.."
rem This .bat pauses at the end; stop the script pausing too.
set STOCK_AI_NO_PAUSE=1

rem ASCII only - cmd.exe reads a .bat in the console codepage, not UTF-8.
rem The Japanese belongs in the .ps1, which carries a UTF-8 BOM.

echo ==================================================
echo   stock-ai : are there half-day sessions?
echo ==================================================
echo.
echo The code says: decide trading days on HolDiv=1 alone
echo and the half-day sessions drop out as holidays. A few
echo days a year, so the count never gives it away.
echo.
echo That guard is right. But if the division is not in the
echo data at all, the guard is not doing anything. Written
echo down and actually present are two different things.
echo.
echo   per-division counts   1 / 2 / 0 / 3 kept apart
echo   the half-day dates    grouped by year
echo   what the guard buys   days lost to a 1-only filter
echo   unlisted divisions    if any, the set has grown
echo.
echo It also checks against the days a roster exists for.
echo The calendar only claims a session was held; whether
echo the data for that day is actually there is something
echo a different file knows.
echo.
echo Hits no API. Finishes quickly.
echo.

powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0..\scripts\calendar-census.ps1" %*
set CODE=%ERRORLEVEL%

echo.
if not "%CODE%"=="0" (
  echo Did not finish. Paste the output above.
) else (
  echo Done. Paste both tables and the last few lines.
)
echo.
pause
exit /b %CODE%
