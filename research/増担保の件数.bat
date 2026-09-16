@echo off
setlocal
cd /d "%~dp0.."
rem This .bat pauses at the end; stop the script pausing too.
set STOCK_AI_NO_PAUSE=1

rem ASCII only - cmd.exe reads a .bat in the console codepage, not UTF-8.
rem The Japanese belongs in the .ps1, which carries a UTF-8 BOM.

echo ==================================================
echo   stock-ai : margin-restriction census (hyp. 8)
echo ==================================================
echo.
echo Counting the events where the exchange raised the
echo margin requirement on a stock. Forced sellers appear;
echo the question is whether the price drifts after the
echo announcement.
echo.
echo NO RETURN IS COMPUTED HERE, so this spends no part of
echo the one judgement. What it produces is the event count
echo and the holding window.
echo.
echo   events           how many, and in which years
echo   busiest decile   clustering costs independence
echo   lendable only    no shorting what cannot be shorted
echo   liquidity floor  100m yen a day
echo   days to release  THIS is what sets the window
echo.
echo The window follows a formula fixed before measuring:
echo the median days to release, capped at 20 sessions. It
echo is not chosen after seeing the number.
echo.
echo Lending status is read as of the event day, not as of
echo today. Days with no roster count as UNKNOWN, never as
echo "not lendable".
echo.
echo No API calls. Reads the originals and every symbol's
echo daily bars, so allow several minutes.
echo.

powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0..\scripts\margin-census.ps1" %*
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
