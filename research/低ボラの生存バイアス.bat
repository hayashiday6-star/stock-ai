@echo off
setlocal
cd /d "%~dp0.."
rem This .bat pauses at the end; stop the script pausing too.
set STOCK_AI_NO_PAUSE=1

rem ASCII only - cmd.exe reads a .bat in the console codepage, not UTF-8.
rem The Japanese belongs in the .ps1, which carries a UTF-8 BOM.

echo ============================================================
echo   stock-ai : low vol, how big was the survivorship bias
echo ============================================================
echo.
echo The verdict is already recorded (2026-09-05, section 18).
echo This cannot change it. Section 11 registered it as a number
echo to read alongside the result, and this is that number.
echo.
echo Same months twice, one thing changed: the universe. Once
echo with the dated rosters, which hold the delisted companies,
echo once with only the names still listed today.
echo.
echo Reversal measured -0.040%% per 20 sessions. Low vol should
echo be smaller - a company heading for delisting gets more
echo volatile, so it lands in quintile 5, not the one we buy.
echo.

powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0..\scripts\lowvol-bias.ps1" %*
set CODE=%ERRORLEVEL%

echo.
if not "%CODE%"=="0" (
  echo Did not finish. Paste the output above.
) else (
  echo Done. Paste the tables only.
)
echo.
pause
exit /b %CODE%
