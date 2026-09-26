@echo off
setlocal
cd /d "%~dp0.."
rem This .bat pauses at the end; stop the script pausing too.
set STOCK_AI_NO_PAUSE=1

rem ASCII only - cmd.exe reads a .bat in the console codepage, not UTF-8.
rem The Japanese belongs in the .ps1, which carries a UTF-8 BOM.

echo ==================================================
echo   stock-ai : drafts and figures for note
echo ==================================================
echo.
echo Builds the note drafts and PNG figures from
echo docs\PUBLIC.md into reports\note, then opens it.
echo If any check fails, nothing is written.
echo.

powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0..\scripts\note-articles.ps1" %*
set CODE=%ERRORLEVEL%

echo.
if not "%CODE%"=="0" (
  echo Did not finish. Paste the list above.
) else (
  echo Done.
)
echo.
pause
exit /b %CODE%
