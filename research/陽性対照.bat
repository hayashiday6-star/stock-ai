@echo off
setlocal
cd /d "%~dp0.."
rem This .bat pauses at the end; stop the script pausing too.
set STOCK_AI_NO_PAUSE=1

rem ASCII only - cmd.exe reads a .bat in the console codepage, not UTF-8.
rem The Japanese belongs in the .ps1, which carries a UTF-8 BOM.

echo ==================================================
echo   stock-ai : the positive control (not a claim)
echo ==================================================
echo.
echo The negative control asks: with nothing there, does
echo the machinery pass it? This asks the opposite: with an
echo effect of known size planted, does it pass as often as
echo it should?
echo.
echo Same random signal, same seeds, same pipe. The effect
echo is 0, 0.5, 1, 1.25 and 1.5 times the required ratio.
echo At 1 a pass is a coin flip.
echo.
echo Not counted against the budget. No API calls. Slower
echo than the negative control; allow a while.
echo.

powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0..\scripts\positive-control.ps1" %*
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
