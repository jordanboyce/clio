#!/bin/bash
# Clio API startup script

echo "Starting Clio API..."
echo ""

# Check if virtual environment exists
if [ ! -d "venv" ]; then
    echo "Creating virtual environment..."
    python -m venv venv
fi

# Activate virtual environment
echo "Activating virtual environment..."
source venv/bin/activate

# Install dependencies if needed
if [ ! -f "venv/.installed" ]; then
    echo "Installing dependencies..."
    pip install -r requirements.txt
    touch venv/.installed
fi

# First run: decide who can reach the app (only this machine, or others on
# the network behind a shared password) and write .env. Re-run
# `python scripts/setup.py` any time to change it.
if [ ! -f ".env" ]; then
    echo ""
    python scripts/setup.py || cp .env.example .env
    echo ""
fi

# Build the frontend if no build exists yet (requires Node.js)
if [ ! -f "frontend/dist/index.html" ]; then
    echo ""
    if command -v npm >/dev/null 2>&1; then
        echo "No frontend build found. Building (one-time)..."
        (cd frontend && npm install --no-fund --no-audit && npm run build)
    else
        echo "WARNING: frontend/dist not found and npm is not installed."
        echo "The API will run, but the web UI won't be served."
        echo "Install Node.js 20+ and run: cd frontend && npm install && npm run build"
    fi
    echo ""
fi

# Auto-generate self-signed dev cert if none exists (optional helper script)
if [ ! -f "certs/server.crt" ] && [ -f "certs/generate-cert.sh" ]; then
    echo ""
    echo "No TLS certificate found. Generating self-signed dev cert..."
    bash certs/generate-cert.sh
    echo ""
    echo "To enable HTTPS, add these lines to your .env:"
    echo "    SSL_CERTFILE=certs/server.crt"
    echo "    SSL_KEYFILE=certs/server.key"
    echo ""
fi

# Start the server
echo ""
echo "Starting server on port 8473"
echo "(HTTPS if SSL_CERTFILE is set in .env, otherwise HTTP)"
echo ""
python main.py
