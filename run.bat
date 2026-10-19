@echo off
cd /d "%~dp0"
if not exist .venv (
    python -m venv .venv
    .venv\Scripts\pip install -r requirements.txt
)
rem сайты постоянно меняются, поэтому yt-dlp обновляем при каждом запуске
.venv\Scripts\pip install -q -r requirements.txt >nul 2>&1
.venv\Scripts\pip install -q -U yt-dlp >nul 2>&1
start "" http://localhost:5000
.venv\Scripts\python app.py
