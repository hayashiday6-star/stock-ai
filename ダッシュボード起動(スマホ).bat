@echo off
setlocal
cd /d "%~dp0"
set STOCK_AI_NO_PAUSE=1

echo ============================================
echo   stock-ai dashboard : phone / tablet access
echo ============================================
echo.
echo This serves the dashboard to other devices on your
echo network. It has NO login - anyone on the same Wi-Fi
echo can open it. Use it on your home network only.
echo.
echo The address to type on your phone is printed below.
echo.

rem Make sure the dashboard extras are present before binding a port.
uv sync --extra data --extra db --extra dashboard
if errorlevel 1 (
  echo.
  echo [ERROR] setup failed. See messages above.
  pause
  exit /b 1
)

powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\dashboard-lan.ps1"
echo.
pause
