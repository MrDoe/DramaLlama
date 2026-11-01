; Inno Setup wrapper for the Drama Llama installer.
;
; This only installs the graphical setup wizard per user and registers an
; uninstall entry. The wizard itself installs the actual application into
; %LOCALAPPDATA%\DramaLlama (or a folder the user picks) and can add engine
; components. No administrator rights are required unless the user asks the
; wizard to install Docker or the Vulkan loader.
;
; Build with scripts/build_installer.py, or manually:
;   iscc /DAppVersion=0.3.0 /DSourceDir=..\..\..\dist\DramaLlama-Setup \
;        /DOutputDir=..\..\..\dist installer\packaging\inno\DramaLlama.iss

#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif
#ifndef SourceDir
  #define SourceDir "..\..\..\dist\DramaLlama-Setup"
#endif
#ifndef OutputDir
  #define OutputDir "..\..\..\dist"
#endif
#ifndef OutputBaseFilename
  #define OutputBaseFilename "DramaLlama-Setup-" + AppVersion
#endif

[Setup]
AppId={{6F1C3C25-0B8E-4E4C-9E1A-6E0B7A2B7D11}
AppName=Drama Llama
AppVersion={#AppVersion}
AppVerName=Drama Llama {#AppVersion}
AppPublisher=Drama Llama
AppPublisherURL=https://github.com/MrDoe/DramaLlama
AppSupportURL=https://github.com/MrDoe/DramaLlama/issues
AppUpdatesURL=https://github.com/MrDoe/DramaLlama/releases
DefaultDirName={localappdata}\Programs\DramaLlama
DefaultGroupName=Drama Llama
DisableProgramGroupPage=yes
DisableDirPage=no
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
OutputDir={#OutputDir}
OutputBaseFilename={#OutputBaseFilename}
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
UninstallDisplayName=Drama Llama
UninstallDisplayIcon={app}\DramaLlama-Setup.exe
CloseApplications=no
RestartApplications=no
SetupLogging=yes
#ifdef SetupIconFile
SetupIconFile={#SetupIconFile}
#endif

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"
Name: "german"; MessagesFile: "compiler:Languages\German.isl"

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut for the installer"; GroupDescription: "Additional shortcuts:"; Flags: unchecked

[Files]
Source: "{#SourceDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
; Keep a small marker so the uninstaller can be re-run safely after the app
; itself was removed by the wizard.
Source: "..\..\..\installer\pins.json"; DestDir: "{app}\installer"; Flags: ignoreversion

[Icons]
Name: "{group}\Install or update Drama Llama"; Filename: "{app}\DramaLlama-Setup.exe"; Comment: "Install, repair or remove Drama Llama"
Name: "{group}\Uninstall Drama Llama"; Filename: "{uninstallexe}"
Name: "{autodesktop}\Drama Llama installer"; Filename: "{app}\DramaLlama-Setup.exe"; Tasks: desktopicon

[Run]
; Offer the wizard after a normal interactive setup. Silent installs skip it;
; pass /MERGETASKS or run DramaLlama-Setup.exe afterwards.
Filename: "{app}\DramaLlama-Setup.exe"; Description: "Open the Drama Llama setup wizard"; Flags: postinstall nowait skipifsilent

[UninstallRun]
; Remove the application (keeping projects) before Inno deletes the wizard
; itself. The wizard follows the default-prefix location pointer written at
; install time, so a custom install folder is found too.
Filename: "{app}\DramaLlama-Setup.exe"; Parameters: "--headless --uninstall --yes --keep-data"; Flags: runhidden waituntilterminated; RunOnceId: "DramaLlamaUninstall"
