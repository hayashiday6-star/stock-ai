@echo off
setlocal
cd /d "%~dp0.."
rem This .bat pauses at the end; stop the script pausing too.
set STOCK_AI_NO_PAUSE=1

rem ASCII only - cmd.exe reads a .bat in the console codepage, not UTF-8.
rem The Japanese belongs in the .ps1, which carries a UTF-8 BOM.

echo ==================================================
echo   stock-ai : do the two price sources agree?
echo ==================================================
echo.
echo The seam check looked at a single day. It said there is no
echo step at 2021-09-01, and nothing more. Whether the two
echo sources agree on every day of the five years they share is
echo a separate question.
echo.
echo   seam check   1 day across 76 names
echo   this         5 years across the names it samples
echo.
echo This can only be done now. Once the plan is cancelled one
echo side stops updating. The originals stay, but the chance to
echo ask two live sources the same question does not.
echo.
echo It starts from the raw close - the plainest figure, which
echo both are reading off the same official print. If that does
echo not line up, the adjustment discussion is moot.
echo.
echo Queries Tachibana, so it takes a while per name.
echo.

powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0..\scripts\crosscheck-prices.ps1" %*
set CODE=%ERRORLEVEL%

echo.
if not "%CODE%"=="0" (
  echo Did not finish. Paste the output above.
) else (
  echo Done. Paste the table and the last two lines.
)
echo.
pause
exit /b %CODE%
