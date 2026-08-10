@echo off
chcp 65001 >nul
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
    echo [错误] 未找到项目环境 .venv\Scripts\python.exe
    pause
    exit /b 1
)

curl -s -o nul -w "%%{http_code}" http://127.0.0.1:7788/ | findstr "200" >nul 2>&1
if errorlevel 1 (
    echo [1/2] 启动 127.0.0.1:7788...
    start "" /b ".venv\Scripts\python.exe" src\main.py >nul 2>&1
    timeout /t 3 /nobreak >nul
)

echo [2/2] 启动桌面应用...
".venv\Scripts\python.exe" desktop\launcher.py
pause
