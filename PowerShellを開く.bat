@echo off
cd /d "%~dp0"

rem Opens PowerShell already sitting in the project folder. Without this a new
rem PowerShell starts in C:\WINDOWS\system32, where uv and git cannot find the
rem project and every command fails with "program not found".

powershell -NoExit -NoProfile -ExecutionPolicy Bypass -Command "Set-Location '%~dp0'; Write-Host ''; Write-Host '  stock-ai - PowerShell is now in the project folder.' -ForegroundColor Cyan; Write-Host ''; Write-Host '  Update to the latest code:'; Write-Host '    git pull origin claude/recent-activity-z1t0is' -ForegroundColor DarkGray; Write-Host ''; Write-Host '  Screen for cheap growth:'; Write-Host '    uv run stock-ai screen --min-revenue-growth 0.1 --max-per 25' -ForegroundColor DarkGray; Write-Host ''; Write-Host '  Test a score against real data:'; Write-Host '    uv run stock-ai factor-test 2024-06-28 --preset tenbagger' -ForegroundColor DarkGray; Write-Host ''; Write-Host '  Check EDINET:'; Write-Host '    uv run stock-ai monitor --source edinet --provider dummy --lookback-days 3' -ForegroundColor DarkGray; Write-Host ''; Write-Host '  Dashboard:'; Write-Host '    uv run streamlit run src/stock_ai/dashboard/app.py' -ForegroundColor DarkGray; Write-Host ''"
