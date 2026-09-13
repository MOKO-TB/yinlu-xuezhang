; 引路学长·完整版(内置 crawl4ai + 无头浏览器内核)安装包脚本
; 构建: python scripts/build_crawl4ai_exe.py  (会自动调用 ISCC 编译本脚本)
; 产物: dist/引路学长-<版本>-crawl4ai-setup.exe
;
; 与轻量版(gcode.iss)的区别:
;   - AppId 不同 → 两个版本可共存,互不覆盖;
;   - 安装目录名带「完整版」,开始菜单/桌面快捷方式同名区分;
;   - 内含 ms-playwright 浏览器内核(约 270MB),压缩用 lzma2/fast 控制构建时长。

#define MyAppName "引路学长·完整版"
#define MyAppExeName "引路学长.exe"
#ifndef AppVersion
#define AppVersion "0.0.0"
#endif

[Setup]
AppId={{7C1A9E04-5B62-4F1D-9E77-3A2D5B8C41F6}
AppName={#MyAppName}
AppVersion={#AppVersion}
AppPublisher={#MyAppName}
PrivilegesRequired=lowest
DefaultDirName={autopf}\引路学长-完整版
DisableProgramGroupPage=yes
UninstallDisplayIcon={app}\{#MyAppExeName}
OutputDir=..\dist
OutputBaseFilename=引路学长-{#AppVersion}-crawl4ai-setup
Compression=lzma2/fast
SolidCompression=no
WizardStyle=modern
SetupIconFile=..\assets\gcode.ico

[Languages]
Name: "chinesesimplified"; MessagesFile: "compiler:Languages\ChineseSimplified.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"

[Files]
Source: "..\dist\引路学长-crawl4ai\*"; DestDir: "{app}"; Flags: recursesubdirs createallsubdirs ignoreversion

[Icons]
Name: "{autoprograms}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "{cm:LaunchProgram,{#MyAppName}}"; Flags: nowait postinstall skipifsilent

[UninstallDelete]
; 升级/卸载时保留用户数据(config.json / sessions / memory / kb)
