@echo off
rem Double-click to start Dispodex. Keep this window open while the shop uses it.
title Dispodex
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo Dispodex is not set up yet. Right-click setup.ps1 and choose "Run with PowerShell" first.
  pause
  exit /b 1
)
".venv\Scripts\python.exe" manage.py serve
pause
