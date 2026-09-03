@echo off
setlocal
cd /d "%~dp0.."
rem This .bat pauses at the end; stop the script pausing too.
set STOCK_AI_NO_PAUSE=1

rem ASCII only - cmd.exe reads a .bat in the console codepage, not UTF-8.
rem The Japanese belongs in the .ps1, which carries a UTF-8 BOM.

echo ============================================
echo   stock-ai : reversal power, before sealing
echo ============================================
echo.
echo Both earnings-drift registrations were sealed first and found
echo short of power afterwards. This runs before, not after.
echo.
echo Variance only. The mean is never computed or printed, and the
echo judged period (2021-09 onward) is refused as an input.
echo.
echo Answers "how big would an effect have to be to show up",
echo not "how big is it".
echo.
echo Takes a few minutes - it scans every symbol.
echo.

powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0..\scripts\reversal-power.ps1" %*
set CODE=%ERRORLEVEL%

echo.
if not "%CODE%"=="0" (
  echo Did not finish. Paste the output above.
) else (
  echo Done. Paste the output above.
)
echo.
pause
exit /b %CODE%
