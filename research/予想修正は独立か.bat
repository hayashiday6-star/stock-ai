@echo off
setlocal
cd /d "%~dp0.."
rem This .bat pauses at the end; stop the script pausing too.
set STOCK_AI_NO_PAUSE=1

rem ASCII only - cmd.exe reads a .bat in the console codepage, not UTF-8.
rem The Japanese belongs in the .ps1, which carries a UTF-8 BOM.

echo ==================================================
echo   stock-ai : are forecast revisions their own event?
echo ==================================================
echo.
echo Hypothesis 5 was closed on the grounds that a forecast
echo revision cannot be obtained as a disclosure of its own -
echo that its date always coincides with an earnings release.
echo.
echo The official document-type table lists EarnForecastRevision
echo as a type in its own right. Being listed and arriving on a
echo separate day are two different things, and only the second
echo one matters here. This counts the second one.
echo.
echo Reads the saved originals. Fetches nothing.
echo.

powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0..\scripts\revision-independence.ps1" %*
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
