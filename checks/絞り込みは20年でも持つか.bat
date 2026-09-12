@echo off
setlocal
cd /d "%~dp0.."
rem This .bat pauses at the end; stop the script pausing too.
set STOCK_AI_NO_PAUSE=1

rem ASCII only - cmd.exe reads a .bat in the console codepage, not UTF-8.
rem The Japanese belongs in the .ps1, which carries a UTF-8 BOM.

echo ==================================================
echo   stock-ai : does the filter hold over 20 years?
echo ==================================================
echo.
echo The roster filter leans on the 33-industry code. When
echo it is missing the row is kept as a company - cheaper to
echo let one ETF through than to empty the universe when a
echo field gets renamed. That part is deliberate.
echo.
echo But it assumes the code is present on nearly every row.
echo Five years of data saying so does not make it true of
echo 2006. Two things can go wrong, in opposite directions:
echo.
echo   sector code missing   ETFs and REITs come IN
echo   code not in the table an ordinary company drops OUT
echo.
echo The second is the worse one. If the coding scheme
echo changed, what drops out is not one name but all of them.
echo Neither raises an error.
echo.
echo 9999 (Other) IS in the official table. It is counted
echo apart from truly unknown codes - mixed together, a few
echo unknown codes vanish behind hundreds of funds.
echo.
echo This prints a BASELINE. Run it again on the day the
echo 20-year pull lands and compare the same line.
echo.
echo Hits no API. Reads the roster originals, a few minutes.
echo.

powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0..\scripts\filter-census.ps1" %*
set CODE=%ERRORLEVEL%

echo.
if not "%CODE%"=="0" (
  echo Did not finish. Paste the output above.
) else (
  echo Done. Paste both tables and the baseline line.
)
echo.
pause
exit /b %CODE%
