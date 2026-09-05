@echo off
setlocal
cd /d "%~dp0.."
rem This .bat pauses at the end; stop the script pausing too.
set STOCK_AI_NO_PAUSE=1

rem ASCII only - cmd.exe reads a .bat in the console codepage, not UTF-8.
rem The Japanese belongs in the .ps1, which carries a UTF-8 BOM.

echo ============================================================
echo   stock-ai : how spread out is the 52-week-high basket
echo ============================================================
echo.
echo The count census passed: 248,217 events over 5,843 days,
echo 39%% of them on the busiest tenth of days, and an open gap
echo of +0.03%% at the median. Next is the input to the gate.
echo.
echo NO MEAN IS PRINTED. Printing it would be seeing the answer
echo before sealing. Only the spread and the overlap inflation
echo come out, which is why this spends no verdict.
echo.
echo Three holding windows (1, 5, 20 sessions) are shown. Spread
echo is not effect, so comparing windows here is not multiple
echo testing - only one window gets sealed, and this table is how
echo that choice gets made.
echo.
echo Read the "difference needed for t>=2.0" column, add the cost
echo beside it, and ask whether event studies report effects that
echo large.
echo.
echo Reads every daily bar three times. Slower than the census.
echo.

powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0..\scripts\high-power.ps1" %*
set CODE=%ERRORLEVEL%

echo.
if not "%CODE%"=="0" (
  echo Did not finish. Paste the output above.
) else (
  echo Done. Paste the table only.
)
echo.
pause
exit /b %CODE%
