<#
  Escribe CREDENCIALES_ADMIN.txt (lo usan configurar.ps1 y restablecer_admin.ps1).
  UTF-8 con BOM para que el Bloc de notas muestre bien las tildes.
#>
function Escribir-Credenciales([string]$Archivo, [string]$Clave, [int]$Puerto) {
  $texto = @"
COSTEO ABC AMBULANCIAS - ACCESO INICIAL
=======================================
Dirección:  http://localhost:$Puerto/   (desde otros equipos: http://$($env:COMPUTERNAME):$Puerto/)
Usuario:    admin
Clave:      $Clave

Use SOLO esta dirección (puerto $Puerto). Otras aplicaciones del equipo, como la de
auditoría de facturación, están en otros puertos y tienen sus propios usuarios.
La página correcta dice "Costeo ABC Ambulancias".

Al ingresar, cambie la clave en "Cambiar mi clave" (parte superior derecha)
y cree un usuario para cada persona en Administración > Usuarios.
Si olvida la clave: Inicio > Costeo ABC Ambulancias > Restablecer clave del administrador.
"@
  if (Test-Path $Archivo) {
    icacls $Archivo /reset | Out-Null
    Remove-Item $Archivo -Force
  }
  [System.IO.File]::WriteAllText($Archivo, $texto, (New-Object System.Text.UTF8Encoding($true)))
  # Solo el usuario que instaló, los administradores y SYSTEM pueden leerlo.
  icacls $Archivo /inheritance:r /grant:r "$($env:USERNAME):(R,W,D)" "*S-1-5-32-544:(F)" "*S-1-5-18:(F)" | Out-Null
}
