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
; /DAppOnly=1 → 编"仅 app"包(排掉馆藏库与浏览器内核)。同一份脚本、同一个 [Setup],
; 只切换输出文件名与 [Files] 的 Excludes —— 两份 .iss 的做法会让压缩参数有机会漂,
; 而"能不能跳过那 731 MB"的判断恰恰建立在两件产物出自同一棵树这件事上。
#ifdef AppOnly
#define AppOnlyMode 1
; 仅 app 包缺库时说的那句 —— 它和"完整包没装好库"是两件事,不能共用一句
#define NoLibraryMsg "'这个安装包不含馆藏库(它是给机器上已经有馆藏库的人更新用的)。' + Chr(13) + Chr(10) + Chr(10) + '第一次安装请改用完整包:引路学长-{#AppVersion}-setup.exe。'"
#else
#define AppOnlyMode 0
#define NoLibraryMsg "'图书馆馆藏数据没有安装成功(缺少 library.db)。' + Chr(13) + Chr(10) + Chr(10) + '图书馆搜书功能将不可用。请确认磁盘空间充足后重新运行本安装包。'"
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
#ifdef AppOnly
OutputBaseFilename=引路学长-{#AppVersion}-app-setup
#else
OutputBaseFilename=引路学长-{#AppVersion}-setup
#endif
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
SetupIconFile=..\assets\gcode.ico
[Languages]
Name: "chinesesimplified"; MessagesFile: "compiler:Languages\ChineseSimplified.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"

[Files]
#ifdef AppOnly
; 仅 app 包:排掉馆藏库(crawled/)与浏览器内核(ms-playwright/)——实测这两块占整包
; 84.6%(馆藏 59.7% + 内核 24.9%)。只有当**本机那两块的凭证与发布清单一致**时,
; App 才会选这一件(判定在 `minicode/proactive.py:plan_update`,不在安装包里)。
Source: "..\dist\引路学长\*"; DestDir: "{app}"; Excludes: "crawled\*,ms-playwright\*"; Flags: recursesubdirs createallsubdirs ignoreversion
#else
Source: "..\dist\引路学长\*"; DestDir: "{app}"; Flags: recursesubdirs createallsubdirs ignoreversion
#endif

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
      MsgBox({#NoLibraryMsg}, mbError, MB_OK)
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
