; 仓库管理系统 —— Windows 安装程序（Inno Setup 6）
; 本文件必须是 UTF-8 BOM，否则 ISCC 按 GBK 读，中文会乱码
; 双击本文件需要本机装了 Inno Setup；GitHub Actions 会自动调用
;
; 说明：程序装到 Program Files（只读），所以数据库会自动落到
;       %APPDATA%\仓库管理系统\warehouse.db —— 升级/卸载都不会弄丢数据。

#define MyAppName      "仓库管理系统"
#define MyAppExeName   "仓库管理系统.exe"
#define MyAppPublisher "仓库管理系统"
; 版本号由打包脚本用 /DMyAppVersion=3.xx 传进来
#ifndef MyAppVersion
  #define MyAppVersion "3.62"
#endif

[Setup]
AppId={{A7F3C1E0-9B24-4D58-B6E1-5C2A8D7F4E10}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppVerName={#MyAppName} v{#MyAppVersion}
AppPublisher={#MyAppPublisher}
DefaultDirName={autopf}\{#MyAppName}
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
DisableDirPage=auto
UninstallDisplayName={#MyAppName} v{#MyAppVersion}
UninstallDisplayIcon={app}\{#MyAppExeName}
SetupIconFile=app.ico
WizardStyle=modern
WizardResizable=no
Compression=lzma2/max
SolidCompression=yes
OutputDir=dist
OutputBaseFilename=仓库管理系统_安装程序_v{#MyAppVersion}
AllowNoIcons=yes

[Languages]
Name: "chinesesimplified"; MessagesFile: "compiler:Languages\ChineseSimplified.isl"
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "创建桌面快捷方式"; GroupDescription: "附加图标:"; Flags: unchecked

[Files]
; 目录模式（onedir）：exe 旁边还有 _internal 等一整套依赖，
; Qt WebEngine 的 QtWebEngineProcess / 资源 / 翻译都在里面，必须整个目录带走。
Source: "dist\仓库管理系统\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "app.ico"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; IconFilename: "{app}\app.ico"
Name: "{group}\卸载 {#MyAppName}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; IconFilename: "{app}\app.ico"; Tasks: desktopicon

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "安装完成后立即运行"; Flags: nowait postinstall skipifsilent

[UninstallDelete]
Type: filesandordirs; Name: "{app}\*.log"

[Code]
// 卸载时问一句要不要连数据一起删，默认保留（数据比程序值钱）
function InitializeUninstall(): Boolean;
begin
  Result := True;
  if MsgBox('要连数据一起删除吗？' #13#10#13#10 +
            '选「是」：程序和数据（台账、采购、盘点记录）全部删除。' #13#10 +
            '选「否」：只删程序，数据保留，下次重装还能接着用。',
            mbConfirmation, MB_YESNO) = IDYES then
  begin
    DelTree(ExpandConstant('{%APPDATA}\仓库管理系统'), True, True, True);
  end;
end;
