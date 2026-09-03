@echo off
setlocal
cd /d "%~dp0.."
rem This .bat pauses at the end; stop the script pausing too.
set STOCK_AI_NO_PAUSE=1

rem ASCII only - cmd.exe reads a .bat in the console codepage, not UTF-8.
rem The Japanese belongs in the .ps1, which carries a UTF-8 BOM.

echo ============================================
echo   stock-ai : Tachibana throughput estimate
echo ============================================
echo.
echo Measures a few symbols and extrapolates to the whole universe.
echo.
echo The price-history call has no date range - every request returns
echo all 25 years. Nobody has measured what that costs across 3,600
echo symbols. Finding out AFTER the J-Quants plan is cancelled is the
echo worst place to find out.
echo.
echo No prices are stored. Only counts, bytes and seconds.
echo.

powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0..\scripts\tachibana-throughput.ps1" %*
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
