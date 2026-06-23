@echo off
setlocal

set "ROOT=%~dp0"
set "VENV=%ROOT%.venv"
set "PYTHON=%VENV%\Scripts\python.exe"

if not exist "%VENV%" (
    echo Creating virtual environment...
    python -m venv "%VENV%"
    if errorlevel 1 (
        echo ERROR: Failed to create virtual environment.
        echo Make sure Python 3.11+ is installed and on PATH.
        pause
        exit /b 1
    )
)

echo Installing dependencies...
"%PYTHON%" -m pip install --quiet -r "%ROOT%requirements.txt" -e "%ROOT%[dev]"
if errorlevel 1 (
    echo ERROR: pip install failed.
    pause
    exit /b 1
)

if not exist "%ROOT%config.yaml" (
    echo Creating config.yaml from template...
    copy "%ROOT%config.example.yaml" "%ROOT%config.yaml" >nul
)

echo Starting KB Builder...
start "" http://127.0.0.1:7700
"%PYTHON%" -m uvicorn src.api:app --host 127.0.0.1 --port 7700

endlocal
