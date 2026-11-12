@echo off
rem Nomad Atlas - double-click to stop. Your data stays saved.
setlocal
title Stop Nomad Atlas
cd /d "%~dp0"
echo.
if not exist ".venv\Scripts\python.exe" (
  echo   Atlas has not been started on this computer yet.
  goto done
)
".venv\Scripts\python.exe" -m atlas stop

:done
echo.
echo   Press any key to close this window.
pause >nul
