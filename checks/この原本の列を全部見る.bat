@echo off
setlocal
cd /d "%~dp0.."
rem This .bat pauses at the end; stop the script pausing too.
set STOCK_AI_NO_PAUSE=1

rem ASCII only - cmd.exe reads a .bat in the console codepage, not UTF-8.
rem The Japanese belongs in the .ps1, which carries a UTF-8 BOM.

echo ==================================================
echo   stock-ai : list every column of one endpoint
echo ==================================================
echo.
echo The shapes table stops at five columns - twenty of
echo them side by side wrecks the layout. To write a
echo reader you need all of them.
echo.
echo This prints them one per line, taken from a file that
echo was actually downloaded, not from a distribution
echo sample. equities/valuation has no sample at all, so
echo this is the only way to learn its shape.
echo.
echo Defaults to equities/valuation. Pass another endpoint
echo to look at that one instead, spelled as it appears in
echo the table.
echo.
echo Hits no API.
echo.

set TARGET=%1
if "%TARGET%"=="" set TARGET=/equities/valuation

powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0..\scripts\jquants-archive-read.ps1" -NoShapes -Columns %TARGET%
set CODE=%ERRORLEVEL%

echo.
if not "%CODE%"=="0" (
  echo Did not finish. Paste the output above.
) else (
  echo Done. Paste the numbered list.
)
echo.
pause
exit /b %CODE%
