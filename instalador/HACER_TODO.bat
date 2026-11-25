@echo off
chcp 65001 >nul
title Cargue RIPS ESE MyC - instalar, vincular Drive y enviar al aplicativo
cd /d "%~dp0"
set "INST=%LOCALAPPDATA%\Programs\CargueRIPS_ESE_MyC"
set "AUD=%LOCALAPPDATA%\Programs\AuditoriaESE_MyC"

echo [1/4] Instalando Cargue RIPS (cierra la version anterior si esta abierta) ...
for %%F in ("Instalador_Cargue_RIPS_ESE_MyC_v*.exe") do set "EXE=%%F"
if not defined EXE (
  echo No se encontro el instalador junto a este archivo. Descomprima todo el ZIP en una carpeta.
  pause & exit /b 1
)
start /wait "" "%EXE%" /S
if not exist "%INST%\iniciar.py" (
  echo [!] La instalacion no se completo. Ejecute "%EXE%" con doble clic.
  pause & exit /b 1
)

echo [2/4] Abriendo el aplicativo de Auditoria de Facturacion ...
if exist "%AUD%\iniciar.py" (
  start "Auditoria ESE MyC" /min "%AUD%\python\python.exe" "%AUD%\iniciar.py"
) else (
  echo     No se encontro el aplicativo de Auditoria instalado: los meses quedaran pendientes de envio.
)

echo [3/4] Abriendo la ventana de cargue ...
start "CARGUE DE RIPS" "%INST%\python\python.exe" "%INST%\iniciar.py"

echo [4/4] Vinculando la carpeta de Google Drive y sincronizando ...
powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "$ok=$false; for($i=0;$i -lt 90 -and -not $ok;$i++){ foreach($p in 8100..8110){ try{ $e=Invoke-RestMethod -TimeoutSec 3 ('http://127.0.0.1:'+$p+'/api/drive/estado'); $ok=$true; break }catch{} }; if(-not $ok){Start-Sleep 2} };" ^
  "if(-not $ok){ Write-Host '[!] La ventana de cargue no respondio.'; exit 1 };" ^
  "if(-not $e.config.carpeta_local){ Write-Host '[!] No se encontro la carpeta Rips TXT y JSON 2026 en Google Drive: abra la ventana y pulse Buscar la carpeta.'; exit 1 };" ^
  "Write-Host ('    Carpeta: ' + $e.config.carpeta_local);" ^
  "if(-not $e.en_curso){ Invoke-RestMethod -Method Post -ContentType 'application/json' -Body '{}' ('http://127.0.0.1:'+$p+'/api/drive/sincronizar') | Out-Null };" ^
  "Write-Host '    Sincronizacion iniciada: siga el avance en la seccion 3 Avance de la ventana.'"

echo.
echo Listo. NO cierre la ventana negra "CARGUE DE RIPS" mientras sincroniza.
echo La primera vez tarda varios minutos; luego revisa Drive cada hora y envia lo nuevo al aplicativo.
pause
