@echo off
chcp 65001 >nul
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
    echo [错误] 未找到项目环境 .venv\Scripts\python.exe
    pause
    exit /b 1
)

".venv\Scripts\python.exe" -c "import webview" >nul 2>&1
if errorlevel 1 ".venv\Scripts\python.exe" -m pip install pywebview
".venv\Scripts\python.exe" run_desktop.py
pause
