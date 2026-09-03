@echo off
setlocal
cd /d "%~dp0"
rem This .bat pauses at the end; stop the script pausing too.
set STOCK_AI_NO_PAUSE=1

rem ASCII only - cmd.exe reads a .bat in the console codepage, not UTF-8.
rem The Japanese belongs in the .ps1, which carries a UTF-8 BOM.

echo ============================================
echo   stock-ai : short-term reversal census
echo ============================================
echo.
echo Counts what a reversal test would have to work with, before
echo the registration is written. The two earnings-drift studies
echo were sealed first and only then found to be thin - this is
echo that order reversed.
echo.
echo Reversal is not event-driven: every symbol carries a trailing
echo return every session, so the population is symbol-days. The
echo binding number is how many symbols clear the filter on a
echo single day, because a thin day cannot be cut into quintiles.
echo.
echo No returns are computed. Scans every symbol and session, so
echo it takes a few minutes.
echo.

powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\reversal-census.ps1" %*
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
