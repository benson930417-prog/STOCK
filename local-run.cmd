@echo off
setlocal DisableDelayedExpansion
where python >nul 2>&1
if errorlevel 1 (
  echo Install Python 3.12 or newer and Git, then run this launcher again.
  pause
  exit /b 1
)
python -B "%~dp0local_workspace.py" run %*
set "run_exit=%errorlevel%"
if not "%run_exit%"=="0" pause
exit /b %run_exit%
