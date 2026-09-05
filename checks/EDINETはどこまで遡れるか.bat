@echo off
setlocal
cd /d "%~dp0.."
rem This .bat pauses at the end; stop the script pausing too.
set STOCK_AI_NO_PAUSE=1

rem ASCII only - cmd.exe reads a .bat in the console codepage, not UTF-8.
rem The Japanese belongs in the .ps1, which carries a UTF-8 BOM.

echo ============================================================
echo   stock-ai : how far back does EDINET actually go
echo ============================================================
echo.
echo The financial history stops at 58 months. 58 months is
echo five years - the same number as the J-Quants rolling
echo window. That may be EDINET's limit, or it may be our own
echo harvest setting. From the database the two look identical.
echo.
echo This matters more than any change of estimator. Going from
echo 150 months to 58 multiplies the standard error by 1.61.
echo.
echo One request a year, counting what comes back. Nothing is
echo stored and nothing is parsed. Takes under a minute.
echo.

powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0..\scripts\edinet-reach.ps1" %*
set CODE=%ERRORLEVEL%

echo.
if not "%CODE%"=="0" (
  echo Did not finish. Paste the output above.
) else (
  echo Done. Paste the table only.
)
echo.
pause
exit /b %CODE%
