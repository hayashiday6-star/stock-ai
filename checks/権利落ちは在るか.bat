@echo off
setlocal
cd /d "%~dp0.."
rem This .bat pauses at the end; stop the script pausing too.
set STOCK_AI_NO_PAUSE=1

rem ASCII only - cmd.exe reads a .bat in the console codepage, not UTF-8.
rem The Japanese belongs in the .ps1, which carries a UTF-8 BOM.

echo ==================================================
echo   stock-ai : are the ex-dividend dates there?
echo ==================================================
echo.
echo Hypothesis 9 says gaps get filled. A gap is the
echo open falling three per cent below the previous
echo close - and that is exactly what a stock going
echo ex-dividend looks like. Unless those days can be
echo taken out, the definition of the event picks up
echo dividends instead.
echo.
echo So this counts what the saved originals can
echo supply: how many rows, how many of them actually
echo carry a date, how many distinct ex-dates, and
echo whether they cover both halves of the sample.
echo.
echo Rows read and dates present are counted
echo separately. A file that opens is not the same as
echo a column that is filled.
echo.
echo The dividend endpoint was Premium only, so what
echo is on disk is all there will ever be. NOTHING IS
echo FETCHED - this only reads what is already saved.
echo.

powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0..\scripts\ex-date-coverage.ps1" %*
set CODE=%ERRORLEVEL%

echo.
if not "%CODE%"=="0" (
  echo Not enough to exclude them. Paste the output above.
) else (
  echo Done. Paste the table and the line above it.
)
echo.
pause
exit /b %CODE%
