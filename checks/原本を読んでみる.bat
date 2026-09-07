@echo off
setlocal
cd /d "%~dp0.."
rem This .bat pauses at the end; stop the script pausing too.
set STOCK_AI_NO_PAUSE=1

rem ASCII only - cmd.exe reads a .bat in the console codepage, not UTF-8.
rem The Japanese belongs in the .ps1, which carries a UTF-8 BOM.

echo ==================================================
echo   stock-ai : read the archived originals
echo ==================================================
echo.
echo Saving a file and being able to read it are two different
echo things. This project has hit that twice in two days.
echo Finding out the parsers do not connect, after the plan has
echo ended, is the expensive way to learn it.
echo.
echo Passing on the shipped samples is not the same as passing on
echo the real files: the samples are a handful of rows, the bulk
echo files hold every symbol for a month.
echo.
echo Downloads nothing. Works after the plan is cancelled.
echo.

powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0..\scripts\jquants-archive-read.ps1" %*
set CODE=%ERRORLEVEL%

echo.
if not "%CODE%"=="0" (
  echo Did not finish. Paste the output above.
) else (
  echo Done. Paste the two tables above.
)
echo.
pause
exit /b %CODE%
