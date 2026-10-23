<#
  Instalador de Costos ABC para Windows (Windows 10/11 o Windows Server 2016+).

  Qué hace:
    1. Verifica (o instala con winget) Python 3.11-3.13.
    2. Copia la aplicación en la carpeta destino y crea un entorno virtual.
    3. Instala las dependencias (sin internet si el paquete trae la carpeta "wheels").
    4. Genera el archivo .env con claves aleatorias.
    5. Crea la base de datos, el administrador y la institución con el catálogo base Res. 002/2014.
    6. Registra el servidor como tarea que arranca con Windows, abre el puerto en el firewall
       y programa una copia de seguridad diaria.

  Uso (como administrador):  INSTALAR.bat   o   powershell -ExecutionPolicy Bypass -File instalar.ps1
#>
param(
    [string]$Destino = "C:\CostosABC",
    [int]$Puerto = 8000,
    [switch]$SinPreguntas,
    # Respuestas que entrega el instalador gráfico (CostosABC-Setup.exe); vacías = preguntar en consola.
    [string]$Codigo = "", [string]$Nombre = "", [string]$Nit = "", [string]$Municipio = "",
    [string]$Usuario = "", [string]$Https = "", [string]$Demo = "", [string]$Hosts = ""
)

$ErrorActionPreference = "Stop"
$env:PYTHONUTF8 = "1"   # salida de Python en UTF-8 aunque la consola use otra página de códigos
$NombreTarea = "CostosABC-Servidor"
$NombreRespaldo = "CostosABC-Respaldo"
$Origen = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path

function Paso($texto) { Write-Host "`n==> $texto" -ForegroundColor Cyan }
function Ok($texto) { Write-Host "    $texto" -ForegroundColor Green }
function Preguntar($texto, $defecto, $valor = "") {
    if ($valor) { return $valor }
    if ($SinPreguntas) { return $defecto }
    $r = Read-Host "$texto [$defecto]"
    if ([string]::IsNullOrWhiteSpace($r)) { return $defecto } else { return $r.Trim() }
}

# --- Permisos de administrador -------------------------------------------------
$esAdmin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole(
    [Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $esAdmin) {
    Write-Host "Se requieren permisos de administrador. Reabriendo..." -ForegroundColor Yellow
    Start-Process powershell -Verb RunAs -ArgumentList "-NoProfile -ExecutionPolicy Bypass -File `"$PSCommandPath`" -Destino `"$Destino`" -Puerto $Puerto"
    exit
}

New-Item -ItemType Directory -Force -Path "$Destino\datos\logs" | Out-Null
Start-Transcript -Path "$Destino\datos\logs\instalacion.log" -Append | Out-Null
trap { Write-Host "ERROR: $_" -ForegroundColor Red; Stop-Transcript | Out-Null; exit 1 }

Write-Host "=============================================================" -ForegroundColor Blue
Write-Host "  Instalador · Herramienta de costos ABC para instituciones de salud" -ForegroundColor Blue
Write-Host "=============================================================" -ForegroundColor Blue

# --- 1. Python -------------------------------------------------------------------
Paso "Buscando Python 3.11 - 3.13"
function Buscar-Python {
    foreach ($v in "3.12", "3.13", "3.11") {
        try { $p = & py "-$v" -c "import sys;print(sys.executable)" 2>$null; if ($LASTEXITCODE -eq 0 -and $p) { return $p.Trim() } } catch {}
    }
    foreach ($cmd in "python", "python3") {
        try {
            $ver = & $cmd -c "import sys;print('%d.%d' % sys.version_info[:2])" 2>$null
            if ($LASTEXITCODE -eq 0 -and @("3.11", "3.12", "3.13") -contains $ver.Trim()) {
                return (& $cmd -c "import sys;print(sys.executable)").Trim()
            }
        } catch {}
    }
    return $null
}
# El instalador .exe trae Python 3.12 portátil con las dependencias ya instaladas en la carpeta "python".
$Incluido = Test-Path (Join-Path $Origen "python\python.exe")
$Python = if ($Incluido) { Join-Path $Origen "python\python.exe" } else { Buscar-Python }
if (-not $Python) {
    Write-Host "    No se encontró Python compatible. Intentando instalar Python 3.12 con winget..." -ForegroundColor Yellow
    try {
        winget install -e --id Python.Python.3.12 --scope machine --silent --accept-package-agreements --accept-source-agreements
        $env:Path = [Environment]::GetEnvironmentVariable("Path", "Machine") + ";" + [Environment]::GetEnvironmentVariable("Path", "User")
        $Python = Buscar-Python
    } catch {}
}
if (-not $Python) {
    throw "Instale Python 3.12 (64 bits) desde https://www.python.org/downloads/windows/ marcando 'Add python.exe to PATH' y vuelva a ejecutar el instalador."
}
Ok "Python: $Python"

# --- 2. Copia de la aplicación -----------------------------------------------------
Paso "Copiando la aplicación a $Destino"
# Actualización: detener el servidor en ejecución para poder reemplazar archivos
Stop-ScheduledTask -TaskName $NombreTarea -ErrorAction SilentlyContinue
Get-CimInstance Win32_Process -Filter "Name='python.exe'" | Where-Object { $_.CommandLine -like "*manage.py servir*" } |
    ForEach-Object { Stop-Process -Id $_.ProcessId -Force }
New-Item -ItemType Directory -Force -Path $Destino, "$Destino\datos", "$Destino\datos\media", "$Destino\datos\logs", "$Destino\datos\respaldos" | Out-Null
if ($Origen -ne $Destino) {
    robocopy $Origen $Destino /E /NFL /NDL /NJH /NJS /NP /XD .venv .git datos media staticfiles __pycache__ /XF .env db.sqlite3 | Out-Null
    if ($LASTEXITCODE -ge 8) { throw "Error copiando archivos (robocopy $LASTEXITCODE)." }
}
Ok "Archivos copiados"

# --- 3. Entorno virtual y dependencias -----------------------------------------------
Paso "Instalando dependencias"
if ($Incluido) {
    $Py = "$Destino\python\python.exe"
    Ok "Python y dependencias incluidos en el instalador"
} else {
if (-not (Test-Path "$Destino\.venv\Scripts\python.exe")) { & $Python -m venv "$Destino\.venv" }
$Py = "$Destino\.venv\Scripts\python.exe"
if (Test-Path "$Destino\wheels") {
    & $Py -m pip install --no-index --find-links "$Destino\wheels" -r "$Destino\requirements.txt" --quiet
} else {
    & $Py -m pip install --upgrade pip --quiet
    & $Py -m pip install -r "$Destino\requirements.txt" --quiet
}
if ($LASTEXITCODE -ne 0) { throw "No se pudieron instalar las dependencias." }
Ok "Dependencias instaladas"
}

# --- 4. Configuración (.env) -----------------------------------------------------------
Paso "Configuración"
$Env = "$Destino\.env"
if (Test-Path $Env) {
    Ok ".env existente conservado (no se regeneran las claves)"
} else {
    $equipo = $env:COMPUTERNAME.ToLower()
    $ips = (Get-NetIPAddress -AddressFamily IPv4 -ErrorAction SilentlyContinue | Where-Object { $_.IPAddress -notlike "169.*" } |
            Select-Object -ExpandProperty IPAddress) -join ","
    $hosts = Preguntar "Nombres/IP con los que se accederá (separados por coma)" "localhost,127.0.0.1,$equipo,$ips" $Hosts
    $https = Preguntar "¿Se publicará con certificado HTTPS (proxy IIS/nginx)? (S/N)" "N" $Https
    $usarHttps = if ($https -match "^[sS]") { "1" } else { "0" }
    $db = Preguntar "URL de PostgreSQL (vacío = base SQLite local)" ""
    $secret = & $Py -c "import secrets;print(secrets.token_urlsafe(50))"
    $seud = & $Py -c "import secrets;print(secrets.token_urlsafe(50))"
    $origenes = ($hosts.Split(",") | Where-Object { $_ } | ForEach-Object {
        if ($usarHttps -eq "1") { "https://$($_.Trim())" } else { "http://$($_.Trim()):$Puerto" } }) -join ","
    $lineas = @(
        "DJANGO_ENV=production", "DJANGO_DEBUG=0", "DJANGO_SECRET_KEY=$secret",
        "# No cambie PSEUDONIMO_KEY después de cargar RIPS o facturación", "PSEUDONIMO_KEY=$seud",
        "DJANGO_ALLOWED_HOSTS=$hosts", "DJANGO_CSRF_TRUSTED_ORIGINS=$origenes",
        "USAR_HTTPS=$usarHttps", "SECURE_SSL_REDIRECT=$usarHttps",
        "MEDIA_ROOT=$Destino\datos\media", "ABC_PUERTO=$Puerto", "CARGA_MAX_MB=25", "SESSION_COOKIE_AGE=1800"
    )
    if ($db) { $lineas += "DATABASE_URL=$db" } else { $lineas += "DATABASE_URL=sqlite:///$($Destino.Replace('\','/'))/datos/db.sqlite3" }
    Set-Content -Path $Env -Value $lineas -Encoding UTF8
    icacls $Env /inheritance:r /grant:r "*S-1-5-32-544:F" "*S-1-5-18:F" | Out-Null
    Ok ".env creado con claves aleatorias (solo lo leen administradores y SYSTEM)"
}

# --- 5. Base de datos, administrador e institución ----------------------------------
Paso "Base de datos, administrador e institución"
$codigo = Preguntar "Código de la institución (REPS o interno)" "INST-001" $Codigo
$nombre = Preguntar "Nombre de la institución" "ESE HOSPITAL" $Nombre
$nit = Preguntar "NIT" "" $Nit
$municipio = Preguntar "Municipio" "" $Municipio
$usuario = Preguntar "Usuario administrador" "admin" $Usuario
# Sin contraseña: el administrador la crea en el navegador con el enlace de primer acceso.
$demo = Preguntar "¿Crear también la institución de demostración con datos sintéticos? (S/N)" "N" $Demo
$args_instalar = @("manage.py", "instalar", "--puerto", $Puerto, "--institucion-codigo", $codigo, "--institucion-nombre", $nombre, "--nit", $nit,
                   "--municipio", $municipio, "--usuario", $usuario)
if ($demo -match "^[sS]") { $args_instalar += "--demo" }
Push-Location $Destino
& $Py @args_instalar
$rc = $LASTEXITCODE
Pop-Location
if ($rc -ne 0) { throw "La configuración inicial falló (ver mensajes anteriores)." }

# --- 6. Servicio, firewall y respaldo ---------------------------------------------------
Paso "Registrando el servidor para que inicie con Windows"
$bat = "$Destino\iniciar_servidor.bat"
Set-Content -Path $bat -Encoding ASCII -Value @(
    "@echo off", "set PYTHONUTF8=1", "cd /d `"$Destino`"",
    "`"$Py`" manage.py servir --puerto $Puerto < NUL >> `"$Destino\datos\logs\servidor.log`" 2>&1"
)
$respaldo = "$Destino\respaldar.bat"
Set-Content -Path $respaldo -Encoding ASCII -Value @(
    "@echo off", "cd /d `"$Destino`"",
    "for /f %%i in ('powershell -NoProfile -Command `"Get-Date -Format yyyyMMdd_HHmm`"') do set F=%%i",
    "mkdir `"datos\respaldos\%F%`"",
    "if exist `"datos\db.sqlite3`" `"$Py`" -c `"import sqlite3;s=sqlite3.connect(r'datos\db.sqlite3');d=sqlite3.connect(r'datos\respaldos\%F%\db.sqlite3');s.backup(d)`"",
    "robocopy `"datos\media`" `"datos\respaldos\%F%\media`" /E /NFL /NDL /NJH /NJS /NP >nul",
    "copy /y .env `"datos\respaldos\%F%\env.txt`" >nul",
    "forfiles /p `"datos\respaldos`" /d -30 /c `"cmd /c if @isdir==TRUE rmdir /s /q @path`" 2>nul",
    "rem Si usa PostgreSQL agregue aqui: pg_dump --dbname=URL > datos\respaldos\%F%\abc.sql"
)
$accion = New-ScheduledTaskAction -Execute "cmd.exe" -Argument "/c `"$bat`""
$disparador = New-ScheduledTaskTrigger -AtStartup
$config = New-ScheduledTaskSettingsSet -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1) -ExecutionTimeLimit ([TimeSpan]::Zero) -StartWhenAvailable
$principal = New-ScheduledTaskPrincipal -UserId "SYSTEM" -RunLevel Highest
Unregister-ScheduledTask -TaskName $NombreTarea -Confirm:$false -ErrorAction SilentlyContinue
Register-ScheduledTask -TaskName $NombreTarea -Action $accion -Trigger $disparador -Settings $config -Principal $principal | Out-Null
$accionR = New-ScheduledTaskAction -Execute "cmd.exe" -Argument "/c `"$respaldo`""
Unregister-ScheduledTask -TaskName $NombreRespaldo -Confirm:$false -ErrorAction SilentlyContinue
Register-ScheduledTask -TaskName $NombreRespaldo -Action $accionR -Trigger (New-ScheduledTaskTrigger -Daily -At "23:00") -Principal $principal | Out-Null
Ok "Tareas '$NombreTarea' (inicio) y '$NombreRespaldo' (23:00, conserva 30 días) registradas"

if (-not (Get-NetFirewallRule -DisplayName "Costos ABC ($Puerto)" -ErrorAction SilentlyContinue)) {
    New-NetFirewallRule -DisplayName "Costos ABC ($Puerto)" -Direction Inbound -Protocol TCP -LocalPort $Puerto -Action Allow -Profile Domain,Private | Out-Null
}
Ok "Puerto $Puerto abierto en el firewall (redes de dominio y privadas)"

Stop-ScheduledTask -TaskName $NombreTarea -ErrorAction SilentlyContinue
Start-ScheduledTask -TaskName $NombreTarea
Start-Sleep -Seconds 4
$url = "http://localhost:$Puerto/"
$tokenArchivo = "$Destino\datos\primer_acceso.token"
if (Test-Path $tokenArchivo) { $url = "http://localhost:$Puerto/primer-acceso/?t=" + (Get-Content $tokenArchivo -Raw).Trim() }
try { Invoke-WebRequest -UseBasicParsing -Uri "http://localhost:$Puerto/cuenta/ingresar/" -TimeoutSec 15 | Out-Null; Ok "Servidor en ejecución" }
catch { Write-Host "    El servidor aún no responde; revise $Destino\datos\logs\servidor.log" -ForegroundColor Yellow }

Write-Host "`n=============================================================" -ForegroundColor Green
Write-Host "  Instalación terminada" -ForegroundColor Green
Write-Host "  Acceso:      $url  (desde otros equipos: http://<nombre o IP del servidor>:$Puerto/)"
Write-Host "  Usuario:     $usuario  (cree su contraseña en la página que se abre; enlace de un solo uso)"
Write-Host "  Datos:       $Destino\datos   (respaldos diarios en datos\respaldos)"
Write-Host "  Plantilla:   $Destino\plantillas\Plantilla_Carga_Mensual_ABC.xlsx"
Write-Host "  Manual:      $Destino\docs\MANUAL_USUARIO.md"
Write-Host "=============================================================" -ForegroundColor Green
Stop-Transcript | Out-Null
if (-not $SinPreguntas) { Start-Process $url }
exit 0
