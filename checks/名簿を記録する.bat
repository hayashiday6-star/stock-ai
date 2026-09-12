@echo off
setlocal
cd /d "%~dp0.."
rem This .bat pauses at the end; stop the script pausing too.
set STOCK_AI_NO_PAUSE=1

rem ASCII only - cmd.exe reads a .bat in the console codepage, not UTF-8.
rem The Japanese belongs in the .ps1, which carries a UTF-8 BOM.

echo ==================================================
echo   stock-ai : commit the dated listing rosters
echo ==================================================
echo.
echo The rosters cannot be rebuilt from anywhere once the plan
echo ends. Sitting only on this machine, they are one disk away
echo from gone. The database can be rebuilt; these cannot.
echo.
echo Only data\universe_snapshots and data\tachibana_snapshots
echo are touched. Nothing else you have edited is swept in.
echo.
echo It shows what it would record and asks before doing it.
echo.

powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0..\scripts\commit-snapshots.ps1" %*
set CODE=%ERRORLEVEL%

echo.
if not "%CODE%"=="0" (
  echo Stopped. Paste the output above.
) else (
  echo Done.
)
echo.
pause
exit /b %CODE%
