@echo off
setlocal
cd /d "%~dp0.."
rem This .bat pauses at the end; stop the script pausing too.
set STOCK_AI_NO_PAUSE=1

rem ASCII only - cmd.exe reads a .bat in the console codepage, not UTF-8.
rem The Japanese belongs in the .ps1, which carries a UTF-8 BOM.

echo ==================================================
echo   stock-ai : is it safe to downgrade the plan?
echo ==================================================
echo.
echo Do not cancel on a hunch that everything was saved.
echo This lines up the official per-plan availability
echo table against how many originals are actually on
echo disk, endpoint by endpoint.
echo.
echo   files / size / span   per endpoint, zeros kept
echo   minimum plan          what each one needs
echo   still fetchable       on the plan you drop to
echo   things to get first   if any are at zero
echo.
echo Free is a special case: apart from the trading
echo calendar it has no bulk download at all, and the
echo API window narrows to 12w..2y12w. Plan order alone
echo cannot say that, so it is checked separately.
echo.
echo No API calls. Counting only.
echo.

powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0..\scripts\plan-coverage.ps1" %*
set CODE=%ERRORLEVEL%

echo.
if not "%CODE%"=="0" (
  echo Did not finish. Paste the output above.
) else (
  echo Done. Paste the table and the lines after it.
)
echo.
pause
exit /b %CODE%
