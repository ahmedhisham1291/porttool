@echo off
setlocal
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo Run install.bat first.
  exit /b 1
)
set "PYTHONPATH=%~dp0;%PYTHONPATH%"
set "PYTHONUTF8=1"
echo PortTool self-check. It works on a generated test repository, never on your project.
echo This takes 2 to 10 minutes ...
".venv\Scripts\python.exe" -m pytest tests\integration\test_reader_vs_generator.py tests\integration\test_end_to_end_real_parts.py tests\unit\core tests\unit\engine tests\unit\writer tests\unit\workbook -m "not slow" -q -p no:cacheprovider > selfcheck_log.txt 2>&1
set "RESULT=%errorlevel%"
findstr /i /c:" passed" /c:" failed" /c:" error" selfcheck_log.txt
echo.
if "%RESULT%"=="0" (
  echo SELF-CHECK PASSED
) else (
  echo SELF-CHECK FOUND PROBLEMS. This is expected on the first Windows run:
  echo send the file selfcheck_log.txt back so they can be fixed.
)
exit /b %RESULT%
