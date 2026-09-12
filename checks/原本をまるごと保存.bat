@echo off
setlocal
cd /d "%~dp0.."
rem This .bat pauses at the end; stop the script pausing too.
set STOCK_AI_NO_PAUSE=1

rem ASCII only - cmd.exe reads a .bat in the console codepage, not UTF-8.
rem The Japanese belongs in the .ps1, which carries a UTF-8 BOM.

echo ==================================================
echo   stock-ai : archive the bulk files as they arrive
echo ==================================================
echo.
echo Saves the bytes exactly as they come back. Nothing is
echo decompressed and nothing is turned into CSV - how to read them
echo is the part worth changing later.
echo.
echo Safe to stop and restart. Files already here at the right size
echo are not fetched again; files at the wrong size are. This takes
echo hours and a lot of disk. Run the dry run first.
echo.

powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0..\scripts\jquants-archive.ps1" %*
set CODE=%ERRORLEVEL%

echo.
if not "%CODE%"=="0" (
  echo Did not finish. Paste the summary and warnings above.
  echo Running it again picks up where it stopped.
) else (
  echo Done. Paste the summary and warnings above.
)
echo.
pause
exit /b %CODE%
