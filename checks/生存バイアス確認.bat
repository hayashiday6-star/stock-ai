@echo off
setlocal
cd /d "%~dp0.."
rem This .bat pauses at the end; stop the script pausing too.
set STOCK_AI_NO_PAUSE=1

rem ASCII only - cmd.exe reads a .bat in the console codepage, not UTF-8.
rem The Japanese belongs in the .ps1, which carries a UTF-8 BOM.

echo ============================================
echo   stock-ai : can survivorship bias be fixed
echo ============================================
echo.
echo Every registration so far says delisted companies are missing
echo from the universe. For a strategy that buys the biggest losers
echo that is not a footnote - the names that fell to nothing are
echo exactly the ones absent.
echo.
echo equities/master takes a date. If a 2018 snapshot returns codes
echo that are not in today's database, the bias becomes work rather
echo than a permanent limit.
echo.
echo This uses the J-Quants plan being cancelled on 2026-09-22.
echo.

powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0..\scripts\universe-snapshots.ps1" %*
set CODE=%ERRORLEVEL%

echo.
if not "%CODE%"=="0" (
  echo Did not finish. Paste the output above.
) else (
  echo Done. Paste the output above.
)
echo.
pause
exit /b %CODE%
