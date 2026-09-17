@echo off
setlocal
cd /d "%~dp0.."
rem This .bat pauses at the end; stop the script pausing too.
set STOCK_AI_NO_PAUSE=1

rem ASCII only - cmd.exe reads a .bat in the console codepage, not UTF-8.
rem The Japanese belongs in the .ps1, which carries a UTF-8 BOM.

echo ==================================================
echo   stock-ai : what would a PASS have to look like?
echo ==================================================
echo.
echo Two things, both recomputed on the spot:
echo.
echo   the return a passing hypothesis must show
echo   the five rules a hypothesis has to follow
echo.
echo Nothing is transcribed. When the verdict line moved
echo from 3.02 to 3.39, every required return moved with
echo it - a copied number would still be sitting there
echo looking plausible.
echo.
echo Each scatter figure cites the pre-registration it was
echo measured in, so no number here is unsourced.
echo.
echo No API calls. Takes seconds. Press it any time.
echo.

powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0..\scripts\passing.ps1" %*
set CODE=%ERRORLEVEL%

echo.
pause
exit /b %CODE%
