# Lo ejecuta el desinstalador. Quita tareas programadas y la regla de firewall.
# NO borra C:\ProgramData\CosteoABCAmbulancias (base de datos y respaldos).
$ErrorActionPreference = "SilentlyContinue"
Stop-ScheduledTask -TaskName "CosteoABC-Servidor"
Unregister-ScheduledTask -TaskName "CosteoABC-Servidor" -Confirm:$false
Unregister-ScheduledTask -TaskName "CosteoABC-Respaldo" -Confirm:$false
Get-NetFirewallRule -DisplayName "Costeo ABC Ambulancias*" | Remove-NetFirewallRule
Get-CimInstance Win32_Process -Filter "Name = 'python.exe'" | Where-Object { $_.CommandLine -like "*servidor.py*" } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force }
