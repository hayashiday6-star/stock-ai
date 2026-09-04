@echo off
setlocal
cd /d "%~dp0.."
rem This .bat pauses at the end; stop the script pausing too.
set STOCK_AI_NO_PAUSE=1

rem ASCII only - cmd.exe reads a .bat in the console codepage, not UTF-8.
rem The Japanese belongs in the .ps1, which carries a UTF-8 BOM.

echo ============================================
echo   stock-ai : grab forecasts before the plan ends
echo ============================================
echo.
echo Company full-year forecasts and disclosure times exist
echo nowhere else. EDINET annual reports carry neither, so after
echo 2026-09-22 these stop growing forever.
echo.
echo Worth taking even with no use for them yet. Discarding is a
echo decision you can make later. Taking is a decision with a
echo deadline.
echo.
echo Only symbols with no statements at all are requested. The
echo delisted names just added are among them.
echo.
echo On a rate limit the run waits or stops - it no longer counts
echo the remainder as failures. If it stops, wait and re-run.
echo.
echo Can take over 30 minutes. Interrupting is safe.
echo.

powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0..\scripts\jquants-statements-harvest.ps1" %*
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
