[Setup]
AppName=FlashDL
AppVersion=1.0
DefaultDirName={autopf}\FlashDL
DefaultGroupName=FlashDL
OutputBaseFilename=FlashDL-Setup
OutputDir=..\installateur
Compression=lzma
SolidCompression=yes

[Files]
Source: "..\dist\FlashDL\*"; DestDir: "{app}"; Flags: recursesubdirs

[Icons]
Name: "{group}\FlashDL"; Filename: "{app}\FlashDL.exe"
Name: "{autodesktop}\FlashDL"; Filename: "{app}\FlashDL.exe"

[Run]
Filename: "{app}\FlashDL.exe"; Description: "Lancer FlashDL"; Flags: nowait postinstall skipifsilent