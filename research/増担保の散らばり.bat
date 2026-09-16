@echo off
setlocal
cd /d "%~dp0.."
rem This .bat pauses at the end; stop the script pausing too.
set STOCK_AI_NO_PAUSE=1

rem ASCII only - cmd.exe reads a .bat in the console codepage, not UTF-8.
rem The Japanese belongs in the .ps1, which carries a UTF-8 BOM.

echo ==================================================
echo   stock-ai : margin-restriction spread (hyp. 8)
echo ==================================================
echo.
echo The last blank in the power gate: how far the returns
echo scatter around their mean, per event.
echo.
echo In-sample only, up to 2017-07-25. The out-of-sample
echo half is never touched here - the judgement is sealed
echo first and run once.
echo.
echo   SD per event    sets the detectable difference
echo   overlap factor  measured, not assumed
echo   the estimate    decides whether to seal at all
echo.
echo Entry is the open of the day AFTER the announcement,
echo held for the window from the census, out at the close.
echo The announcement lands around 16:30, so that day's
echo close is not reachable. Short, so the sign flips, and
echo 0.4 percent round-trip comes off.
echo.
echo The floor was committed before measuring: a one-sided
echo 95 percent lower bound of 1.2 percent per event, which
echo is three times the round trip. Three times, not one,
echo because a floor at cost walks straight into the band
echo that hypothesis 7 died in.
echo.
echo No API calls. Reads originals and daily bars, so allow
echo several minutes.
echo.

powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0..\scripts\margin-power.ps1" %*
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
