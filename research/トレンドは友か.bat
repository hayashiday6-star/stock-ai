@echo off
setlocal
cd /d "%~dp0.."
rem This .bat pauses at the end; stop the script pausing too.
set STOCK_AI_NO_PAUSE=1

rem ASCII only - cmd.exe reads a .bat in the console codepage, not UTF-8.
rem The Japanese belongs in the .ps1, which carries a UTF-8 BOM.

echo ==================================================
echo   stock-ai : #12 trend is your friend (IS only)
echo ==================================================
echo.
echo Do stocks that have been going up keep going up?
echo Rank on the past twelve months, skipping the most
echo recent one, split into fifths, take top minus
echo bottom.
echo.
echo THIS IS NOT THE VERDICT. It measures the in-sample
echo window so the gate table can be filled. The out of
echo sample window - 2018 onward - is not touched.
echo.
echo The design is fixed by the preregistration and this
echo command has no knob for it. If formation length were
echo adjustable, trying 3, 6 and 12 and keeping the best
echo would itself be multiple testing. Hypothesis 10 died
echo that way: its in-sample t moved by a factor of 2.8
echo depending on choices, so it could never be sealed.
echo.
echo Four things come out:
echo.
echo   SD per month    sets the detectable difference
echo   turnover        sets the cost. MEASURED, not
echo                   copied from hypothesis 9
echo   the estimate    decides whether to seal at all
echo   the tail        how much a sharp reversal takes
echo.
echo Both the raw spread and the alpha are printed. A
echo long-short book still has a beta, and the spread can
echo be dominated by it.
echo.
echo No API calls.
echo.

powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0..\scripts\momentum-power.ps1" %*
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
