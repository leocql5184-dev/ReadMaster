#ifndef Payload
  #error Payload must be supplied by build_installer.py
#endif
#ifndef Output
  #error Output must be supplied
#endif
#define Version "0.5.0.1"

[Setup]
AppId={{7E0C4C63-5F5A-47EE-AB88-7331232D937D}
AppName=ReadMaster
AppVersion={#Version}
AppPublisher=ReadMaster contributors
AppPublisherURL=https://github.com/leocql5184-dev/ReadMaster
DefaultDirName={localappdata}\Programs\ReadMaster
DefaultGroupName=ReadMaster
PrivilegesRequired=lowest
SetupArchitecture=x64
ArchitecturesAllowed=x64os
ArchitecturesInstallIn64BitMode=x64os
MinVersion=10.0
OutputDir={#Output}
OutputBaseFilename=ReadMaster-Full-{#Version}-Windows-x64
SetupIconFile={#Payload}\assets\ReadMaster.ico
UninstallDisplayIcon={app}\ReadMaster.exe
WizardStyle=modern
DisableProgramGroupPage=yes
LicenseFile={#Payload}\INSTALL-LICENSE.txt
InfoBeforeFile={#Payload}\INSTALL-README.txt
Compression=lzma2/fast
SolidCompression=no
DiskSpanning=yes
DiskSliceSize=1500000000
SlicesPerDisk=1
CloseApplications=yes
RestartApplications=no
SetupLogging=yes
UninstallLogging=yes
VersionInfoVersion={#Version}

[Languages]
Name: "chinesesimplified"; MessagesFile: "compiler:Languages\ChineseSimplified.isl"
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "创建桌面快捷方式 / Create desktop shortcut"; Flags: unchecked

[Files]
Source: "{#Payload}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "{#WebViewInstaller}"; DestName: "WebView2-x64.exe"; Flags: dontcopy

[Icons]
Name: "{group}\ReadMaster"; Filename: "{app}\ReadMaster.exe"; WorkingDir: "{app}"
Name: "{autodesktop}\ReadMaster"; Filename: "{app}\ReadMaster.exe"; WorkingDir: "{app}"; Tasks: desktopicon

[Run]
Filename: "{app}\ReadMaster.exe"; Description: "打开 ReadMaster"; Flags: nowait postinstall skipifsilent

[Code]
var
  RuntimeDownload: TDownloadWizardPage;

procedure InitializeWizard;
begin
  RuntimeDownload := CreateDownloadPage('下载微软运行库 / Microsoft runtime',
    '仅在系统缺少运行库时，从微软官方下载并校验。', nil);
  RuntimeDownload.ShowBaseNameInsteadOfUrl := True;
end;

function HasVCRuntime(): Boolean;
begin
  Result := FileExists(ExpandConstant('{sys}\msvcp140.dll')) and
    FileExists(ExpandConstant('{sys}\msvcp140_1.dll')) and
    FileExists(ExpandConstant('{sys}\vcruntime140.dll')) and
    FileExists(ExpandConstant('{sys}\vcruntime140_1.dll'));
end;

function InstallVCRuntime(var NeedsRestart: Boolean): String;
var
  RuntimePath: String;
  Code: Integer;
begin
  Result := '';
  if HasVCRuntime() then begin
    Log('Microsoft VC++ x64 runtime is already present.');
    Exit;
  end;
  RuntimePath := ExpandConstant('{src}\VC_redist.x64.exe');
  try
    if FileExists(RuntimePath) then begin
      if CompareText(GetSHA256OfFile(RuntimePath), '{#VCRedistSHA256}') <> 0 then begin
        Result := '同目录微软运行库校验失败，请使用安装说明指定的官方版本。 / VC++ checksum mismatch.';
        Exit;
      end;
    end else begin
      RuntimeDownload.Clear;
      RuntimeDownload.Add('{#VCRedistURL}', 'VC_redist.x64.exe', '{#VCRedistSHA256}');
      RuntimeDownload.Show;
      try
        RuntimeDownload.Download;
      finally
        RuntimeDownload.Hide;
      end;
      RuntimePath := ExpandConstant('{tmp}\VC_redist.x64.exe');
    end;
    if not ShellExec('runas', RuntimePath, '/install /passive /norestart', '',
      SW_SHOWNORMAL, ewWaitUntilTerminated, Code) then begin
      Result := '微软运行库未能安装；请允许其 Windows 管理员提示后重试。 / Cannot start Microsoft VC++ installer.';
      Exit;
    end;
    if Code = 3010 then NeedsRestart := True;
    if not HasVCRuntime() then
      Result := '微软运行库尚未就绪，请重启或手动安装后重试。 / Microsoft VC++ runtime unavailable. Code: ' + IntToStr(Code);
  except
    Result := '微软运行库准备失败 / Microsoft runtime preparation failed: ' + GetExceptionMessage;
  end;
end;

function HasWebView2(): Boolean;
var
  Version: String;
  Key: String;
begin
  Key := 'Software\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}';
  Result := (RegQueryStringValue(HKLM32, Key, 'pv', Version) and (Version <> '') and (Version <> '0.0.0.0'));
  if not Result then
    Result := (RegQueryStringValue(HKCU, Key, 'pv', Version) and (Version <> '') and (Version <> '0.0.0.0'));
end;

function PrepareToInstall(var NeedsRestart: Boolean): String;
var
  Code: Integer;
begin
  Result := InstallVCRuntime(NeedsRestart);
  if Result <> '' then Exit;
  if not HasWebView2() then begin
    ExtractTemporaryFile('WebView2-x64.exe');
    if not Exec(ExpandConstant('{tmp}\WebView2-x64.exe'), '/silent /install', '', SW_HIDE, ewWaitUntilTerminated, Code) then
      Result := '无法启动 WebView2 安装程序 / Cannot start WebView2 installer.'
    else if not HasWebView2() then
      Result := 'WebView2 尚未安装成功。请查看安装日志并重试。 / WebView2 installation failed. Code: ' + IntToStr(Code);
  end;
end;
