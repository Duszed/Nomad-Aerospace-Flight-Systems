@echo off
rem Nomad Atlas - double-click to start. No typing needed.
rem First start: sets up Python and the parts Atlas needs (needs the internet once).
rem Later starts: works offline.
setlocal
title Nomad Atlas
cd /d "%~dp0"
echo.
echo   NOMAD ATLAS
echo   ===========
echo.

set "PY=.venv\Scripts\python.exe"
if exist "%PY%" goto have_env

rem ---- first start: find Python 3.11 or newer ---------------------------------
call :find_python
if defined SYSPY goto make_env

echo   Python is not installed on this computer yet. Atlas needs it.
echo.
echo   Atlas can install it for you now: official Python 3.12 from the
echo   Windows package catalogue, for your user only. It needs the internet
echo   and Windows may show its own progress window.
echo.
echo   Press any key to install Python, or close this window to cancel.
pause >nul
winget install -e --id Python.Python.3.12 --scope user --accept-package-agreements --accept-source-agreements
call :find_python
if defined SYSPY goto make_env
echo.
echo   Python could not be installed automatically.
echo   Please install it yourself:
echo     1. Open  https://www.python.org/downloads/
echo     2. Download Python and run the installer
echo     3. Tick "Add python.exe to PATH" on the first screen
echo     4. Double-click "Start Atlas" again
goto failed

:make_env
echo   Setting up Atlas for the first time.
echo   This needs the internet and takes a few minutes. Please wait ...
echo.
%SYSPY% -m venv .venv
if errorlevel 1 (
  echo   Atlas could not create its working folder ".venv" here.
  echo   Check that this folder is not read-only, then start Atlas again.
  goto failed
)

:have_env
rem ---- install or update the parts Atlas needs, only when the list changed ----
fc /b requirements.txt ".venv\requirements.installed" >nul 2>&1
if not errorlevel 1 goto run
echo   Installing the parts Atlas needs ...
"%PY%" -m pip install --disable-pip-version-check -q -r requirements.txt
if errorlevel 1 (
  echo.
  echo   The parts Atlas needs could not be downloaded.
  echo   Check that this computer is connected to the internet, then
  echo   double-click "Start Atlas" again. This is only needed once.
  goto failed
)
copy /y requirements.txt ".venv\requirements.installed" >nul

:run
echo   Starting ...
echo.
"%PY%" -m atlas start
if errorlevel 1 goto failed
goto done

:find_python
set "SYSPY="
py -3 -c "import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)" >nul 2>&1
if not errorlevel 1 ( set "SYSPY=py -3" & goto :eof )
python -c "import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)" >nul 2>&1
if not errorlevel 1 ( set "SYSPY=python" & goto :eof )
for %%V in (314 313 312 311) do (
  if exist "%LocalAppData%\Programs\Python\Python%%V\python.exe" (
    set SYSPY="%LocalAppData%\Programs\Python\Python%%V\python.exe"
    goto :eof
  )
)
goto :eof

:failed
echo.
echo   Atlas did not start. Nothing was lost.
echo   Press any key to close this window.
pause >nul
exit /b 1

:done
echo.
echo   Press any key to close this window.
pause >nul
exit /b 0
