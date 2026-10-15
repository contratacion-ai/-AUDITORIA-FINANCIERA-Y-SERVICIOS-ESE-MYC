@echo off
REM ==========================================================================
REM  Instalador - Auditoria RIPS (Res. 3374 de 2000) - ESE Moreno y Clavijo
REM  Doble clic para instalar. Requiere Python 3.10 o superior
REM  (https://www.python.org/downloads/ - marcar "Add python.exe to PATH").
REM ==========================================================================
setlocal
chcp 65001 >nul
cd /d "%~dp0"
title Instalador Auditoria RIPS

echo.
echo  === Instalacion de la aplicacion de auditoria RIPS ===
echo.

REM --- 1. Buscar Python 3.10+ (primero el lanzador "py", luego "python") ---
set "PY="
py -3 -c "import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)" >nul 2>nul && set "PY=py -3"
if not defined PY (
    python -c "import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)" >nul 2>nul && set "PY=python"
)
if not defined PY (
    echo  [ERROR] No se encontro Python 3.10 o superior.
    echo.
    echo  1. Descarguelo de https://www.python.org/downloads/
    echo  2. En el instalador marque "Add python.exe to PATH"
    echo  3. Vuelva a ejecutar instalar.bat
    echo.
    pause
    exit /b 1
)
for /f "delims=" %%v in ('%PY% -c "import platform; print(platform.python_version())"') do set "PYVER=%%v"
echo  [1/4] Python %PYVER% encontrado.

REM --- 2. Entorno virtual propio de la aplicacion (no altera otros programas) ---
if not exist ".venv\Scripts\python.exe" (
    echo  [2/4] Creando entorno de la aplicacion en .venv ...
    %PY% -m venv .venv
    if errorlevel 1 (
        echo  [ERROR] No se pudo crear el entorno virtual.
        pause
        exit /b 1
    )
) else (
    echo  [2/4] Entorno .venv ya existe; se actualizara.
)

REM --- 3. Dependencias (pandas, openpyxl, XlsxWriter, PyYAML) ---
echo  [3/4] Instalando dependencias (requiere internet, puede tardar unos minutos) ...
".venv\Scripts\python.exe" -m pip install --upgrade pip --quiet
".venv\Scripts\python.exe" -m pip install -r requirements.txt --quiet
if errorlevel 1 (
    echo  [ERROR] Fallo la instalacion de dependencias. Revise la conexion a internet
    echo          o la configuracion de proxy de la red institucional.
    pause
    exit /b 1
)

REM --- 4. Verificacion y acceso directo en el escritorio ---
".venv\Scripts\python.exe" -m rips_auditoria --version
if errorlevel 1 (
    echo  [ERROR] La aplicacion no responde despues de instalar.
    pause
    exit /b 1
)
echo  [4/4] Creando acceso directo "Cargue RIPS" en el escritorio ...
powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "$d=[Environment]::GetFolderPath('Desktop'); $s=(New-Object -ComObject WScript.Shell).CreateShortcut((Join-Path $d 'Cargue RIPS.lnk')); $s.TargetPath='%~dp0iniciar_cargue.bat'; $s.WorkingDirectory='%~dp0'; $s.Description='Cargue y auditoria de RIPS Res. 3374'; $s.Save()" >nul 2>nul
if errorlevel 1 echo  (No se pudo crear el acceso directo; use iniciar_cargue.bat directamente.)

echo.
echo  === Instalacion completada ===
echo.
echo  Para cargar RIPS:
echo    - Doble clic en "Cargue RIPS" (escritorio) o en iniciar_cargue.bat, o
echo    - Arrastre el ZIP / carpeta / TXT sobre iniciar_cargue.bat
echo.
choice /c SN /m "Desea iniciar un cargue ahora"
if errorlevel 2 goto fin
call "%~dp0iniciar_cargue.bat"
:fin
endlocal
