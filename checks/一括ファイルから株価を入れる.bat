@echo off
setlocal
cd /d "%~dp0.."
rem This .bat pauses at the end; stop the script pausing too.
set STOCK_AI_NO_PAUSE=1

rem ASCII only - cmd.exe reads a .bat in the console codepage, not UTF-8.
rem The Japanese belongs in the .ps1, which carries a UTF-8 BOM.

echo ==================================================
echo   stock-ai : load prices from the bulk files
echo ==================================================
echo.
echo The per-symbol path spends one request per name. It has
echo stopped twice on rate limits - once at 84 symbols, once at
echo 3,700. Twenty years across 4,400 names will not fit in a
echo week that way.
echo.
echo The bulk files already hold every symbol, day by day, and
echo they are on disk. Nothing is fetched here, so this works
echo after the plan is cancelled.
echo.
echo Delisted names are in there too - that is the survivorship
echo material, arriving for free.
echo.
echo Try -Limit 1 first.
echo.

powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0..\scripts\bulk-prices.ps1" %*
set CODE=%ERRORLEVEL%

echo.
if not "%CODE%"=="0" (
  echo Did not finish. Paste the output above.
) else (
  echo Done. Paste the summary above.
)
echo.
pause
exit /b %CODE%
