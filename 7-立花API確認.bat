@echo off
setlocal
cd /d "%~dp0"
rem This .bat pauses at the end; stop the script pausing too.
set STOCK_AI_NO_PAUSE=1

echo ============================================
echo   stock-ai : Tachibana e-shiten API probe
echo ============================================
echo.
echo J-Quants の有料プランをやめるための下調べです。
echo 立花の API から株価を取れるかを、1 銘柄だけ
echo 実際に取得して確かめます。
echo.
echo 初回は鍵ペアを作って止まります。表示された公開鍵を
echo e支店の利用設定画面に登録し、認証IDを .env に書いてから
echo もう一度このファイルを実行してください。
echo.
echo 認証IDと仮想URLは表示されません。
echo.

powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\tachibana-probe.ps1"
if errorlevel 1 (
  echo.
  echo [ERROR] The probe did not finish. See the messages above.
)
echo.
pause
