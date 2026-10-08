; FAOS NSIS installer hooks
; PREINSTALL: detect previous FAOS install, kill running instance, silently
; uninstall it and wipe the old directory, so every install starts clean.

!macro NSIS_HOOK_PREINSTALL
  ; make sure no running instance blocks file replacement (silent, ignore result)
  nsExec::Exec 'taskkill /F /IM "${MAINBINARYNAME}.exe"'
  Pop $0

  ; locate previous install's uninstaller (current context, then HKLM/HKCU fallback)
  ReadRegStr $R0 SHCTX "${UNINSTKEY}" "UninstallString"
  ${If} $R0 == ""
    ReadRegStr $R0 HKLM "${UNINSTKEY}" "UninstallString"
  ${EndIf}
  ${If} $R0 == ""
    ReadRegStr $R0 HKCU "${UNINSTKEY}" "UninstallString"
  ${EndIf}

  ${If} $R0 != ""
    ; registry stores quoted path "C:\...\FAOS\uninstall.exe" -> strip quotes
    StrCpy $R1 $R0 "" 1
    StrCpy $R1 $R1 -1
    ; dir = uninstaller path minus "\uninstall.exe" (14 chars)
    StrCpy $R3 $R1 -14

    DetailPrint "FAOS: previous install found at $R3, uninstalling first ..."
    ExecWait '"$R1" /S _?=$R3' $2

    ; NSIS uninstaller only removes files it installed; wipe the whole old dir
    ; (guard: only paths ending in \FAOS) so no stale code/junk survives
    StrCpy $R4 $R3 "" -5
    ${If} $R4 == "\FAOS"
      RMDir /r "$R3"
    ${EndIf}

    DetailPrint "FAOS: previous install removed (rc=$2)"
  ${EndIf}
!macroend
