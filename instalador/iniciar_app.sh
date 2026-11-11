#!/usr/bin/env bash
cd "$(dirname "$0")"
echo "Abra http://localhost:8501 en el navegador"
exec .venv/bin/python -m streamlit run app.py
