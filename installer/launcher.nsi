; Tiny, windowless launcher: ModernMidiPlayer.exe [files...]
; Starts the bundled Python runtime with the app, forwarding command-line files.
Unicode true
!include "FileFunc.nsh"

!ifndef VERSION
  !define VERSION "1.0.0"
!endif

Name "Modern MIDI Player"
OutFile "${OUTDIR}\ModernMidiPlayer.exe"
Icon "${BUILDDIR}\icon.ico"
SilentInstall silent
RequestExecutionLevel user
SetCompressor /SOLID lzma

VIProductVersion "${VERSION}.0"
VIAddVersionKey "ProductName" "Modern MIDI Player"
VIAddVersionKey "FileDescription" "Modern MIDI Player"
VIAddVersionKey "FileVersion" "${VERSION}"
VIAddVersionKey "ProductVersion" "${VERSION}"
VIAddVersionKey "LegalCopyright" "Modern MIDI Player"

Section
  ${GetParameters} $0
  IfFileExists "$EXEDIR\python\pythonw.exe" +3
    MessageBox MB_ICONSTOP "The Python runtime is missing. Please reinstall Modern MIDI Player."
    Quit
  SetOutPath "$EXEDIR\app"
  Exec '"$EXEDIR\python\pythonw.exe" "$EXEDIR\app\run.py" $0'
SectionEnd
