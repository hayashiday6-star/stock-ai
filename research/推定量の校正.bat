@echo off
setlocal
cd /d "%~dp0.."
rem This .bat pauses at the end; stop the script pausing too.
set STOCK_AI_NO_PAUSE=1

rem ASCII only - cmd.exe reads a .bat in the console codepage, not UTF-8.
rem The Japanese belongs in the .ps1, which carries a UTF-8 BOM.

echo ============================================================
echo   stock-ai : calibrate the estimator (not a judgement)
echo ============================================================
echo.
echo #6 and #7 are decided and closed. The numbers here compare
echo one estimator against another. They are not evidence for or
echo against any hypothesis, and nothing is checked against a
echo pass threshold.
echo.
echo Ratios are taken on t, never on the standard deviation. A
echo quintile spread is about 2.8x a one-sigma tilt, so matching
echo standard deviations alone would manufacture that 2.8x as a
echo free improvement.
echo.
echo Same months, same universe, same factor. Only the estimator
echo changes. Takes a few minutes.
echo.

powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0..\scripts\lowvol-estimator.ps1" %*
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
