@echo off
setlocal
cd /d "%~dp0.."
rem This .bat pauses at the end; stop the script pausing too.
set STOCK_AI_NO_PAUSE=1

rem ASCII only - cmd.exe reads a .bat in the console codepage, not UTF-8.
rem The Japanese belongs in the .ps1, which carries a UTF-8 BOM.

echo ==================================================
echo   stock-ai : rows in the roster with no prices
echo ==================================================
echo.
echo The event control drew 800,000 symbol-and-day pairs
echo and 16,677 of them - 2.1%% - landed on a symbol with
echo no price bars at all.
echo.
echo That is not a quirk of drawing at random. The draw
echo comes from list_securities, and so do the candidates
echo for a real hypothesis. An upward revision filed by a
echo company whose bars were never fetched produces no
echo observation, and nothing downstream says so: the
echo mean and the t are computed from what survived.
echo.
echo This counts them. Symbols with bars but fewer than
echo 22 are counted separately - a twenty session window
echo needs that many, so they are present but unusable.
echo.
echo It only counts. Filling the holes is a separate
echo decision, because they do not all have one cause:
echo not listed yet, outside the plan, or a fetch that
echo failed.
echo.
echo No API calls.
echo.

powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0..\scripts\price-coverage.ps1" %*
set CODE=%ERRORLEVEL%

echo.
if not "%CODE%"=="0" (
  echo Did not finish. Paste the output above.
) else (
  echo Done. Paste the tables and the warnings.
)
echo.
pause
exit /b %CODE%
