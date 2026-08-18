@echo off
setlocal
cd /d "%~dp0"
rem This .bat pauses at the end; stop the script pausing too.
set STOCK_AI_NO_PAUSE=1

echo ============================================
echo   stock-ai : verify AI and notifications
echo ============================================
echo.
echo Checks the two features that have never run: AI and notifications.
echo.
echo The free checks run first ^(key, notifier, cost estimate^).
echo The AI checks are BILLED and do not start until you type yes.
echo Ceiling for all of them together is under 10 cents.
echo.
echo Output is saved to verify-ai-output.txt - paste that file back.
echo.

powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\6-verify-ai.ps1"
echo.
pause
