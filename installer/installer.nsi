; Modern MIDI Player - Windows installer (NSIS 3, Modern UI 2)
; Per-user install, no administrator rights needed.
Unicode true
!include "MUI2.nsh"
!include "FileFunc.nsh"
!include "LogicLib.nsh"
!include "x64.nsh"
!include "Sections.nsh"

!ifndef VERSION
  !define VERSION "1.0.0"
!endif
!define APPNAME   "Modern MIDI Player"
!define APPID     "ModernMidiPlayer"
!define PROGID    "ModernMidiPlayer.MIDI"
!define EXE       "ModernMidiPlayer.exe"
!define UNINSTKEY "Software\Microsoft\Windows\CurrentVersion\Uninstall\${APPID}"
!define SETTINGSKEY "Software\ModernMidi"
!define PYSIDE    "PySide6-Essentials>=6.5,<7"

Name "${APPNAME}"
OutFile "${OUTDIR}\ModernMidiPlayer-Setup-${VERSION}.exe"
InstallDir "$LOCALAPPDATA\Programs\${APPNAME}"
InstallDirRegKey HKCU "Software\${APPID}" "InstallDir"
RequestExecutionLevel user
Var Relaunch
Var OldDir
Var OldVer
SetCompressor /SOLID lzma
SetCompressorDictSize 64
ShowInstDetails show
ShowUninstDetails show
BrandingText "${APPNAME} ${VERSION}"

VIProductVersion "${VERSION}.0"
VIAddVersionKey "ProductName" "${APPNAME}"
VIAddVersionKey "FileDescription" "${APPNAME} Setup"
VIAddVersionKey "FileVersion" "${VERSION}"
VIAddVersionKey "ProductVersion" "${VERSION}"
VIAddVersionKey "LegalCopyright" "${APPNAME}"

; ---------------------------------------------------------------- UI
!define MUI_ICON   "${BUILDDIR}\icon.ico"
!define MUI_UNICON "${BUILDDIR}\icon.ico"
!define MUI_WELCOMEFINISHPAGE_BITMAP   "${BUILDDIR}\wizard.bmp"
!define MUI_UNWELCOMEFINISHPAGE_BITMAP "${BUILDDIR}\wizard.bmp"
!define MUI_HEADERIMAGE
!define MUI_HEADERIMAGE_RIGHT
!define MUI_HEADERIMAGE_BITMAP "${BUILDDIR}\header.bmp"
!define MUI_ABORTWARNING
!define MUI_COMPONENTSPAGE_SMALLDESC

!define MUI_WELCOMEPAGE_TEXT "This will install ${APPNAME} ${VERSION} on your computer.$\r$\n$\r$\n\
A modern MIDI player with per-channel mute / solo / volume, a 16-channel mixer, \
playlists and a piano-roll editor.$\r$\n$\r$\n\
No administrator rights are needed. An internet connection is used once during setup \
to download the Qt user-interface library (about 80 MB).$\r$\n$\r$\nClick Next to continue."
!insertmacro MUI_PAGE_WELCOME
!define MUI_LICENSEPAGE_TEXT_TOP "Third-party components included with or downloaded by this installer:"
!define MUI_LICENSEPAGE_BUTTON "&Next >"
!define MUI_LICENSEPAGE_TEXT_BOTTOM "Click Next to continue."
!insertmacro MUI_PAGE_LICENSE "${STAGEDIR}\app\THIRD-PARTY-NOTICES.txt"
!insertmacro MUI_PAGE_COMPONENTS
!define MUI_PAGE_CUSTOMFUNCTION_PRE SkipDirIfInstalled
!insertmacro MUI_PAGE_DIRECTORY
!insertmacro MUI_PAGE_INSTFILES
!define MUI_FINISHPAGE_RUN "$INSTDIR\${EXE}"
!define MUI_FINISHPAGE_RUN_TEXT "Launch ${APPNAME}"
!define MUI_FINISHPAGE_SHOWREADME "$INSTDIR\app\demos\demo_groove.mid"
!define MUI_FINISHPAGE_SHOWREADME_TEXT "Open the demo song"
!define MUI_FINISHPAGE_SHOWREADME_NOTCHECKED
!define MUI_FINISHPAGE_SHOWREADME_FUNCTION OpenDemo
!insertmacro MUI_PAGE_FINISH

!insertmacro MUI_UNPAGE_CONFIRM
!insertmacro MUI_UNPAGE_INSTFILES

!insertmacro MUI_LANGUAGE "English"

; ------------------------------------------------------------ helpers
!macro RESTORE_CHOICE NAME SEC
  ClearErrors
  ReadRegDWORD $0 HKCU "Software\${APPID}\Components" "${NAME}"
  ${IfNot} ${Errors}
    ${If} $0 == 1
      !insertmacro SelectSection ${SEC}
    ${Else}
      !insertmacro UnselectSection ${SEC}
    ${EndIf}
  ${EndIf}
!macroend

!macro SAVE_CHOICE NAME SEC
  ${If} ${SectionIsSelected} ${SEC}
    WriteRegDWORD HKCU "Software\${APPID}\Components" "${NAME}" 1
  ${Else}
    WriteRegDWORD HKCU "Software\${APPID}\Components" "${NAME}" 0
  ${EndIf}
!macroend


; An existing installation is updated in place, so don't ask for a folder again.
Function SkipDirIfInstalled
  ${If} $OldDir != ""
    Abort
  ${EndIf}
FunctionEnd

Function OpenDemo
  Exec '"$INSTDIR\${EXE}" "$INSTDIR\app\demos\demo_groove.mid"'
FunctionEnd

; Wait until the app is closed (its Python DLL is locked while running).
!macro WAIT_FOR_APP_CLOSED UN
Function ${UN}WaitForAppClosed
  StrCpy $9 0
  check:
  IfFileExists "$INSTDIR\python\python312.dll" 0 done
  ClearErrors
  Rename "$INSTDIR\python\python312.dll" "$INSTDIR\python\python312.dll.chk"
  ${If} ${Errors}
    ${If} $9 < 30
      IntOp $9 $9 + 1
      Sleep 500
      Goto check
    ${EndIf}
    StrCpy $9 0
    MessageBox MB_RETRYCANCEL|MB_ICONEXCLAMATION \
      "${APPNAME} is running. Please close it, then click Retry." /SD IDCANCEL IDRETRY check
    Abort
  ${EndIf}
  Rename "$INSTDIR\python\python312.dll.chk" "$INSTDIR\python\python312.dll"
  done:
FunctionEnd
!macroend
!insertmacro WAIT_FOR_APP_CLOSED ""
!insertmacro WAIT_FOR_APP_CLOSED "un."

; ------------------------------------------------------------ sections
Section "${APPNAME} (required)" SecMain
  SectionIn RO
  Call WaitForAppClosed

  ; Clean previous program files but keep the downloaded Qt library
  RMDir /r "$INSTDIR\app\midiplayer"
  RMDir /r "$INSTDIR\app\fluidsynth"

  SetOutPath "$INSTDIR"
  File "${OUTDIR}\${EXE}"
  File "${BUILDDIR}\icon.ico"
  SetOutPath "$INSTDIR\app"
  File /r "${STAGEDIR}\app\*.*"
  SetOutPath "$INSTDIR\python"
  File /r "${STAGEDIR}\python\*.*"

  ; ---- Qt user interface library (PySide6) from PyPI
  SetOutPath "$INSTDIR\app"
  DetailPrint "Downloading the Qt user-interface library (PySide6)... this can take a minute."
  pipretry:
  nsExec::ExecToLog '"$INSTDIR\python\python.exe" -m pip install --disable-pip-version-check --no-input --no-warn-script-location --progress-bar off --prefer-binary "${PYSIDE}"'
  Pop $0
  ${If} $0 != 0
    MessageBox MB_RETRYCANCEL|MB_ICONEXCLAMATION \
      "Downloading PySide6 failed (error $0).$\r$\n$\r$\nPlease check your internet connection and click Retry.$\r$\n\
If you Cancel, ${APPNAME} will offer to download it the first time it starts." /SD IDCANCEL IDRETRY pipretry
    DetailPrint "PySide6 was not installed - the app will offer to install it on first start."
  ${Else}
    DetailPrint "PySide6 installed."
  ${EndIf}

  ; ---- shortcuts
  CreateShortcut "$SMPROGRAMS\${APPNAME}.lnk" "$INSTDIR\${EXE}" "" "$INSTDIR\icon.ico" 0

  ; ---- registration
  WriteRegStr HKCU "Software\${APPID}" "InstallDir" "$INSTDIR"
  WriteRegStr HKCU "Software\${APPID}" "Version" "${VERSION}"
  WriteRegStr HKCU "Software\Microsoft\Windows\CurrentVersion\App Paths\${EXE}" "" "$INSTDIR\${EXE}"
  WriteRegStr HKCU "Software\Microsoft\Windows\CurrentVersion\App Paths\${EXE}" "Path" "$INSTDIR"

  WriteUninstaller "$INSTDIR\Uninstall.exe"
  WriteRegStr   HKCU "${UNINSTKEY}" "DisplayName" "${APPNAME}"
  WriteRegStr   HKCU "${UNINSTKEY}" "DisplayVersion" "${VERSION}"
  WriteRegStr   HKCU "${UNINSTKEY}" "Publisher" "${APPNAME}"
  WriteRegStr   HKCU "${UNINSTKEY}" "DisplayIcon" "$INSTDIR\icon.ico"
  WriteRegStr   HKCU "${UNINSTKEY}" "InstallLocation" "$INSTDIR"
  WriteRegStr   HKCU "${UNINSTKEY}" "UninstallString" '"$INSTDIR\Uninstall.exe"'
  WriteRegStr   HKCU "${UNINSTKEY}" "QuietUninstallString" '"$INSTDIR\Uninstall.exe" /S'
  WriteRegDWORD HKCU "${UNINSTKEY}" "NoModify" 1
  WriteRegDWORD HKCU "${UNINSTKEY}" "NoRepair" 1
  ${GetSize} "$INSTDIR" "/S=0K" $0 $1 $2
  IntFmt $0 "0x%08X" $0
  WriteRegDWORD HKCU "${UNINSTKEY}" "EstimatedSize" "$0"
SectionEnd

Section "MIDI port support (hardware / virtual devices)" SecRtMidi
  SetOutPath "$INSTDIR\app"
  DetailPrint "Installing python-rtmidi for external MIDI devices..."
  nsExec::ExecToLog '"$INSTDIR\python\python.exe" -m pip install --disable-pip-version-check --no-input --no-warn-script-location --progress-bar off --only-binary=:all: "python-rtmidi>=1.5"'
  Pop $0
  ${If} $0 != 0
    DetailPrint "python-rtmidi could not be installed (optional) - external MIDI ports will not be listed."
  ${EndIf}
SectionEnd

Section "Desktop shortcut" SecDesktop
  CreateShortcut "$DESKTOP\${APPNAME}.lnk" "$INSTDIR\${EXE}" "" "$INSTDIR\icon.ico" 0
SectionEnd

!macro ASSOC EXT
  WriteRegStr HKCU "Software\Classes\${EXT}\OpenWithProgids" "${PROGID}" ""
  WriteRegStr HKCU "Software\${APPID}\Capabilities\FileAssociations" "${EXT}" "${PROGID}"
  WriteRegStr HKCU "Software\Classes\Applications\${EXE}\SupportedTypes" "${EXT}" ""
  ; Become the default only when no program is registered for the type yet
  ReadRegStr $1 HKCR "${EXT}" ""
  ${If} $1 == ""
    WriteRegStr HKCU "Software\Classes\${EXT}" "" "${PROGID}"
  ${EndIf}
!macroend

Section "Open MIDI files (.mid .midi .kar .rmi) with ${APPNAME}" SecAssoc
  WriteRegStr HKCU "Software\Classes\${PROGID}" "" "MIDI Sequence"
  WriteRegStr HKCU "Software\Classes\${PROGID}\DefaultIcon" "" "$INSTDIR\icon.ico"
  WriteRegStr HKCU "Software\Classes\${PROGID}\shell\open\command" "" '"$INSTDIR\${EXE}" "%1"'
  WriteRegStr HKCU "Software\Classes\Applications\${EXE}" "FriendlyAppName" "${APPNAME}"
  WriteRegStr HKCU "Software\Classes\Applications\${EXE}\DefaultIcon" "" "$INSTDIR\icon.ico"
  WriteRegStr HKCU "Software\Classes\Applications\${EXE}\shell\open\command" "" '"$INSTDIR\${EXE}" "%1"'
  WriteRegStr HKCU "Software\${APPID}\Capabilities" "ApplicationName" "${APPNAME}"
  WriteRegStr HKCU "Software\${APPID}\Capabilities" "ApplicationDescription" "Play, mix and edit MIDI files"
  WriteRegStr HKCU "Software\RegisteredApplications" "${APPNAME}" "Software\${APPID}\Capabilities"
  !insertmacro ASSOC ".mid"
  !insertmacro ASSOC ".midi"
  !insertmacro ASSOC ".kar"
  !insertmacro ASSOC ".rmi"
  System::Call 'shell32::SHChangeNotify(i 0x08000000, i 0, p 0, p 0)'
SectionEnd

!insertmacro MUI_FUNCTION_DESCRIPTION_BEGIN
  !insertmacro MUI_DESCRIPTION_TEXT ${SecMain} "The player, mixer, playlist and editor, with a private Python runtime, the FluidSynth synthesizer and a General MIDI SoundFont."
  !insertmacro MUI_DESCRIPTION_TEXT ${SecRtMidi} "Lets the player send to hardware synths and virtual MIDI ports (e.g. loopMIDI)."
  !insertmacro MUI_DESCRIPTION_TEXT ${SecDesktop} "Put a shortcut on the desktop."
  !insertmacro MUI_DESCRIPTION_TEXT ${SecAssoc} "Adds ${APPNAME} to 'Open with' for MIDI files (and makes it the default if none is set)."
!insertmacro MUI_FUNCTION_DESCRIPTION_END

; ------------------------------------------------ init / update mode
Function .onInit
  ${IfNot} ${RunningX64}
    MessageBox MB_ICONSTOP "${APPNAME} requires 64-bit Windows 10 or newer." /SD IDOK
    Abort
  ${EndIf}
  ; /RELAUNCH = started by the in-app updater: start the app again when done
  StrCpy $Relaunch 0
  ${GetParameters} $R0
  ClearErrors
  ${GetOptions} $R0 "/RELAUNCH" $R1
  ${IfNot} ${Errors}
    StrCpy $Relaunch 1
  ${EndIf}
  ; Detect a previous installation so we update it instead of duplicating it
  StrCpy $OldDir ""
  StrCpy $OldVer ""
  ReadRegStr $OldDir HKCU "Software\${APPID}" "InstallDir"
  ReadRegStr $OldVer HKCU "Software\${APPID}" "Version"
  ${If} $OldDir != ""
  ${AndIfNot} ${FileExists} "$OldDir\${EXE}"
    StrCpy $OldDir ""    ; stale registry entry - the files are gone
  ${EndIf}
  ${If} $OldDir != ""
    StrCpy $INSTDIR "$OldDir"
    ${If} $OldVer == "${VERSION}"
      MessageBox MB_YESNO|MB_ICONQUESTION \
        "${APPNAME} ${VERSION} is already installed.$\r$\n$\r$\nReinstall it (your settings and playlists are kept)?" \
        /SD IDYES IDYES +2
      Abort
    ${EndIf}
  ${EndIf}
  ; Updating: keep the optional components the user chose last time
  !insertmacro RESTORE_CHOICE "RtMidi" ${SecRtMidi}
  !insertmacro RESTORE_CHOICE "Desktop" ${SecDesktop}
  !insertmacro RESTORE_CHOICE "Assoc" ${SecAssoc}
FunctionEnd

Function .onInstSuccess
  !insertmacro SAVE_CHOICE "RtMidi" ${SecRtMidi}
  !insertmacro SAVE_CHOICE "Desktop" ${SecDesktop}
  !insertmacro SAVE_CHOICE "Assoc" ${SecAssoc}
  ${If} $Relaunch == 1
  ${AndIf} ${Silent}
    Exec '"$INSTDIR\${EXE}"'
  ${EndIf}
FunctionEnd

; ------------------------------------------------------------ uninstall
!macro UNASSOC EXT
  DeleteRegValue HKCU "Software\Classes\${EXT}\OpenWithProgids" "${PROGID}"
  ReadRegStr $1 HKCU "Software\Classes\${EXT}" ""
  ${If} $1 == "${PROGID}"
    DeleteRegValue HKCU "Software\Classes\${EXT}" ""
  ${EndIf}
!macroend

Section "Uninstall"
  Call un.WaitForAppClosed
  Delete "$SMPROGRAMS\${APPNAME}.lnk"
  Delete "$DESKTOP\${APPNAME}.lnk"

  !insertmacro UNASSOC ".mid"
  !insertmacro UNASSOC ".midi"
  !insertmacro UNASSOC ".kar"
  !insertmacro UNASSOC ".rmi"
  DeleteRegKey HKCU "Software\Classes\${PROGID}"
  DeleteRegKey HKCU "Software\Classes\Applications\${EXE}"
  DeleteRegValue HKCU "Software\RegisteredApplications" "${APPNAME}"
  DeleteRegKey HKCU "Software\Microsoft\Windows\CurrentVersion\App Paths\${EXE}"
  DeleteRegKey HKCU "${UNINSTKEY}"
  DeleteRegKey HKCU "Software\${APPID}"
  System::Call 'shell32::SHChangeNotify(i 0x08000000, i 0, p 0, p 0)'

  RMDir /r "$INSTDIR\app"
  RMDir /r "$INSTDIR\python"
  Delete "$INSTDIR\${EXE}"
  Delete "$INSTDIR\icon.ico"
  Delete "$INSTDIR\Uninstall.exe"
  RMDir "$INSTDIR"

  MessageBox MB_YESNO|MB_ICONQUESTION "Also remove your settings and saved playlist?" /SD IDNO IDNO keep
    DeleteRegKey HKCU "${SETTINGSKEY}"
  keep:
SectionEnd
