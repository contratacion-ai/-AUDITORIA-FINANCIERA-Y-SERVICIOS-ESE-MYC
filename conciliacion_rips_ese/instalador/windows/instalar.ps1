<#
  Instalador de la herramienta de Auditoría ESE Moreno y Clavijo para Windows 10/11.

  - NO requiere permisos de administrador: instala en %LOCALAPPDATA%\ConciliacionRIPS_ESE (carpeta del usuario).
  - NO genera conflictos: usa un Python "embebido" propio, aislado; no modifica el PATH, el registro,
    el Python que ya tenga el equipo ni otras herramientas. Los accesos directos se crean solo para el usuario.
  - Si se ejecuta de nuevo, ACTUALIZA la aplicación y conserva los datos (auditorías, archivos cargados).
  - Instalación sin Internet: ejecute antes preparar_offline.ps1 en un equipo con Internet y copie la
    carpeta "offline" junto a este script.
#>
param(
    [string]$Destino = (Join-Path $env:LOCALAPPDATA "ConciliacionRIPS_ESE"),
    [switch]$SinAccesos,
    [switch]$NoIniciar
)

$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"
try { [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12 } catch {}

$PyVersion = "3.12.10"
$PyZip     = "python-$PyVersion-embed-amd64.zip"
$PyUrl     = "https://www.python.org/ftp/python/$PyVersion/$PyZip"
$GetPipUrl = "https://bootstrap.pypa.io/get-pip.py"

$Origen  = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path   # raíz del proyecto
$Offline = Join-Path $PSScriptRoot "offline"
$PyDir   = Join-Path $Destino "python"
$AppDir  = Join-Path $Destino "app"
$Datos   = Join-Path $Destino "datos"
$Temp    = Join-Path $Destino "temp_instalacion"

function Paso($texto) { Write-Host ""; Write-Host "==> $texto" -ForegroundColor Cyan }

if (-not [Environment]::Is64BitOperatingSystem) {
    throw "Se requiere Windows de 64 bits."
}

Write-Host "Instalando Conciliación RIPS - ESE Moreno y Clavijo en: $Destino"
Write-Host "(instalación por usuario, sin permisos de administrador)"
New-Item -ItemType Directory -Force -Path $Destino, $Datos, $Temp | Out-Null

# 1. Cerrar SOLO una instancia anterior de esta herramienta (procesos del Python propio)
Paso "Cerrando versiones anteriores de la herramienta (si están abiertas)"
Get-Process -ErrorAction SilentlyContinue | Where-Object {
    $_.Path -and $_.Path.StartsWith($PyDir, [StringComparison]::OrdinalIgnoreCase)
} | Stop-Process -Force -ErrorAction SilentlyContinue

# 2-4. Python propio (aislado) con sus librerías
$PythonListo = Join-Path $Offline "python_listo"
$PythonExe = Join-Path $PyDir "python.exe"
$PythonZipListo = Join-Path $Offline "python.zip"
if (Test-Path $PythonZipListo) {
    # Paquete "todo incluido": Python y librerías comprimidos en un solo archivo para evitar el límite
    # de 260 caracteres de Windows al descomprimir. Se abre directamente en la carpeta de destino (ruta corta).
    Paso "Copiando Python y librerías incluidos en el paquete (sin Internet, puede tardar un minuto)"
    if (Test-Path $PyDir) { Remove-Item -Recurse -Force $PyDir }
    Add-Type -AssemblyName System.IO.Compression.FileSystem
    [System.IO.Compression.ZipFile]::ExtractToDirectory($PythonZipListo, $PyDir)
} elseif (Test-Path (Join-Path $PythonListo "python.exe")) {
    # Paquete "todo incluido" (construir_paquete_windows.sh): Python y librerías ya preparados.
    # Solo se copian archivos: no se descarga nada ni se necesita Internet.
    Paso "Copiando Python y librerías incluidos en el paquete (sin Internet)"
    if (Test-Path $PyDir) { Remove-Item -Recurse -Force $PyDir }
    Copy-Item -Recurse -Force $PythonListo $PyDir
} else {
    # 2. Python embebido propio (aislado)
    $MarcaVersion = Join-Path $PyDir "VERSION_AUDITORIA_ESE.txt"
    $PythonExe = Join-Path $PyDir "python.exe"
    if (-not (Test-Path $PythonExe) -or -not (Test-Path $MarcaVersion) -or ((Get-Content $MarcaVersion -Raw).Trim() -ne $PyVersion)) {
        Paso "Preparando Python $PyVersion privado de la herramienta"
        if (Test-Path $PyDir) { Remove-Item -Recurse -Force $PyDir }
        $ZipLocal = Join-Path $Offline $PyZip
        $ZipTemp = Join-Path $Temp $PyZip
        if (Test-Path $ZipLocal) {
            Copy-Item $ZipLocal $ZipTemp -Force
        } else {
            Write-Host "Descargando $PyUrl"
            Invoke-WebRequest -Uri $PyUrl -OutFile $ZipTemp -UseBasicParsing
        }
        Expand-Archive -Path $ZipTemp -DestinationPath $PyDir -Force

        # Habilita site-packages y la carpeta de la aplicación. El archivo ._pth aísla este Python:
        # ignora PYTHONPATH y cualquier otra instalación de Python del equipo.
        $Pth = Get-ChildItem $PyDir -Filter "python*._pth" | Select-Object -First 1
        $Zip = (Get-ChildItem $PyDir -Filter "python3*.zip" | Select-Object -First 1).Name
        $Contenido = @($Zip, ".", "Lib\site-packages", "..\app", "import site")
        [IO.File]::WriteAllLines($Pth.FullName, $Contenido)
        Set-Content -Path $MarcaVersion -Value $PyVersion -Encoding ASCII
    }

    # 3. pip dentro del Python propio
    $PipOpciones = @("--no-warn-script-location", "--disable-pip-version-check")
    $Wheels = Join-Path $Offline "wheels"
    $UsarOffline = Test-Path $Wheels
    if ($UsarOffline) { $PipOpciones += @("--no-index", "--find-links", $Wheels) }

    # (Se verifica la carpeta en lugar de ejecutar "pip --version": en PowerShell 5.1 la salida de error
    #  redirigida de un programa externo detendría el script.)
    if (-not (Test-Path (Join-Path $PyDir "Lib\site-packages\pip"))) {
        Paso "Instalando pip (gestor de paquetes) en el Python privado"
        $GetPip = Join-Path $Offline "get-pip.py"
        if (-not (Test-Path $GetPip)) {
            $GetPip = Join-Path $Temp "get-pip.py"
            Invoke-WebRequest -Uri $GetPipUrl -OutFile $GetPip -UseBasicParsing
        }
        & $PythonExe $GetPip @PipOpciones
        if ($LASTEXITCODE -ne 0) { throw "No se pudo instalar pip." }
    }

    # 4. Librerías de la aplicación (solo paquetes binarios: no requiere compiladores)
    Paso "Instalando librerías (Django, pandas, openpyxl, ...)"
    & $PythonExe -m pip install --upgrade --only-binary=:all: @PipOpciones -r (Join-Path $Origen "requirements.txt")
    if ($LASTEXITCODE -ne 0) { throw "No se pudieron instalar las librerías. Revise la conexión a Internet o use el modo sin Internet (preparar_offline.ps1)." }
}

# 5. Copiar/actualizar la aplicación (los datos del usuario NO se tocan)
Paso "Copiando la aplicación"
if (Test-Path $AppDir) { Remove-Item -Recurse -Force $AppDir }
New-Item -ItemType Directory -Force -Path $AppDir | Out-Null
foreach ($item in @("auditoria", "conciliacion", "ese_auditoria", "rips_conciliacion", "templates", "escritorio",
                    "ejemplos", "manage.py", "requirements.txt", "README.md", "LICENSE")) {
    $ruta = Join-Path $Origen $item
    if (Test-Path $ruta) { Copy-Item -Recurse -Force $ruta $AppDir }
}
Get-ChildItem $AppDir -Recurse -Directory -Filter "__pycache__" | Remove-Item -Recurse -Force

# 6. Scripts de inicio y desinstalación
Paso "Creando accesos de inicio"
$Iniciar = Join-Path $Destino "Iniciar Conciliacion RIPS ESE.cmd"
@"
@echo off
title Conciliacion RIPS ESE Moreno y Clavijo - no cierre esta ventana mientras use la herramienta
cd /d "%~dp0app"
"%~dp0python\python.exe" "%~dp0app\escritorio\lanzador.py" %*
if errorlevel 1 pause
"@ | Set-Content -Path $Iniciar -Encoding ASCII

Copy-Item -Force (Join-Path $PSScriptRoot "desinstalar.ps1") (Join-Path $Destino "desinstalar.ps1")
@"
@echo off
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0desinstalar.ps1"
pause
"@ | Set-Content -Path (Join-Path $Destino "Desinstalar Conciliacion RIPS ESE.cmd") -Encoding ASCII

# Usuario del panel de Administración (/admin). Opcional: la herramienta se usa sin iniciar sesión.
@"
@echo off
title Crear usuario administrador - Conciliacion RIPS ESE
cd /d "%~dp0app"
"%~dp0python\python.exe" "%~dp0app\escritorio\lanzador.py" --crear-administrador
pause
"@ | Set-Content -Path (Join-Path $Destino "Crear usuario administrador.cmd") -Encoding ASCII

if (-not $SinAccesos) {
    $Shell = New-Object -ComObject WScript.Shell
    $Escritorio = [Environment]::GetFolderPath("Desktop")
    $MenuInicio = Join-Path ([Environment]::GetFolderPath("Programs")) "Conciliacion RIPS ESE"
    New-Item -ItemType Directory -Force -Path $MenuInicio | Out-Null
    foreach ($lnk in @((Join-Path $Escritorio "Conciliacion RIPS ESE.lnk"), (Join-Path $MenuInicio "Conciliacion RIPS ESE.lnk"))) {
        $a = $Shell.CreateShortcut($lnk)
        $a.TargetPath = $Iniciar
        $a.WorkingDirectory = $Destino
        $a.WindowStyle = 7   # minimizada: la herramienta se usa desde el navegador
        $a.IconLocation = "$env:SystemRoot\System32\imageres.dll,109"
        $a.Description = "Conciliación Facturación vs RIPS y auditoría - ESE Moreno y Clavijo"
        $a.Save()
    }
    $d = $Shell.CreateShortcut((Join-Path $MenuInicio "Desinstalar Conciliacion RIPS ESE.lnk"))
    $d.TargetPath = Join-Path $Destino "Desinstalar Conciliacion RIPS ESE.cmd"
    $d.WorkingDirectory = $Destino
    $d.Save()
    $u = $Shell.CreateShortcut((Join-Path $MenuInicio "Crear usuario administrador.lnk"))
    $u.TargetPath = Join-Path $Destino "Crear usuario administrador.cmd"
    $u.WorkingDirectory = $Destino
    $u.Save()
}

# 7. Verificación rápida
Paso "Verificando la instalación"
Push-Location $AppDir
& $PythonExe -c "import django, pandas, openpyxl, rarfile, waitress, whitenoise; print('Librerías OK - Django', django.get_version(), '- pandas', pandas.__version__)"
if ($LASTEXITCODE -ne 0) { Pop-Location; throw "La verificación falló." }
Pop-Location
Remove-Item -Recurse -Force $Temp -ErrorAction SilentlyContinue

Write-Host ""
Write-Host "Instalación terminada." -ForegroundColor Green
Write-Host "  Aplicación: $AppDir"
Write-Host "  Datos:      $Datos  (se conservan al actualizar o reinstalar)"
Write-Host "  Para abrirla use el acceso directo 'Conciliacion RIPS ESE' del escritorio o del menú Inicio."

if (-not $NoIniciar) {
    Write-Host "Iniciando la herramienta en su navegador..."
    Start-Process -FilePath $Iniciar -WorkingDirectory $Destino -WindowStyle Minimized
}
