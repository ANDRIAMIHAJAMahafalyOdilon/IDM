[Setup]
AppName=Roch Xd
AppVersion=1.0
DefaultDirName={autopf}\Roch Xd
DefaultGroupName=Roch Xd
OutputBaseFilename=Roch-Xd-Setup
OutputDir=..\installateur
Compression=lzma
SolidCompression=yes

[Files]
Source: "..\dist\Roch Xd\*"; DestDir: "{app}"; Flags: recursesubdirs
Source: "..\extension\*"; DestDir: "{app}\extension"; Flags: recursesubdirs

[Icons]
Name: "{group}\Roch Xd"; Filename: "{app}\Roch Xd.exe"
Name: "{autodesktop}\Roch Xd"; Filename: "{app}\Roch Xd.exe"

[Run]
Filename: "{app}\Roch Xd.exe"; Description: "Lancer Roch Xd"; Flags: nowait postinstall skipifsilent
