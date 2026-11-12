@echo off
setlocal
set "REPO=%~1"
if "%REPO%"=="" set /p "REPO=Path of the ECU repository (the folder that holds porttool.toml): "
call "%~dp0porttool.bat" gui --repo "%REPO%"
if errorlevel 1 pause
