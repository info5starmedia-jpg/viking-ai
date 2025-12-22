<#
=============================================================
VikingAI Windows Auto Setup & Launcher
=============================================================
#>

Write-Host "`n=============================================================" -ForegroundColor Cyan
Write-Host "VikingAI Windows Auto Setup & Launcher" -ForegroundColor Green
Write-Host "=============================================================`n" -ForegroundColor Cyan

# Detect working directory
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Definition
Set-Location $ScriptDir
Write-Host "Current directory: $ScriptDir"

# Check Python
Write-Host "`nChecking Python installation..." -ForegroundColor Cyan
$pythonVersion = & python --version 2>$null
if (-not $pythonVersion) {
    Write-Host "Python not found in PATH. Please install Python 3.10+ and add it to PATH." -ForegroundColor Red
    pause
    exit 1
}
Write-Host "Python detected: $pythonVersion" -ForegroundColor Green

# Create venv if missing
if (-not (Test-Path ".\.venv")) {
    Write-Host "`nCreating virtual environment (.venv)..." -ForegroundColor Yellow
    & python -m venv .venv
    if (-not (Test-Path ".\.venv")) {
        Write-Host "Failed to create virtual environment." -ForegroundColor Red
        pause
        exit 1
    }
    Write-Host "Virtual environment created successfully." -ForegroundColor Green
} else {
    Write-Host "Virtual environment already exists." -ForegroundColor Green
}

# Activate venv
Write-Host "`nActivating virtual environment..." -ForegroundColor Cyan
$venvActivate = ".\.venv\Scripts\Activate.ps1"
if (Test-Path $venvActivate) {
    & $venvActivate
} else {
    Write-Host "Activation script not found in .venv\Scripts!" -ForegroundColor Red
    pause
    exit 1
}

# Upgrade pip
Write-Host "`nUpgrading pip..." -ForegroundColor Cyan
python -m pip install --upgrade pip

# Install dependencies
if (Test-Path "requirements.txt") {
    Write-Host "`nInstalling dependencies from requirements.txt..." -ForegroundColor Yellow
    pip install -r requirements.txt
} else {
    Write-Host "requirements.txt not found. Skipping dependency installation." -ForegroundColor Yellow
}

# Optional diagnostics
if (Test-Path "diagnostics.py") {
    Write-Host "`nRunning diagnostics..." -ForegroundColor Cyan
    python diagnostics.py
}

# Launch the bot
Write-Host "`nStarting Viking AI Discord bot..." -ForegroundColor Green
python bot.py

Write-Host "`nViking AI stopped or exited. Press any key to close." -ForegroundColor Red
pause
