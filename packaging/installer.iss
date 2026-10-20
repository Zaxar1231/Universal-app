; Установщик Universal для Windows (Inno Setup 6).
; Сборка: iscc /DAppVersion=1.1.0 packaging\installer.iss  (после pyinstaller)
; Ставится в профиль пользователя, права администратора не нужны.

#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif

[Setup]
AppId={{6B1F3C2E-8D4A-4E59-9A3B-2C7D5E0F1A84}
AppName=Universal
AppVersion={#AppVersion}
AppVerName=Universal {#AppVersion}
AppPublisher=Universal
AppPublisherURL=https://github.com/Zaxar1231/Universal-app
AppSupportURL=https://github.com/Zaxar1231/Universal-app/issues
DefaultDirName={localappdata}\Programs\Universal
DefaultGroupName=Universal
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
OutputDir=..\Output
OutputBaseFilename=Universal-Setup-{#AppVersion}
SetupIconFile=universal.ico
UninstallDisplayIcon={app}\Universal.exe
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
CloseApplications=yes

[Languages]
Name: "ru"; MessagesFile: "compiler:Languages\Russian.isl"
Name: "en"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"

[Files]
Source: "..\dist\Universal\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\Universal"; Filename: "{app}\Universal.exe"
Name: "{autodesktop}\Universal"; Filename: "{app}\Universal.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\Universal.exe"; Description: "{cm:LaunchProgram,Universal}"; Flags: nowait postinstall skipifsilent
; обновление из самого приложения идёт тихо (/SILENT): после него сразу открываем новую версию
Filename: "{app}\Universal.exe"; Flags: nowait; Check: WizardSilent

[UninstallDelete]
; задания и логи; скачанные движки (ffmpeg и т.д.) удаляются вместе с ними
Type: filesandordirs; Name: "{localappdata}\Universal"

[Code]
{ Окно приложения рисует WebView2. В Windows 11 он встроен, в Windows 10 почти всегда
  приходит с Edge; если его нет, скачиваем официальный установщик Microsoft. }
const
  WebView2Key = 'SOFTWARE\WOW6432Node\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}';
  WebView2KeyUser = 'Software\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}';

var
  DownloadPage: TDownloadWizardPage;

function HasWebView2: Boolean;
var
  V: String;
begin
  Result := (RegQueryStringValue(HKLM, WebView2Key, 'pv', V) and (V <> '') and (V <> '0.0.0.0'))
    or (RegQueryStringValue(HKCU, WebView2KeyUser, 'pv', V) and (V <> '') and (V <> '0.0.0.0'));
end;

procedure InitializeWizard;
begin
  DownloadPage := CreateDownloadPage(SetupMessage(msgWizardPreparing), 'Скачиваю Microsoft WebView2…', nil);
end;

function NextButtonClick(CurPageID: Integer): Boolean;
var
  Code: Integer;
begin
  Result := True;
  if (CurPageID = wpReady) and not HasWebView2 then
  begin
    DownloadPage.Clear;
    DownloadPage.Add('https://go.microsoft.com/fwlink/p/?LinkId=2124703', 'MicrosoftEdgeWebview2Setup.exe', '');
    DownloadPage.Show;
    try
      try
        DownloadPage.Download;
        Exec(ExpandConstant('{tmp}\MicrosoftEdgeWebview2Setup.exe'), '/silent /install', '', SW_SHOW, ewWaitUntilTerminated, Code);
      except
        MsgBox('Не удалось скачать WebView2: ' + GetExceptionMessage + #13#10 +
               'Universal установится, но для окна приложения понадобится WebView2 Runtime с сайта Microsoft.',
               mbInformation, MB_OK);
      end;
    finally
      DownloadPage.Hide;
    end;
  end;
end;
