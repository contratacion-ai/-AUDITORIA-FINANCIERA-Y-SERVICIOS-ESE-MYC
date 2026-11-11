@echo off
chcp 65001 >nul
setlocal EnableDelayedExpansion
cd /d "%~dp0"
title Instalador - Control de gastos ESE Moreno y Clavijo
echo.
echo ===============================================================
echo   Control de servicios publicos, combustible y mantenimientos
echo   ESE Moreno y Clavijo  -  Instalador
echo ===============================================================
echo.

rem --- 1. Buscar Python 3.10 o superior
set "PY="
for %%C in ("py -3" "python") do (
    if not defined PY (
        %%~C -c "import sys; sys.exit(0 if sys.version_info>=(3,10) else 1)" >nul 2>&1 && set "PY=%%~C"
    )
)
if not defined PY (
    echo No se encontro Python 3.10 o superior en este equipo.
    echo.
    set /p RESP="Desea instalarlo ahora con winget? [S/N]: "
    if /i "!RESP!"=="S" (
        winget install -e --id Python.Python.3.12 --accept-package-agreements --accept-source-agreements
        echo.
        echo Python instalado. CIERRE esta ventana y vuelva a ejecutar instalar.bat.
    ) else (
        echo Instale Python desde https://www.python.org/downloads/ marcando la casilla
        echo "Add python.exe to PATH" y vuelva a ejecutar instalar.bat.
    )
    pause
    exit /b 1
)
echo [1/4] Python encontrado: %PY%

rem --- 2. Entorno virtual y dependencias
if not exist ".venv\Scripts\python.exe" (
    echo [2/4] Creando el entorno de la aplicacion...
    %PY% -m venv .venv || goto :error
) else (
    echo [2/4] Entorno existente, se reutiliza.
)
echo [3/4] Instalando dependencias (puede tardar unos minutos la primera vez)...
".venv\Scripts\python.exe" -m pip install --quiet --upgrade pip
".venv\Scripts\python.exe" -m pip install --quiet -r requirements.txt || goto :error

rem --- 3. Datos de demostracion
if exist "demo\control_ese.db" if not exist "datos\control_ese.db" (
    echo.
    echo Hay datos de DEMOSTRACION (cifras ficticias) para conocer la herramienta.
    set /p DEMO="Desea cargarlos? [S/N]: "
    if /i "!DEMO!"=="S" (
        mkdir datos >nul 2>&1
        copy /y "demo\control_ese.db" "datos\control_ese.db" >nul
        echo.
        echo Usuarios de prueba:
        echo    admin  /  Demo2026*   ^(area central, ve toda la ESE^)
        echo    fortul /  Demo2026*   ^(usuario de sede, solo Hospital de Fortul^)
    )
)

rem --- 4. Acceso directo en el escritorio
echo [4/4] Creando acceso directo en el escritorio...
powershell -NoProfile -Command "$s=(New-Object -ComObject WScript.Shell).CreateShortcut([Environment]::GetFolderPath('Desktop')+'\Control ESE Moreno y Clavijo.lnk'); $s.TargetPath='%~dp0iniciar_app.bat'; $s.WorkingDirectory='%~dp0'; $s.Save()" >nul 2>&1

echo.
echo Instalacion terminada.
set /p GO="Desea iniciar la aplicacion ahora? [S/N]: "
if /i "!GO!"=="S" call "%~dp0iniciar_app.bat"
exit /b 0

:error
echo.
echo La instalacion fallo. Revise la conexion a internet (se necesita para
echo descargar las dependencias) y vuelva a ejecutar instalar.bat.
pause
exit /b 1
