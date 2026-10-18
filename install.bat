@echo off
setlocal
cd /d "%~dp0"
where py >nul 2>nul
if %errorlevel%==0 (
  py -3 -m venv .venv
) else (
  python -m venv .venv
)
if not exist .venv\Scripts\python.exe (
  echo Could not create a virtual environment. Install Python 3.11 or newer first.
  pause
  exit /b 1
)
.venv\Scripts\python.exe -m pip install --upgrade pip
if errorlevel 1 goto fail
.venv\Scripts\python.exe -m pip install -r requirements.txt
if errorlevel 1 goto fail
echo.
echo Installation complete. Run start.bat to launch MosaicRoute.
pause
exit /b 0
:fail
echo Installation failed. Review the error above and try again.
pause
exit /b 1
