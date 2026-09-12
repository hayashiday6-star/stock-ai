@echo off
setlocal
cd /d "%~dp0.."
rem This .bat pauses at the end; stop the script pausing too.
set STOCK_AI_NO_PAUSE=1

rem ASCII only - cmd.exe reads a .bat in the console codepage, not UTF-8.
rem The Japanese belongs in the .ps1, which carries a UTF-8 BOM.

echo ==================================================
echo   stock-ai : what is this product category?
echo ==================================================
echo.
echo The filter census found ProdCat 012 on BOTH sides -
echo on rows the roster keeps, and on rows it drops as a
echo fund. That tells us the field cannot back up the
echo sector code. It does not tell us why they split.
echo.
echo Counting will not settle it. The 16-symbol question
echo only closed once the names and markets were on screen.
echo Same here: read the company names.
echo.
echo   kept side shows funds/ETFs/REITs
echo     -> they are in the universe RIGHT NOW
echo   kept side shows ordinary companies
echo     -> ProdCat is simply a different axis
echo.
echo There is no source for what these codes mean. The
echo ProdCat in the official reference is the futures and
echo options product code, which is a different field.
echo So the contents are all we have to go on.
echo.
echo Defaults to 012. Pass another value to look at it
echo instead. Hits no API.
echo.

set VALUE=%1
if "%VALUE%"=="" set VALUE=012

powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0..\scripts\filter-census.ps1" -Product %VALUE%
set CODE=%ERRORLEVEL%

echo.
if not "%CODE%"=="0" (
  echo Did not finish. Paste the output above.
) else (
  echo Done. Paste the last table - the names are the answer.
)
echo.
pause
exit /b %CODE%
