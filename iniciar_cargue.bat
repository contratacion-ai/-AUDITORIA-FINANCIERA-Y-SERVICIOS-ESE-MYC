@echo off
REM ==========================================================================
REM  Inicia el cargue de RIPS. Uso:
REM    - Doble clic: abre una ventana para elegir el ZIP, los TXT o la carpeta.
REM    - Arrastrar y soltar un ZIP, carpeta o TXT sobre este archivo.
REM  Resultados en la carpeta "salida" de la aplicacion.
REM ==========================================================================
setlocal
chcp 65001 >nul
cd /d "%~dp0"
title Cargue de RIPS - Auditoria financiera
if not exist ".venv\Scripts\python.exe" (
    echo  La aplicacion no esta instalada. Ejecute primero instalar.bat
    pause
    exit /b 1
)
".venv\Scripts\python.exe" -m rips_auditoria.cargue %*
echo.
pause
endlocal
