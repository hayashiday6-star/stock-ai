@echo off
setlocal
cd /d "%~dp0"
rem This .bat pauses at the end; stop the script pausing too.
set STOCK_AI_NO_PAUSE=1

echo ============================================
echo   stock-ai : daily accumulation screen
echo ============================================
echo.
echo Scans the US market for the accumulation shape
echo and sends the result to a channel.
echo.
echo Discord needs DISCORD_WEBHOOK_URL in .env.
echo Set it with:  .\scripts\set-key.ps1 DISCORD_WEBHOOK_URL
echo.
echo   1 = run it now, print to the console only
echo   2 = run it now and send to Discord
echo   3 = register it to run every day at 07:00
echo   4 = register it, and send even on quiet days
echo.
set "CHOICE="
set /p "CHOICE=Number: "

set "ARGS=-Channel console"
if "%CHOICE%"=="2" set "ARGS=-Channel discord"
if "%CHOICE%"=="3" set "ARGS=-Register -At 07:00 -Channel discord"
if "%CHOICE%"=="4" set "ARGS=-Register -At 07:00 -Channel discord -Heartbeat"

echo.
echo A whole-market scan takes several minutes.
echo.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\7-accumulation-daily.ps1" %ARGS%
echo.
pause
