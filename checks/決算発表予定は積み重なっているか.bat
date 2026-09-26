@echo off
setlocal
cd /d "%~dp0.."
rem This .bat pauses at the end; stop the script pausing too.
set STOCK_AI_NO_PAUSE=1

rem ASCII only - cmd.exe reads a .bat in the console codepage, not UTF-8.
rem The Japanese belongs in the .ps1, which carries a UTF-8 BOM.

echo ==================================================
echo   stock-ai : do the saved earnings dates form a history
echo ==================================================
echo.
echo Candidate 17 needs to know, on each day, which
echo earnings announcements were already scheduled.
echo The API only returns the latest schedule, but the
echo saved files carry the date each schedule was
echo published. If they pile up, they are that history.
echo.
echo No effect is computed. NOTHING IS FETCHED - this
echo only reads what is already saved.
echo.

powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0..\scripts\earnings-schedule.ps1" %*
set CODE=%ERRORLEVEL%

echo.
if not "%CODE%"=="0" (
  echo Could not count. Paste the output above.
) else (
  echo Done. Paste the lines and the warnings.
)
echo.
pause
exit /b %CODE%
