@echo off
setlocal
cd /d "%~dp0.."
rem This .bat pauses at the end; stop the script pausing too.
set STOCK_AI_NO_PAUSE=1

rem ASCII only - cmd.exe reads a .bat in the console codepage, not UTF-8.
rem The Japanese belongs in the .ps1, which carries a UTF-8 BOM.

echo ==================================================
echo   stock-ai : what the bulk download offers
echo ==================================================
echo.
echo The statements fetch spends one request per symbol. At 3,700
echo symbols it stopped on 429 - 315 of them were never fetched.
echo.
echo J-Quants serves /fins/summary and /equities/bars/daily as bulk
echo files instead: one gzipped CSV a month, every symbol in it.
echo Company forecasts and disclosure times ride on /fins/summary,
echo which is the deadline work.
echo.
echo This downloads nothing. It lists what is there, so the ingest
echo is built against real file counts rather than a guess.
echo.

powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0..\scripts\jquants-bulk-list.ps1" %*
set CODE=%ERRORLEVEL%

echo.
if not "%CODE%"=="0" (
  echo Did not finish. Paste the output above.
) else (
  echo Done. Paste the output above.
)
echo.
pause
exit /b %CODE%
