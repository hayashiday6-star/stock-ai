@echo off
setlocal
cd /d "%~dp0.."
rem This .bat pauses at the end; stop the script pausing too.
set STOCK_AI_NO_PAUSE=1

rem ASCII only - cmd.exe reads a .bat in the console codepage, not UTF-8.
rem The Japanese belongs in the .ps1, which carries a UTF-8 BOM.

echo ============================================================
echo   stock-ai : 52-week highs - how many, and how clustered
echo ============================================================
echo.
echo Candidates 1 and 2 closed on 2026-09-05. This is number 3,
echo in the order that was fixed beforehand.
echo.
echo The binding number here is NOT the count. New highs pile up
echo in rising markets, and names sharing a day share the market's
echo move, so independent observations are far fewer than events.
echo Reading the raw count as sample size understates the
echo difference we could detect.
echo.
echo Three things to read off the output:
echo   1. day count - this, not the event count, is the sample
echo   2. share on the busiest 10%% of days - 10%% means evenly
echo      spread; over 50%% means half the events sit on a tenth
echo   3. execution - the open gap. Limit-up died here (+5.37%%)
echo.
echo No returns are computed, so this spends no verdict.
echo Reads every daily bar. Takes several minutes.
echo.

powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0..\scripts\high-census.ps1" %*
set CODE=%ERRORLEVEL%

echo.
if not "%CODE%"=="0" (
  echo Did not finish. Paste the output above.
) else (
  echo Done. Paste the tables only.
)
echo.
pause
exit /b %CODE%
