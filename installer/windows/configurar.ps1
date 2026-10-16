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

  Reconfigurar a mano:
    powershell -ExecutionPolicy Bypass -File "<carpeta del programa>\scripts\configurar.ps1" -Puerto 8080
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

function Aleatorio([int]$n) {
  $bytes = New-Object byte[] $n
  [System.Security.Cryptography.RandomNumberGenerator]::Create().GetBytes($bytes)
  return [Convert]::ToBase64String($bytes).Replace("+", "-").Replace("/", "_").TrimEnd("=")
}

if (-not (Test-Path $Ini)) {
  if ($Puerto -eq 0) { $Puerto = 8000 }
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
  if ($Puerto -gt 0) {
    (Get-Content $Ini) -replace '^puerto = \d+$', "puerto = $Puerto" | Set-Content $Ini -Encoding UTF8
  } else {
    $linea = (Get-Content $Ini) | Where-Object { $_ -match '^puerto = (\d+)$' } | Select-Object -First 1
    $Puerto = if ($linea -match '(\d+)$') { [int]$Matches[1] } else { 8000 }
  }
  Write-Host "Se conserva la configuración existente en $Ini (puerto $Puerto)"
}

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

Stop-ScheduledTask -TaskName "CosteoABC-Servidor" -ErrorAction SilentlyContinue
Start-ScheduledTask -TaskName "CosteoABC-Servidor"

$Ip = (Get-NetIPAddress -AddressFamily IPv4 -ErrorAction SilentlyContinue | Where-Object { $_.IPAddress -notlike "127.*" -and $_.IPAddress -notlike "169.254.*" } | Select-Object -First 1).IPAddress
Write-Host ""
Write-Host "Listo. Abra http://localhost:$Puerto/  (otros equipos: http://$($env:COMPUTERNAME):$Puerto/ o http://$($Ip):$Puerto/)"
Stop-Transcript | Out-Null
