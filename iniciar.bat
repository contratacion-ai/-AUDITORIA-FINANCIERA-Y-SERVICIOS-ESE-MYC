@echo off
rem Inicia la herramienta en el equipo servidor de la ESE (Windows).
cd /d "%~dp0"
if not exist .venv (
    python -m venv .venv
    call .venv\Scripts\activate.bat
    pip install -r requirements.txt
) else (
    call .venv\Scripts\activate.bat
)
streamlit run app.py
