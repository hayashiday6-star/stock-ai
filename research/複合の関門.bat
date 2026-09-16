@echo off
setlocal
cd /d "%~dp0.."
rem This .bat pauses at the end; stop the script pausing too.
set STOCK_AI_NO_PAUSE=1

rem ASCII only - cmd.exe reads a .bat in the console codepage, not UTF-8.
rem The Japanese belongs in the .ps1, which carries a UTF-8 BOM.

echo ==================================================
echo   stock-ai : the composite gate (hypothesis 11)
echo ==================================================
echo.
echo Low volatility crossed with value. The first composite
echo that spans two kinds: a price factor and a financial
echo one. The bundle closed in September 2026 was three
echo price factors and nothing else.
echo.
echo This is NOT the judgement. It reads 2009-01 to 2017-12
echo only and never touches the out-of-sample window.
echo.
echo Six things get checked before anything is sealed:
echo.
echo   legs registered   an unlisted leg states no claim
echo   tries in sample   fixed at one, committed already
echo   counts and float  demanding PBR thins the sort
echo   the power gate    can this ever be detected?
echo   kinds spanned     not blocked, but reported
echo   counted as one    not as its number of legs
echo.
echo The legs are measured on the same panel, as the bar the
echo composite has to clear. That is not a verdict on them.
echo.
echo Needs the month-end PBR file. If it is missing, run the
echo month-end PBR check in the checks folder first.
echo.
echo No API calls. Reads every symbol, so allow minutes.
echo.

powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0..\scripts\composite-gate.ps1" %*
set CODE=%ERRORLEVEL%

echo.
if not "%CODE%"=="0" (
  echo Stopped at a gate. Paste the output above.
) else (
  echo Done. Paste the three tables and the last two lines.
)
echo.
pause
exit /b %CODE%
