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
PrivilegesRequired=admin
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

[Dirs]
Name: "{commonappdata}\CosteoABCAmbulancias"; Flags: uninsneveruninstall
Name: "{commonappdata}\CosteoABCAmbulancias\respaldos"; Flags: uninsneveruninstall

[INI]
Filename: "{app}\Costeo ABC Ambulancias.url"; Section: "InternetShortcut"; Key: "URL"; String: "http://localhost:{code:Puerto}/"

[Icons]
Name: "{group}\Abrir Costeo ABC Ambulancias"; Filename: "{app}\Costeo ABC Ambulancias.url"
Name: "{commondesktop}\Costeo ABC Ambulancias"; Filename: "{app}\Costeo ABC Ambulancias.url"
Name: "{group}\Respaldar base de datos ahora"; Filename: "{app}\python\python.exe"; Parameters: "manage.py respaldar_bd"; WorkingDir: "{app}\app"
Name: "{group}\Documentación"; Filename: "{app}\docs"
Name: "{group}\Desinstalar"; Filename: "{uninstallexe}"

[Run]
Filename: "powershell.exe"; \
  Parameters: "-NoProfile -ExecutionPolicy Bypass -File ""{app}\scripts\configurar.ps1"" -Puerto {code:Puerto} -ClaveAdmin ""{code:ClaveAdmin}"""; \
  StatusMsg: "Configurando base de datos, firewall y tareas programadas..."; Flags: runhidden waituntilterminated
Filename: "http://localhost:{code:Puerto}/"; Description: "Abrir la aplicación en el navegador"; Flags: postinstall shellexec nowait skipifsilent

[UninstallRun]
Filename: "powershell.exe"; Parameters: "-NoProfile -ExecutionPolicy Bypass -File ""{app}\scripts\desinstalar.ps1"""; Flags: runhidden; RunOnceId: "QuitarTareas"

[UninstallDelete]
Type: filesandordirs; Name: "{app}\app"
Type: files; Name: "{app}\Costeo ABC Ambulancias.url"

[Code]
var
  PaginaConfig: TInputQueryWizardPage;

procedure InitializeWizard;
begin
  PaginaConfig := CreateInputQueryPage(wpSelectDir,
    'Configuración del servidor',
    'Puerto web y usuario administrador',
    'Los demás equipos de la red abrirán http://NOMBRE-DE-ESTE-EQUIPO:PUERTO/ en el navegador. ' +
    'La base de datos queda centralizada en C:\ProgramData\CosteoABCAmbulancias. ' +
    'Deje la clave en blanco si está actualizando y no desea cambiarla.');
  PaginaConfig.Add('Puerto (por defecto 8000):', False);
  PaginaConfig.Add('Clave del usuario "admin" (mínimo 8 caracteres):', True);
  PaginaConfig.Values[0] := '8000';
end;

function NextButtonClick(CurPageID: Integer): Boolean;
var
  P: Integer;
begin
  Result := True;
  if CurPageID = PaginaConfig.ID then
  begin
    P := StrToIntDef(PaginaConfig.Values[0], 0);
    if (P < 1024) or (P > 65535) then
    begin
      MsgBox('Indique un puerto entre 1024 y 65535.', mbError, MB_OK);
      Result := False;
    end
    else if (PaginaConfig.Values[1] <> '') and (Length(PaginaConfig.Values[1]) < 8) then
    begin
      MsgBox('La clave debe tener al menos 8 caracteres.', mbError, MB_OK);
      Result := False;
    end
    else if (PaginaConfig.Values[1] = '') and not FileExists(ExpandConstant('{commonappdata}\CosteoABCAmbulancias\ambulancias.ini')) then
    begin
      MsgBox('En una instalación nueva debe definir la clave del administrador.', mbError, MB_OK);
      Result := False;
    end;
  end;
end;

function Puerto(Param: String): String;
begin
  Result := PaginaConfig.Values[0];
end;

function ClaveAdmin(Param: String): String;
begin
  Result := PaginaConfig.Values[1];
  StringChangeEx(Result, '"', '', True);
end;
