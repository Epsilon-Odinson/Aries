; Aries installer — Inno Setup script.
; Builds Aries-Setup.exe from the PyInstaller build (dist\Aries.exe).
; The installer offers a desktop shortcut and a taskbar pin on its
; "Select Additional Tasks" page. Desktop shortcut always works.
; Taskbar pinning is attempted via the shell "Pin to taskbar" verb — Windows
; 10/11 restricts programmatic pinning, so if it does not stick the fallback
; is one right-click on the desktop icon -> "Pin to taskbar".
; The workflow builds this with Inno Setup (choco install innosetup).

#define MyAppName "Aries"
#define MyAppVersion "0.5.0"
#define MyAppPublisher "Epsilon"
#define MyAppExeName "Aries.exe"

[Setup]
AppId={{29687FB5-5AD6-48E4-BF8B-B8E37154F70C}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
DefaultDirName={autopf}\{#MyAppName}
DefaultGroupName={#MyAppName}
AllowNoIcons=yes
OutputDir=installer
OutputBaseFilename=Aries-Setup
SetupIconFile=aries.ico
Compression=lzma
SolidCompression=yes
WizardStyle=modern
PrivilegesRequired=lowest
UninstallDisplayIcon={app}\{#MyAppExeName}

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"
Name: "taskbarpin"; Description: "Pin Aries to the taskbar"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[Files]
Source: "dist\{#MyAppExeName}"; DestDir: "{app}"; Flags: ignoreversion
Source: "pin-taskbar.ps1"; DestDir: "{app}"; Flags: ignoreversion deleteafterinstall

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Run]
Filename: "powershell.exe"; Parameters: "-NoProfile -ExecutionPolicy Bypass -File ""{app}\pin-taskbar.ps1"" -Shortcut ""{group}\{#MyAppName}.lnk"""; Tasks: taskbarpin; Flags: runhidden; StatusMsg: "Pinning Aries to the taskbar..."
Filename: "{app}\{#MyAppExeName}"; Description: "{cm:LaunchProgram,{#StringChange(MyAppName, '&', '&&')}}"; Flags: nowait postinstall skipifsilent
