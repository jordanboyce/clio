@echo off
REM Clio API startup script for Windows

echo Starting Clio API...
echo.

REM Check if virtual environment exists
if not exist "venv" (
    echo Creating virtual environment...
    python -m venv venv
)

REM Activate virtual environment
echo Activating virtual environment...
call venv\Scripts\activate.bat

REM Install dependencies if needed
if not exist "venv\.installed" (
    echo Installing dependencies...
    pip install -r requirements.txt
    type nul > venv\.installed
)

REM First run: decide who can reach the app (only this machine, or others on
REM the network behind a shared password) and write .env. Re-run
REM `python scripts\setup.py` any time to change it.
if not exist ".env" (
    echo.
    python scripts\setup.py
    if not exist ".env" copy .env.example .env
    echo.
)

REM Build the frontend if no build exists yet (requires Node.js)
if not exist "frontend\dist\index.html" (
    echo.
    where npm >nul 2>nul
    if errorlevel 1 (
        echo WARNING: frontend\dist not found and npm is not installed.
        echo The API will run, but the web UI won't be served.
        echo Install Node.js 20+ and run: cd frontend ^&^& npm install ^&^& npm run build
    ) else (
        echo No frontend build found. Building one-time...
        pushd frontend
        call npm install --no-fund --no-audit
        call npm run build
        popd
    )
    echo.
)

REM Auto-generate self-signed dev cert if none exists (optional helper script)
if not exist "certs\server.crt" if exist "certs\generate-cert.sh" (
    echo.
    echo No TLS certificate found. Generating self-signed dev cert...
    bash certs/generate-cert.sh
    echo.
    echo To enable HTTPS, add these lines to your .env:
    echo     SSL_CERTFILE=certs/server.crt
    echo     SSL_KEYFILE=certs/server.key
    echo.
)

REM Start the server
echo.
echo Starting server on port 8473
echo (HTTPS if SSL_CERTFILE is set in .env, otherwise HTTP)
echo.
python main.py
