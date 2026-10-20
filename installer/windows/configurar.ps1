<#
  Configuración posterior a la instalación. La ejecuta el instalador; no pide
  usuario ni contraseña.

  Dos modos, según cómo se ejecutó el instalador:
   * Para todos los usuarios (con permisos de administrador): datos en
     C:\ProgramData\CosteoABCAmbulancias, servidor como tarea de SYSTEM al
     iniciar Windows, regla de firewall para que entren los demás equipos.
   * Solo para mí (sin permisos de administrador): datos en
     %APPDATA%\CosteoABCAmbulancias, servidor al iniciar sesión el usuario.
     Sin regla de firewall: los demás equipos no entran hasta que un
     administrador ejecute scripts\habilitar_red.ps1.

  Pasos:
   1. Crea la carpeta de datos y ambulancias.ini con claves aleatorias (solo la
      primera vez; conserva la sal y el puerto en las actualizaciones).
   2. Migra la base de datos y carga los datos iniciales (idempotente).
   3. Crea el usuario "admin" con una clave aleatoria (solo si no existe) y la
      guarda en CREDENCIALES_ADMIN.txt dentro de la carpeta de datos.
   4. Firewall (solo con administrador) y tareas programadas.

  Puerto: por defecto 8090. Si el puerto pedido lo usa otra aplicación (por
  ejemplo la de auditoría de facturación, que suele estar en el 8000) se toma
  el siguiente libre, y el acceso directo queda con el puerto real.

  Reconfigurar a mano:
    powershell -ExecutionPolicy Bypass -File "<carpeta del programa>\scripts\configurar.ps1" -Puerto 8091
#>
param(
  [int]$Puerto = 0,
  [ValidateSet("", "todos", "usuario")][string]$Modo = "",
  [string]$DirDatos = "",
  [string]$HoraRespaldo = "23:00"
)
$ErrorActionPreference = "Stop"
# El instalador indica el modo elegido; a mano se deduce de los permisos actuales.
$EsAdmin = if ($Modo) { $Modo -eq "todos" } else {
  ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
}
if (-not $DirDatos) {
  $DirDatos = if ($EsAdmin) { "$env:ProgramData\CosteoABCAmbulancias" } else { "$env:APPDATA\CosteoABCAmbulancias" }
}
$Raiz = Split-Path -Parent $PSScriptRoot
$App = Join-Path $Raiz "app"
$Python = Join-Path $Raiz "python\python.exe"
$Ini = Join-Path $DirDatos "ambulancias.ini"   # fuera de la carpeta del programa: sobrevive a actualizaciones
$Log = Join-Path $DirDatos "instalacion.log"
$Credenciales = Join-Path $DirDatos "CREDENCIALES_ADMIN.txt"
$env:ABC_CONFIG = $Ini

New-Item -ItemType Directory -Force -Path $DirDatos, (Join-Path $DirDatos "respaldos") | Out-Null
Start-Transcript -Path $Log -Append | Out-Null
Write-Host "Modo: $(if ($EsAdmin) { 'todos los usuarios (administrador)' } else { 'solo este usuario (sin administrador)' })"

$PuertoDefecto = 8090
$PuertoAnteriorDefecto = 8000   # versiones hasta la 1.0.7; choca con otras aplicaciones Django

# Detiene el servidor de esta aplicación (para que no cuente como "otra aplicación" en el puerto).
function Detener-Servidor {
  Stop-ScheduledTask -TaskName "CosteoABC-Servidor" -ErrorAction SilentlyContinue
  Get-CimInstance Win32_Process -Filter "Name = 'python.exe'" -ErrorAction SilentlyContinue |
    Where-Object { $_.CommandLine -like "*servidor.py*" -and $_.ExecutablePath -like "$Raiz*" } |
    ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
  Start-Sleep -Seconds 2
}

# Un puerto está libre si nadie responde en localhost y se puede abrir en todas las interfaces.
function Puerto-Libre([int]$p) {
  $c = New-Object Net.Sockets.TcpClient
  try {
    $intento = $c.BeginConnect("127.0.0.1", $p, $null, $null)
    if ($intento.AsyncWaitHandle.WaitOne(700) -and $c.Connected) { return $false }
  } catch { } finally { $c.Close() }
  try {
    $l = New-Object Net.Sockets.TcpListener([Net.IPAddress]::Any, $p)
    $l.Start(); $l.Stop(); return $true
  } catch { return $false }
}

function Quien-Usa([int]$p) {
  try {
    $pid_ = (Get-NetTCPConnection -LocalPort $p -State Listen -ErrorAction Stop | Select-Object -First 1).OwningProcess
    $proc = Get-CimInstance Win32_Process -Filter "ProcessId = $pid_"
    return "$($proc.Name) (PID $pid_) $($proc.CommandLine)"
  } catch { return "otra aplicación" }
}

function Aleatorio([int]$n) {
  $bytes = New-Object byte[] $n
  [System.Security.Cryptography.RandomNumberGenerator]::Create().GetBytes($bytes)
  return [Convert]::ToBase64String($bytes).Replace("+", "-").Replace("/", "_").TrimEnd("=")
}

# Puerto deseado: el indicado, el de la instalación anterior o el por defecto.
# Solo la sección [servidor]: [base_datos] también tiene "puerto" (PostgreSQL).
function Puerto-Ini {
  $seccion = ""
  foreach ($l in (Get-Content $Ini)) {
    if ($l -match '^\s*\[(.+)\]') { $seccion = $Matches[1] }
    elseif ($seccion -eq "servidor" -and $l -match '^\s*puerto\s*=\s*(\d+)') { return [int]$Matches[1] }
  }
  return 0
}
$PuertoIni = 0
if (Test-Path $Ini) { $PuertoIni = Puerto-Ini }
if ($Puerto -eq 0) {
  $Puerto = if ($PuertoIni -and $PuertoIni -ne $PuertoAnteriorDefecto) { $PuertoIni } else { $PuertoDefecto }
}
Detener-Servidor
$Pedido = $Puerto
while (-not (Puerto-Libre $Puerto)) {
  Write-Host "El puerto $Puerto está ocupado por: $(Quien-Usa $Puerto)"
  $Puerto++
  if ($Puerto -gt $Pedido + 50) { throw "No se encontró un puerto libre entre $Pedido y $Puerto" }
}
if ($Puerto -ne $Pedido) { Write-Host "Se usará el puerto $Puerto (el $Pedido lo usa otra aplicación)." }

if (-not (Test-Path $Ini)) {
  $contenido = @"
[servidor]
secret_key = $(Aleatorio 48)
sal_documento = $(Aleatorio 32)
debug = false
puerto = $Puerto
host = 0.0.0.0
hilos = 12
allowed_hosts = *
https = false
data_dir = $DirDatos
backup_retencion = 30

[base_datos]
motor = sqlite
"@
  Set-Content -Path $Ini -Value $contenido -Encoding UTF8
  Write-Host "Creado $Ini"
} else {
  $seccion = ""; $lineas = @()
  foreach ($l in (Get-Content $Ini)) {
    if ($l -match '^\s*\[(.+)\]') {
      $seccion = $Matches[1]; $lineas += $l
      if ($seccion -eq "servidor" -and -not $PuertoIni) { $lineas += "puerto = $Puerto" }
    } elseif ($seccion -eq "servidor" -and $l -match '^\s*puerto\s*=') {
      $lineas += "puerto = $Puerto"
    } else { $lineas += $l }
  }
  $lineas | Set-Content $Ini -Encoding UTF8
  Write-Host "Se conserva la configuración existente en $Ini; puerto $Puerto (antes $PuertoIni)"
}

# Acceso directo con el puerto real.
"[InternetShortcut]`r`nURL=http://localhost:$Puerto/" | Set-Content -Path (Join-Path $Raiz "Costeo ABC Ambulancias.url") -Encoding ASCII

Push-Location $App
try {
  & $Python manage.py migrate --noinput
  if ($LASTEXITCODE -ne 0) { throw "Falló la migración" }
  & $Python manage.py collectstatic --noinput -v 0
  & $Python manage.py cargar_datos_iniciales
  if ($LASTEXITCODE -ne 0) { throw "Falló la carga de datos iniciales" }

  # Usuario administrador con clave aleatoria: solo si todavía no existe.
  $Clave = "Abc-" + (Aleatorio 12)
  $salida = & $Python manage.py crear_admin --usuario admin --clave $Clave --si-no-existe
  if ($LASTEXITCODE -ne 0) { throw "No se pudo crear el usuario admin" }
  if ("$salida" -match "CREADO") {
    @"
COSTEO ABC AMBULANCIAS - ACCESO INICIAL
=======================================
Dirección:  http://localhost:$Puerto/   (desde otros equipos: http://$($env:COMPUTERNAME):$Puerto/)
Usuario:    admin
Clave:      $Clave

Al ingresar, cambie la clave en "Cambiar mi clave" (parte superior derecha)
y cree un usuario para cada persona en Administración > Usuarios.
Después de cambiar la clave puede borrar este archivo.
"@ | Set-Content -Path $Credenciales -Encoding UTF8
    # Solo el usuario que instaló, los administradores y SYSTEM pueden leerlo.
    icacls $Credenciales /inheritance:r /grant:r "$($env:USERNAME):(R,W,D)" "*S-1-5-32-544:(F)" "*S-1-5-18:(F)" | Out-Null
    Write-Host "Usuario admin creado. Credenciales en $Credenciales"
  } else {
    Write-Host "El usuario admin ya existía: no se cambió su clave"
    if (Test-Path $Credenciales) {
      # la dirección puede haber cambiado de puerto
      (Get-Content $Credenciales) -replace '^Dirección:.*$', "Dirección:  http://localhost:$Puerto/   (desde otros equipos: http://$($env:COMPUTERNAME):$Puerto/)" |
        Set-Content $Credenciales -Encoding UTF8
    }
  }
} finally {
  Pop-Location
}

$AjustesSrv = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -ExecutionTimeLimit ([TimeSpan]::Zero) -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1)
$AccionSrv = New-ScheduledTaskAction -Execute $Python -Argument "servidor.py" -WorkingDirectory $App
$AccionResp = New-ScheduledTaskAction -Execute $Python -Argument "manage.py respaldar_bd" -WorkingDirectory $App

if ($EsAdmin) {
  # Firewall: solo perfiles de dominio y privado (no redes públicas).
  Get-NetFirewallRule -DisplayName "Costeo ABC Ambulancias*" -ErrorAction SilentlyContinue | Remove-NetFirewallRule
  New-NetFirewallRule -DisplayName "Costeo ABC Ambulancias ($Puerto)" -Direction Inbound -Protocol TCP -LocalPort $Puerto -Action Allow -Profile Domain,Private | Out-Null
  # Tareas como SYSTEM: el servidor corre aunque nadie haya iniciado sesión.
  $Principal = New-ScheduledTaskPrincipal -UserId "SYSTEM" -LogonType ServiceAccount -RunLevel Highest
  Register-ScheduledTask -TaskName "CosteoABC-Servidor" -Action $AccionSrv -Trigger (New-ScheduledTaskTrigger -AtStartup) -Principal $Principal -Settings $AjustesSrv -Force | Out-Null
  Register-ScheduledTask -TaskName "CosteoABC-Respaldo" -Action $AccionResp -Trigger (New-ScheduledTaskTrigger -Daily -At $HoraRespaldo) -Principal $Principal -Force | Out-Null
} else {
  # Sin administrador: tareas del propio usuario (corren con su sesión iniciada).
  $Yo = "$env:USERDOMAIN\$env:USERNAME"
  $Principal = New-ScheduledTaskPrincipal -UserId $Yo -LogonType Interactive -RunLevel Limited
  Register-ScheduledTask -TaskName "CosteoABC-Servidor" -Action $AccionSrv -Trigger (New-ScheduledTaskTrigger -AtLogOn -User $Yo) -Principal $Principal -Settings $AjustesSrv -Force | Out-Null
  Register-ScheduledTask -TaskName "CosteoABC-Respaldo" -Action $AccionResp -Trigger (New-ScheduledTaskTrigger -Daily -At $HoraRespaldo) -Principal $Principal -Force | Out-Null
  Write-Host "AVISO: instalado sin permisos de administrador. La aplicación funciona en este equipo;"
  Write-Host "para que entren otros equipos, un administrador debe ejecutar scripts\habilitar_red.ps1."
}

Detener-Servidor
Start-ScheduledTask -TaskName "CosteoABC-Servidor"

$Ip = (Get-NetIPAddress -AddressFamily IPv4 -ErrorAction SilentlyContinue | Where-Object { $_.IPAddress -notlike "127.*" -and $_.IPAddress -notlike "169.254.*" } | Select-Object -First 1).IPAddress
Write-Host ""
Write-Host "Listo. Abra http://localhost:$Puerto/  (otros equipos: http://$($env:COMPUTERNAME):$Puerto/ o http://$($Ip):$Puerto/)"
Stop-Transcript | Out-Null
