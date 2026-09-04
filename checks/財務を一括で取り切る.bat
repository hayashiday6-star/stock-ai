@echo off
setlocal
cd /d "%~dp0.."
rem This .bat pauses at the end; stop the script pausing too.
set STOCK_AI_NO_PAUSE=1

rem ASCII only - cmd.exe reads a .bat in the console codepage, not UTF-8.
rem The Japanese belongs in the .ps1, which carries a UTF-8 BOM.

echo ============================================================
echo   stock-ai : pull the statements as bulk files
echo ============================================================
echo.
echo 83 files, about 9 MB, covering 2021-09 to 2026-09 for every
echo symbol. The per-symbol path would spend 3,700 requests and
echo stopped on 429 after 84 of them.
echo.
echo Company forecasts (FSales/FOP/FNP/FEPS) and disclosure times
echo (DiscTime) ride on these rows. Nothing else serves them once
echo the plan ends on 2026-09-22.
echo.
echo Safe to re-run. Rows are keyed by fiscal period and a stored
echo value is never overwritten with a blank.
echo.

powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0..\scripts\jquants-bulk-fetch.ps1" %*
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
