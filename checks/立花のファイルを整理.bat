@echo off
setlocal
cd /d "%~dp0.."
rem This .bat pauses at the end; stop the script pausing too.
set STOCK_AI_NO_PAUSE=1

rem ASCII only - cmd.exe reads a .bat in the console codepage, not UTF-8.
rem The Japanese belongs in the .ps1, which carries a UTF-8 BOM.

echo ============================================
echo   stock-ai : tidy the Tachibana files
echo ============================================
echo.
echo Four or five files named tachibana_* were sitting loose in
echo the project folder, with no way to tell which are secrets
echo and which are throwaway output. This gathers them into one
echo folder.
echo.
echo   tachibana_private.pem  -^> tachibana\private.pem   (secret)
echo   tachibana_public.txt   -^> tachibana\public.txt
echo   tachibana_session.json -^> tachibana\session.json  (secret)
echo   tachibana_history.json -^> tachibana\history.json
echo   tachibana_master.json  -^> tachibana\master.json
echo.
echo Nothing is deleted. Nothing is overwritten.
echo.
echo The private key matters: regenerating it means registering
echo again with Tachibana. The code reads the old location too,
echo so there is no rush and nothing breaks either way.
echo.

powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0..\scripts\tachibana-tidy.ps1" %*
set CODE=%ERRORLEVEL%

echo.
if not "%CODE%"=="0" (
  echo Did not finish. Paste the output above.
) else (
  echo Done.
)
echo.
pause
exit /b %CODE%
