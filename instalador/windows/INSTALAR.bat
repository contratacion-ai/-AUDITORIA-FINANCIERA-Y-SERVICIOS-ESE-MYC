@echo off
rem Doble clic para instalar Costos ABC (pide permisos de administrador).
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0instalar.ps1" %*
pause
