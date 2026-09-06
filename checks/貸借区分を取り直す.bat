@echo off
setlocal
cd /d "%~dp0.."
rem This .bat pauses at the end; stop the script pausing too.
set STOCK_AI_NO_PAUSE=1

rem ASCII only - cmd.exe reads a .bat in the console codepage, not UTF-8.
rem The Japanese belongs in the .ps1, which carries a UTF-8 BOM.

echo ============================================================
echo   stock-ai : re-pull the rosters, this time with Mrgn
echo ============================================================
echo.
echo RUN THIS BEFORE 2026-09-22. After that it cannot be done.
echo.
echo The 63 rosters already on disk are missing a column. The
echo J-Quants listing master returns Mrgn / MrgnNm - whether a
echo name is lendable, which decides whether it can be shorted -
echo and our reader was dropping it. The master is dated, so this
echo is the only route to what it was on a past day. Tachibana's
echo master only ever returns today.
echo.
echo Free plan. No prices are fetched - the DB already has them.
echo Every one of the 63 dates gets requested again, so this takes
echo a while. Interrupting is safe; re-running continues.
echo.
echo Afterwards the files under data\universe_snapshots\ will have
echo changed. Commit them - they cannot be refetched later.
echo.

powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0..\scripts\delisted-harvest.ps1" -Refetch -NoPrices %*
set CODE=%ERRORLEVEL%

echo.
if not "%CODE%"=="0" (
  echo Did not finish. Paste the output above.
) else (
  echo Done. Commit data\universe_snapshots\ and paste the summary.
)
echo.
pause
exit /b %CODE%
