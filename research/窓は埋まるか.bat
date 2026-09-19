@echo off
setlocal
cd /d "%~dp0.."
rem This .bat pauses at the end; stop the script pausing too.
set STOCK_AI_NO_PAUSE=1

rem ASCII only - cmd.exe reads a .bat in the console codepage, not UTF-8.
rem The Japanese belongs in the .ps1, which carries a UTF-8 BOM.

echo ==================================================
echo   stock-ai : #15 do gaps get filled? (IS only)
echo ==================================================
echo.
echo When the open falls three per cent below the
echo previous close, does the stock beat an equally
echo weighted basket over the next twenty sessions?
echo.
echo Entry is the following morning's open - the gap
echo itself is the open, so there is no way to be in
echo before it. Exit is the close twenty sessions
echo later. A round trip costs 0.4%% and is deducted.
echo.
echo THIS IS NOT THE VERDICT. It measures the in-sample
echo window so the gate table can be filled. The
echo judgement window is only counted, never priced.
echo.
echo Two things are taken out, because both look
echo exactly like a three per cent gap down:
echo.
echo   ex-dividend days - the drop is mechanical and
echo   does not come back, the dividend was paid
echo.
echo   discontinuities - splits and mergers the
echo   adjustment missed, the same fault that made one
echo   earlier measurement read 273%% a day
echo.
echo The two counts are printed separately. If nothing
echo was excluded for dividends, either the originals
echo are missing or the adjusted prices already take
echo them out - and those are different problems.
echo.
echo In sample starts in 2013, not 2009: the dividend
echo dates only go back to the end of 2012, and mixing
echo years where they can be excluded with years where
echo they cannot is not measuring one thing.
echo.
echo No API calls. Every symbol is read twice, so allow
echo a while.
echo.

powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0..\scripts\gap-fill-power.ps1" %*
set CODE=%ERRORLEVEL%

echo.
if not "%CODE%"=="0" (
  echo Did not finish. Paste the output above.
) else (
  echo Done. Paste the tables and the lines below them.
)
echo.
pause
exit /b %CODE%
