<#
  Desinstala el servicio de Costos ABC. Por defecto CONSERVA los datos (carpeta datos y .env).
  Uso (como administrador): powershell -ExecutionPolicy Bypass -File desinstalar.ps1 [-Destino C:\CostosABC] [-BorrarTodo]
#>
param([string]$Destino = "C:\CostosABC", [int]$Puerto = 8000, [switch]$BorrarTodo)
$ErrorActionPreference = "Continue"
foreach ($t in "CostosABC-Servidor", "CostosABC-Respaldo") {
    Stop-ScheduledTask -TaskName $t -ErrorAction SilentlyContinue
    Unregister-ScheduledTask -TaskName $t -Confirm:$false -ErrorAction SilentlyContinue
}
Get-CimInstance Win32_Process -Filter "Name='python.exe'" | Where-Object { $_.CommandLine -like "*manage.py servir*" } |
    ForEach-Object { Stop-Process -Id $_.ProcessId -Force }
Remove-NetFirewallRule -DisplayName "Costos ABC ($Puerto)" -ErrorAction SilentlyContinue
if ($BorrarTodo) {
    $r = Read-Host "Se borrarán la base de datos, los archivos y los respaldos de $Destino. Escriba BORRAR para confirmar"
    if ($r -eq "BORRAR") { Remove-Item -Recurse -Force $Destino; Write-Host "Carpeta eliminada." }
} else {
    Write-Host "Servicio, tareas y regla de firewall eliminados. Los datos se conservan en $Destino\datos y $Destino\.env"
}
