; Aries 0.7.0 — electron-builder NSIS include.
; Best-effort taskbar pin after install: runs the bundled
; pin-taskbar.ps1 against the desktop shortcut, if one was created.
; Windows 10/11 restricts programmatic pinning, so this may silently
; do nothing on newer builds — it never fails the install, and the
; fallback is right-click the desktop icon -> "Pin to taskbar".
!macro customInstall
  IfFileExists "$DESKTOP\Aries.lnk" 0 +3
    nsExec::ExecToLog '"$WINDIR\System32\WindowsPowerShell\v1.0\powershell.exe" -ExecutionPolicy Bypass -WindowStyle Hidden -File "$INSTDIR\resources\pin-taskbar.ps1" -Shortcut "$DESKTOP\Aries.lnk"'
    Pop $0
!macroend
