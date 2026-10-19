#!/bin/sh
# Запуск Universal: свои чистые Python-библиотеки идут раньше системных.
export PYTHONPATH="/usr/lib/universal/vendor${PYTHONPATH:+:$PYTHONPATH}"
export PYTHONDONTWRITEBYTECODE=1
exec python3 /usr/lib/universal/desktop.py "$@"
