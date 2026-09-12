@echo off
setlocal
cd /d "%~dp0.."
rem This .bat pauses at the end; stop the script pausing too.
set STOCK_AI_NO_PAUSE=1

rem ASCII only - cmd.exe reads a .bat in the console codepage, not UTF-8.
rem The Japanese belongs in the .ps1, which carries a UTF-8 BOM.

echo ==================================================
echo   stock-ai : do the roster and the prices agree?
echo ==================================================
echo.
echo Two paths come out of the same originals.
echo.
echo   roster   /equities/master      full listing filter
echo   prices   /equities/bars/daily  code conversion only
echo.
echo They do NOT pass the same filter, so a difference is
echo normal and the count on its own says nothing. What says
echo something is whether every difference has a reason.
echo.
echo   in the roster, no price row      warning
echo   no close, no volume              a day with no trading
echo   no close but volume              warning
echo   a close but not in the roster    ETF / REIT / TOKYO PRO
echo.
echo Last time 109 roster differences were nearly waved off as
echo "probably a different market" - and the lookup was reading
echo today's market, not the one on the day. So each one gets a
echo reason, and the count of ones without a reason must be 0.
echo.
echo Hits no API. Reads the originals once, so it takes a few
echo minutes.
echo.

powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0..\scripts\roster-prices.ps1" %*
set CODE=%ERRORLEVEL%

echo.
if not "%CODE%"=="0" (
  echo Did not finish. Paste the output above.
) else (
  echo Done. Paste the tables and the last line.
)
echo.
pause
exit /b %CODE%
