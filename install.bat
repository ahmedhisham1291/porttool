@echo off
setlocal
cd /d "%~dp0"
where py >nul 2>nul
if %errorlevel%==0 (set "PY=py -3") else (set "PY=python")
%PY% -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 11) and sys.maxsize > 2**32 else 1)"
if errorlevel 1 (
  echo PortTool needs 64-bit Python 3.11 or newer.
  echo Install it from python.org, tick "Add python.exe to PATH", then run this file again.
  exit /b 1
)
if not exist ".venv\Scripts\python.exe" (
  echo Creating the Python environment in .venv ...
  %PY% -m venv .venv
  if errorlevel 1 goto failed
)
echo Installing packages. This needs internet access to pypi.org ...
".venv\Scripts\python.exe" -m pip install --upgrade pip >nul 2>nul
".venv\Scripts\python.exe" -m pip install -r requirements.txt
if errorlevel 1 goto failed
".venv\Scripts\python.exe" -m pip install -r requirements-dev.txt
if errorlevel 1 goto failed
where git >nul 2>nul
if errorlevel 1 echo WARNING: git was not found on PATH. Install Git for Windows before you use "apply".
echo.
echo Installation finished. Next: run selfcheck.bat, then read docs\user\FIRST_RUN.md
exit /b 0
:failed
echo.
echo Installation failed. Read the messages above.
exit /b 1
