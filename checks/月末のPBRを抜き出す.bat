@echo off
setlocal
cd /d "%~dp0.."
rem This .bat pauses at the end; stop the script pausing too.
set STOCK_AI_NO_PAUSE=1

rem ASCII only - cmd.exe reads a .bat in the console codepage, not UTF-8.
rem The Japanese belongs in the .ps1, which carries a UTF-8 BOM.

echo ==================================================
echo   stock-ai : month-end PBR, pulled into one file
echo ==================================================
echo.
echo Material for hypothesis 9, the proverb that a market
echo hard to buy is one that keeps rising. Only the PBR at
echo each month end is needed, and reading 15.9m rows every
echo time to get it costs minutes. This pulls that one
echo point out, into a few megabytes.
echo.
echo Month end here means the last observation a symbol
echo actually has that month, not the calendar date. A
echo company delisted mid-month has its final print then,
echo and looking for the calendar end would drop it from
echo the month entirely.
echo.
echo   rows / symbols / months  what came out
echo   dropped for no PBR       blanks are not zeros
echo.
echo No API calls. Reads 229 originals, so allow minutes.
echo.

powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0..\scripts\valuation-monthly.ps1" %*
set CODE=%ERRORLEVEL%

echo.
if not "%CODE%"=="0" (
  echo Did not finish. Paste the output above.
) else (
  echo Done. Paste the last two lines.
)
echo.
pause
exit /b %CODE%
