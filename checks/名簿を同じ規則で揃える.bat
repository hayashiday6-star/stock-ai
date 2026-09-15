@echo off
setlocal
cd /d "%~dp0.."
rem This .bat pauses at the end; stop the script pausing too.
set STOCK_AI_NO_PAUSE=1

rem ASCII only - cmd.exe reads a .bat in the console codepage, not UTF-8.
rem The Japanese belongs in the .ps1, which carries a UTF-8 BOM.

echo ==================================================
echo   stock-ai : put all 66 rosters on one rule
echo ==================================================
echo.
echo DO NOT RUN THIS WHILE ANOTHER J-QUANTS JOB IS GOING.
echo They share one rate limit and both end up waiting.
echo.
echo The 66 rosters under data\universe_snapshots were
echo written on 2026-09-07, before TOKYO PRO Market was
echo excluded from the universe on 09-08. One of them -
echo 2021-09-04 - was refetched today and so came back
echo under the NEW rule, 49 names shorter.
echo.
echo So the set is now mixed, and that is the hardest
echo state to read. All-old is explainable in one
echo sentence. All-new makes the comparison against the
echo bulk-derived rosters clean. Mixed means the
echo explanation changes per date.
echo.
echo This refetches all 66 on the current rule. No prices
echo are fetched. Nothing is lost - the TOKYO PRO names
echo that drop out are still in the saved originals.
echo.
echo Needs Premium (or Standard). On Free or Light the
echo 2021 dates are outside the window and this cannot
echo be done again.
echo.
echo Afterwards the files change. Commit them with the
echo roster-recording .bat next to this one.
echo.

powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0..\scripts\delisted-harvest.ps1" -Refetch -NoPrices %*
set CODE=%ERRORLEVEL%

echo.
if not "%CODE%"=="0" (
  echo Did not finish. Paste the output above.
) else (
  echo Done. Paste the summary line.
)
echo.
pause
exit /b %CODE%
