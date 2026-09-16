@echo off
setlocal
cd /d "%~dp0.."
rem This .bat pauses at the end; stop the script pausing too.
set STOCK_AI_NO_PAUSE=1

rem ASCII only - cmd.exe reads a .bat in the console codepage, not UTF-8.
rem The Japanese belongs in the .ps1, which carries a UTF-8 BOM.

echo ==================================================
echo   stock-ai : does taking sectors out move the t?
echo ==================================================
echo.
echo A calibration, NOT a judgement. In-sample only.
echo.
echo Every hypothesis so far has subtracted market beta and
echo nothing else. A value or low-volatility spread carries
echo large sector bets; taking those out shrinks what is
echo left to scatter.
echo.
echo If the effect is not itself a sector bet, t rises. If
echo it is, t falls. Either answer is worth having.
echo.
echo   r >= 1.4        it moved; design new tests with it
echo   1.2 <= r < 1.4  ambiguous - STOP, no re-measuring
echo   r < 1.2         it does not move
echo.
echo 1.4 comes from hypothesis 7, which needed 1.38x and
echo missed. The line sits just above what would have been
echo enough there.
echo.
echo Measured as a ratio of t, never of SD: changing the
echo estimator shrinks the effect along with the scatter,
echo so an SD ratio shows a gain that is not there.
echo.
echo This is not used to re-run hypotheses already judged.
echo That would be changing the ruler after seeing the
echo answer.
echo.
echo No API calls. Reads every symbol, so allow minutes.
echo.

powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0..\scripts\estimator-gain.ps1" %*
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
