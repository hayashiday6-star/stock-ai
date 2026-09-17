@echo off
setlocal
cd /d "%~dp0.."
rem This .bat pauses at the end; stop the script pausing too.
set STOCK_AI_NO_PAUSE=1

rem ASCII only - cmd.exe reads a .bat in the console codepage, not UTF-8.
rem The Japanese belongs in the .ps1, which carries a UTF-8 BOM.

echo ==================================================
echo   stock-ai : calibrate the EVENT pipe
echo ==================================================
echo.
echo The monthly control came back with a t spread of 1.12
echo instead of 1.00, so the verdict line was three times
echo looser than designed. That number was measured on the
echo monthly panel.
echo.
echo Hypotheses 8 and 5 do not use that pipe. Their series
echo is one point per event day and the Newey-West lag is
echo the holding period. A different pipe can have a
echo different number.
echo.
echo This draws random days and symbols and pushes them
echo through event_window.event_returns - the very function
echo those two hypotheses call. Building a parallel path
echo would prove nothing about the real one.
echo.
echo   spread near 1.12   the monthly figure carries over
echo   spread far from it the event designs need their own
echo.
echo Not a claim, not counted against the budget, and no
echo judgement is spent.
echo.
echo No API calls. Reads daily bars 400 times over, so this
echo one is slow - allow a while.
echo.

powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0..\scripts\rehearsal-events.ps1" %*
set CODE=%ERRORLEVEL%

echo.
if not "%CODE%"=="0" (
  echo Did not finish. Paste the output above.
) else (
  echo Done. Paste the table and the lines below it.
)
echo.
pause
exit /b %CODE%
