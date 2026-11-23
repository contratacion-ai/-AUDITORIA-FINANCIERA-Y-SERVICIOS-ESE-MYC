@echo off
chcp 65001 >nul
title Auditoria ESE Moreno y Clavijo
cd /d "%~dp0"
echo ============================================================
echo   AUDITORIA DE FACTURACION - ESE MORENO Y CLAVIJO
echo ============================================================
set PY=
py -3 --version >nul 2>&1 && set PY=py -3
if "%PY%"=="" (python --version >nul 2>&1 && set PY=python)
if "%PY%"=="" (
  echo.
  echo [!] No se encontro Python en este equipo.
  echo     1. Descargue Python 3.11 o superior desde https://www.python.org/downloads/
  echo     2. En el instalador MARQUE la casilla "Add python.exe to PATH"
  echo     3. Vuelva a abrir este archivo.
  echo.
  pause
  exit /b 1
)
if not exist ".venv\Scripts\python.exe" (
  echo Preparando entorno ^(solo la primera vez, requiere internet, 2-5 min^)...
  %PY% -m venv .venv || goto :error
  ".venv\Scripts\python.exe" -m pip install --upgrade pip >nul
  ".venv\Scripts\python.exe" -m pip install -r requirements.txt || goto :error
)
echo.
echo Iniciando... El navegador se abrira en http://localhost:8000
echo NO cierre esta ventana mientras use la herramienta. Para salir: Ctrl+C o cierre la ventana.
start "" cmd /c "timeout /t 4 >nul & start http://localhost:8000"
".venv\Scripts\python.exe" -m uvicorn app.main:app --host 127.0.0.1 --port 8000
pause
exit /b 0
:error
echo.
echo [!] Ocurrio un error instalando. Verifique su conexion a internet y vuelva a intentar.
pause
exit /b 1
