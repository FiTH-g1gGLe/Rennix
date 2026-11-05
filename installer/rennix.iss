; Rennix Windows installer (Inno Setup 6)
; Per-user install: no admin rights needed. Settings and history in %APPDATA% /
; %LOCALAPPDATA% are kept when Rennix is uninstalled or updated.

#ifndef AppVersion
  #define AppVersion "0.1.0"
#endif

[Setup]
AppId={{6F1E7B2A-4C8D-4E2B-9A61-52E0B7C3D9F4}
AppName=Rennix
AppVersion={#AppVersion}
AppPublisher=Rennix
AppPublisherURL=https://github.com/FiTH-g1gGLe/Rennix
DefaultDirName={localappdata}\Programs\Rennix
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
OutputDir=..\dist
OutputBaseFilename=Rennix-Setup-{#AppVersion}
SetupIconFile=..\assets\rennix.ico
UninstallDisplayIcon={app}\Rennix.exe
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
ArchitecturesInstallIn64BitMode=x64compatible

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; GroupDescription: "Shortcuts:"

[Files]
Source: "..\dist\Rennix\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\Rennix"; Filename: "{app}\Rennix.exe"
Name: "{autodesktop}\Rennix"; Filename: "{app}\Rennix.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\Rennix.exe"; Description: "Start Rennix"; Flags: nowait postinstall skipifsilent
