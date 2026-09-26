@echo off
setlocal
cd /d "%~dp0.."
rem This .bat pauses at the end; stop the script pausing too.
set STOCK_AI_NO_PAUSE=1

rem ASCII only - cmd.exe reads a .bat in the console codepage, not UTF-8.
rem The Japanese belongs in the .ps1, which carries a UTF-8 BOM.

echo ==================================================
echo   stock-ai : dividend yields that came out absurd
echo ==================================================
echo.
echo Some months produce a dividend yield far higher
echo than any Japanese listed company pays. Candidate
echo 14 only ranks stocks into quintiles, so the value
echo itself never enters the spread - what it does is
echo put a stock in the wrong quintile. The share of
echo the month's quintile decides how much that bites.
echo.
echo Printed: the split ratio from disclosure to month,
echo how many stock-months crossed a split or a reverse
echo split (what each way of handling them would cost),
echo and the rows that crossed none, sorted by two
echo yardsticks: a split just before the disclosure,
echo and the price at the disclosure month.
echo.
echo No cause is assumed. The share is what decides.
echo.
echo NOTHING IS FETCHED - this only reads what is
echo already saved.
echo.

powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0..\scripts\yield-audit.ps1" %*
set CODE=%ERRORLEVEL%

echo.
if not "%CODE%"=="0" (
  echo Could not look. Paste the output above.
) else (
  echo Done. Paste the table and the warnings.
)
echo.
pause
exit /b %CODE%
