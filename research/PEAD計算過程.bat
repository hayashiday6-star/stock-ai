@echo off
setlocal
cd /d "%~dp0.."
rem This .bat pauses at the end; stop the script pausing too.
set STOCK_AI_NO_PAUSE=1

rem ASCII only - cmd.exe reads a .bat in the console codepage, not UTF-8.
rem The Japanese belongs in the .ps1, which carries a UTF-8 BOM.

echo ============================================
echo   stock-ai : PEAD event walkthrough
echo ============================================
echo.
echo Prints every date and price behind one symbol s events,
echo so the arithmetic can be checked by hand.
echo.
set /p SYMBOL=Symbol code (e.g. 7203): 
echo.

powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0..\scripts\pead-explain.ps1" -Symbol %SYMBOL%
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
