@echo off
setlocal
cd /d "%~dp0.."
rem This .bat pauses at the end; stop the script pausing too.
set STOCK_AI_NO_PAUSE=1

rem ASCII only - cmd.exe reads a .bat in the console codepage, not UTF-8.
rem The Japanese belongs in the .ps1, which carries a UTF-8 BOM.

echo ==================================================
echo   stock-ai : check the archived originals
echo ==================================================
echo.
echo Compares what is on disk against the manifest. Downloads
echo nothing, so it still works after the plan is cancelled - and
echo that is when it matters most. It cannot get a missing file
echo back, but knowing one is missing beats assuming it is there.
echo.
echo Silence means everything matches.
echo.

powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0..\scripts\jquants-archive-verify.ps1" %*
set CODE=%ERRORLEVEL%

echo.
if not "%CODE%"=="0" (
  echo Mismatch found. Paste the list above.
) else (
  echo Everything matches the manifest.
)
echo.
pause
exit /b %CODE%
