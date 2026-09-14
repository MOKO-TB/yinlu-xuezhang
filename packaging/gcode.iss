; 引路学长 安装包脚本(Inno Setup 6.7+)
; 构建: python scripts/build_exe.py  (会自动调用 ISCC 编译本脚本)
; 产物: dist/引路学长-<版本>-setup.exe
; 构建脚本会额外传 /DLibDbBytes=<馆藏库字节数>,安装完成后用它校验库是否完整。

#define MyAppName "引路学长"
#define MyAppExeName "引路学长.exe"
#ifndef AppVersion
#define AppVersion "0.0.0"
#endif
; 内置馆藏库(library.db)的字节数;0 表示未知,则跳过完整性校验
#ifndef LibDbBytes
#define LibDbBytes "0"
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

[Code]
{ 安装后校验内置馆藏库(library.db,约 1.3 GB)。
  这是“分享给别人装完却说图书馆搜不到书”的头号原因:盘不够/复制被中断时,
  库里只剩半截。安装时就当面报错,比用户事后搜书搜不到好查得多。 }
procedure CurStepChanged(CurStep: TSetupStep);
var
  P: String;
  Wanted: Int64;
  ActualHigh, ActualLow: LongWord;
  Rec: TFindRec;
begin
  if CurStep = ssPostInstall then
  begin
    P := ExpandConstant('{app}\crawled\mfindgzyykj.libsp.cn\library.db');
    Wanted := StrToInt64Def('{#LibDbBytes}', 0);
    if not FileExists(P) then
      MsgBox('图书馆馆藏数据没有安装成功(缺少 library.db)。' + Chr(13) + Chr(10) + Chr(10) +
             '图书馆搜书功能将不可用。请确认磁盘空间充足后重新运行本安装包。',
             mbError, MB_OK)
    else
    begin
      ActualHigh := 0;
      ActualLow := 0;
      if FindFirst(P, Rec) then
      begin
        ActualHigh := Rec.SizeHigh;
        ActualLow := Rec.SizeLow;
        FindClose(Rec);
      end;
      if (Wanted > 0) and ((ActualHigh <> 0) or (ActualLow <> Wanted)) then
        MsgBox('图书馆馆藏数据不完整(安装后大小与安装包里的不一致)。' + Chr(13) + Chr(10) + Chr(10) +
               '图书馆搜书功能会不可用。请腾出磁盘空间后重新运行本安装包。',
               mbError, MB_OK);
    end;
  end;
end;
