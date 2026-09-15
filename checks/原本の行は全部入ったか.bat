@echo off
setlocal
cd /d "%~dp0.."
rem This .bat pauses at the end; stop the script pausing too.
set STOCK_AI_NO_PAUSE=1

rem ASCII only - cmd.exe reads a .bat in the console codepage, not UTF-8.
rem The Japanese belongs in the .ps1, which carries a UTF-8 BOM.

echo ==================================================
echo   stock-ai : did every archived row reach the DB?
echo ==================================================
echo.
echo The database does not record where a row came from.
echo Over five years that did not matter - the window sat
echo entirely inside what J-Quants covers. At twenty it
echo overlaps Tachibana, and a difference no longer says
echo whether a row failed to land or simply came from the
echo other source.
echo.
echo Tachibana only ever returns currently-listed names.
echo So a delisted symbol priced in the DB can only have
echo come from J-Quants. Those are counted, both sides.
echo.
echo   delisted symbols      how many, over what span
echo   archive rows          same symbols, same window
echo   database rows         same again
echo   the difference        zero closes the item
echo.
echo No API calls. Reads every original, so allow a few
echo minutes.
echo.

powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0..\scripts\row-audit.ps1" %*
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
