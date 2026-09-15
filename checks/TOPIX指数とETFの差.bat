@echo off
setlocal
cd /d "%~dp0.."
rem This .bat pauses at the end; stop the script pausing too.
set STOCK_AI_NO_PAUSE=1

rem ASCII only - cmd.exe reads a .bat in the console codepage, not UTF-8.
rem The Japanese belongs in the .ps1, which carries a UTF-8 BOM.

echo ==================================================
echo   stock-ai : the index vs the ETF standing in
echo ==================================================
echo.
echo The benchmark has been 1306, the TOPIX-tracking ETF,
echo for one reason: the index itself was not on hand.
echo Premium opened it - 18 years of it, in 88 KB.
echo.
echo   TOPIX   the index. no trust fee, no trading drift
echo   1306    a fund tracking it. both of those apply
echo.
echo Over eighteen years that is not nothing - but "not
echo nothing" is a guess until it is subtracted. So this
echo subtracts it.
echo.
echo A POSITIVE gap - the ETF ahead of the index - has no
echo explanation: the fee only ever takes from the fund.
echo If that shows up, suspect dividends or splits being
echo handled on one side and not the other.
echo.
echo Swapping the benchmark is not a re-run of a verdict.
echo Doing that to hypothesis 7 would spend a second
echo judgement on it. This is a measuring tool; the ones
echo that can use it are 5 and 8, which have spent none.
echo.
echo Hits no API. Finishes quickly.
echo.

powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0..\scripts\topix-check.ps1" %*
set CODE=%ERRORLEVEL%

echo.
if not "%CODE%"=="0" (
  echo Did not finish. Paste the output above.
) else (
  echo Done. Paste the table and the last few lines.
)
echo.
pause
exit /b %CODE%
