@echo off
setlocal
cd /d "%~dp0.."
rem This .bat pauses at the end; stop the script pausing too.
set STOCK_AI_NO_PAUSE=1

rem ASCII only - cmd.exe reads a .bat in the console codepage, not UTF-8.
rem The Japanese belongs in the .ps1, which carries a UTF-8 BOM.

echo ==================================================
echo   stock-ai : rosters for every trading day
echo ==================================================
echo.
echo One bulk file holds the whole month, day by day. The August
echo 2026 file is 88,870 rows: 4,441 symbols across 20 sessions.
echo.
echo The rosters on disk today came from the JSON API on a
echo 30-day grid - 66 of them for five years. The same five
echo years are already here at roughly 1,220, one per session.
echo That is the difference between knowing which month a
echo listing disappeared and knowing which day.
echo.
echo Fetches nothing. Works after the plan is cancelled.
echo.

powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0..\scripts\daily-rosters.ps1" %*
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
