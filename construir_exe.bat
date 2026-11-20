@echo off
REM ==========================================================================
REM  Genera el ejecutable CargueRIPS.exe (requiere haber corrido instalar.bat).
REM  Resultado: dist\CargueRIPS\  y  dist\CargueRIPS_v<version>.zip
REM  Ese ZIP se copia a los demas equipos: no necesitan Python.
REM ==========================================================================
setlocal
chcp 65001 >nul
cd /d "%~dp0"
title Construir CargueRIPS.exe
if not exist ".venv\Scripts\python.exe" (
    echo  Ejecute primero instalar.bat
    pause
    exit /b 1
)
echo  [1/3] Instalando PyInstaller en el entorno de la aplicacion ...
".venv\Scripts\python.exe" -m pip install "pyinstaller>=6" --quiet
if errorlevel 1 (
    echo  [ERROR] No se pudo instalar PyInstaller. Revise internet o proxy.
    pause
    exit /b 1
)
echo  [2/3] Construyendo el ejecutable (tarda unos minutos) ...
".venv\Scripts\python.exe" -m PyInstaller --noconfirm --clean empaquetado\CargueRIPS.spec
if errorlevel 1 (
    echo  [ERROR] Fallo la construccion. Revise los mensajes anteriores.
    pause
    exit /b 1
)
for /f "delims=" %%v in ('".venv\Scripts\python.exe" -c "import rips_auditoria; print(rips_auditoria.__version__)"') do set "VER=%%v"
echo  [3/3] Comprimiendo dist\CargueRIPS_v%VER%.zip ...
powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "Compress-Archive -Path 'dist\CargueRIPS' -DestinationPath 'dist\CargueRIPS_v%VER%.zip' -Force"
echo.
echo  === Listo ===
echo  Ejecutable:  %~dp0dist\CargueRIPS\CargueRIPS.exe
echo  Para otros equipos copie y descomprima dist\CargueRIPS_v%VER%.zip
echo  (en una carpeta del usuario, p. ej. Documentos, no en Archivos de programa).
echo.
pause
endlocal
