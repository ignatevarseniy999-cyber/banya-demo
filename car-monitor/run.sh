#!/usr/bin/env bash
set -e
cd "$(dirname "$0")"
if [ ! -d .venv ]; then
    python3 -m venv .venv
    .venv/bin/pip install -r requirements.txt
    .venv/bin/python -m playwright install chromium
fi
if [ ! -f .env ]; then
    cp .env.example .env
    echo "Впишите токен бота в файл .env и запустите ./run.sh ещё раз."
    exit 0
fi
exec .venv/bin/python monitor.py
