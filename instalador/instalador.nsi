; =============================================================================
;  Instalador de Windows · Cargue RIPS (Res. 3374/2000) · ESE Moreno y Clavijo
;  Se construye con instalador/construir_instalador.sh (NSIS 3, Modern UI 2).
;  Variables que entrega el script: VERSION, APPDIR (archivos a instalar),
;  RECURSOS (imágenes e ícono), SALIDA (ruta del .exe).
; =============================================================================
Unicode true
SetCompressor /SOLID lzma
RequestExecutionLevel user          ; no requiere administrador (igual que el aplicativo de auditoría v5)

!define NOMBRE      "Cargue RIPS ESE MyC"
!define NOMBRE_LARGO "Cargue RIPS · ESE Moreno y Clavijo"
!define EDITOR      "ESE Departamental de Primer Nivel Moreno y Clavijo"
!define CLAVE_DESINST "Software\Microsoft\Windows\CurrentVersion\Uninstall\CargueRIPS_ESE_MyC"
!define DIR_AUDITORIA "$LOCALAPPDATA\Programs\AuditoriaESE_MyC"

Name "${NOMBRE_LARGO}"
OutFile "${SALIDA}"
InstallDir "$LOCALAPPDATA\Programs\CargueRIPS_ESE_MyC"
InstallDirRegKey HKCU "${CLAVE_DESINST}" "InstallLocation"
BrandingText "${EDITOR} · v${VERSION}"
VIProductVersion "${VERSION}.0"
VIAddVersionKey /LANG=1034 "ProductName" "${NOMBRE_LARGO}"
VIAddVersionKey /LANG=1034 "CompanyName" "${EDITOR}"
VIAddVersionKey /LANG=1034 "FileDescription" "Instalador del Cargue RIPS (Res. 3374/2000)"
VIAddVersionKey /LANG=1034 "FileVersion" "${VERSION}"
VIAddVersionKey /LANG=1034 "LegalCopyright" "${EDITOR}"

!include "MUI2.nsh"
!include "FileFunc.nsh"
!include "x64.nsh"

!define MUI_ICON   "${RECURSOS}\icono.ico"
!define MUI_UNICON "${RECURSOS}\icono.ico"
!define MUI_WELCOMEFINISHPAGE_BITMAP   "${RECURSOS}\asistente.bmp"
!define MUI_UNWELCOMEFINISHPAGE_BITMAP "${RECURSOS}\asistente.bmp"
!define MUI_HEADERIMAGE
!define MUI_HEADERIMAGE_RIGHT
!define MUI_HEADERIMAGE_BITMAP   "${RECURSOS}\encabezado.bmp"
!define MUI_HEADERIMAGE_UNBITMAP "${RECURSOS}\encabezado.bmp"
!define MUI_ABORTWARNING

!define MUI_WELCOMEPAGE_TITLE "Cargue de RIPS$\r$\nESE Moreno y Clavijo"
!define MUI_WELCOMEPAGE_TEXT "Este asistente instala el Cargue de RIPS (Resolución 3374 de 2000, Anexo Técnico No. 1).$\r$\n$\r$\n\
• Carga los RIPS descargados (ZIP, carpetas o TXT), los valida y los organiza por hospital y mes.$\r$\n\
• Genera los archivos Excel y JSON y la homologación para auditoría financiera.$\r$\n\
• Tablero de análisis: por sede, mes, empresa, servicio y CUPS, comparativos, frecuencias de uso, alertas e informe Excel.$\r$\n\
• Se enlaza con el aplicativo de Auditoría de Facturación ESE MyC (envía cada mes con todas las sedes) y puede abrirse desde otros equipos de la red con clave.$\r$\n$\r$\n\
No necesita internet, Python ni permisos de administrador."
!define MUI_FINISHPAGE_TITLE "Instalación terminada"
!define MUI_FINISHPAGE_TEXT "Ábralo con el acceso «Cargue RIPS ESE MyC» del Escritorio o del menú Inicio.$\r$\n$\r$\nResultados: Documentos\Cargue RIPS ESE MyC"
!define MUI_FINISHPAGE_RUN
!define MUI_FINISHPAGE_RUN_TEXT "Abrir el Cargue de RIPS ahora"
!define MUI_FINISHPAGE_RUN_FUNCTION AbrirAhora
!define MUI_FINISHPAGE_SHOWREADME "$INSTDIR\LEAME.txt"
!define MUI_FINISHPAGE_SHOWREADME_TEXT "Ver la guía rápida"

!insertmacro MUI_PAGE_WELCOME
!insertmacro MUI_PAGE_LICENSE "${APPDIR}\LICENSE"
!insertmacro MUI_PAGE_DIRECTORY
!insertmacro MUI_PAGE_INSTFILES
!insertmacro MUI_PAGE_FINISH
!insertmacro MUI_UNPAGE_CONFIRM
!insertmacro MUI_UNPAGE_INSTFILES
!insertmacro MUI_LANGUAGE "Spanish"

Var DATOS

Function .onInit
  ${IfNot} ${RunningX64}
    MessageBox MB_ICONSTOP "El Cargue de RIPS requiere Windows de 64 bits."
    Abort
  ${EndIf}
FunctionEnd

Function AbrirAhora
  Exec '"$INSTDIR\python\python.exe" "$INSTDIR\iniciar.py"'
FunctionEnd

Section "Cargue RIPS" SEC_APP
  SectionIn RO
  SetOutPath "$INSTDIR"
  ; La configuración editable no se sobrescribe al actualizar
  SetOverwrite off
  File /r "${APPDIR}\config"
  SetOverwrite on
  File /r /x "config" "${APPDIR}\*.*"

  ; Carpeta de resultados en Documentos
  StrCpy $DATOS "$DOCUMENTS\Cargue RIPS ESE MyC"
  CreateDirectory "$DATOS"

  ; Accesos directos (ventana del servidor minimizada, como el aplicativo v5)
  CreateShortCut "$DESKTOP\${NOMBRE}.lnk" "$INSTDIR\python\python.exe" '"$INSTDIR\iniciar.py"' \
      "$INSTDIR\icono.ico" 0 SW_SHOWMINIMIZED "" "Cargue y homologación de RIPS - ESE Moreno y Clavijo"
  CreateDirectory "$SMPROGRAMS\ESE Moreno y Clavijo"
  CreateShortCut "$SMPROGRAMS\ESE Moreno y Clavijo\${NOMBRE}.lnk" "$INSTDIR\python\python.exe" '"$INSTDIR\iniciar.py"' \
      "$INSTDIR\icono.ico" 0 SW_SHOWMINIMIZED
  CreateShortCut "$SMPROGRAMS\ESE Moreno y Clavijo\${NOMBRE} (en red).lnk" "$INSTDIR\python\python.exe" '"$INSTDIR\iniciar.py" --red' \
      "$INSTDIR\icono.ico" 0 SW_SHOWNORMAL "" "Abrir el cargue, el tablero y la auditoria desde otros equipos (con clave)"
  CreateShortCut "$SMPROGRAMS\ESE Moreno y Clavijo\Permitir acceso en red.lnk" "$INSTDIR\permitir_red.bat" "" "$INSTDIR\icono.ico" 0
  CreateShortCut "$SMPROGRAMS\ESE Moreno y Clavijo\Resultados del cargue RIPS.lnk" "$DATOS"
  CreateShortCut "$SMPROGRAMS\ESE Moreno y Clavijo\Guia rapida del cargue RIPS.lnk" "$INSTDIR\LEAME.txt"
  CreateShortCut "$SMPROGRAMS\ESE Moreno y Clavijo\Desinstalar Cargue RIPS.lnk" "$INSTDIR\Desinstalar.exe"
  ; Enlace con el aplicativo de Auditoría de Facturación si ya está instalado
  IfFileExists "${DIR_AUDITORIA}\iniciar.py" 0 +3
    CreateShortCut "$SMPROGRAMS\ESE Moreno y Clavijo\Auditoria de Facturacion ESE MyC.lnk" \
        "${DIR_AUDITORIA}\python\python.exe" '"${DIR_AUDITORIA}\iniciar.py"' "${DIR_AUDITORIA}\icono.ico" 0 SW_SHOWMINIMIZED
    DetailPrint "Aplicativo de Auditoría de Facturación encontrado: quedó enlazado."

  ; Agregar o quitar programas
  WriteUninstaller "$INSTDIR\Desinstalar.exe"
  WriteRegStr HKCU "${CLAVE_DESINST}" "DisplayName" "${NOMBRE_LARGO}"
  WriteRegStr HKCU "${CLAVE_DESINST}" "DisplayVersion" "${VERSION}"
  WriteRegStr HKCU "${CLAVE_DESINST}" "Publisher" "${EDITOR}"
  WriteRegStr HKCU "${CLAVE_DESINST}" "DisplayIcon" "$INSTDIR\icono.ico"
  WriteRegStr HKCU "${CLAVE_DESINST}" "InstallLocation" "$INSTDIR"
  WriteRegStr HKCU "${CLAVE_DESINST}" "UninstallString" '"$INSTDIR\Desinstalar.exe"'
  WriteRegDWORD HKCU "${CLAVE_DESINST}" "NoModify" 1
  WriteRegDWORD HKCU "${CLAVE_DESINST}" "NoRepair" 1
  ${GetSize} "$INSTDIR" "/S=0K" $0 $1 $2
  IntFmt $0 "0x%08X" $0
  WriteRegDWORD HKCU "${CLAVE_DESINST}" "EstimatedSize" "$0"
SectionEnd

Section "Uninstall"
  Delete "$DESKTOP\${NOMBRE}.lnk"
  Delete "$SMPROGRAMS\ESE Moreno y Clavijo\${NOMBRE}.lnk"
  Delete "$SMPROGRAMS\ESE Moreno y Clavijo\${NOMBRE} (en red).lnk"
  Delete "$SMPROGRAMS\ESE Moreno y Clavijo\Permitir acceso en red.lnk"
  Delete "$SMPROGRAMS\ESE Moreno y Clavijo\Resultados del cargue RIPS.lnk"
  Delete "$SMPROGRAMS\ESE Moreno y Clavijo\Guia rapida del cargue RIPS.lnk"
  Delete "$SMPROGRAMS\ESE Moreno y Clavijo\Desinstalar Cargue RIPS.lnk"
  Delete "$SMPROGRAMS\ESE Moreno y Clavijo\Auditoria de Facturacion ESE MyC.lnk"
  RMDir "$SMPROGRAMS\ESE Moreno y Clavijo"
  RMDir /r "$INSTDIR"
  DeleteRegKey HKCU "${CLAVE_DESINST}"
  MessageBox MB_YESNO|MB_ICONQUESTION|MB_DEFBUTTON2 \
    "¿Borrar también los resultados y el acumulado para el aplicativo de auditoría?$\r$\n($DOCUMENTS\Cargue RIPS ESE MyC)" \
    /SD IDNO IDNO fin
    RMDir /r "$DOCUMENTS\Cargue RIPS ESE MyC"
  fin:
SectionEnd
