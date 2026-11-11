@echo off
chcp 65001 >nul
setlocal EnableDelayedExpansion
cd /d "%~dp0"
title Instalador - Control de gastos ESE Moreno y Clavijo
set "DEST=%LOCALAPPDATA%\ControlESE"
echo.
echo ===============================================================
echo   Control de servicios publicos, combustible y mantenimientos
echo   ESE Moreno y Clavijo  -  Instalador (no requiere internet)
echo ===============================================================
echo.
echo Se instalara en: %DEST%
echo (No necesita permisos de administrador ni tener Python instalado.)
echo.

if not exist "python\python.exe" (
    echo ERROR: falta la carpeta "python". Descomprima TODO el ZIP antes de instalar.
    pause & exit /b 1
)

rem --- Si la aplicacion esta abierta, hay que cerrarla antes de actualizar
tasklist /fi "imagename eq python.exe" 2>nul | find /i "python.exe" >nul
if not errorlevel 1 (
    echo Hay programas Python abiertos. Si la aplicacion Control ESE esta abierta, cierre su ventana negra.
    pause
)

echo [1/4] Copiando la aplicacion...
robocopy "app" "%DEST%\app" /MIR /NFL /NDL /NJH /NJS /NP >nul
if errorlevel 8 goto :error
echo [2/4] Copiando el motor de Python y las librerias (puede tardar un par de minutos)...
robocopy "python" "%DEST%\python" /MIR /NFL /NDL /NJH /NJS /NP >nul
if errorlevel 8 goto :error
copy /y "iniciar_app.bat" "%DEST%\iniciar_app.bat" >nul
copy /y "desinstalar.bat" "%DEST%\desinstalar.bat" >nul
copy /y "LEEME_PRIMERO.txt" "%DEST%\LEEME_PRIMERO.txt" >nul
if not exist "%DEST%\datos" mkdir "%DEST%\datos"

echo [3/4] Verificando la instalacion...
"%DEST%\python\python.exe" -c "import streamlit, pandas, plotly, openpyxl, pdfplumber; print('   Librerias correctas')" 2>nul
if errorlevel 1 (
    echo.
    echo ERROR: las librerias no cargan. Lo mas comun es que falte el paquete
    echo "Microsoft Visual C++ Redistributable 2015-2022 (x64)". Instalelo desde
    echo   https://aka.ms/vs/17/release/vc_redist.x64.exe
    echo y vuelva a ejecutar este instalador.
    pause & exit /b 1
)

if exist "demo\control_ese.db" if not exist "%DEST%\datos\control_ese.db" (
    echo.
    echo Hay datos de DEMOSTRACION (cifras ficticias) para conocer la herramienta.
    set /p DEMO="Desea cargarlos? [S/N]: "
    if /i "!DEMO!"=="S" (
        copy /y "demo\control_ese.db" "%DEST%\datos\control_ese.db" >nul
        echo.
        echo Usuarios de prueba:
        echo    admin  /  Demo2026*   ^(area central: ve toda la ESE^)
        echo    fortul /  Demo2026*   ^(usuario de sede: solo Hospital de Fortul^)
    )
)

echo [4/4] Creando accesos directos...
powershell -NoProfile -Command "$w=New-Object -ComObject WScript.Shell; foreach($d in @([Environment]::GetFolderPath('Desktop'), [Environment]::GetFolderPath('Programs'))){ $s=$w.CreateShortcut($d+'\Control ESE Moreno y Clavijo.lnk'); $s.TargetPath='%DEST%\iniciar_app.bat'; $s.WorkingDirectory='%DEST%'; $s.IconLocation='%SystemRoot%\System32\shell32.dll,13'; $s.Save() }" >nul 2>&1

echo.
echo Instalacion terminada. Use el acceso directo "Control ESE Moreno y Clavijo" del escritorio.
set /p GO="Desea iniciar la aplicacion ahora? [S/N]: "
if /i "!GO!"=="S" start "" "%DEST%\iniciar_app.bat"
exit /b 0

:error
echo.
echo La copia de archivos fallo. Cierre la aplicacion si esta abierta y vuelva a intentarlo.
pause
exit /b 1
