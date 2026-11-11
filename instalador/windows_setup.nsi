; Instalador de Windows (NSIS) - Control ESE Moreno y Clavijo
; Se compila con:  makensis /DORIGEN=<carpeta_preparada> /DSALIDA=<ruta.exe> /DVERSION=1.0.0 windows_setup.nsi
Unicode true
!include "MUI2.nsh"
!include "LogicLib.nsh"

!define NOMBRE "Control ESE Moreno y Clavijo"
!define CLAVE_DESINST "Software\Microsoft\Windows\CurrentVersion\Uninstall\ControlESE"

Name "${NOMBRE}"
OutFile "${SALIDA}"
InstallDir "$LOCALAPPDATA\ControlESE"
RequestExecutionLevel user          ; no pide administrador
SetCompressor /SOLID lzma
SetCompressorDictSize 32
BrandingText "ESE Moreno y Clavijo - Control de servicios, combustible y mantenimientos"
VIProductVersion "${VERSION}.0"
VIAddVersionKey "ProductName" "${NOMBRE}"
VIAddVersionKey "FileVersion" "${VERSION}"
VIAddVersionKey "FileDescription" "Instalador de ${NOMBRE}"

!define MUI_ABORTWARNING
!define MUI_WELCOMEPAGE_TEXT "Este asistente instalará la herramienta de control de servicios públicos, combustible y mantenimientos por sede y hospital.$\r$\n$\r$\nNo necesita internet, Python ni permisos de administrador.$\r$\n$\r$\nLa herramienta carga únicamente los archivos de energía, acueducto, combustible, plantas eléctricas, desplazamientos y mantenimientos (o sus plantillas).$\r$\n$\r$\nHaga clic en Siguiente para continuar."
!define MUI_FINISHPAGE_RUN
!define MUI_FINISHPAGE_RUN_TEXT "Abrir la aplicación ahora"
!define MUI_FINISHPAGE_RUN_FUNCTION AbrirApp
!define MUI_FINISHPAGE_SHOWREADME "$INSTDIR\LEEME_PRIMERO.txt"
!define MUI_FINISHPAGE_SHOWREADME_TEXT "Ver las instrucciones de uso"

!insertmacro MUI_PAGE_WELCOME
!insertmacro MUI_PAGE_COMPONENTS
!insertmacro MUI_PAGE_DIRECTORY
!insertmacro MUI_PAGE_INSTFILES
!insertmacro MUI_PAGE_FINISH
!insertmacro MUI_UNPAGE_CONFIRM
!insertmacro MUI_UNPAGE_INSTFILES
!insertmacro MUI_LANGUAGE "Spanish"

Function .onInit
    ; La aplicación abierta bloquea sus archivos: se pide cerrarla antes de actualizar.
    IfFileExists "$INSTDIR\app\app.py" 0 +3
        MessageBox MB_OKCANCEL|MB_ICONINFORMATION "Si la aplicación Control ESE está abierta, cierre su ventana negra y luego pulse Aceptar para actualizar." IDOK +2
        Abort
FunctionEnd

Section "Aplicación (obligatoria)" SecApp
    SectionIn RO
    ; Instalación limpia del programa; la carpeta de datos NO se toca al actualizar.
    RMDir /r "$INSTDIR\app"
    RMDir /r "$INSTDIR\python"
    SetOutPath "$INSTDIR\app"
    File /r "${ORIGEN}\app\*.*"
    SetOutPath "$INSTDIR\python"
    File /r "${ORIGEN}\python\*.*"
    SetOutPath "$INSTDIR"
    File "${ORIGEN}\iniciar_app.bat"
    File "${ORIGEN}\LEEME_PRIMERO.txt"
    CreateDirectory "$INSTDIR\datos"

    ; Verificar el Visual C++ Redistributable (lo necesitan numpy y pandas)
    SetRegView 64
    ReadRegDWORD $0 HKLM "SOFTWARE\Microsoft\VisualStudio\14.0\VC\Runtimes\x64" "Installed"
    ${If} $0 != 1
        MessageBox MB_OK|MB_ICONEXCLAMATION "Falta el componente 'Microsoft Visual C++ Redistributable 2015-2022 (x64)'.$\r$\n$\r$\nInstálelo desde https://aka.ms/vs/17/release/vc_redist.x64.exe antes de abrir la aplicación."
    ${EndIf}

    CreateShortcut "$DESKTOP\Control ESE Moreno y Clavijo.lnk" "$INSTDIR\iniciar_app.bat" "" "$SYSDIR\shell32.dll" 13
    CreateDirectory "$SMPROGRAMS\Control ESE"
    CreateShortcut "$SMPROGRAMS\Control ESE\Control ESE Moreno y Clavijo.lnk" "$INSTDIR\iniciar_app.bat" "" "$SYSDIR\shell32.dll" 13
    CreateShortcut "$SMPROGRAMS\Control ESE\Desinstalar.lnk" "$INSTDIR\desinstalar.exe"
    WriteUninstaller "$INSTDIR\desinstalar.exe"
    WriteRegStr HKCU "${CLAVE_DESINST}" "DisplayName" "${NOMBRE}"
    WriteRegStr HKCU "${CLAVE_DESINST}" "DisplayVersion" "${VERSION}"
    WriteRegStr HKCU "${CLAVE_DESINST}" "Publisher" "ESE Moreno y Clavijo"
    WriteRegStr HKCU "${CLAVE_DESINST}" "InstallLocation" "$INSTDIR"
    WriteRegStr HKCU "${CLAVE_DESINST}" "UninstallString" '"$INSTDIR\desinstalar.exe"'
    WriteRegDWORD HKCU "${CLAVE_DESINST}" "NoModify" 1
    WriteRegDWORD HKCU "${CLAVE_DESINST}" "NoRepair" 1
SectionEnd

Section /o "Datos de demostración (cifras ficticias, para conocer la herramienta)" SecDemo
    IfFileExists "$INSTDIR\datos\control_ese.db" 0 +3
        MessageBox MB_OK|MB_ICONINFORMATION "Ya existen datos en $INSTDIR\datos; no se reemplazan con la demostración."
        Goto fin
    CreateDirectory "$INSTDIR\datos"
    SetOutPath "$INSTDIR\datos"
    File "${ORIGEN}\demo\control_ese.db"
    MessageBox MB_OK|MB_ICONINFORMATION "Datos de demostración cargados.$\r$\n$\r$\nUsuarios de prueba:$\r$\n  admin  /  Demo2026*   (área central, ve toda la ESE)$\r$\n  fortul /  Demo2026*   (usuario de sede, solo Hospital de Fortul)"
    fin:
SectionEnd

!insertmacro MUI_FUNCTION_DESCRIPTION_BEGIN
    !insertmacro MUI_DESCRIPTION_TEXT ${SecApp} "Programa, motor de Python y librerías. Sin esto no funciona."
    !insertmacro MUI_DESCRIPTION_TEXT ${SecDemo} "Base de datos de ejemplo con cifras inventadas y usuarios de prueba. NO la marque si va a trabajar con datos reales."
!insertmacro MUI_FUNCTION_DESCRIPTION_END

Function AbrirApp
    Exec '"$INSTDIR\iniciar_app.bat"'
FunctionEnd

Section "Uninstall"
    MessageBox MB_YESNO|MB_ICONQUESTION "¿Desea CONSERVAR la carpeta de datos (base de datos y archivos cargados)?$\r$\n$\r$\nSí = se conservan.  No = se borran definitivamente." IDYES conservar
    RMDir /r "$INSTDIR\datos"
    conservar:
    RMDir /r "$INSTDIR\app"
    RMDir /r "$INSTDIR\python"
    Delete "$INSTDIR\iniciar_app.bat"
    Delete "$INSTDIR\LEEME_PRIMERO.txt"
    Delete "$INSTDIR\desinstalar.exe"
    RMDir "$INSTDIR"
    Delete "$DESKTOP\Control ESE Moreno y Clavijo.lnk"
    RMDir /r "$SMPROGRAMS\Control ESE"
    DeleteRegKey HKCU "${CLAVE_DESINST}"
SectionEnd
