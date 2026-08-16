@echo off
setlocal
cd /d "%~dp0"
rem This .bat pauses at the end; stop the script pausing too.
set STOCK_AI_NO_PAUSE=1

echo ============================================
echo   stock-ai : why is EDINET refusing the key?
echo ============================================
echo.
echo EDINET says "invalid subscription key" both when the
echo key is wrong and when it is simply somewhere the
echo gateway does not look. One failure cannot tell those
echo apart, so this sends the key four different ways and
echo reports each one.
echo.
echo The key itself is never displayed.
echo.

powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\edinet-check.ps1"
if errorlevel 1 (
  echo.
  echo [ERROR] The check did not finish. See the messages above.
)
echo.
pause
