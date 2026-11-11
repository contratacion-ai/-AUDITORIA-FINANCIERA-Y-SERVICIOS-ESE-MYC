@echo off
chcp 65001 >nul
cd /d "%~dp0app"
title Control ESE Moreno y Clavijo - no cierre esta ventana mientras use la aplicacion
set "ESE_CONTROL_DATOS=%~dp0datos"
set "PYTHONUTF8=1"
set "PYTHONDONTWRITEBYTECODE="
echo Iniciando... se abrira el navegador en http://localhost:8501
echo Desde otros equipos de la red interna: http://NOMBRE-O-IP-DE-ESTE-EQUIPO:8501
echo Para detener la aplicacion, cierre esta ventana.
echo.
start "" /b cmd /c "timeout /t 6 /nobreak >nul & start http://localhost:8501"
"%~dp0python\python.exe" -m streamlit run app.py
if errorlevel 1 (
    echo.
    echo La aplicacion se detuvo con un error. Si el puerto 8501 esta ocupado, cierre otra ventana de la
    echo aplicacion y vuelva a abrirla.
    pause
)
