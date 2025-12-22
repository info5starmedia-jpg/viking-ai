@echo off
setlocal ENABLEDELAYEDEXPANSION

echo.
echo =====================================================
echo 🚀  VikingAI Universal Auto Setup & Launcher
echo =====================================================
echo.

REM --- Detect OS type ---
ver | find "Windows" > nul
if %errorlevel%==0 (
    echo 🪟 Windows detected!
    echo -----------------------------------------------------
    if exist setup.ps1 (
        echo ⚙️ Running PowerShell setup script...
        powershell -NoProfile -ExecutionPolicy Bypass -File ".\setup.ps1"
        if %errorlevel% neq 0 (
            echo ❌ PowerShell setup failed with error code %errorlevel%.
            pause
            exit /b %errorlevel%
        )
    ) else (
        echo ❌ setup.ps1 not found. Please verify file exists.
        pause
        exit /b 1
    )
) else (
    echo 🐧 Linux/macOS detected!
    echo -----------------------------------------------------
    if exist setup.sh (
        echo ⚙️ Running Bash setup script...
        bash ./setup.sh
        if %errorlevel% neq 0 (
            echo ❌ Bash setup failed with error code %errorlevel%.
            pause
            exit /b %errorlevel%
        )
    ) else (
        echo ❌ setup.sh not found. Please verify file exists.
        pause
        exit /b 1
    )
)

echo.
echo ✅ VikingAI Universal Auto Setup finished successfully.
pause
exit /b 0
