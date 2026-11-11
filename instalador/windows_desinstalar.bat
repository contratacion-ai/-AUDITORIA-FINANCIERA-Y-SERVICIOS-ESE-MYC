@echo off
chcp 65001 >nul
set "DEST=%LOCALAPPDATA%\ControlESE"
echo Se eliminara la aplicacion de %DEST%
echo ATENCION: tambien se borra la carpeta "datos" (base de datos y archivos cargados).
echo Haga antes una copia de %DEST%\datos si necesita conservarla.
set /p R="Escriba BORRAR para continuar: "
if /i not "%R%"=="BORRAR" exit /b 0
del "%USERPROFILE%\Desktop\Control ESE Moreno y Clavijo.lnk" 2>nul
del "%APPDATA%\Microsoft\Windows\Start Menu\Programs\Control ESE Moreno y Clavijo.lnk" 2>nul
cd /d "%TEMP%"
rmdir /s /q "%DEST%"
echo Listo.
pause
