@echo off
setlocal
cd /d "%~dp0.."
rem This .bat pauses at the end; stop the script pausing too.
set STOCK_AI_NO_PAUSE=1

rem ASCII only - cmd.exe reads a .bat in the console codepage, not UTF-8.
rem The Japanese belongs in the .ps1, which carries a UTF-8 BOM.

echo ==================================================
echo   stock-ai : upward guidance revisions (hyp. 5)
echo ==================================================
echo.
echo Counting the times a company raised its own full-year
echo forecast. The claim is that the price keeps rising
echo afterwards.
echo.
echo NO RETURN IS COMPUTED HERE, so this spends no part of
echo the one judgement. Hypothesis 5 has never spent one:
echo it was closed in error and the error is on the record.
echo.
echo Revisions filed on the same day as an earnings release
echo are DROPPED. Keeping the date set separate from
echo hypotheses 2 and 3 is the whole premise of reopening
echo this one.
echo.
echo   rows found        every EarnForecastRevision
echo   what did not read forecast, fiscal year, prior value
echo   up / down / small +5 percent or more counts as up
echo   same day or not   the ones dropped, and the ones kept
echo   liquidity floor   100m yen a day
echo   in / out sample   the gate counts the OUT half
echo.
echo Nothing unreadable is turned into a zero. If no
echo forecast can be read at all, the run prints the field
echo names that WERE present - "absent" and "spelled
echo differently" are not the same finding.
echo.
echo No API calls. Reads originals and daily bars, so allow
echo several minutes.
echo.

powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0..\scripts\revision-census-upward.ps1" %*
set CODE=%ERRORLEVEL%

echo.
if not "%CODE%"=="0" (
  echo Did not finish. Paste the output above.
) else (
  echo Done. Paste the two tables and any warnings.
)
echo.
pause
exit /b %CODE%
