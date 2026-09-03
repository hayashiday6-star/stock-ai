@echo off
setlocal
cd /d "%~dp0.."
rem This .bat pauses at the end; stop the script pausing too.
set STOCK_AI_NO_PAUSE=1

rem ASCII only - cmd.exe reads a .bat in the console codepage, not UTF-8.
rem The Japanese belongs in the .ps1, which carries a UTF-8 BOM.

echo ============================================
echo   stock-ai : how big is survivorship bias
echo ============================================
echo.
echo Runs the same days twice, changing one thing: the universe.
echo Once with the dated rosters, which hold the companies that
echo were later delisted. Once with only the names still listed.
echo The difference is the bias, in size and in sign.
echo.
echo In-sample only. Out-of-sample dates are refused as input.
echo.
echo Every registration so far asserted this bias existed without
echo a number. Run checks\delisted harvest first - no rosters,
echo no difference to take.
echo.

powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0..\scripts\reversal-bias.ps1" %*
set CODE=%ERRORLEVEL%

echo.
if not "%CODE%"=="0" (
  echo Did not finish. Paste the output above.
) else (
  echo Done. Paste the output above.
)
echo.
pause
exit /b %CODE%
