<#
  Configuración posterior a la instalación (la ejecuta el instalador como administrador).
  También sirve para reconfigurar a mano:
    powershell -ExecutionPolicy Bypass -File "C:\Program Files\CosteoABCAmbulancias\scripts\configurar.ps1" -Puerto 8000

  Pasos:
   1. Crea C:\ProgramData\CosteoABCAmbulancias (base de datos, logs, respaldos).
   2. Crea C:\ProgramData\CosteoABCAmbulancias\ambulancias.ini con claves aleatorias (solo la primera vez; conserva la sal).
   3. Migra la base de datos y carga los datos iniciales (idempotente).
   4. Crea/actualiza el usuario admin si se indicó clave.
   5. Abre el puerto en el firewall (solo red de dominio/privada).
   6. Registra dos tareas programadas: servidor al iniciar Windows y respaldo diario.
#>
param(
  [int]$Puerto = 8000,
  [string]$ClaveAdmin = "",
  [string]$DirDatos = "$env:ProgramData\CosteoABCAmbulancias",
  [string]$HoraRespaldo = "23:00"
)
$ErrorActionPreference = "Stop"
$Raiz = Split-Path -Parent $PSScriptRoot
$App = Join-Path $Raiz "app"
$Python = Join-Path $Raiz "python\python.exe"
$Ini = Join-Path $DirDatos "ambulancias.ini"   # fuera de la carpeta del programa: sobrevive a actualizaciones
$Log = Join-Path $DirDatos "instalacion.log"
if ($DirDatos -ne "$env:ProgramData\CosteoABCAmbulancias") { $env:ABC_CONFIG = Join-Path $DirDatos "ambulancias.ini" }

New-Item -ItemType Directory -Force -Path $DirDatos, (Join-Path $DirDatos "respaldos") | Out-Null
Start-Transcript -Path $Log -Append | Out-Null

function Aleatorio([int]$n) {
  $bytes = New-Object byte[] $n
  [System.Security.Cryptography.RandomNumberGenerator]::Create().GetBytes($bytes)
  return [Convert]::ToBase64String($bytes).Replace("+", "-").Replace("/", "_").TrimEnd("=")
}

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
  # Actualiza solo el puerto; conserva claves y sal.
  (Get-Content $Ini) -replace '^puerto = \d+$', "puerto = $Puerto" | Set-Content $Ini -Encoding UTF8
  Write-Host "Se conserva la configuración existente en $Ini"
}

Push-Location $App
try {
  & $Python manage.py migrate --noinput
  if ($LASTEXITCODE -ne 0) { throw "Falló la migración" }
  & $Python manage.py collectstatic --noinput -v 0
  & $Python manage.py cargar_datos_iniciales
  if ($ClaveAdmin) {
    & $Python manage.py crear_admin --usuario admin --clave $ClaveAdmin
    if ($LASTEXITCODE -ne 0) { throw "No se pudo crear el usuario admin" }
  }
} finally {
  Pop-Location
}

# Firewall: solo perfiles de dominio y privado (no redes públicas).
$Regla = "Costeo ABC Ambulancias ($Puerto)"
Get-NetFirewallRule -DisplayName "Costeo ABC Ambulancias*" -ErrorAction SilentlyContinue | Remove-NetFirewallRule
New-NetFirewallRule -DisplayName $Regla -Direction Inbound -Protocol TCP -LocalPort $Puerto -Action Allow -Profile Domain,Private | Out-Null

# Tareas programadas (se ejecutan como SYSTEM, sin sesión iniciada).
$Usuario = New-ScheduledTaskPrincipal -UserId "SYSTEM" -LogonType ServiceAccount -RunLevel Highest
$AjustesSrv = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -ExecutionTimeLimit ([TimeSpan]::Zero) -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1)
$AccionSrv = New-ScheduledTaskAction -Execute $Python -Argument "servidor.py" -WorkingDirectory $App
Register-ScheduledTask -TaskName "CosteoABC-Servidor" -Action $AccionSrv -Trigger (New-ScheduledTaskTrigger -AtStartup) -Principal $Usuario -Settings $AjustesSrv -Force | Out-Null

$AccionResp = New-ScheduledTaskAction -Execute $Python -Argument "manage.py respaldar_bd" -WorkingDirectory $App
Register-ScheduledTask -TaskName "CosteoABC-Respaldo" -Action $AccionResp -Trigger (New-ScheduledTaskTrigger -Daily -At $HoraRespaldo) -Principal $Usuario -Force | Out-Null

Stop-ScheduledTask -TaskName "CosteoABC-Servidor" -ErrorAction SilentlyContinue
Start-ScheduledTask -TaskName "CosteoABC-Servidor"

$Ip = (Get-NetIPAddress -AddressFamily IPv4 | Where-Object { $_.IPAddress -notlike "127.*" -and $_.IPAddress -notlike "169.254.*" } | Select-Object -First 1).IPAddress
Write-Host ""
Write-Host "Listo. Desde cualquier equipo de la red abra:  http://$($env:COMPUTERNAME):$Puerto/   o   http://$($Ip):$Puerto/"
Stop-Transcript | Out-Null
