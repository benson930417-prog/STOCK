@echo off
setlocal DisableDelayedExpansion
echo Stop local research jobs before creating an immutable history snapshot.
python -B "%~dp0local_workspace.py" snapshot
set "snapshot_exit=%errorlevel%"
pause
exit /b %snapshot_exit%
