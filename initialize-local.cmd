@echo off
setlocal DisableDelayedExpansion
python -B "%~dp0local_workspace.py" prepare
if errorlevel 1 (
  echo Initialization failed. Source and archives have been preserved.
  pause
  exit /b 1
)
echo Local workspace is ready. Use the usual launchers or local-run.cmd.
pause
