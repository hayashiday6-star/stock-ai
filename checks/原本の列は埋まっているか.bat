@echo off
setlocal
cd /d "%~dp0.."
rem This .bat pauses at the end; stop the script pausing too.
set STOCK_AI_NO_PAUSE=1

rem ASCII only - cmd.exe reads a .bat in the console codepage, not UTF-8.
rem The Japanese belongs in the .ps1, which carries a UTF-8 BOM.

echo ==================================================
echo   stock-ai : what the originals actually hold
echo ==================================================
echo.
echo A reader can only show the columns it already
echo takes. When the answer sits in a column nobody
echo reads, no amount of staring at the reader finds
echo it. This counts the original's own columns.
echo.
echo A column being present is not the same as it
echo holding values. The implied volatility column
echo exists in the 2008 files and is empty in every
echo row of them. Acting on "it is there" produced a
echo design with a year and a half of estimation data.
echo.
echo So each column gets a fill count and the years it
echo actually spans, across every saved file.
echo.
echo Defaults to the endpoints the next candidates
echo need. Pass -Endpoint to look at one, -ShowEmpty
echo to include columns that are never filled.
echo.
echo One of these files carries over a hundred
echo columns, so printing all of them buries the
echo handful that matter. The default shows the ones
echo being looked for and says how many were held
echo back. -Match narrows to a spelling, -All prints
echo the lot.
echo.
echo NOTHING IS FETCHED - this only reads what is
echo already saved.
echo.

powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0..\scripts\column-census.ps1" %*
set CODE=%ERRORLEVEL%

echo.
if not "%CODE%"=="0" (
  echo Could not count. Paste the output above.
) else (
  echo Done. Paste the tables and the warnings.
)
echo.
pause
exit /b %CODE%
