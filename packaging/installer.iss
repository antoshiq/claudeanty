; Inno Setup 6: установщик claudeAnty. Собирается из packaging/build.ps1:
;   ISCC packaging\installer.iss /DAppVersion=1.6.0 /DSourceDir=build\dist\claudeAnty /Obuild\release
; Ставится без прав администратора в %LOCALAPPDATA%\Programs\claudeAnty. Данные профилей — отдельно,
; в %LOCALAPPDATA%\claudeAnty\data: обновление и удаление программы их не трогают.

#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif
#ifndef SourceDir
  #define SourceDir "..\build\dist\claudeAnty"
#endif

[Setup]
AppId={{6F1B7A52-3C0E-4C55-9B0B-6C1A2D4E8F31}
AppName=claudeAnty
AppVersion={#AppVersion}
AppVerName=claudeAnty {#AppVersion}
AppPublisher=claudeAnty
DefaultDirName={localappdata}\Programs\claudeAnty
DefaultGroupName=claudeAnty
DisableProgramGroupPage=yes
DisableDirPage=auto
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
OutputBaseFilename=claudeAnty-setup-{#AppVersion}
Compression=lzma2/ultra64
SolidCompression=yes
WizardStyle=modern
; при обновлении: закрыть работающий claudeAnty (иначе файлы заняты)
CloseApplications=force
RestartApplications=no
UninstallDisplayIcon={app}\claudeAnty.exe
UninstallDisplayName=claudeAnty

[Languages]
Name: "ru"; MessagesFile: "compiler:Languages\Russian.isl"
Name: "en"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"

[InstallDelete]
; библиотеки старой версии — удалить перед копированием новых (иначе останутся лишние файлы)
Type: filesandordirs; Name: "{app}\_internal"

[Files]
Source: "{#SourceDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\claudeAnty"; Filename: "{app}\claudeAnty.exe"
Name: "{autodesktop}\claudeAnty"; Filename: "{app}\claudeAnty.exe"; Tasks: desktopicon

[Run]
; без skipifsilent: после тихого автообновления программа запускается снова
Filename: "{app}\claudeAnty.exe"; Description: "{cm:LaunchProgram,claudeAnty}"; Flags: nowait postinstall
