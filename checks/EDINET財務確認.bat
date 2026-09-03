@echo off
setlocal
cd /d "%~dp0.."
rem This .bat pauses at the end; stop the script pausing too.
set STOCK_AI_NO_PAUSE=1

rem ASCII only - cmd.exe reads a .bat in the console codepage, not UTF-8.
rem The Japanese belongs in the .ps1, which carries a UTF-8 BOM.

echo ============================================
echo   stock-ai : EDINET financials check
echo ============================================
echo.
echo The parser's element names were derived from ONE
echo filing (Hitachi, IFRS). A Japan-GAAP filer may
echo name the same figures differently.
echo.
echo This reads a real filing and lists every element
echo in its summary table, marking the ones the parser
echo already knows. Unmarked rows are the gaps.
echo.
echo Default is 8306 (MUFG), a Japan-GAAP filer.
echo.

powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0..\scripts\edinet-financials-check.ps1" %*
set CODE=%ERRORLEVEL%

echo.
if not "%CODE%"=="0" (
  echo Did not finish. Paste the output above.
) else (
  echo Done. Paste the output above.
)
echo.
pause
exit /b %CODE%
