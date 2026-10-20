; Instalador de Costeo ABC Ambulancias (servidor de red local).
; Se compila con build_installer.ps1, que prepara la carpeta {DirBuild}
; (Python embebido + dependencias + aplicación + scripts + docs).

#ifndef VersionApp
  #define VersionApp "1.0.0"
#endif
#ifndef DirBuild
  #define DirBuild "build"
#endif

[Setup]
AppId={{6C1F3E2A-9B7D-4E55-A1C3-ABC0A2026001}
AppName=Costeo ABC Ambulancias - ESE Moreno y Clavijo
AppVersion={#VersionApp}
AppPublisher=ESE Departamental de Primer Nivel Moreno y Clavijo
DefaultDirName={autopf}\CosteoABCAmbulancias
DefaultGroupName=Costeo ABC Ambulancias
DisableProgramGroupPage=yes
OutputBaseFilename=Instalador_CosteoABC_Ambulancias_{#VersionApp}
Compression=lzma2/max
SolidCompression=yes
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
; Sin usuario ni contraseña: al iniciar, el instalador ofrece "para todos los
; usuarios" (pide permiso de administrador de Windows; servidor para toda la red)
; o "solo para mí" (no pide credenciales). Con /CURRENTUSER o /ALLUSERS se
; elige sin preguntar. El usuario "admin" de la aplicación se crea con clave
; aleatoria, que se muestra al final.
PrivilegesRequired=admin
PrivilegesRequiredOverridesAllowed=dialog commandline
UsedUserAreasWarning=no
WizardStyle=modern
SetupLogging=yes
InfoBeforeFile={#DirBuild}\docs\LEEME_INSTALACION.txt
UninstallDisplayName=Costeo ABC Ambulancias

[Languages]
Name: "es"; MessagesFile: "compiler:Languages\Spanish.isl"

[Files]
Source: "{#DirBuild}\python\*"; DestDir: "{app}\python"; Flags: recursesubdirs ignoreversion
Source: "{#DirBuild}\app\*"; DestDir: "{app}\app"; Flags: recursesubdirs ignoreversion
Source: "{#DirBuild}\scripts\*"; DestDir: "{app}\scripts"; Flags: ignoreversion
Source: "{#DirBuild}\docs\*"; DestDir: "{app}\docs"; Flags: ignoreversion

[INI]
Filename: "{app}\Costeo ABC Ambulancias.url"; Section: "InternetShortcut"; Key: "URL"; String: "http://localhost:{code:Puerto}/"

[Icons]
Name: "{group}\Abrir Costeo ABC Ambulancias"; Filename: "{app}\Costeo ABC Ambulancias.url"
Name: "{autodesktop}\Costeo ABC Ambulancias"; Filename: "{app}\Costeo ABC Ambulancias.url"
Name: "{group}\Respaldar base de datos ahora"; Filename: "{app}\python\python.exe"; Parameters: "manage.py respaldar_bd"; WorkingDir: "{app}\app"
Name: "{group}\Documentación"; Filename: "{app}\docs"
Name: "{group}\Usuario y clave inicial"; Filename: "{autoappdata}\CosteoABCAmbulancias\CREDENCIALES_ADMIN.txt"
Name: "{group}\Habilitar acceso desde la red (requiere administrador)"; Filename: "powershell.exe"; Parameters: "-NoProfile -ExecutionPolicy Bypass -File ""{app}\scripts\habilitar_red.ps1"""; Check: not IsAdminInstallMode
Name: "{group}\Desinstalar"; Filename: "{uninstallexe}"

[Run]
Filename: "powershell.exe"; \
  Parameters: "-NoProfile -ExecutionPolicy Bypass -File ""{app}\scripts\configurar.ps1"" -Puerto {param:PUERTO|0} -Modo {code:Modo} -DirDatos ""{autoappdata}\CosteoABCAmbulancias"""; \
  StatusMsg: "Configurando base de datos, usuario administrador y tareas programadas..."; Flags: runhidden waituntilterminated
Filename: "notepad.exe"; Parameters: """{autoappdata}\CosteoABCAmbulancias\CREDENCIALES_ADMIN.txt"""; \
  Description: "Ver el usuario y la clave inicial del administrador"; Flags: postinstall nowait skipifsilent; \
  Check: FileExists(ExpandConstant('{autoappdata}\CosteoABCAmbulancias\CREDENCIALES_ADMIN.txt'))
Filename: "http://localhost:{code:Puerto}/"; Description: "Abrir la aplicación en el navegador"; Flags: postinstall shellexec nowait skipifsilent

[UninstallRun]
Filename: "powershell.exe"; Parameters: "-NoProfile -ExecutionPolicy Bypass -File ""{app}\scripts\desinstalar.ps1"""; Flags: runhidden; RunOnceId: "QuitarTareas"

[UninstallDelete]
Type: filesandordirs; Name: "{app}\app"
Type: files; Name: "{app}\Costeo ABC Ambulancias.url"

[Code]
{ Puerto: /PUERTO=nnnn en la línea de comandos, o el que dejó configurar.ps1 en
  ambulancias.ini (puede ser otro si el pedido estaba ocupado), o 8090. }
function Puerto(Param: String): String;
var
  Ini: String;
begin
  Result := ExpandConstant('{param:PUERTO|}');
  if Result = '' then
  begin
    Ini := ExpandConstant('{autoappdata}\CosteoABCAmbulancias\ambulancias.ini');
    Result := Trim(GetIniString('servidor', 'puerto', '8090', Ini));
  end;
  if StrToIntDef(Result, 0) = 0 then
    Result := '8090';
end;

function Modo(Param: String): String;
begin
  if IsAdminInstallMode then
    Result := 'todos'
  else
    Result := 'usuario';
end;
