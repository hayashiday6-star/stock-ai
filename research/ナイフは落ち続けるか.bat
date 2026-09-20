@echo off
setlocal
cd /d "%~dp0.."
rem This .bat pauses at the end; stop the script pausing too.
set STOCK_AI_NO_PAUSE=1

rem ASCII only - cmd.exe reads a .bat in the console codepage, not UTF-8.
rem The Japanese belongs in the .ps1, which carries a UTF-8 BOM.

echo ==================================================
echo   stock-ai : #16 does the knife keep falling?
echo ==================================================
echo.
echo After a stock loses twenty per cent over five
echo sessions, does it keep losing over the next five?
echo.
echo The saying is "do not catch a falling knife", so
echo the side measured is the SHORT. The direction was
echo taken from the saying, written down before #15 was
echo measured - not from how #15 came out.
echo.
echo The window is five sessions because the saying is
echo about the days right after the fall. It is not
echo five because five is easier to detect.
echo.
echo Entry is the following morning's open, exit is the
echo close five sessions later. A round trip costs
echo 0.4%% and is deducted.
echo.
echo THIS IS NOT THE VERDICT. It measures the in-sample
echo window so the gate table can be filled. The
echo judgement window is only counted, never priced.
echo.
echo Two things are taken out, because both can look
echo like a twenty per cent fall:
echo.
echo   ex-dividend days - here the count should be
echo   near zero, a dividend does not move a stock
echo   twenty per cent. If it is not, something else
echo   is being caught and the number is printed.
echo.
echo   discontinuities - splits and mergers the
echo   adjustment missed. This is the fault that made
echo   the first survey of this very event read 273%%
echo   a day.
echo.
echo In sample starts in 2013, not 2009: the dividend
echo dates only go back to the end of 2012, and mixing
echo years where they can be excluded with years where
echo they cannot is not measuring one thing.
echo.
echo No API calls. Every symbol is read twice, so allow
echo a while.
echo.

powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0..\scripts\knife-power.ps1" %*
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
