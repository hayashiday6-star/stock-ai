@echo off
setlocal
cd /d "%~dp0"

echo ============================================
echo   stock-ai dashboard launcher
echo   (first run may take a few minutes)
echo ============================================
echo.

where uv >nul 2>nul
if errorlevel 1 (
  echo [ERROR] uv not found.
  echo   Run once:  winget install --id=astral-sh.uv -e
  echo.
  pause
  exit /b 1
)

rem --- skip Streamlit's first-run "Email:" prompt ---
if not exist "%USERPROFILE%\.streamlit" mkdir "%USERPROFILE%\.streamlit"
if not exist "%USERPROFILE%\.streamlit\credentials.toml" (
  >"%USERPROFILE%\.streamlit\credentials.toml" echo [general]
  >>"%USERPROFILE%\.streamlit\credentials.toml" echo email = ""
)
set STREAMLIT_BROWSER_GATHERUSAGESTATS=false

echo Installing libraries (data, db, dashboard)...
uv sync --extra data --extra db --extra dashboard
if errorlevel 1 (
  echo [ERROR] setup failed. See messages above.
  pause
  exit /b 1
)

echo.
echo Starting dashboard. Your browser will open automatically.
echo To stop: press Ctrl+C in this window.
echo.
uv run --no-sync streamlit run src/stock_ai/dashboard/app.py

pause
