#!/usr/bin/env bash
set -e

echo "Installing requirements..."
pip install -r docs/requirements.txt --quiet
mkdir -p input

echo "Starting AP Inbox Control on http://localhost:5000"

# Open browser (works on macOS and most Linux desktops)
if command -v xdg-open &>/dev/null; then
    xdg-open http://localhost:5000 &
elif command -v open &>/dev/null; then
    open http://localhost:5000 &
fi

python app.py
