@echo off
setlocal
cd /d "%~dp0"
rem This .bat pauses at the end; stop the script pausing too.
set STOCK_AI_NO_PAUSE=1

rem ASCII only - cmd.exe reads a .bat in the console codepage, not UTF-8.
rem The Japanese belongs in the .ps1, which carries a UTF-8 BOM.

echo ============================================
echo   stock-ai : EDINET filing body probe
echo ============================================
echo.
echo Groundwork for dropping the paid J-Quants plan.
echo We read the EDINET index today but never open the
echo filing itself. Financial statements live inside.
echo.
echo This downloads ONE annual report and reports what
echo shape it comes back in, so the parser is written
echo against the real thing rather than a guess.
echo.
echo The API key is never displayed.
echo.

powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\edinet-xbrl-probe.ps1"
if errorlevel 1 (
  echo.
  echo [ERROR] The probe did not finish. See the messages above.
)
echo.
pause
