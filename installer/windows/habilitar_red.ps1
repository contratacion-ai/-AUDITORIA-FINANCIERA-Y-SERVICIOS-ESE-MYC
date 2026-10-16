<#
  Abre el puerto de Costeo ABC Ambulancias en el firewall de Windows para que
  los demás equipos de la red puedan entrar. Solo hace falta si se instaló
  "solo para mí" (sin permisos de administrador). Ejecútelo como administrador:
    clic derecho > Ejecutar con PowerShell (como administrador)
#>
param([int]$Puerto = 0)
$ErrorActionPreference = "Stop"
if ($Puerto -eq 0) {
  $ini = @("$env:ProgramData\CosteoABCAmbulancias\ambulancias.ini", "$env:APPDATA\CosteoABCAmbulancias\ambulancias.ini") | Where-Object { Test-Path $_ } | Select-Object -First 1
  $linea = if ($ini) { (Get-Content $ini) | Where-Object { $_ -match '^puerto = (\d+)$' } | Select-Object -First 1 }
  $Puerto = if ($linea -match '(\d+)$') { [int]$Matches[1] } else { 8000 }
}
Get-NetFirewallRule -DisplayName "Costeo ABC Ambulancias*" -ErrorAction SilentlyContinue | Remove-NetFirewallRule
New-NetFirewallRule -DisplayName "Costeo ABC Ambulancias ($Puerto)" -Direction Inbound -Protocol TCP -LocalPort $Puerto -Action Allow -Profile Domain,Private | Out-Null
Write-Host "Puerto $Puerto abierto para la red de dominio y privada."
