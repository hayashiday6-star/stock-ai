@echo off
setlocal
cd /d "%~dp0"
rem This .bat pauses at the end; stop the script pausing too.
set STOCK_AI_NO_PAUSE=1

rem ASCII only - cmd.exe reads a .bat in the console codepage, not UTF-8.
rem The Japanese belongs in the .ps1, which carries a UTF-8 BOM.

echo ============================================
echo   stock-ai : SUE census
echo ============================================
echo.
echo Pairs each full-year result with the forecast that was already
echo public before it, and counts how many such events exist.
echo.
echo Quarterly SUE cannot be built here: the forecast covers twelve
echo months while the actual is year-to-date. Full-year only.
echo.
echo No returns are computed - counts and the surprise distribution only.
echo.

powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\sue-census.ps1" %*
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
