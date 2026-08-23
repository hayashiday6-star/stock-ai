@echo off
setlocal
cd /d "%~dp0"
rem This .bat pauses at the end; stop the script pausing too.
set STOCK_AI_NO_PAUSE=1

rem ASCII only - cmd.exe reads a .bat in the console codepage, not UTF-8.
rem The Japanese belongs in the .ps1, which carries a UTF-8 BOM.

echo ============================================
echo   stock-ai : update to the latest
echo ============================================
echo.
echo Pulls the latest commits, syncs dependencies,
echo and lists any .bat files that were added.
echo.
echo A fix that was pushed but not pulled looks
echo exactly like a fix that does not work. Run
echo this first when something seems missing.
echo.

powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\0-update.ps1" %*
set CODE=%ERRORLEVEL%

echo.
if not "%CODE%"=="0" (
  echo Did not finish. Paste the output above.
)
echo.
pause
exit /b %CODE%
