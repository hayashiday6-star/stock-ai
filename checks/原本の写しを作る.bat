@echo off
setlocal
cd /d "%~dp0.."
rem This .bat pauses at the end; stop the script pausing too.
set STOCK_AI_NO_PAUSE=1

rem ASCII only - cmd.exe reads a .bat in the console codepage, not UTF-8.
rem The Japanese belongs in the .ps1, which carries a UTF-8 BOM.

echo ==================================================
echo   stock-ai : copy the archived originals
echo ==================================================
echo.
echo The originals are not in git - they are too big for it.
echo Not in git does not mean safe to lose: after 2026-09-22
echo nothing can fetch them again.
echo.
echo This copies whatever is new to a drive or sync folder you
echo name, then checks the copy against the manifest. Checking
echo is the point: a copy you believe in but never verified is
echo the one that fails when you need it.
echo.
echo It never deletes anything at the destination.
echo.

powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0..\scripts\archive-backup.ps1" %*
set CODE=%ERRORLEVEL%

echo.
if not "%CODE%"=="0" (
  echo Stopped. Paste the output above.
) else (
  echo Done. The copy matches the manifest.
)
echo.
pause
exit /b %CODE%
