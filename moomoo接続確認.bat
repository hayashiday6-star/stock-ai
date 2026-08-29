@echo off
setlocal
cd /d "%~dp0"
rem This .bat pauses at the end; stop the script pausing too.
set STOCK_AI_NO_PAUSE=1

echo ============================================
echo   stock-ai : moomoo OpenD connection check
echo ============================================
echo.
echo moomoo has no API key. Logging in means running
echo OpenD on this PC and signing into it with your
echo moomoo securities account.
echo.
echo Every failure in that chain reaches Python as the
echo same symptom - a command that never comes back - so
echo this checks the links one at a time and stops at the
echo first one that is broken.
echo.
echo No order is ever placed. Balances are not shown and
echo account numbers are masked.
echo.
echo Which account? (press Enter for the paper account)
echo   1 = SIMULATE  paper account, nothing to unlock
echo   2 = REAL      live account, read only
echo   3 = REAL      live account, and test the trading PIN too
echo.
set "CHOICE="
set /p "CHOICE=Number: "

set "ARGS="
if "%CHOICE%"=="2" set "ARGS=-Real"
if "%CHOICE%"=="3" set "ARGS=-Real -Unlock"

echo.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\moomoo-check.ps1" %ARGS%
if errorlevel 1 (
  echo.
  echo [ERROR] The check did not finish. See the messages above.
)
echo.
pause
