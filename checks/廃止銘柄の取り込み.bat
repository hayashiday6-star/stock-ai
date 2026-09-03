@echo off
setlocal
cd /d "%~dp0.."
rem This .bat pauses at the end; stop the script pausing too.
set STOCK_AI_NO_PAUSE=1

rem ASCII only - cmd.exe reads a .bat in the console codepage, not UTF-8.
rem The Japanese belongs in the .ps1, which carries a UTF-8 BOM.

echo ============================================
echo   stock-ai : harvest the delisted names
echo ============================================
echo.
echo Saves a dated listing roster for every month, then backfills
echo prices for the names the database does not hold. Those are
echo mostly the companies that fell and were delisted - exactly the
echo ones a reversal test needs and every registration so far has
echo said were missing.
echo.
echo Dated rosters, not one merged list. A merged list would let a
echo 2023 listing into a 2021 quintile, which is look-ahead.
echo.
echo Pinned to J-Quants. The Tachibana master holds currently listed
echo names only, so it cannot serve this.
echo.
echo The J-Quants plan ends 2026-09-22. After that this is gone.
echo Anything before roughly 2021-09 is already gone.
echo.
echo Can take over an hour. Interrupting is safe - re-run to resume.
echo.

powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0..\scripts\delisted-harvest.ps1" %*
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
