; Instalador de Windows · Auditoría de Facturación, RIPS, Costos y Medicamentos · ESE Moreno y Clavijo
; Se compila con instalador/construir_instalador.sh (makensis con /DSTAGE=<carpeta preparada> /DVERSION=<x.y> /DOUTFILE=<ruta.exe>)
; Instalación por usuario (no requiere administrador). Incluye Python 3.12 y todas las librerías: no necesita internet.

Unicode true
; El archivo se guarda en UTF-8 CON BOM: sin él makensis lo lee como ANSI y los nombres con tilde (Auditoría, Guía) fallan.
SetCompressor /SOLID lzma
!include "MUI2.nsh"

!define APPNAME "Auditoria ESE Moreno y Clavijo"
!define APPTITLE "Auditoría de Facturación, RIPS, Costos y Medicamentos"
!define APPID "AuditoriaESE_MyC"
!define PUBLISHER "ESE Departamental de Primer Nivel Moreno y Clavijo"
!define UNINSTKEY "Software\Microsoft\Windows\CurrentVersion\Uninstall\${APPID}"

Name "${APPTITLE}"
OutFile "${OUTFILE}"
InstallDir "$LOCALAPPDATA\Programs\${APPID}"
InstallDirRegKey HKCU "Software\${APPID}" "InstallDir"
RequestExecutionLevel user
BrandingText "${PUBLISHER} · v${VERSION}"
VIProductVersion "${VERSION}.0.0"
VIAddVersionKey /LANG=1034 "ProductName" "${APPTITLE}"
VIAddVersionKey /LANG=1034 "CompanyName" "${PUBLISHER}"
VIAddVersionKey /LANG=1034 "FileDescription" "Instalador de ${APPTITLE}"
VIAddVersionKey /LANG=1034 "FileVersion" "${VERSION}"
VIAddVersionKey /LANG=1034 "LegalCopyright" "Winston Gerlein Gutiérrez Pastrana – Profesional de Costos"

!define MUI_ICON "${STAGE}\icono.ico"
!define MUI_UNICON "${STAGE}\icono.ico"
!define MUI_ABORTWARNING
!define MUI_WELCOMEPAGE_TITLE "Bienvenido al instalador de la Auditoría ESE Moreno y Clavijo"
!define MUI_WELCOMEPAGE_TEXT "Este asistente instalará ${APPTITLE} v${VERSION}.$\r$\n$\r$\nIncluye todo lo necesario (Python y librerías): no requiere internet ni permisos de administrador.$\r$\n$\r$\nSus datos se guardan en %LOCALAPPDATA%\${APPID}\data y se conservan al actualizar.$\r$\n$\r$\nHaga clic en Siguiente para continuar."
!define MUI_FINISHPAGE_RUN
!define MUI_FINISHPAGE_RUN_TEXT "Abrir el aplicativo ahora"
!define MUI_FINISHPAGE_RUN_FUNCTION Abrir
!define MUI_FINISHPAGE_SHOWREADME "$INSTDIR\LEEME_PRIMERO.txt"
!define MUI_FINISHPAGE_SHOWREADME_TEXT "Ver la guía rápida (LEEME_PRIMERO)"
!define MUI_FINISHPAGE_SHOWREADME_NOTCHECKED

!insertmacro MUI_PAGE_WELCOME
!insertmacro MUI_PAGE_LICENSE "${STAGE}\LICENSE"
!insertmacro MUI_PAGE_DIRECTORY
!insertmacro MUI_PAGE_INSTFILES
!insertmacro MUI_PAGE_FINISH
!insertmacro MUI_UNPAGE_CONFIRM
!insertmacro MUI_UNPAGE_INSTFILES
!insertmacro MUI_LANGUAGE "Spanish"

Function Abrir
  SetOutPath "$INSTDIR"
  Exec '"$INSTDIR\python\python.exe" "$INSTDIR\iniciar.py"'
FunctionEnd

Section "Aplicativo" SecApp
  SectionIn RO
  ; una actualización reemplaza el programa pero NO toca los datos (están en %LOCALAPPDATA%\${APPID}\data)
  RMDir /r "$INSTDIR\app"
  RMDir /r "$INSTDIR\static"
  RMDir /r "$INSTDIR\python"
  SetOutPath "$INSTDIR"
  File /r "${STAGE}\*.*"
  CreateDirectory "$LOCALAPPDATA\${APPID}\data"

  CreateDirectory "$SMPROGRAMS\${APPNAME}"
  CreateShortCut "$SMPROGRAMS\${APPNAME}\Auditoría ESE MyC.lnk" "$INSTDIR\python\python.exe" '"$INSTDIR\iniciar.py"' "$INSTDIR\icono.ico" 0 SW_SHOWMINIMIZED "" "Abrir el aplicativo de auditoría"
  CreateShortCut "$SMPROGRAMS\${APPNAME}\Carpeta de datos.lnk" "$LOCALAPPDATA\${APPID}\data"
  CreateShortCut "$SMPROGRAMS\${APPNAME}\Guía rápida.lnk" "$INSTDIR\LEEME_PRIMERO.txt"
  CreateShortCut "$SMPROGRAMS\${APPNAME}\Desinstalar.lnk" "$INSTDIR\Desinstalar.exe"
  CreateShortCut "$DESKTOP\Auditoría ESE MyC.lnk" "$INSTDIR\python\python.exe" '"$INSTDIR\iniciar.py"' "$INSTDIR\icono.ico" 0 SW_SHOWMINIMIZED "" "Abrir el aplicativo de auditoría"

  WriteUninstaller "$INSTDIR\Desinstalar.exe"
  WriteRegStr HKCU "Software\${APPID}" "InstallDir" "$INSTDIR"
  WriteRegStr HKCU "${UNINSTKEY}" "DisplayName" "${APPTITLE}"
  WriteRegStr HKCU "${UNINSTKEY}" "DisplayVersion" "${VERSION}"
  WriteRegStr HKCU "${UNINSTKEY}" "Publisher" "${PUBLISHER}"
  WriteRegStr HKCU "${UNINSTKEY}" "DisplayIcon" "$INSTDIR\icono.ico"
  WriteRegStr HKCU "${UNINSTKEY}" "InstallLocation" "$INSTDIR"
  WriteRegStr HKCU "${UNINSTKEY}" "UninstallString" '"$INSTDIR\Desinstalar.exe"'
  WriteRegDWORD HKCU "${UNINSTKEY}" "NoModify" 1
  WriteRegDWORD HKCU "${UNINSTKEY}" "NoRepair" 1
SectionEnd

Section "Uninstall"
  Delete "$DESKTOP\Auditoría ESE MyC.lnk"
  RMDir /r "$SMPROGRAMS\${APPNAME}"
  RMDir /r "$INSTDIR"
  DeleteRegKey HKCU "${UNINSTKEY}"
  DeleteRegKey HKCU "Software\${APPID}"
  MessageBox MB_YESNO|MB_ICONQUESTION|MB_DEFBUTTON2 "¿Desea borrar también la base de datos y los archivos cargados?$\r$\n($LOCALAPPDATA\${APPID})$\r$\n$\r$\nElija No para conservarlos (recomendado si va a reinstalar)." /SD IDNO IDNO fin
    RMDir /r "$LOCALAPPDATA\${APPID}"
  fin:
SectionEnd
