@echo off
title 🕒 VikingAI Scheduler Manager
color 0A
setlocal enabledelayedexpansion

:: ==============================
:: CONFIG (edit only if you want)
:: ==============================
set LOG_FILE=C:\VikingAI\logs\viking_scheduler.log
set VENV_PATH=C:\VikingAI\.venv312\Scripts\python.exe
set ORCHESTRATOR_PATH=C:\VikingAI\viking_orchestrator.py
set CLEANUP_PATH=C:\VikingAI\auto_setup.py
set WORK_DIR=C:\VikingAI

:: Option A: leave blank to auto-load from .env
set "DISCORD_WEBHOOK_URL="
:: Option B: OR paste your webhook directly:
:: set "DISCORD_WEBHOOK_URL=https://discord.com/api/webhooks/XXXXXXXX/YYYYYYYY"

:: ==============================
:: SETUP
:: ==============================
if not exist "C:\VikingAI\logs" mkdir "C:\VikingAI\logs"

:: If webhook not set above, try reading from .env
if not defined DISCORD_WEBHOOK_URL (
  if exist "%WORK_DIR%\.env" (
    for /f "usebackq tokens=1,* delims==" %%A in ("%WORK_DIR%\.env") do (
      if /I "%%~A"=="DISCORD_WEBHOOK_URL" (
        set "DISCORD_WEBHOOK_URL=%%~B"
      )
    )
  )
)

:: ==============================
:: Helper: timestamp and logging
:: ==============================
set "TS=[%date% %time%]"

:: ==============================
:: Helper: Discord notify
:: call :notify "message text"
:: ==============================
:notify
if "%DISCORD_WEBHOOK_URL%"=="" goto :eof
setlocal
set "MSG=%~1"
:: Use PowerShell to POST JSON to Discord webhook
powershell -NoLogo -NoProfile -Command ^
  "$uri=$Env:DISCORD_WEBHOOK_URL; $msg=$Env:MSG; " ^
  "if($uri){try{Invoke-RestMethod -Uri $uri -Method Post -ContentType 'application/json' -Body (@{content=$msg}|ConvertTo-Json) | Out-Null}catch{}}" 
endlocal & goto :eof

:: ==============================
:: MENU
:: ==============================
echo ========================================
echo ⚙️  VikingAI Scheduler Setup / Removal Tool
echo ========================================
echo.
echo [1] Setup VikingAI Scheduler (auto backup + cleanup)
echo [2] Remove VikingAI Scheduler
echo [3] Exit
echo.
set /p choice="Enter your choice (1/2/3): "

if "%choice%"=="1" goto setup
if "%choice%"=="2" goto remove
if "%choice%"=="3" goto exit

echo.
echo ❌ Invalid choice. Please enter 1, 2, or 3.
echo %TS% ⚠️ Invalid menu choice entered.>> "%LOG_FILE%"
call :notify "⚠️ VikingAI: Invalid menu choice in Scheduler Manager."
goto end


:setup
echo.
echo 🚀 Setting up VikingAI Automated Scheduler...
echo %TS% Starting setup...>> "%LOG_FILE%"

:: Every 3 weeks on Sunday @ 03:00 — runs the orchestrator
schtasks /create ^
  /tn "VikingAI_Orchestrator" ^
  /tr "\"%VENV_PATH%\" \"%ORCHESTRATOR_PATH%\"" ^
  /sc weekly /mo 3 /d SUN /st 03:00 ^
  /rl highest /f /ru "SYSTEM" /rp "" ^
  /startin "%WORK_DIR%"

if %errorlevel%==0 (
  echo %TS% ✅ Created: VikingAI_Orchestrator>> "%LOG_FILE%"
) else (
  echo %TS% ❌ Failed: VikingAI_Orchestrator>> "%LOG_FILE%"
  call :notify "❌ VikingAI: Failed creating 'VikingAI_Orchestrator' scheduled task."
)

:: Daily @ 03:30 — cleanup old backups
schtasks /create ^
  /tn "VikingAI_Cleanup" ^
  /tr "\"%VENV_PATH%\" \"%CLEANUP_PATH%\" --cleanup-now" ^
  /sc daily /st 03:30 ^
  /rl highest /f /ru "SYSTEM" /rp "" ^
  /startin "%WORK_DIR%"

if %errorlevel%==0 (
  echo %TS% ✅ Created: VikingAI_Cleanup>> "%LOG_FILE%"
) else (
  echo %TS% ❌ Failed: VikingAI_Cleanup>> "%LOG_FILE%"
  call :notify "❌ VikingAI: Failed creating 'VikingAI_Cleanup' scheduled task."
)

echo.
echo ✅ Setup complete!
echo ----------------------------------------
echo   • VikingAI_Orchestrator (every 3 weeks, Sun 03:00)
echo   • VikingAI_Cleanup (daily, 03:30)
echo %TS% ✅ Scheduler setup completed successfully.>> "%LOG_FILE%"
call :notify "✅ VikingAI: Scheduler setup complete — Orchestrator (q3w@03:00 Sun) + Cleanup (daily@03:30)."
goto end


:remove
echo.
echo 🧹 Removing VikingAI scheduled tasks...
echo %TS% Starting removal...>> "%LOG_FILE%"

schtasks /delete /tn "VikingAI_Orchestrator" /f
if %errorlevel%==0 (
  echo %TS% 🗑️ Deleted: VikingAI_Orchestrator>> "%LOG_FILE%"
) else (
  echo %TS% ⚠️ Not found: VikingAI_Orchestrator>> "%LOG_FILE%"
)

schtasks /delete /tn "VikingAI_Cleanup" /f
if %errorlevel%==0 (
  echo %TS% 🗑️ Deleted: VikingAI_Cleanup>> "%LOG_FILE%"
) else (
  echo %TS% ⚠️ Not found: VikingAI_Cleanup>> "%LOG_FILE%"
)

echo.
echo ✅ All VikingAI scheduled tasks removed.
echo %TS% ✅ Scheduler removal completed successfully.>> "%LOG_FILE%"
call :notify "🗑️ VikingAI: Scheduler tasks removed (Orchestrator + Cleanup)."
goto end


:exit
echo.
echo 👋 Bye!
goto end


:end
echo.
echo 📜 Log saved to: %LOG_FILE%
pause
exit /b 0
