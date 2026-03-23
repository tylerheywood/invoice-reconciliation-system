#!/usr/bin/env bash
set -e

# Activate venv if present
if [ -f .venv/bin/activate ]; then
    # shellcheck disable=SC1091
    source .venv/bin/activate
fi

# Prefer python3/pip3 if available
PYTHON=$(command -v python3 || command -v python)
PIP=$(command -v pip3 || command -v pip)

echo "Installing requirements..."
$PIP install -r docs/requirements.txt --quiet
mkdir -p input

echo "Starting Invoice Reconciliation System on http://localhost:5000"

# Start Flask in the background
$PYTHON app.py &
FLASK_PID=$!

# Wait for server to be ready before opening browser
for _ in $(seq 1 30); do
    if curl -s -o /dev/null http://localhost:5000 2>/dev/null; then
        if command -v xdg-open &>/dev/null; then
            xdg-open http://localhost:5000 &
        elif command -v open &>/dev/null; then
            open http://localhost:5000 &
        fi
        break
    fi
    sleep 1
done

# Keep script alive so Flask stays running
wait $FLASK_PID
