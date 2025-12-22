@echo off
REM ===========================================
REM  VIKING AI  AUTO-START SCRIPT
REM ===========================================

REM Change directory to your Viking AI folder
cd /d "C:\VikingAI"

REM Activate the Python virtual environment
call .venv\Scripts\activate

REM Launch the Discord bot silently in the background
start "" python bot.py

exit
