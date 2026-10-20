<#
  Restablece la clave del usuario "admin" de Costeo ABC Ambulancias: genera una
  clave nueva, la guarda en CREDENCIALES_ADMIN.txt (con el puerto real), la
  muestra y abre la aplicación. Úselo si no puede ingresar o si olvidó la clave.
  Si la aplicación se instaló para todos los usuarios, pide permiso de administrador.
#>
param([string]$DirDatos = "", [switch]$SinAbrir)
$ErrorActionPreference = "Stop"
$Raiz = Split-Path -Parent $PSScriptRoot
$App = Join-Path $Raiz "app"
$Python = Join-Path $Raiz "python\python.exe"
. (Join-Path $PSScriptRoot "credenciales.ps1")

if (-not $DirDatos) {
  $todos = "$env:ProgramData\CosteoABCAmbulancias"
  $DirDatos = if (Test-Path (Join-Path $todos "ambulancias.ini")) { $todos } else { "$env:APPDATA\CosteoABCAmbulancias" }
}
$Ini = Join-Path $DirDatos "ambulancias.ini"
if (-not (Test-Path $Ini)) { throw "No se encontró $Ini. ¿Está instalada la aplicación?" }

# La carpeta de ProgramData solo la pueden modificar los administradores.
$EsAdmin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if ($DirDatos -like "$env:ProgramData*" -and -not $EsAdmin) {
  Start-Process powershell.exe -Verb RunAs -ArgumentList "-NoProfile -ExecutionPolicy Bypass -File `"$PSCommandPath`" -DirDatos `"$DirDatos`""
  exit
}

$Puerto = 8090
$seccion = ""
foreach ($l in (Get-Content $Ini)) {
  if ($l -match '^\s*\[(.+)\]') { $seccion = $Matches[1] }
  elseif ($seccion -eq "servidor" -and $l -match '^\s*puerto\s*=\s*(\d+)') { $Puerto = [int]$Matches[1]; break }
}

$bytes = New-Object byte[] 12
[System.Security.Cryptography.RandomNumberGenerator]::Create().GetBytes($bytes)
$Clave = "Abc-" + [Convert]::ToBase64String($bytes).Replace("+", "-").Replace("/", "_").TrimEnd("=")

$env:ABC_CONFIG = $Ini
Push-Location $App
try {
  & $Python manage.py crear_admin --usuario admin --clave $Clave
  if ($LASTEXITCODE -ne 0) { throw "No se pudo restablecer la clave" }
} finally { Pop-Location }

$Credenciales = Join-Path $DirDatos "CREDENCIALES_ADMIN.txt"
Escribir-Credenciales $Credenciales $Clave $Puerto
$Escritorio = [Environment]::GetFolderPath("Desktop")
if ($Escritorio) {
  Copy-Item $Credenciales (Join-Path $Escritorio "USUARIO Y CLAVE - Costeo ABC Ambulancias.txt") -Force -ErrorAction SilentlyContinue
}
Write-Host "Clave del usuario admin restablecida. Ver $Credenciales"
if (-not $SinAbrir) {
  Start-Process notepad.exe -ArgumentList "`"$Credenciales`""
  Start-Process "http://localhost:$Puerto/"
}
