; Instalador gráfico de Costos ABC para Windows (64 bits). Se compila con instalador/construir_instalador_windows.sh
; Incluye Python 3.12 portátil y todas las dependencias: no requiere internet ni Python instalado.
Unicode true
!include "MUI2.nsh"
!include "nsDialogs.nsh"
!include "LogicLib.nsh"
!include "x64.nsh"

!ifndef VERSION
  !define VERSION "0.0.0"
!endif
!ifndef STAGE
  !error "Defina STAGE (carpeta con la aplicación preparada)"
!endif
!ifndef SALIDA
  !define SALIDA "CostosABC-Setup.exe"
!endif

Name "Costos ABC"
Caption "Instalación de Costos ABC ${VERSION}"
OutFile "${SALIDA}"
InstallDir "C:\CostosABC"
InstallDirRegKey HKLM "Software\CostosABC" "Ruta"
RequestExecutionLevel admin
SetCompressor /SOLID lzma
ShowInstDetails show
ShowUninstDetails show
BrandingText "Herramienta de costos ABC para instituciones de salud"
VIProductVersion "1.0.0.0"
VIAddVersionKey "ProductName" "Costos ABC"
VIAddVersionKey "FileDescription" "Instalador de la herramienta de costos ABC"
VIAddVersionKey "ProductVersion" "${VERSION}"
VIAddVersionKey "FileVersion" "${VERSION}"
VIAddVersionKey "LegalCopyright" "GPL-3.0"

Var Dialogo
Var hCodigo
Var hNombre
Var hNit
Var hMunicipio
Var hUsuario
Var hClave
Var hClave2
Var hPuerto
Var hDemo
Var Codigo
Var Nombre
Var Nit
Var Municipio
Var Usuario
Var Clave
Var Puerto
Var Demo
Var Actualizacion

!define MUI_ABORTWARNING
!define MUI_WELCOMEPAGE_TITLE "Instalación de Costos ABC"
!define MUI_WELCOMEPAGE_TEXT "Este asistente instala la herramienta de costos ABC (Res. DDC-000002 de 2014) en este equipo.$\r$\n$\r$\nIncluye todo lo necesario: no requiere internet ni tener Python instalado.$\r$\n$\r$\nAl terminar, la aplicación quedará funcionando como servicio que arranca con Windows y se podrá abrir desde el navegador de cualquier equipo de la red."
!define MUI_FINISHPAGE_RUN
!define MUI_FINISHPAGE_RUN_TEXT "Abrir Costos ABC en el navegador"
!define MUI_FINISHPAGE_RUN_FUNCTION AbrirNavegador
!define MUI_FINISHPAGE_SHOWREADME "$INSTDIR\LEAME_INSTALACION.txt"
!define MUI_FINISHPAGE_SHOWREADME_TEXT "Ver instrucciones"
!define MUI_FINISHPAGE_SHOWREADME_NOTCHECKED

!insertmacro MUI_PAGE_WELCOME
!insertmacro MUI_PAGE_LICENSE "${STAGE}\LICENSE"
!insertmacro MUI_PAGE_DIRECTORY
Page custom PaginaDatos PaginaDatosSalir
!insertmacro MUI_PAGE_INSTFILES
!insertmacro MUI_PAGE_FINISH
!insertmacro MUI_UNPAGE_CONFIRM
!insertmacro MUI_UNPAGE_INSTFILES
!insertmacro MUI_LANGUAGE "Spanish"

Function .onInit
  ${IfNot} ${RunningX64}
    MessageBox MB_ICONSTOP "Costos ABC requiere Windows de 64 bits."
    Abort
  ${EndIf}
  StrCpy $Puerto "8000"
FunctionEnd

Function PaginaDatos
  ; En una actualización la base de datos y el administrador ya existen: no se piden de nuevo.
  StrCpy $Actualizacion "0"
  ${If} ${FileExists} "$INSTDIR\.env"
    StrCpy $Actualizacion "1"
    Abort
  ${EndIf}
  !insertmacro MUI_HEADER_TEXT "Datos de la institución" "Se crearán la institución, su catálogo base y el usuario administrador."
  nsDialogs::Create 1018
  Pop $Dialogo
  ${NSD_CreateLabel} 0 0u 40% 10u "Código institución (REPS):"
  ${NSD_CreateText} 42% 0u 58% 11u "INST-001"
  Pop $hCodigo
  ${NSD_CreateLabel} 0 15u 40% 10u "Nombre de la institución:"
  ${NSD_CreateText} 42% 15u 58% 11u "ESE HOSPITAL"
  Pop $hNombre
  ${NSD_CreateLabel} 0 30u 40% 10u "NIT:"
  ${NSD_CreateText} 42% 30u 58% 11u ""
  Pop $hNit
  ${NSD_CreateLabel} 0 45u 40% 10u "Municipio:"
  ${NSD_CreateText} 42% 45u 58% 11u ""
  Pop $hMunicipio
  ${NSD_CreateLabel} 0 62u 40% 10u "Usuario administrador:"
  ${NSD_CreateText} 42% 62u 58% 11u "admin"
  Pop $hUsuario
  ${NSD_CreateLabel} 0 77u 40% 10u "Contraseña (mín. 10 caracteres):"
  ${NSD_CreatePassword} 42% 77u 58% 11u ""
  Pop $hClave
  ${NSD_CreateLabel} 0 92u 40% 10u "Repita la contraseña:"
  ${NSD_CreatePassword} 42% 92u 58% 11u ""
  Pop $hClave2
  ${NSD_CreateLabel} 0 109u 40% 10u "Puerto web:"
  ${NSD_CreateNumber} 42% 109u 20% 11u "$Puerto"
  Pop $hPuerto
  ${NSD_CreateCheckbox} 0 126u 100% 10u "Crear también una institución de demostración con datos sintéticos"
  Pop $hDemo
  nsDialogs::Show
FunctionEnd

Function PaginaDatosSalir
  ${NSD_GetText} $hCodigo $Codigo
  ${NSD_GetText} $hNombre $Nombre
  ${NSD_GetText} $hNit $Nit
  ${NSD_GetText} $hMunicipio $Municipio
  ${NSD_GetText} $hUsuario $Usuario
  ${NSD_GetText} $hClave $Clave
  ${NSD_GetText} $hClave2 $R0
  ${NSD_GetText} $hPuerto $Puerto
  ${NSD_GetState} $hDemo $R1
  ${If} $R1 == ${BST_CHECKED}
    StrCpy $Demo "S"
  ${Else}
    StrCpy $Demo "N"
  ${EndIf}
  ${If} $Codigo == ""
  ${OrIf} $Nombre == ""
  ${OrIf} $Usuario == ""
    MessageBox MB_ICONEXCLAMATION "Complete código, nombre de la institución y usuario administrador."
    Abort
  ${EndIf}
  StrLen $R2 $Clave
  ${If} $R2 < 10
    MessageBox MB_ICONEXCLAMATION "La contraseña debe tener al menos 10 caracteres."
    Abort
  ${EndIf}
  ${If} $Clave != $R0
    MessageBox MB_ICONEXCLAMATION "Las contraseñas no coinciden."
    Abort
  ${EndIf}
  ${If} $Puerto < 1
  ${OrIf} $Puerto > 65535
    MessageBox MB_ICONEXCLAMATION "Puerto no válido."
    Abort
  ${EndIf}
  ; Las comillas romperían la línea de comandos del configurador
  Push $Codigo
  Call SinComillas
  Push $Nombre
  Call SinComillas
  Push $Nit
  Call SinComillas
  Push $Municipio
  Call SinComillas
  Push $Usuario
  Call SinComillas
FunctionEnd

Function SinComillas
  Exch $R3
  Push $R4
  Push $R5
  StrLen $R4 $R3
  ${DoWhile} $R4 > 0
    IntOp $R4 $R4 - 1
    StrCpy $R5 $R3 1 $R4
    ${If} $R5 == '"'
      MessageBox MB_ICONEXCLAMATION 'No use comillas (") en los datos.'
      Pop $R5
      Pop $R4
      Pop $R3
      Abort
    ${EndIf}
  ${Loop}
  Pop $R5
  Pop $R4
  Pop $R3
FunctionEnd

Function AbrirNavegador
  ExecShell "open" "http://localhost:$Puerto/"
FunctionEnd

!macro DetenerServidor
  ; Detiene el servidor en ejecución (actualización / desinstalación) usando el PID que escribe "manage.py servir".
  nsExec::ExecToLog 'schtasks.exe /End /TN "CostosABC-Servidor"'
  Pop $0
  ${If} ${FileExists} "$INSTDIR\datos\servidor.pid"
    FileOpen $1 "$INSTDIR\datos\servidor.pid" r
    FileRead $1 $2
    FileClose $1
    ${If} $2 != ""
      nsExec::ExecToLog 'taskkill.exe /F /T /PID $2'
      Pop $0
    ${EndIf}
    Delete "$INSTDIR\datos\servidor.pid"
  ${EndIf}
  Sleep 2000
!macroend

Section "Costos ABC" SecPrincipal
  SetShellVarContext all
  System::Call 'Kernel32::SetEnvironmentVariable(t "PYTHONUTF8", t "1") i'
  DetailPrint "Deteniendo el servidor si está en ejecución..."
  !insertmacro DetenerServidor

  DetailPrint "Copiando archivos..."
  SetOutPath "$INSTDIR"
  File /r "${STAGE}\*.*"
  WriteUninstaller "$INSTDIR\Desinstalar.exe"

  ${If} $Actualizacion == "1"
    ReadRegStr $Puerto HKLM "Software\CostosABC" "Puerto"
    ${If} $Puerto == ""
      StrCpy $Puerto "8000"
    ${EndIf}
    DetailPrint "Actualización: migraciones, servicio y reinicio..."
    nsExec::ExecToLog '"$INSTDIR\python\python.exe" "$INSTDIR\instalador\windows\configurar.py" --destino "$INSTDIR" --puerto $Puerto --actualizar'
    Pop $0
  ${Else}
    DetailPrint "Configurando base de datos, institución, servicio, firewall y respaldo..."
    System::Call 'Kernel32::SetEnvironmentVariable(t "ABC_ADMIN_PASSWORD", t "$Clave") i'
    nsExec::ExecToLog '"$INSTDIR\python\python.exe" "$INSTDIR\instalador\windows\configurar.py" --destino "$INSTDIR" --puerto $Puerto --codigo "$Codigo" --nombre "$Nombre" --nit "$Nit" --municipio "$Municipio" --usuario "$Usuario" --demo $Demo'
    Pop $0
    System::Call 'Kernel32::SetEnvironmentVariable(t "ABC_ADMIN_PASSWORD", n) i'
    StrCpy $Clave ""
  ${EndIf}
  ; Verificación real: sin .env o sin base de datos la configuración no se completó.
  ${If} $0 == "2"
    MessageBox MB_ICONEXCLAMATION "Costos ABC quedó instalado, con advertencias (por ejemplo, no se pudo programar el arranque automático o la aplicación tardó en responder).$\r$\nRevise: $INSTDIR\datos\logs\instalacion.log"
    StrCpy $0 "0"
  ${EndIf}
  ${If} $0 != "0"
  ${OrIfNot} ${FileExists} "$INSTDIR\.env"
  ${OrIfNot} ${FileExists} "$INSTDIR\datos\db.sqlite3"
    MessageBox MB_ICONSTOP "La configuración no terminó (resultado: $0).$\r$\nRevise el registro: $INSTDIR\datos\logs\instalacion.log$\r$\nPuede volver a ejecutar el instalador después de corregir la causa."
    Abort "La configuración falló"
  ${EndIf}

  CreateDirectory "$SMPROGRAMS\Costos ABC"
  WriteINIStr "$SMPROGRAMS\Costos ABC\Costos ABC.url" "InternetShortcut" "URL" "http://localhost:$Puerto/"
  WriteINIStr "$DESKTOP\Costos ABC.url" "InternetShortcut" "URL" "http://localhost:$Puerto/"
  CreateShortcut "$SMPROGRAMS\Costos ABC\Plantilla de carga mensual.lnk" "$INSTDIR\plantillas\Plantilla_Carga_Mensual_ABC.xlsx"
  CreateShortcut "$SMPROGRAMS\Costos ABC\Respaldar ahora.lnk" "$INSTDIR\respaldar.bat"
  CreateShortcut "$SMPROGRAMS\Costos ABC\Registros del servidor.lnk" "$INSTDIR\datos\logs"
  CreateShortcut "$SMPROGRAMS\Costos ABC\Desinstalar Costos ABC.lnk" "$INSTDIR\Desinstalar.exe"

  WriteRegStr HKLM "Software\CostosABC" "Ruta" "$INSTDIR"
  WriteRegStr HKLM "Software\CostosABC" "Puerto" "$Puerto"
  !define UNINST "Software\Microsoft\Windows\CurrentVersion\Uninstall\CostosABC"
  WriteRegStr HKLM "${UNINST}" "DisplayName" "Costos ABC - herramienta de costos en salud"
  WriteRegStr HKLM "${UNINST}" "DisplayVersion" "${VERSION}"
  WriteRegStr HKLM "${UNINST}" "Publisher" "Costos ABC"
  WriteRegStr HKLM "${UNINST}" "InstallLocation" "$INSTDIR"
  WriteRegStr HKLM "${UNINST}" "UninstallString" '"$INSTDIR\Desinstalar.exe"'
  WriteRegDWORD HKLM "${UNINST}" "NoModify" 1
  WriteRegDWORD HKLM "${UNINST}" "NoRepair" 1
SectionEnd

Section "Uninstall"
  SetShellVarContext all
  ReadRegStr $Puerto HKLM "Software\CostosABC" "Puerto"
  ${If} $Puerto == ""
    StrCpy $Puerto "8000"
  ${EndIf}
  !insertmacro DetenerServidor
  nsExec::ExecToLog 'schtasks.exe /Delete /TN "CostosABC-Servidor" /F'
  Pop $0
  nsExec::ExecToLog 'schtasks.exe /Delete /TN "CostosABC-Respaldo" /F'
  Pop $0
  nsExec::ExecToLog 'netsh.exe advfirewall firewall delete rule "name=Costos ABC ($Puerto)"'
  Pop $0
  ; Se elimina el programa; los datos (base de datos, archivos, respaldos y .env) se conservan salvo que el usuario confirme.
  !macro QuitarDir d
    RMDir /r "$INSTDIR\${d}"
  !macroend
  !insertmacro QuitarDir "python"
  !insertmacro QuitarDir "abc_salud"
  !insertmacro QuitarDir "analisis"
  !insertmacro QuitarDir "cargas"
  !insertmacro QuitarDir "core"
  !insertmacro QuitarDir "modelo"
  !insertmacro QuitarDir "motor"
  !insertmacro QuitarDir "templates"
  !insertmacro QuitarDir "static"
  !insertmacro QuitarDir "staticfiles"
  !insertmacro QuitarDir "seed"
  !insertmacro QuitarDir "docs"
  !insertmacro QuitarDir "plantillas"
  !insertmacro QuitarDir "instalador"
  Delete "$INSTDIR\*.py"
  Delete "$INSTDIR\*.txt"
  Delete "$INSTDIR\*.md"
  Delete "$INSTDIR\*.bat"
  Delete "$INSTDIR\*.yml"
  Delete "$INSTDIR\*.ini"
  Delete "$INSTDIR\Dockerfile"
  Delete "$INSTDIR\LICENSE"
  Delete "$INSTDIR\VERSION"
  Delete "$INSTDIR\.env.example"
  Delete "$INSTDIR\.gitattributes"
  Delete "$INSTDIR\.gitignore"
  Delete "$INSTDIR\Desinstalar.exe"
  MessageBox MB_YESNO|MB_ICONQUESTION|MB_DEFBUTTON2 "¿Eliminar también la base de datos, los archivos cargados y los respaldos?$\r$\n(Responda No para conservarlos y poder reinstalar sin perder información.)" IDNO conservar
    RMDir /r "$INSTDIR\datos"
    Delete "$INSTDIR\.env"
    RMDir "$INSTDIR"
  conservar:
  RMDir /r "$SMPROGRAMS\Costos ABC"
  Delete "$DESKTOP\Costos ABC.url"
  DeleteRegKey HKLM "Software\Microsoft\Windows\CurrentVersion\Uninstall\CostosABC"
  DeleteRegKey HKLM "Software\CostosABC"
SectionEnd
