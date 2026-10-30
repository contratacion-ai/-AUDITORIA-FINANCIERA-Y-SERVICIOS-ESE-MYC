@echo off
REM Inicia la interfaz web local en http://127.0.0.1:8000 (Windows)
cd /d "%~dp0\.."
python -m uvicorn api.app:app --host 127.0.0.1 --port 8000
pause
