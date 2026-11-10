<#
  Prepara un paquete para instalar SIN Internet (por ejemplo, equipos de la ESE con navegación restringida).
  Ejecútelo en un equipo con Internet (no requiere administrador). Crea instalador\windows\offline\ con:
    - Python embebido, get-pip.py y todas las librerías (.whl) para Windows de 64 bits.
  Luego copie la carpeta completa del proyecto (con "offline") al equipo destino y ejecute INSTALAR_WINDOWS.bat.
  Requiere un Python instalado en el equipo con Internet solo para descargar las librerías (comando "py" o "python").
#>
$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"
try { [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12 } catch {}

$PyVersion = "3.12.10"
$PyZip = "python-$PyVersion-embed-amd64.zip"
$Origen = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$Offline = Join-Path $PSScriptRoot "offline"
$Wheels = Join-Path $Offline "wheels"
New-Item -ItemType Directory -Force -Path $Wheels | Out-Null

Invoke-WebRequest -Uri "https://www.python.org/ftp/python/$PyVersion/$PyZip" -OutFile (Join-Path $Offline $PyZip) -UseBasicParsing
Invoke-WebRequest -Uri "https://bootstrap.pypa.io/get-pip.py" -OutFile (Join-Path $Offline "get-pip.py") -UseBasicParsing

$py = (Get-Command py -ErrorAction SilentlyContinue)
$args_py = @()
if ($py) { $exe = "py"; $args_py = @("-3") } else { $exe = "python" }
& $exe @args_py -m pip download --only-binary=:all: --platform win_amd64 --python-version 3.12 `
    -d $Wheels -r (Join-Path $Origen "requirements.txt") pip setuptools wheel
if ($LASTEXITCODE -ne 0) { throw "No se pudieron descargar las librerías." }
Write-Host "Paquete sin Internet listo en: $Offline" -ForegroundColor Green
