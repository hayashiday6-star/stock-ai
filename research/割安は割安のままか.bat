@echo off
setlocal
cd /d "%~dp0.."
rem This .bat pauses at the end; stop the script pausing too.
set STOCK_AI_NO_PAUSE=1

rem ASCII only - cmd.exe reads a .bat in the console codepage, not UTF-8.
rem The Japanese belongs in the .ps1, which carries a UTF-8 BOM.

echo ==================================================
echo   stock-ai : is cheap still cheap? (IS estimate)
echo ==================================================
echo.
echo Hypothesis 9, the proverb that a market hard to buy
echo is one that keeps rising, read through PBR: buy the
echo cheap half, sell the dear half, and see which way it
echo goes.
echo.
echo This is NOT the judgement. It looks only at 2009-01 to
echo 2017-12 and never touches the out-of-sample window.
echo What comes out are the three numbers the gate needs.
echo.
echo   SD          decides the detectable difference
echo   turnover    decides the cost
echo   effect      decides whether to seal at all
echo.
echo The floor was committed before measuring: 1.0 percent
echo a year after costs. If the estimate falls short, this
echo says so, and the floor does not move.
echo.
echo Needs the month-end PBR file. If it is missing, run the
echo month-end PBR check in the checks folder first.
echo.
echo No API calls.
echo.

powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0..\scripts\antivalue-estimate.ps1" %*
set CODE=%ERRORLEVEL%

echo.
if not "%CODE%"=="0" (
  echo Did not finish. Paste the output above.
) else (
  echo Done. Paste the table and the lines below it.
)
echo.
pause
exit /b %CODE%
