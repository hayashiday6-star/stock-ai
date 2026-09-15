@echo off
setlocal
cd /d "%~dp0.."
rem This .bat pauses at the end; stop the script pausing too.
set STOCK_AI_NO_PAUSE=1

rem ASCII only - cmd.exe reads a .bat in the console codepage, not UTF-8.
rem The Japanese belongs in the .ps1, which carries a UTF-8 BOM.

echo ==================================================
echo   stock-ai : per-share figures and market cap
echo ==================================================
echo.
echo Market cap was never on disk, so anything sorting by
echo size had to build it from price times share count -
echo and that count changes scale across a split. These
echo files carry the figure already assembled.
echo.
echo   filled by year        which columns, which years
echo   thin years            market cap under half full
echo   PER x EPS vs PBR x BPS   both should be the close
echo.
echo The last one is the point. Two multiplications that
echo must land on the same close. If they do not, the
echo columns do not mean what we think - and that can be
echo said without opening a second set of originals.
echo.
echo No API calls. 229 files, so allow a few minutes.
echo.

powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0..\scripts\valuation-read.ps1" %*
set CODE=%ERRORLEVEL%

echo.
if not "%CODE%"=="0" (
  echo Did not finish. Paste the output above.
) else (
  echo Done. Paste the table and the lines after it.
)
echo.
pause
exit /b %CODE%
