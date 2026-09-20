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
echo #16 throws out some crashes because a dividend
echo fell inside them. This looks at what they were.
echo.
echo No counts are printed here on purpose: the run
echo itself counts them. A number written into this
echo header goes stale the moment the code changes,
echo and nothing tells you it has.
echo.
echo Each thrown-out crash is measured again with the
echo dividend added back:
echo.
echo   still down twenty per cent - the dividend did
echo   not cause the fall, so a real crash was dropped
echo.
echo   no longer down twenty per cent - the dividend
echo   pushed it over the line, correctly dropped
echo.
echo   later revised to no dividend - correct at the
echo   time it was dropped; using the revision would
echo   be hindsight
echo.
echo   no amount ever published - unknown, so it was
echo   dropped to be safe
echo.
echo The first table lines up every known ex-dividend
echo date, not just the thrown-out ones, so it also
echo checks the dates #15 used. If the step is not on
echo day zero the run stops there: if the day is wrong,
echo nothing below it means anything.
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
