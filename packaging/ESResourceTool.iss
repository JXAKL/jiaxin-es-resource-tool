#ifndef SourceFolder
  #error SourceFolder must point to the complete clean portable application
#endif
#ifndef ReleaseVersion
  #define ReleaseVersion "2.1.24"
#endif
#ifndef ReleaseOutput
  #define ReleaseOutput "..\dist"
#endif
[Setup]
AppId={{77D99753-6E94-413F-8242-A9981C692F62}
AppName=夹心的ES资源工具
AppVersion={#ReleaseVersion}
AppVerName=夹心的ES资源工具 {#ReleaseVersion}
DefaultDirName={localappdata}\Programs\JiaxinESResourceTool
DefaultGroupName=夹心的ES资源工具
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0
DisableProgramGroupPage=yes
DisableDirPage=no
UninstallDisplayIcon={app}\夹心的ES资源工具.exe
SetupIconFile=..\assets\jiaxin.ico
OutputDir={#ReleaseOutput}
OutputBaseFilename=Jiaxin-ES-Resource-Tool-{#ReleaseVersion}-Setup
Compression=lzma2/normal
SolidCompression=yes
WizardStyle=modern
CloseApplications=no
RestartApplications=no

[Languages]
Name: "chinesesimp"; MessagesFile: "compiler:Languages\ChineseSimplified.isl"
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "创建桌面快捷方式"; GroupDescription: "快捷方式："

[Files]
Source: "{#SourceFolder}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\夹心的ES资源工具"; Filename: "{app}\夹心的ES资源工具.exe"; WorkingDir: "{app}"
Name: "{autodesktop}\夹心的ES资源工具"; Filename: "{app}\夹心的ES资源工具.exe"; WorkingDir: "{app}"; Tasks: desktopicon
