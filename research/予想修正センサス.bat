@echo off
setlocal
cd /d "%~dp0.."
rem This .bat pauses at the end; stop the script pausing too.
set STOCK_AI_NO_PAUSE=1

rem ASCII only - cmd.exe reads a .bat in the console codepage, not UTF-8.
rem The Japanese belongs in the .ps1, which carries a UTF-8 BOM.

echo ============================================
echo   stock-ai : forecast-revision census
echo ============================================
echo.
echo Counts company-forecast revisions by comparing the full-year
echo forecast carried by consecutive earnings statements.
echo.
echo Revisions are not available as their own filings, so this is
echo the only route. The count decides whether hypothesis 2 is viable.
echo.

powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0..\scripts\revision-census.ps1" %*
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
