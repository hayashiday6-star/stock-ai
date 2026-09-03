@echo off
setlocal
cd /d "%~dp0.."
rem This .bat pauses at the end; stop the script pausing too.
set STOCK_AI_NO_PAUSE=1

rem ASCII only - cmd.exe reads a .bat in the console codepage, not UTF-8.
rem The Japanese belongs in the .ps1, which carries a UTF-8 BOM.

echo ============================================
echo   stock-ai : reversal, the sealed judgement
echo ============================================
echo.
echo The registration was sealed on 2026-09-04 without one
echo reversal return having been looked at. This is the single
echo judgement that design bought. Run it once.
echo.
echo The verdict is not read off by a person. Section 8's table
echo is applied in code. On the last hypothesis the standard
echo nearly moved after the number was seen.
echo.
echo Remember the third row of that table:
echo   0.40%% to 0.85%% with t below 2.0 is the structural band.
echo   A fail, but not "no effect". Do not loosen the standard.
echo.
echo The universe is the dated rosters, so delisted companies
echo are in it. Measured survivorship bias: -0.040%% per 20 days.
echo.

powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0..\scripts\reversal-run.ps1" %*
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
