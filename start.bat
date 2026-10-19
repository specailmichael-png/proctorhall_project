@echo off
setlocal

set "PROJECT_DIR=%~dp0proctorhall"
set "VENV_DIR=%PROJECT_DIR%\.venv"
set "DEPENDENCY_MARKER=%VENV_DIR%\.requirements-installed"
set "PYTHON_CMD=py -3.12"

if not exist "%PROJECT_DIR%\manage.py" (
    echo Could not find the ProctorHall Django project at:
    echo %PROJECT_DIR%
    pause
    exit /b 1
)

%PYTHON_CMD% --version >nul 2>&1
if errorlevel 1 set "PYTHON_CMD=python"

%PYTHON_CMD% --version >nul 2>&1
if errorlevel 1 (
    echo Python was not found. Install Python 3.12 and enable the Python launcher,
    echo then run this file again.
    pause
    exit /b 1
)

if not exist "%VENV_DIR%\Scripts\python.exe" (
    echo Creating the project virtual environment...
    %PYTHON_CMD% -m venv "%VENV_DIR%"
    if errorlevel 1 (
        echo Failed to create the virtual environment.
        pause
        exit /b 1
    )

)

if not exist "%DEPENDENCY_MARKER%" (
    echo Installing project dependencies...
    "%VENV_DIR%\Scripts\python.exe" -m pip install -r "%PROJECT_DIR%\requirements.txt"
    if errorlevel 1 (
        echo Dependency installation failed. Check the error above and try again.
        pause
        exit /b 1
    )
    type nul > "%DEPENDENCY_MARKER%"
)

cd /d "%PROJECT_DIR%"
echo Applying database migrations...
"%VENV_DIR%\Scripts\python.exe" manage.py migrate
if errorlevel 1 (
    echo Database migration failed. The server was not started.
    pause
    exit /b 1
)

echo Starting ProctorHall at http://127.0.0.1:8000/
start "" "http://127.0.0.1:8000/"
"%VENV_DIR%\Scripts\python.exe" manage.py runserver 127.0.0.1:8000

endlocal
