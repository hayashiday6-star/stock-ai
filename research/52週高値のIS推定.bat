@echo off
setlocal
cd /d "%~dp0.."
rem This .bat pauses at the end; stop the script pausing too.
set STOCK_AI_NO_PAUSE=1

rem ASCII only - cmd.exe reads a .bat in the console codepage, not UTF-8.
rem The Japanese belongs in the .ps1, which carries a UTF-8 BOM.

echo ============================================================
echo   stock-ai : 52-week highs - estimate on IS, apply the line
echo ============================================================
echo.
echo This is tier 2 of the gate. No literature reports this exact
echo design, so the floor gets estimated from our own in-sample
echo period instead.
echo.
echo THE LINE WAS FIXED BEFORE ANY ESTIMATE WAS SEEN:
echo.
echo   if the IS estimate over a 20-session window comes in
echo   below 1.26%%, the hypothesis is NOT sealed. It closes,
echo   and it is not re-measured.
echo.
echo 1.26%% = 0.86%% detectable over the 1,197 OOS days, plus
echo 0.40%% cost. That is above the 1.0%% the candidate list called
echo realistic - the price of screening on our own data.
echo.
echo OOS is never touched. Passing a cutoff past 2021-09-30 stops
echo the run with exit code 2, because mixing OOS in is not
echo visible in the numbers afterwards.
echo.
echo Reads every daily bar. Takes several minutes.
echo.

powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0..\scripts\high-screen.ps1" %*
set CODE=%ERRORLEVEL%

echo.
if not "%CODE%"=="0" (
  echo Did not finish. Paste the output above.
) else (
  echo Done. Paste the table and the verdict.
)
echo.
pause
exit /b %CODE%
