@echo off
setlocal
rem Registering a scheduled task needs administrator rights, so this asks for
rem them up front rather than failing halfway with a permissions error.
net session >nul 2>&1
if %errorlevel% neq 0 (
  echo Administrator rights are needed to register a scheduled task.
  echo A confirmation prompt will appear - choose Yes.
  powershell -NoProfile -Command "Start-Process -FilePath '%~f0' -Verb RunAs"
  exit /b
)

cd /d "%~dp0"

echo ============================================
echo   stock-ai : run the daily job automatically
echo ============================================
echo.
echo Windows will run the job every day, and will catch
echo up after the machine has been asleep. Leaving a
echo PowerShell window open cannot do that.
echo.
echo Answer the questions that follow. Each one is checked
echo before anything is registered.
echo.

rem Every question is asked by the PowerShell script, not here. cmd puts a
rem variable into the command line unquoted, so a pasted answer containing a
rem space silently splits into several arguments and lands on the wrong
rem parameter - which is exactly how -At once received the word "Time".
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\4-daily.ps1" -Interactive
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
