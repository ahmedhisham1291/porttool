@echo off
rem Fake DVSwcGen for tests. Starts fake_davinci.py with the Python named by
rem FAKE_DV_PYTHON (default: python on the PATH). See fake_davinci.py.
setlocal
if not defined FAKE_DV_PYTHON set "FAKE_DV_PYTHON=python"
"%FAKE_DV_PYTHON%" "%~dp0fake_davinci.py" --tool DVSwcGen %*
exit /b %ERRORLEVEL%
