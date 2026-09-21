@echo off
setlocal
cd /d "%~dp0.."
rem This .bat pauses at the end; stop the script pausing too.
set STOCK_AI_NO_PAUSE=1

rem ASCII only - cmd.exe reads a .bat in the console codepage, not UTF-8.
rem The Japanese belongs in the .ps1, which carries a UTF-8 BOM.

echo ==================================================
echo   stock-ai : dividend yields that came out absurd
echo ==================================================
echo.
echo Some months produce a dividend yield far higher
echo than any Japanese listed company pays. Those rows
echo are NOT dropped, so they sit inside the spread
echo that sets the wall. An outlier moves a standard
echo deviation, so a small count is not a reason to
echo leave it alone.
echo.
echo There are three ways it can happen: a figure that
echo was later corrected, a price or unit that is
echo wrong, or a forecast with no matching actual. The
echo forecast and the actual are printed side by side,
echo so the first of those separates out on sight.
echo.
echo No cause is assumed. A ratio landing on a round
echo number in some rows does not make it the rule -
echo the share is what decides.
echo.
echo NOTHING IS FETCHED - this only reads what is
echo already saved.
echo.

powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0..\scripts\yield-audit.ps1" %*
set CODE=%ERRORLEVEL%

echo.
if not "%CODE%"=="0" (
  echo Could not look. Paste the output above.
) else (
  echo Done. Paste the table and the warnings.
)
echo.
pause
exit /b %CODE%
