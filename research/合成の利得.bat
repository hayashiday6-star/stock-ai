@echo off
setlocal
cd /d "%~dp0.."
rem This .bat pauses at the end; stop the script pausing too.
set STOCK_AI_NO_PAUSE=1

rem ASCII only - cmd.exe reads a .bat in the console codepage, not UTF-8.
rem The Japanese belongs in the .ps1, which carries a UTF-8 BOM.

echo ============================================================
echo   stock-ai : does combining factors buy anything (r)
echo ============================================================
echo.
echo The thresholds were fixed before this was written:
echo   r greater or equal 2.0  proceed to the financial extraction
echo   1.5 to 2.0              ambiguous - STOP, no re-measurement
echo   below 1.5               clearly short - STOP
echo.
echo The ambiguous band stops on purpose. "It underestimates,
echo so adding financial factors would clear it" only becomes
echo available after seeing the number.
echo.
echo Low volatility alone is checked against #7 first. If it
echo does not reproduce, the filters differ and the run stops.
echo.
echo Takes several minutes.
echo.

powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0..\scripts\composite-gain.ps1" %*
set CODE=%ERRORLEVEL%

echo.
if not "%CODE%"=="0" (
  echo Did not finish. Paste the output above.
) else (
  echo Done. Paste the tables only.
)
echo.
pause
exit /b %CODE%
