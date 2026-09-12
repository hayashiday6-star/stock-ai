@echo off
setlocal
cd /d "%~dp0.."
rem This .bat pauses at the end; stop the script pausing too.
set STOCK_AI_NO_PAUSE=1

rem ASCII only - cmd.exe reads a .bat in the console codepage, not UTF-8.
rem The Japanese belongs in the .ps1, which carries a UTF-8 BOM.

echo ==================================================
echo   stock-ai : how much there is to archive
echo ==================================================
echo.
echo Fetching happens once. Parsing can be redone forever. The plan
echo ends 2026-09-22, but a parser bug will still turn up in October,
echo and by then there is no original left to re-read.
echo.
echo This lists every bulk endpoint and adds up the bytes. It
echo downloads nothing. The file count and the total MB are what
echo decide how many contract days the archive run needs.
echo.
echo Run this one first.
echo.

powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0..\scripts\jquants-archive.ps1" -DryRun %*
set CODE=%ERRORLEVEL%

echo.
if not "%CODE%"=="0" (
  echo Did not finish. Paste the output above.
) else (
  echo Done. Nothing was downloaded. Paste the table above.
)
echo.
pause
exit /b %CODE%
