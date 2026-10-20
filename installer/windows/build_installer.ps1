<#
  Construye el instalador de Windows (Instalador_CosteoABC_Ambulancias_<version>.exe).

  Requisitos en el equipo de construcción (Windows 10/11 x64):
    - PowerShell 5.1+ y acceso a internet (descarga Python embebido y paquetes).
    - Inno Setup 6 (ISCC.exe). Si no está: winget install JRSoftware.InnoSetup
  Uso:
    powershell -ExecutionPolicy Bypass -File installer\windows\build_installer.ps1 [-Version 1.0.0] [-ConPostgres]

  Resultado: installer\windows\salida\Instalador_CosteoABC_Ambulancias_<version>.exe
  El instalador incluye su propio Python (no requiere instalar Python en el servidor).
#>
param(
  [string]$Version = "1.0.0",
  [string]$PythonVersion = "3.12.10",
  [switch]$ConPostgres
)
$ErrorActionPreference = "Stop"
$Aqui = $PSScriptRoot
$Repo = Resolve-Path (Join-Path $Aqui "..\..")
$Build = Join-Path $Aqui "build"
$Salida = Join-Path $Aqui "salida"

Remove-Item -Recurse -Force $Build -ErrorAction SilentlyContinue
New-Item -ItemType Directory -Force -Path $Build, $Salida | Out-Null

# 1. Python embebido (oficial de python.org)
$Zip = Join-Path $Build "python.zip"
$Url = "https://www.python.org/ftp/python/$PythonVersion/python-$PythonVersion-embed-amd64.zip"
Write-Host "Descargando $Url"
Invoke-WebRequest -Uri $Url -OutFile $Zip
Expand-Archive $Zip -DestinationPath (Join-Path $Build "python")
Remove-Item $Zip

# Habilitar site-packages en el Python embebido
$Pth = Get-ChildItem (Join-Path $Build "python") -Filter "python3*._pth" | Select-Object -First 1
$lineas = Get-Content $Pth.FullName | ForEach-Object { if ($_ -eq "#import site") { "import site" } else { $_ } }
$lineas += "Lib\site-packages"
$lineas += "..\app"   # en modo ._pth la carpeta del script no se agrega sola a sys.path
Set-Content -Path $Pth.FullName -Value $lineas -Encoding ASCII

# 2. pip y dependencias
$Py = Join-Path $Build "python\python.exe"
Invoke-WebRequest -Uri "https://bootstrap.pypa.io/get-pip.py" -OutFile (Join-Path $Build "get-pip.py")
& $Py (Join-Path $Build "get-pip.py") --no-warn-script-location
if ($LASTEXITCODE -ne 0) { throw "Falló get-pip" }
$Req = if ($ConPostgres) { "requirements-postgres.txt" } else { "requirements.txt" }
& $Py -m pip install --no-warn-script-location -r (Join-Path $Repo "ambulancias_abc\$Req")
if ($LASTEXITCODE -ne 0) { throw "Falló la instalación de dependencias" }
Remove-Item (Join-Path $Build "get-pip.py")

# 3. Aplicación (sin bases de datos ni configuraciones locales)
$App = Join-Path $Build "app"
robocopy (Join-Path $Repo "ambulancias_abc") $App /E /XD __pycache__ datos .pytest_cache /XF *.sqlite3 ambulancias.ini *.log | Out-Null
if ($LASTEXITCODE -ge 8) { throw "Falló la copia de la aplicación" }

# 4. Scripts y documentación
New-Item -ItemType Directory -Force -Path (Join-Path $Build "scripts"), (Join-Path $Build "docs") | Out-Null
Copy-Item (Join-Path $Aqui "configurar.ps1"), (Join-Path $Aqui "desinstalar.ps1"), (Join-Path $Aqui "habilitar_red.ps1"), (Join-Path $Aqui "probar_instalacion.ps1") (Join-Path $Build "scripts")
Copy-Item (Join-Path $Repo "docs\ambulancias_abc\*") (Join-Path $Build "docs")

# 5. Verificación rápida con el Python embebido
Push-Location $App
$env:ABC_DATA_DIR = Join-Path $Build "verificacion"
& $Py manage.py check
if ($LASTEXITCODE -ne 0) { Pop-Location; throw "manage.py check falló" }
& $Py -m unittest discover -s abc_engine -t .
if ($LASTEXITCODE -ne 0) { Pop-Location; throw "Pruebas del motor ABC fallaron" }
Pop-Location
Remove-Item -Recurse -Force $env:ABC_DATA_DIR -ErrorAction SilentlyContinue
Remove-Item Env:\ABC_DATA_DIR

# 6. Compilar con Inno Setup
$Iscc = (Get-Command ISCC.exe -ErrorAction SilentlyContinue).Source
if (-not $Iscc) {
  $Iscc = @("${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe", "$env:ProgramFiles\Inno Setup 6\ISCC.exe", "$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe") |
    Where-Object { Test-Path $_ } | Select-Object -First 1
}
if (-not $Iscc) { throw "No se encontró Inno Setup 6 (ISCC.exe). Instálelo con: winget install JRSoftware.InnoSetup" }
& $Iscc "/DVersionApp=$Version" "/DDirBuild=$Build" "/O$Salida" (Join-Path $Aqui "costeo_abc_ambulancias.iss")
if ($LASTEXITCODE -ne 0) { throw "Inno Setup falló" }
Write-Host "Instalador generado en $Salida"
