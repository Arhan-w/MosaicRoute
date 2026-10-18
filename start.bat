@echo off
setlocal
cd /d "%~dp0"
if not exist .venv\Scripts\python.exe (
  echo First run install.bat to install dependencies.
  pause
  exit /b 1
)
.venv\Scripts\python.exe app.py
pause
