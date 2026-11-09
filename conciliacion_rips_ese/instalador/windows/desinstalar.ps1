<#
  Desinstala la herramienta de Auditoría ESE (solo lo que creó el instalador, sin permisos de administrador).
  Pregunta antes de borrar los datos (auditorías, conciliaciones y archivos cargados).
#>
param([string]$Destino = (Join-Path $env:LOCALAPPDATA "ConciliacionRIPS_ESE"))
$ErrorActionPreference = "Continue"

$PyDir = Join-Path $Destino "python"
Get-Process -ErrorAction SilentlyContinue | Where-Object {
    $_.Path -and $_.Path.StartsWith($PyDir, [StringComparison]::OrdinalIgnoreCase)
} | Stop-Process -Force -ErrorAction SilentlyContinue

Remove-Item -Force (Join-Path ([Environment]::GetFolderPath("Desktop")) "Conciliacion RIPS ESE.lnk") -ErrorAction SilentlyContinue
Remove-Item -Recurse -Force (Join-Path ([Environment]::GetFolderPath("Programs")) "Conciliacion RIPS ESE") -ErrorAction SilentlyContinue
Remove-Item -Recurse -Force $PyDir, (Join-Path $Destino "app"), (Join-Path $Destino "temp_instalacion") -ErrorAction SilentlyContinue
Remove-Item -Force (Join-Path $Destino "Iniciar Conciliacion RIPS ESE.cmd") -ErrorAction SilentlyContinue

$Datos = Join-Path $Destino "datos"
if (Test-Path $Datos) {
    $r = Read-Host "¿Desea borrar también los DATOS (auditorías, conciliaciones y archivos cargados)? Escriba SI para borrarlos"
    if ($r -eq "SI") {
        Remove-Item -Recurse -Force $Datos
        Write-Host "Datos eliminados."
    } else {
        Write-Host "Los datos se conservaron en: $Datos"
    }
}
Write-Host "La herramienta fue desinstalada."
# Se elimina a sí mismo y la carpeta si quedó vacía
Start-Process -WindowStyle Hidden -FilePath "cmd.exe" -ArgumentList "/c timeout /t 2 >nul & del /q `"$Destino\desinstalar.ps1`" `"$Destino\Desinstalar Conciliacion RIPS ESE.cmd`" & rmdir `"$Destino`" 2>nul"
