@echo off
setlocal
cd /d "%~dp0.."
rem This .bat pauses at the end; stop the script pausing too.
set STOCK_AI_NO_PAUSE=1

rem ASCII only - cmd.exe reads a .bat in the console codepage, not UTF-8.
rem The Japanese belongs in the .ps1, which carries a UTF-8 BOM.

echo ==================================================
echo   stock-ai : why does value read 2.26 and 0.76?
echo ==================================================
echo.
echo The same in-sample window, measured twice, gave a t of
echo +2.26 one way and +0.76 the other. Only the universe
echo and the estimator differ.
echo.
echo When two estimates of one thing disagree threefold,
echo the thing to believe is the instability, not the
echo larger number.
echo.
echo This moves one lever at a time to find out which one
echo does it. NOT a judgement - no verdict is spent here.
echo.
echo   moves across   the estimator is doing it
echo   moves down     the universe is doing it
echo   moves both     the effect is not robust to design
echo.
echo That last case is itself evidence against the effect.
echo.
echo Both ends are reproduced first. If they do not come
echo back, the numbers are not read: comparing two things
echo that were never aligned measures the filters, not the
echo estimators.
echo.
echo Every cell is gross of costs, on purpose. Costs move
echo the mean, so they move t; subtracting on one side only
echo would add a fourth difference.
echo.
echo No API calls. Builds two panels, so allow minutes.
echo.

powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0..\scripts\value-reconcile.ps1" %*
set CODE=%ERRORLEVEL%

echo.
if not "%CODE%"=="0" (
  echo Did not finish. Paste the output above.
) else (
  echo Done. Paste the two check lines and the table.
)
echo.
pause
exit /b %CODE%
