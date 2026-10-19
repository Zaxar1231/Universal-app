#!/usr/bin/env bash
cd "$(dirname "$0")"
[ -d .venv ] || { python3 -m venv .venv && .venv/bin/pip install -r requirements.txt; }
# сайты постоянно меняются, поэтому yt-dlp обновляем при каждом запуске (без интернета просто пропустится)
.venv/bin/pip install -q -U yt-dlp >/dev/null 2>&1 || true
.venv/bin/python app.py
