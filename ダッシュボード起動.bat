@echo off
chcp 65001 >nul
cd /d "%~dp0"

echo ============================================
echo   stock-ai ダッシュボードを起動します
echo   （初回は準備に数分かかることがあります）
echo ============================================
echo.

where uv >nul 2>nul
if errorlevel 1 (
  echo [エラー] uv が見つかりません。
  echo   一度だけ次を実行してください: winget install --id=astral-sh.uv -e
  echo.
  pause
  exit /b 1
)

echo 必要なライブラリを準備中...
uv sync --extra data --extra db --extra dashboard
if errorlevel 1 (
  echo [エラー] 準備に失敗しました。上の表示を確認してください。
  pause
  exit /b 1
)

echo.
echo ブラウザが自動で開きます。閉じるにはこの黒い画面で Ctrl+C を押してください。
echo.
uv run streamlit run src/stock_ai/dashboard/app.py

pause
