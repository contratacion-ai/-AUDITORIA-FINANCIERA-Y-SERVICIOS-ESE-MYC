@echo off
rem ===========================================================================
rem  Permite que otros computadores de la red de la ESE abran el Cargue RIPS
rem  (puertos 8100-8110) y el aplicativo de Auditoria (puente, 8000-8010).
rem  Solo redes privadas o de dominio (no redes publicas). Pide administrador.
rem ===========================================================================
net session >nul 2>&1
if errorlevel 1 (
  powershell -NoProfile -Command "Start-Process -FilePath '%~f0' -Verb RunAs"
  exit /b
)
set REGLA=ESE MyC - Cargue RIPS y Auditoria
netsh advfirewall firewall delete rule name="%REGLA%" >nul 2>&1
netsh advfirewall firewall add rule name="%REGLA%" dir=in action=allow protocol=TCP localport=8000-8010,8100-8110 profile=domain,private
if errorlevel 1 (
  echo No se pudo crear la regla del Firewall de Windows.
) else (
  echo Listo: los equipos de la red ya pueden abrir el Cargue RIPS y el aplicativo de Auditoria.
  echo Inicie el acceso "Cargue RIPS ESE MyC (en red)" y comparta la direccion y la clave que muestra.
)
pause
