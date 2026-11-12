@echo off
setlocal
if not exist "%~dp0.venv\Scripts\python.exe" (
  echo Run install.bat first.
  exit /b 1
)
set "PYTHONPATH=%~dp0;%PYTHONPATH%"
set "PYTHONUTF8=1"
"%~dp0.venv\Scripts\python.exe" -m porttool.cli %*
