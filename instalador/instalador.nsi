; Instalador por usuario de "Auditoría RIPS - E.S.E. Moreno y Clavijo".
; NO pide permisos de administrador (RequestExecutionLevel user): instala en %LOCALAPPDATA%\Programs,
; crea accesos en el menú Inicio y escritorio del usuario y registra el desinstalador en HKCU.
; Se compila con makensis desde instalador/construir_instalador.sh.

Unicode true
!ifndef VERSION
  !define VERSION "1.1.0"
!endif
!define APP "Auditoria RIPS ESE Moreno y Clavijo"
!define CLAVE "AuditoriaRIPS"
!define UNINST "Software\Microsoft\Windows\CurrentVersion\Uninstall\${CLAVE}"

Name "Auditoría RIPS · E.S.E. Moreno y Clavijo"
OutFile "dist\Instalador_AuditoriaRIPS_${VERSION}.exe"
RequestExecutionLevel user
InstallDir "$LOCALAPPDATA\Programs\${CLAVE}"
SetCompressor /SOLID lzma
ShowInstDetails nevershow
ShowUninstDetails nevershow
BrandingText "E.S.E. Moreno y Clavijo · Auditoría RIPS ${VERSION}"

!include "MUI2.nsh"
!define MUI_ICON "build\icono.ico"
!define MUI_UNICON "build\icono.ico"
!define MUI_WELCOMEPAGE_TITLE "Auditoría RIPS · CÁPITA · EVENTO"
!define MUI_WELCOMEPAGE_TEXT "Este asistente instala la herramienta de auditoría RIPS de la E.S.E. Moreno y Clavijo.$\r$\n$\r$\nSe instala solo para su usuario y no requiere permisos de administrador.$\r$\n$\r$\nLos datos (RIPS, base consolidada, maestros e informes) quedan en:$\r$\n%LOCALAPPDATA%\AuditoriaRIPS$\r$\ny se conservan al actualizar."
!define MUI_FINISHPAGE_RUN
!define MUI_FINISHPAGE_RUN_FUNCTION AbrirAplicacion
!define MUI_FINISHPAGE_RUN_TEXT "Abrir Auditoría RIPS ahora"
!insertmacro MUI_PAGE_WELCOME
!insertmacro MUI_PAGE_INSTFILES
!insertmacro MUI_PAGE_FINISH
!insertmacro MUI_UNPAGE_CONFIRM
!insertmacro MUI_UNPAGE_INSTFILES
!insertmacro MUI_LANGUAGE "Spanish"

Function AbrirAplicacion
  Exec '"$INSTDIR\python\pythonw.exe" "$INSTDIR\app\iniciar.pyw"'
FunctionEnd

VIProductVersion "${VERSION}.0"
VIAddVersionKey /LANG=${LANG_SPANISH} "ProductName" "Auditoría RIPS E.S.E. Moreno y Clavijo"
VIAddVersionKey /LANG=${LANG_SPANISH} "CompanyName" "E.S.E. Moreno y Clavijo"
VIAddVersionKey /LANG=${LANG_SPANISH} "FileDescription" "Instalador Auditoría RIPS (por usuario)"
VIAddVersionKey /LANG=${LANG_SPANISH} "FileVersion" "${VERSION}"
VIAddVersionKey /LANG=${LANG_SPANISH} "LegalCopyright" "E.S.E. Moreno y Clavijo"

Section "Programa" SecPrograma
  SetShellVarContext current
  ; Cierra una instancia abierta antes de reemplazar archivos
  nsExec::Exec '"$INSTDIR\python\pythonw.exe" "$INSTDIR\app\detener_silencioso.pyw"'
  Sleep 1000
  RMDir /r "$INSTDIR\app"
  RMDir /r "$INSTDIR\python"
  SetOutPath "$INSTDIR"
  File /r "build\AuditoriaRIPS\*.*"
  File "build\icono.ico"

  CreateDirectory "$SMPROGRAMS\Auditoria RIPS"
  CreateShortcut "$SMPROGRAMS\Auditoria RIPS\Auditoría RIPS.lnk" "$INSTDIR\python\pythonw.exe" '"$INSTDIR\app\iniciar.pyw"' "$INSTDIR\icono.ico"
  CreateShortcut "$SMPROGRAMS\Auditoria RIPS\Sincronizar carpetas de Drive.lnk" "$INSTDIR\python\pythonw.exe" '"$INSTDIR\app\sincronizar.pyw"' "$INSTDIR\icono.ico"
  CreateShortcut "$SMPROGRAMS\Auditoria RIPS\Cerrar Auditoría RIPS.lnk" "$INSTDIR\python\pythonw.exe" '"$INSTDIR\app\detener.pyw"' "$INSTDIR\icono.ico"
  CreateShortcut "$SMPROGRAMS\Auditoria RIPS\Carpeta de datos e informes.lnk" "$LOCALAPPDATA\AuditoriaRIPS"
  CreateShortcut "$SMPROGRAMS\Auditoria RIPS\Desinstalar.lnk" "$INSTDIR\Desinstalar.exe"
  CreateShortcut "$DESKTOP\Auditoría RIPS.lnk" "$INSTDIR\python\pythonw.exe" '"$INSTDIR\app\iniciar.pyw"' "$INSTDIR\icono.ico"

  WriteUninstaller "$INSTDIR\Desinstalar.exe"
  WriteRegStr HKCU "${UNINST}" "DisplayName" "Auditoría RIPS · E.S.E. Moreno y Clavijo"
  WriteRegStr HKCU "${UNINST}" "DisplayVersion" "${VERSION}"
  WriteRegStr HKCU "${UNINST}" "Publisher" "E.S.E. Moreno y Clavijo"
  WriteRegStr HKCU "${UNINST}" "InstallLocation" "$INSTDIR"
  WriteRegStr HKCU "${UNINST}" "DisplayIcon" "$INSTDIR\icono.ico"
  WriteRegStr HKCU "${UNINST}" "UninstallString" '"$INSTDIR\Desinstalar.exe"'
  WriteRegDWORD HKCU "${UNINST}" "NoModify" 1
  WriteRegDWORD HKCU "${UNINST}" "NoRepair" 1
SectionEnd

Section "Uninstall"
  SetShellVarContext current
  nsExec::Exec '"$INSTDIR\python\pythonw.exe" "$INSTDIR\app\detener_silencioso.pyw"'
  Sleep 1000
  RMDir /r "$INSTDIR\app"
  RMDir /r "$INSTDIR\python"
  Delete "$INSTDIR\icono.ico"
  Delete "$INSTDIR\Desinstalar.exe"
  RMDir "$INSTDIR"
  RMDir /r "$SMPROGRAMS\Auditoria RIPS"
  Delete "$DESKTOP\Auditoría RIPS.lnk"
  DeleteRegKey HKCU "${UNINST}"
  MessageBox MB_ICONINFORMATION "Se desinstaló el programa. Sus datos se conservan en $LOCALAPPDATA\AuditoriaRIPS (bórrelos manualmente si ya no los necesita)."
SectionEnd
