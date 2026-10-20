<#
  Verifica una instalación: el servidor responde, existe el archivo de
  credenciales y se puede ingresar con el usuario admin y la clave generada.
  Lo usa el flujo de CI; también sirve para diagnosticar un servidor.
#>
param([string]$Url = "", [string]$DirDatos = "$env:ProgramData\CosteoABCAmbulancias")
$ErrorActionPreference = "Stop"
if (-not $Url) {
  # el puerto lo elige configurar.ps1 (8090 o el siguiente libre) y lo guarda en el .ini
  $ini = Join-Path $DirDatos "ambulancias.ini"
  $m = Select-String -Path $ini -Pattern '^\s*puerto\s*=\s*(\d+)' -ErrorAction SilentlyContinue | Select-Object -First 1
  $puerto = if ($m) { $m.Matches[0].Groups[1].Value } else { "8090" }
  $Url = "http://localhost:$puerto"
}
$ok = $false
for ($i = 0; $i -lt 60 -and -not $ok; $i++) {
  try { $ok = (Invoke-WebRequest "$Url/ingresar/" -UseBasicParsing -TimeoutSec 5).StatusCode -eq 200 } catch { Start-Sleep 2 }
}
if (-not $ok) {
  Get-Content (Join-Path $DirDatos "instalacion.log") -ErrorAction SilentlyContinue | Select-Object -Last 60
  Get-Content (Join-Path $DirDatos "ambulancias_abc.log") -ErrorAction SilentlyContinue | Select-Object -Last 30
  throw "El servidor no respondió en $Url"
}
$cred = Join-Path $DirDatos "CREDENCIALES_ADMIN.txt"
if (-not (Test-Path $cred)) { throw "No se creó $cred" }
$clave = ((Select-String -Path $cred -Pattern '^Clave:\s+(\S+)').Matches[0].Groups[1].Value)
$sesion = New-Object Microsoft.PowerShell.Commands.WebRequestSession
$r = Invoke-WebRequest "$Url/ingresar/" -WebSession $sesion -UseBasicParsing
if ($r.Content -notmatch "Costeo ABC") { throw "En $Url responde otra aplicación, no Costeo ABC Ambulancias" }
$csrf = ([regex]'name="csrfmiddlewaretoken" value="([^"]+)"').Match($r.Content).Groups[1].Value
$r = Invoke-WebRequest "$Url/ingresar/" -Method Post -WebSession $sesion -UseBasicParsing -Headers @{ Referer = "$Url/ingresar/" } `
  -Body @{ csrfmiddlewaretoken = $csrf; username = "admin"; password = $clave; next = "/" }
if ($r.Content -notmatch "Cargas del mes") { throw "No se pudo ingresar con la clave generada" }
$r = Invoke-WebRequest "$Url/rentabilidad/rutas/" -WebSession $sesion -UseBasicParsing
if ($r.Content -notmatch "TAME") { throw "No se cargaron las tarifas iniciales" }
Write-Host "Servidor en $Url responde; ingreso con admin y la clave generada: OK"
