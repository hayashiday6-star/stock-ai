@echo off
setlocal
cd /d "%~dp0"
rem This .bat pauses at the end; stop the script pausing too.
set STOCK_AI_NO_PAUSE=1

echo ============================================
echo   stock-ai : store an API key in .env
echo ============================================
echo.
echo The key is hidden while you paste it, so it never
echo reaches the console history. .env is git-ignored.
echo.
echo Which key? (press Enter for EDINET_API_KEY)
echo   1 = EDINET_API_KEY     JP statutory disclosures
echo   2 = JQUANTS_API_KEY    JP prices and financials
echo   3 = ANTHROPIC_API_KEY  AI summaries and search
echo.
set "CHOICE="
set /p "CHOICE=Number: "

set "KEYNAME=EDINET_API_KEY"
if "%CHOICE%"=="2" set "KEYNAME=JQUANTS_API_KEY"
if "%CHOICE%"=="3" set "KEYNAME=ANTHROPIC_API_KEY"

echo.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\set-key.ps1" -Name %KEYNAME%
if errorlevel 1 (
  echo.
  echo [ERROR] The key was not saved. See the messages above.
)
echo.
pause
