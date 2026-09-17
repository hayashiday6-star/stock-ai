@echo off
setlocal
cd /d "%~dp0.."
rem This .bat pauses at the end; stop the script pausing too.
set STOCK_AI_NO_PAUSE=1

rem ASCII only - cmd.exe reads a .bat in the console codepage, not UTF-8.
rem The Japanese belongs in the .ps1, which carries a UTF-8 BOM.

echo ==================================================
echo   stock-ai : where the event pipe's tilt comes from
echo ==================================================
echo.
echo The first run of this control settled the spread: the
echo event pipe's t has an SD of 0.94, close to the 1.00 it
echo should have. That part is fine.
echo.
echo What it also showed, and what matters more, is a MEAN
echo of +0.49. Random symbols on random days, held twenty
echo sessions, beat the index. With no information at all.
echo.
echo There are two ways that happens, and they call for
echo opposite fixes:
echo.
echo   A  the index is cap weighted, the draw is uniform.
echo      If small names won, the mean is positive and no
echo      code is wrong - the wrong thing is being
echo      subtracted.
echo   B  events whose window runs past the last quote are
echo      dropped. Delistings end badly, so dropping them
echo      lifts what is left.
echo.
echo This run now counts every event it throws away, by
echo reason, and measures the dropped side as far as its
echo quotes go. That splits the +0.49 into B, which it can
echo measure, and A, which is the rest.
echo.
echo Same function hypotheses 8 and 5 call. Building a
echo parallel path would prove nothing about the real one.
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
  echo Done. Paste both tables and the lines below them.
)
echo.
pause
exit /b %CODE%
