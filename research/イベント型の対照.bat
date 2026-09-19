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
echo Two runs settled what was wrong. The spread was fine
echo all along - an SD of 0.94 against the 1.00 it should
echo have. The MEAN was not: +0.49, meaning random symbols
echo on random days beat the index with no information.
echo.
echo The split said where it came from:
echo.
echo   stock side   +1.11%%
echo   index side   +0.88%%
echo   difference   +0.22%%   per event, twenty sessions
echo   of which survivorship  -0.00%%
echo.
echo So it was never the dropped delistings. 1306 is cap
echo weighted and an event basket is equal weighted, and
echo subtracting one from the other is the whole story.
echo.
echo This run now subtracts the EQUAL WEIGHTED UNIVERSE
echo instead. The mean should come back near zero.
echo.
echo Some of that is close to circular - drawing uniformly
echo and subtracting a uniform average will agree. It is
echo still worth running: this is the only place the
echo implementation gets checked against the real prices.
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
