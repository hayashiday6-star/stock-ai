@echo off
setlocal
cd /d "%~dp0.."
rem This .bat pauses at the end; stop the script pausing too.
set STOCK_AI_NO_PAUSE=1

rem ASCII only - cmd.exe reads a .bat in the console codepage, not UTF-8.
rem The Japanese belongs in the .ps1, which carries a UTF-8 BOM.

echo ============================================
echo   stock-ai : low vol, the sealed judgement
echo ============================================
echo.
echo The registration was sealed on 2026-09-04 without one
echo low-vol return having been looked at. This is the single
echo judgement that design bought. Run it once.
echo.
echo The primary measure is alpha - quintile 1 minus beta times
echo the benchmark, with beta fixed at 0.542 from 2002-2013.
echo The hypothesis claims a risk-adjusted effect, so that is
echo what gets measured.
echo.
echo A positive alpha does not mean you beat the index. With
echo beta at 0.542, quintile 1 beats the index only when alpha
echo exceeds 0.458 times the market return. The output says so
echo every time.
echo.
echo Remember the third row of the table:
echo   0.046%% to 0.33%% a month with t below 2.0 is the
echo   structural band. A fail, but not "no effect".
echo.

powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0..\scripts\lowvol-run.ps1" %*
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
