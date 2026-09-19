@echo off
setlocal
cd /d "%~dp0.."
rem This .bat pauses at the end; stop the script pausing too.
set STOCK_AI_NO_PAUSE=1

rem ASCII only - cmd.exe reads a .bat in the console codepage, not UTF-8.
rem The Japanese belongs in the .ps1, which carries a UTF-8 BOM.

echo ==================================================
echo   stock-ai : #14 the January effect (IS only)
echo ==================================================
echo.
echo Not "does January go up" but "is the gap between
echo small and large companies wider in January". The
echo universe is split into five by market value and
echo the spread is smallest minus largest.
echo.
echo   one observation =
echo     the spread in that year's January
echo     minus
echo     the average spread of that year's other months
echo.
echo THIS IS NOT THE VERDICT. It measures the in-sample
echo window so the gate table can be filled. Out of
echo sample - 2018 onward - is not touched.
echo.
echo There are only nine observations. One a year, from
echo 2009 to 2017. With nine points the t statistic is
echo no longer close to normal: the correct line is
echo 4.33, while scaling 3.02 by the spread of t gives
echo 3.49 - about a quarter too lenient. The table
echo prints the detectable difference at both 3.02,
echo which is the floor the line can never go below,
echo and at 4.33.
echo.
echo No costs are deducted. Instead the line in the
echo preregistration was set from the cheapest tradeable
echo version - buy at the end of December, sell at the
echo end of January, one round trip a year, 0.4%%.
echo.
echo The liquidity filter is the same one used by
echo hypotheses 9 and 12: a hundred million yen a day.
echo That cuts into the small end, so the number of
echo names it removed is printed. Nothing is claimed
echo about companies below that line.
echo.
echo No API calls. Every symbol is read once, so allow
echo a while.
echo.

powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0..\scripts\january-power.ps1" %*
set CODE=%ERRORLEVEL%

echo.
if not "%CODE%"=="0" (
  echo Did not finish. Paste the output above.
) else (
  echo Done. Paste both tables and the lines below them.
)
echo.
pause
exit /b %CODE%
