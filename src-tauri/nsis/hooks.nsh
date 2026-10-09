; FAOS NSIS 安装钩子
; PREINSTALL：检测旧安装 → 结束运行中的进程 → 静默卸载并清空旧目录，保证每次安装都是干净环境。

!macro NSIS_HOOK_PREINSTALL
  ; 结束运行中的实例，避免文件占用（静默，忽略结果）
  nsExec::Exec 'taskkill /F /IM "${MAINBINARYNAME}.exe"'
  Pop $0

  ; 定位旧安装的卸载程序（当前上下文，HKLM/HKCU 兜底）
  ReadRegStr $R0 SHCTX "${UNINSTKEY}" "UninstallString"
  ${If} $R0 == ""
    ReadRegStr $R0 HKLM "${UNINSTKEY}" "UninstallString"
  ${EndIf}
  ${If} $R0 == ""
    ReadRegStr $R0 HKCU "${UNINSTKEY}" "UninstallString"
  ${EndIf}

  ${If} $R0 != ""
    ; 注册表存的是带引号路径 "C:\...\FAOS\uninstall.exe" → 去引号
    StrCpy $R1 $R0 "" 1
    StrCpy $R1 $R1 -1
    ; 目录 = 卸载程序路径去掉 "\uninstall.exe"（14 字符）
    StrCpy $R3 $R1 -14

    DetailPrint "FAOS：检测到旧版本（$R3），正在先卸载 ..."
    ExecWait '"$R1" /S _?=$R3' $2

    ; NSIS 卸载器只删除它安装过的文件，整目录清空兜底
    ; （护栏：仅清理以 \FAOS 结尾的路径，防误删）
    StrCpy $R4 $R3 "" -5
    ${If} $R4 == "\FAOS"
      RMDir /r "$R3"
    ${EndIf}

    DetailPrint "FAOS：旧版本已卸载（退出码 $2）"
  ${EndIf}
!macroend
