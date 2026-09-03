@echo off
setlocal
cd /d "%~dp0.."
rem This .bat pauses at the end; stop the script pausing too.
set STOCK_AI_NO_PAUSE=1

rem ASCII only - cmd.exe reads a .bat in the console codepage, not UTF-8.
rem The Japanese belongs in the .ps1, which carries a UTF-8 BOM.

echo ============================================
echo   stock-ai : Tachibana issue master
echo ============================================
echo.
echo The v4r10 manual settled the request format. This fetches the
echo stock master and the stock-market master and reports how many
echo records carry each field, plus the segment and sector spread.
echo.
echo A manual is a specification, not a measurement. Field names
echo being documented does not mean values are present - that exact
echo mistake went unnoticed once already with J-Quants.
echo.
echo No prices, no returns. Codes and classifications only.
echo.

powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0..\scripts\tachibana-master.ps1" %*
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
