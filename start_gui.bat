@echo off
setlocal
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
  echo Python virtual environment was not found.
  echo Run: python -m venv .venv
  echo Then install: .venv\Scripts\python.exe -m pip install -e .
  pause
  exit /b 1
)

".venv\Scripts\python.exe" main.py gui
if errorlevel 1 pause
