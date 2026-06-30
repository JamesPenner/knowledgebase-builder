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

:: Install dependencies only when requirements.txt or pyproject.toml have changed.
:: Exits 1 (needs install) or 0 (up to date).
"%PYTHON%" -c "import hashlib,pathlib,sys;h=hashlib.md5();[h.update(pathlib.Path(f).read_bytes()) for f in ['requirements.txt','pyproject.toml'] if pathlib.Path(f).exists()];s=pathlib.Path('.venv/.install_stamp');c=h.hexdigest();(s.write_text(c),sys.exit(1)) if not s.exists() or s.read_text().strip()!=c else sys.exit(0)"
if errorlevel 1 (
    echo Installing dependencies...
    "%PYTHON%" -m pip install --quiet -r "%ROOT%requirements.txt" -e "%ROOT%[dev]"
    if errorlevel 1 (
        echo ERROR: pip install failed.
        pause
        exit /b 1
    )
)

if not exist "%ROOT%config.yaml" (
    echo Creating config.yaml from template...
    copy "%ROOT%config.example.yaml" "%ROOT%config.yaml" >nul
)

echo Starting KB Builder...
start "" http://127.0.0.1:7700
"%PYTHON%" -m uvicorn src.api:app --host 127.0.0.1 --port 7700

endlocal
