@echo off
chcp 65001 >nul
cd /d "%~dp0"
title Control ESE Moreno y Clavijo (no cierre esta ventana mientras use la aplicacion)
if not exist ".venv\Scripts\python.exe" (
    echo Primero ejecute instalar.bat
    pause
    exit /b 1
)
echo Iniciando... se abrira el navegador en http://localhost:8501
echo Desde otros equipos de la red: http://NOMBRE-O-IP-DE-ESTE-EQUIPO:8501
echo Para detener la aplicacion cierre esta ventana.
start "" "http://localhost:8501"
".venv\Scripts\python.exe" -m streamlit run app.py
