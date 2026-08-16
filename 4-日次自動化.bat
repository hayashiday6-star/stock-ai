@echo off
setlocal
rem Registering a scheduled task needs administrator rights, so this asks for
rem them up front rather than failing halfway through with a permissions error.
net session >nul 2>&1
if %errorlevel% neq 0 (
  echo Administrator rights are needed to register a scheduled task.
  echo A confirmation prompt will appear - choose Yes.
  powershell -NoProfile -Command "Start-Process -FilePath '%~f0' -Verb RunAs"
  exit /b
)

cd /d "%~dp0"
set STOCK_AI_NO_PAUSE=1

echo ============================================
echo   stock-ai : run the daily job automatically
echo ============================================
echo.
echo Windows will run the job every day, and will catch
echo up after the machine has been asleep. Leaving a
echo PowerShell window open cannot do that.
echo.

set "AT=18:00"
set /p "AT=Time to run, HH:MM (Enter for 18:00): "

echo.
echo Symbols whose prices to refresh, comma separated.
echo Four-digit codes go to J-Quants, the rest to yfinance,
echo so you can mix them: 7203,6758,AAPL
echo Leave empty to refresh nothing and only check the watchlist.
set "SYMBOLS="
set /p "SYMBOLS=Symbols: "

echo.
echo Disclosure feed:
echo   1 = all     EDINET + news (default)
echo   2 = edinet  JP statutory filings only
echo   3 = news    news wire only
set "FEEDCHOICE="
set /p "FEEDCHOICE=Number: "
set "FEED=all"
if "%FEEDCHOICE%"=="2" set "FEED=edinet"
if "%FEEDCHOICE%"=="3" set "FEED=news"

echo.
echo Notification channel (console, discord, telegram, line).
echo Leave empty to print alerts instead of sending them.
set "CHANNEL="
set /p "CHANNEL=Channel: "

set "ARGS=-Register -At %AT% -Feed %FEED%"
if not "%SYMBOLS%"=="" set "ARGS=%ARGS% -Symbols %SYMBOLS%"
if not "%CHANNEL%"=="" set "ARGS=%ARGS% -Channel %CHANNEL%"

echo.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\4-daily.ps1" %ARGS%
if errorlevel 1 (
  echo.
  echo [ERROR] Registration did not finish. See the messages above.
)
echo.
echo Logs are written to logs\daily\ , one file per day.
echo Check the first few runs there - a job that fails still
echo lets the others run, so a quiet failure is possible.
echo.
pause
