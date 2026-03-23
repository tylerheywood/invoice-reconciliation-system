@echo off
echo Installing requirements...
pip install -r docs\requirements.txt --quiet
if not exist input mkdir input
echo Starting AP Inbox Control on http://localhost:5000
start http://localhost:5000
python app.py
