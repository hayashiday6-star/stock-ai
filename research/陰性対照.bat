@echo off
setlocal
cd /d "%~dp0.."
rem This .bat pauses at the end; stop the script pausing too.
set STOCK_AI_NO_PAUSE=1

rem ASCII only - cmd.exe reads a .bat in the console codepage, not UTF-8.
rem The Japanese belongs in the .ps1, which carries a UTF-8 BOM.

echo ==================================================
echo   stock-ai : the negative control (not a claim)
echo ==================================================
echo.
echo Nothing here claims anything about markets. A random
echo signal is pushed through the SAME pipe a real factor
echo goes through, end to end: in-sample, seal, one run
echo out-of-sample, verdict.
echo.
echo The question is one line: with nothing there, does
echo this machinery hand back a PASS?
echo.
echo If it does, the machinery is broken. If it does not,
echo that is a stronger guarantee than the four hypotheses
echo stopped at the gate - those never reached a verdict at
echo all.
echo.
echo Only the signal is random. The months, the universe
echo and the returns are the real ones. Randomising all of
echo it would erase the overlap and the autocorrelation,
echo which is the part most worth checking.
echo.
echo It is NOT counted against the multiple-testing budget,
echo and it does NOT go through the power gate. A gate that
echo protects the one judgement has nothing to protect when
echo no judgement is being spent.
echo.
echo One run cannot calibrate anything: a pass has a 0.125
echo percent chance, so 400 runs expect half of one. Use
echo -Repeat 400 to see the shape of t instead. Under the
echo null its spread should be 1.00; at 1.15 the correction
echo is not doing its job.
echo.
echo No API calls. Builds two panels, so allow minutes.
echo.

powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0..\scripts\rehearsal.ps1" %*
set CODE=%ERRORLEVEL%

echo.
if not "%CODE%"=="0" (
  echo Did not finish. Paste the output above.
) else (
  echo Done. Paste the table and the line below it.
)
echo.
pause
exit /b %CODE%
