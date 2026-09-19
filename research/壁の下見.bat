@echo off
setlocal
cd /d "%~dp0.."
rem This .bat pauses at the end; stop the script pausing too.
set STOCK_AI_NO_PAUSE=1

rem ASCII only - cmd.exe reads a .bat in the console codepage, not UTF-8.
rem The Japanese belongs in the .ps1, which carries a UTF-8 BOM.

echo ==================================================
echo   stock-ai : how big would it have to be?
echo ==================================================
echo.
echo Seven hypotheses in a row have now closed at the
echo detectability gate. Not because the sayings are
echo wrong, but because the smallest difference these
echo designs can tell apart is larger than any real
echo effect. A monthly five-way long-short needs about
echo twenty per cent a year before it can be told from
echo luck.
echo.
echo So before writing another preregistration, this
echo measures the wall instead: for each candidate that
echo can be built from the data on hand, how big would
echo the effect have to be.
echo.
echo NO EFFECT IS COMPUTED. The smallest detectable
echo difference is the line times the spread divided by
echo the root of the count - the average does not enter
echo it. That is why nothing here counts as looking at
echo the answer first.
echo.
echo   3   Sell in May         one observation a year
echo   5   the 52 week high    monthly, five way
echo   9   gaps get filled     event, 20 sessions
echo   10  falling knives      event, 20 sessions
echo.
echo Candidates that cannot be built at all - sentiment,
echo order flow, rumours - are printed too. What is
echo absent does not show up on its own.
echo.
echo Only in-sample returns are read. Event counts are
echo taken from the judgement window as well, because
echo that is where the number of observations comes
echo from, and a count is not an effect.
echo.
echo No API calls. Every symbol is read four times, so
echo allow a while.
echo.

powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0..\scripts\wall-survey.ps1" %*
set CODE=%ERRORLEVEL%

echo.
if not "%CODE%"=="0" (
  echo Did not finish. Paste the output above.
) else (
  echo Done. Paste both tables and any warnings.
)
echo.
pause
exit /b %CODE%
