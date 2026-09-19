@echo off
setlocal
cd /d "%~dp0.."
rem This .bat pauses at the end; stop the script pausing too.
set STOCK_AI_NO_PAUSE=1

rem ASCII only - cmd.exe reads a .bat in the console codepage, not UTF-8.
rem The Japanese belongs in the .ps1, which carries a UTF-8 BOM.

echo ==================================================
echo   stock-ai : is the spread coming from the
echo              deduction's own noise?
echo ==================================================
echo.
echo Switching the deduction to an equal weighted
echo universe removed the tilt - the mean went from
echo +0.49 to -0.13, which is what it should be.
echo.
echo But the SPREAD moved the other way: 0.94 to 1.09.
echo The line goes up from 3.02 to 3.30 because of it,
echo and nobody knows why it moved.
echo.
echo One candidate: what gets deducted is itself an
echo estimate, an average of the symbols trading that
echo day. Its own error rides along on every event.
echo.
echo This run builds that average from a QUARTER of the
echo symbols, so its error roughly doubles. Not a single
echo day is dropped, so the period does not change.
echo.
echo   spread rises well past 1.09   that is the cause
echo   spread stays near 1.09        look elsewhere
echo.
echo Raising the minimum symbols per day would NOT
echo answer this: it drops the thin early days, so the
echo period changes too and the two causes cannot be
echo told apart.
echo.
echo Diagnostic only. This deduction is never used for
echo a verdict.
echo.
echo No API calls. Reads daily bars 400 times over, so
echo this one is slow - allow a while.
echo.

powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0..\scripts\rehearsal-events.ps1" -BenchmarkFraction 0.25 %*
set CODE=%ERRORLEVEL%

echo.
if not "%CODE%"=="0" (
  echo Did not finish. Paste the output above.
) else (
  echo Done. Paste both tables and the lines below them.
)
echo.
pause
exit /b %CODE%
