@echo off
setlocal
cd /d "%~dp0.."
rem This .bat pauses at the end; stop the script pausing too.
set STOCK_AI_NO_PAUSE=1

rem Everything printed here is ASCII on purpose. cmd.exe reads a .bat in the
rem console codepage (cp932 on a Japanese Windows), so UTF-8 Japanese in this
rem file arrives as mojibake - and mojibake bytes can end an echo early, after
rem which cmd tries to run the rest of the line as a command. The Japanese
rem belongs in the .ps1, which is UTF-8 with a BOM and handles it correctly.

echo ============================================
echo   stock-ai : Tachibana e-shiten API probe
echo ============================================
echo.
echo Groundwork for dropping the paid J-Quants plan.
echo This fetches ONE symbol from Tachibana to find out
echo whether the price feed can replace it.
echo.
echo The first run creates a key pair and stops. Register
echo the printed public key in the e-shiten settings page,
echo put the auth ID in .env, then run this file again.
echo.
echo The auth ID and the virtual URLs are never displayed.
echo.

powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0..\scripts\tachibana-probe.ps1"
if errorlevel 1 (
  echo.
  echo [ERROR] The probe did not finish. See the messages above.
)
echo.
pause
