@echo off
setlocal
cd /d "%~dp0.."
rem This .bat pauses at the end; stop the script pausing too.
set STOCK_AI_NO_PAUSE=1

rem ASCII only - cmd.exe reads a .bat in the console codepage, not UTF-8.
rem The Japanese belongs in the .ps1, which carries a UTF-8 BOM.

echo ==================================================
echo   stock-ai : spread after a guidance raise (hyp. 5)
echo ==================================================
echo.
echo The last blank in the power gate: how far the returns
echo scatter around their mean, per event.
echo.
echo In-sample only, up to 2017-08-24. The out-of-sample
echo half is never touched here - the judgement is sealed
echo first and run once.
echo.
echo   SD per event    sets the detectable difference
echo   trimmed SD      is it a handful of outliers?
echo   overlap factor  measured, not assumed
echo   the estimate    decides whether to seal at all
echo.
echo Entry is the open of the day AFTER the filing, held 20
echo sessions, out at the close. Long, so no sign flip, and
echo 0.4 percent round-trip comes off. Twenty sessions is
echo the window hypotheses 3 and 6 used; it was not chosen
echo by looking at returns.
echo.
echo The floor was committed before measuring: a one-sided
echo 95 percent lower bound of 1.2 percent per event, three
echo times the round trip. Three times, not one, because a
echo floor at cost walks into the band hypothesis 7 died in.
echo.
echo No API calls. Reads originals and daily bars, so allow
echo several minutes.
echo.

powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0..\scripts\revision-power.ps1" %*
set CODE=%ERRORLEVEL%

echo.
if not "%CODE%"=="0" (
  echo Did not finish. Paste the output above.
) else (
  echo Done. Paste the table and the lines below it.
)
echo.
pause
exit /b %CODE%
