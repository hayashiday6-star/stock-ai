@echo off
setlocal
cd /d "%~dp0"
rem This .bat pauses at the end; stop the script pausing too.
set STOCK_AI_NO_PAUSE=1

rem ASCII only - cmd.exe reads a .bat in the console codepage, not UTF-8.
rem The Japanese belongs in the .ps1, which carries a UTF-8 BOM.

echo ============================================
echo   stock-ai : PEAD backtest (in-sample)
echo ============================================
echo.
echo Runs the sealed pre-registration on the IN-SAMPLE period only.
echo The out-of-sample period stays untouched until the checks pass.
echo.

powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\pead-run.ps1" -Period is %*
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
