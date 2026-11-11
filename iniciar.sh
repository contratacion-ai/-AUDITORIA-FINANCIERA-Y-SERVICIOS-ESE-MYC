#!/usr/bin/env bash
# Inicia la herramienta en el equipo servidor de la ESE (Linux).
set -e
cd "$(dirname "$0")"
if [ ! -d .venv ]; then
    python3 -m venv .venv
    . .venv/bin/activate
    pip install -r requirements.txt
else
    . .venv/bin/activate
fi
exec streamlit run app.py
