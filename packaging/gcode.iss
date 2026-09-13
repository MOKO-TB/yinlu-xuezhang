; 引路学长 安装包脚本(Inno Setup 6.7+)
; 构建: python scripts/build_exe.py  (会自动调用 ISCC 编译本脚本)
; 产物: dist/引路学长-<版本>-setup.exe

#define MyAppName "引路学长"
#define MyAppExeName "引路学长.exe"
#ifndef AppVersion
#define AppVersion "0.0.0"
#endif

[Setup]
AppId={{BE78355C-FF23-4B30-976C-1294DE86B24C}
AppName={#MyAppName}
AppVersion={#AppVersion}
AppPublisher={#MyAppName}
; 仅当前用户安装:无需管理员权限,默认装到 %LOCALAPPDATA%\Programs\引路学长
PrivilegesRequired=lowest
DefaultDirName={autopf}\{#MyAppName}
DisableProgramGroupPage=yes
UninstallDisplayIcon={app}\{#MyAppExeName}
OutputDir=..\dist
OutputBaseFilename=引路学长-{#AppVersion}-setup
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
SetupIconFile=..\assets\gcode.ico

[Languages]
Name: "chinesesimplified"; MessagesFile: "compiler:Languages\ChineseSimplified.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"

[Files]
Source: "..\dist\引路学长\*"; DestDir: "{app}"; Flags: recursesubdirs createallsubdirs ignoreversion

[Icons]
Name: "{autoprograms}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "{cm:LaunchProgram,{#MyAppName}}"; Flags: nowait postinstall skipifsilent

[UninstallDelete]
; 升级/卸载时保留用户数据(config.json / sessions / memory / kb),这里不配置删除
