@echo off
setlocal
cd /d "%~dp0.."
rem This .bat pauses at the end; stop the script pausing too.
set STOCK_AI_NO_PAUSE=1

rem ASCII only - cmd.exe reads a .bat in the console codepage, not UTF-8.
rem The Japanese belongs in the .ps1, which carries a UTF-8 BOM.

echo ==================================================
echo   stock-ai : are the ex-dividend dates right?
echo ==================================================
echo.
echo #16 threw out 819 crashes because a dividend fell
echo inside them, and the warning said "look at what
echo they were". This is that look.
echo.
echo There are three explanations and they can be told
echo apart:
echo.
echo   the ExDate column is off - the price does not
echo   drop on the day the column claims
echo.
echo   special dividends - the yield is large
echo.
echo   the window is simply wide - the yield is one or
echo   two per cent, and the fall is still twenty per
echo   cent once the dividend is added back
echo.
echo The third one means real crashes are being thrown
echo away.
echo.
echo The first table lines up every known ex-dividend
echo date, not just the 819, so it also checks the
echo dates #15 used. If the step is not on day zero the
echo run stops there: if the day is wrong, nothing
echo below it means anything.
echo.
echo NO RETURNS ARE COMPUTED. This looks at the data,
echo not at whether the idea works.
echo.
echo No API calls. Every symbol is read several times,
echo so allow a while.
echo.

powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0..\scripts\ex-date-audit.ps1" %*
set CODE=%ERRORLEVEL%

echo.
if not "%CODE%"=="0" (
  echo Stopped. Paste the output above - the stop is
  echo itself a result.
) else (
  echo Done. Paste the tables and the lines below them.
)
echo.
pause
exit /b %CODE%
