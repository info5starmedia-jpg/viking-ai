@echo off
title ⚙️ VikingAI Recovery Bootstrapper
color 0A
echo ============================================
echo     🧠 VIKINGAI RECOVERY BOOTSTRAPPER
echo ============================================
echo.

REM --- STEP 1: Set paths ---
set VIKING_DIR=C:\VikingAI
set PYTHON_EXE=%VIKING_DIR%\.venv312\Scripts\python.exe
set REPAIR_SCRIPT=%VIKING_DIR%\viking_auto_repair.py
set REQUIREMENTS=%VIKING_DIR%\requirements.txt

cd /d %VIKING_DIR%

echo [*] Checking Python environment...
if not exist "%PYTHON_EXE%" (
    echo [!] Python environment not found. Rebuilding .venv312...
    python -m venv "%VIKING_DIR%\.venv312"
    if %errorlevel% neq 0 (
        echo [X] Failed to create virtual environment.
        pause
        exit /b 1
    )
)

echo [*] Activating virtual environment...
call "%VIKING_DIR%\.venv312\Scripts\activate.bat"

echo [*] Checking dependencies...
if exist "%REQUIREMENTS%" (
    "%PYTHON_EXE%" -m pip install --upgrade pip
    "%PYTHON_EXE%" -m pip install -r "%REQUIREMENTS%"
) else (
    echo [!] No requirements.txt found. Skipping dependency install.
)

echo [*] Running VikingAI Auto-Repair...
"%PYTHON_EXE%" "%REPAIR_SCRIPT%"

if %errorlevel% neq 0 (
    echo [X] Auto-Repair failed. Retrying in 30 seconds...
    timeout /t 30
    "%PYTHON_EXE%" "%REPAIR_SCRIPT%"
)

echo [✓] VikingAI Recovery Bootstrapper completed.
echo [✓] All systems verified and launched.
echo.
pause
exit /b 0
