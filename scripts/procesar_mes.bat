@echo off
REM Arrastre una carpeta (o ZIP/RAR) de RIPS sobre este archivo para procesarla (Windows).
cd /d "%~dp0\.."
python scripts\procesar_carpeta.py %* --pdf
pause
