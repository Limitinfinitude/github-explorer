@echo off
setlocal
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
    echo [1/3] Creating virtual environment...
    if exist "D:\anaconda3\python.exe" (
        "D:\anaconda3\python.exe" -m venv .venv
    ) else (
        where py >nul 2>&1
        if not errorlevel 1 (
            py -3 -m venv .venv
        ) else (
            where python >nul 2>&1
            if errorlevel 1 (
                echo Python 3 was not found. Install Python 3.10 or newer.
                pause
                exit /b 1
            )
            python -m venv .venv
        )
    )
    if errorlevel 1 (
        echo Failed to create .venv.
        pause
        exit /b 1
    )
    echo [2/3] Installing dependencies...
    ".venv\Scripts\python.exe" -m pip install -r requirements.txt
    if errorlevel 1 (
        echo Dependency installation failed. Check your network and retry.
        pause
        exit /b 1
    )
)

echo [3/3] Starting GitHub Explorer...
".venv\Scripts\python.exe" run_desktop.py
set "EXIT_CODE=%ERRORLEVEL%"
if not "%EXIT_CODE%"=="0" (
    echo GitHub Explorer exited with code %EXIT_CODE%.
    pause
)
exit /b %EXIT_CODE%
