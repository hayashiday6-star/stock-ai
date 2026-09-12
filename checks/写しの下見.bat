@echo off
setlocal
cd /d "%~dp0.."
rem This .bat pauses at the end; stop the script pausing too.
set STOCK_AI_NO_PAUSE=1

rem ASCII only - cmd.exe reads a .bat in the console codepage, not UTF-8.
rem The Japanese belongs in the .ps1, which carries a UTF-8 BOM.

echo ==================================================
echo   stock-ai : what would the backup copy?
echo ==================================================
echo.
echo Copies NOTHING. Lists what the next copy would move,
echo and checks the destination has room for it.
echo.
echo Five years is 265 MB and goes through in seconds, so
echo none of this mattered. Twenty years is over a GB. The
echo point of looking first is that running out of space
echo halfway leaves a half-written copy, and you only find
echo out at the end.
echo.
echo It also names the destination free space. If that can
echo not be read - a network path, for one - it says so
echo out loud instead of skipping the check quietly.
echo.

powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0..\scripts\archive-backup.ps1" -DryRun %*
set CODE=%ERRORLEVEL%

echo.
if not "%CODE%"=="0" (
  echo Did not finish. Paste the output above.
) else (
  echo Done. Nothing was copied.
)
echo.
pause
exit /b %CODE%
