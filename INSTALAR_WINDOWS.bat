@echo off
REM ==========================================================================
REM  Instalador de Auditoria ESE Moreno y Clavijo (Windows 10/11, 64 bits)
REM  - No requiere permisos de administrador (se instala en la carpeta del usuario)
REM  - Usa su propio Python aislado: no afecta otras herramientas ni programas
REM  - Volver a ejecutarlo ACTUALIZA la herramienta y conserva los datos
REM ==========================================================================
title Instalando Auditoria ESE Moreno y Clavijo
echo.
echo  Instalando Auditoria ESE Moreno y Clavijo (no requiere administrador)...
echo  Este proceso descarga unos 60 MB la primera vez. Espere por favor.
echo.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0instalador\windows\instalar.ps1" %*
if errorlevel 1 (
  echo.
  echo  *** La instalacion no termino. Revise el mensaje anterior. ***
  echo  Si el equipo no tiene Internet, use instalador\windows\preparar_offline.ps1 en otro equipo.
  echo.
)
pause
