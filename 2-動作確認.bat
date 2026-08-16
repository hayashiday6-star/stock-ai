@echo off
setlocal
cd /d "%~dp0"
rem This .bat pauses at the end; stop the script pausing too.
set STOCK_AI_NO_PAUSE=1

echo ============================================
echo   stock-ai : verify integrations (step 2 of 3)
echo ============================================
echo.
echo This checks the data sources and writes verify-output.txt.
echo Paste that file when asking for help.
echo.

powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\2-verify.ps1"
echo.
pause
