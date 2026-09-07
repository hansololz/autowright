; §3 Windows installer hook — pulled into electron-builder's NSIS script via
; `nsis.include` (app/package.json). Spec: spec/packaging.md, the Windows
; "Installer shape" bullet.
;
; Already installed at this exact version → open the app instead of
; reinstalling it. After a first install, Windows Search and the Start menu's
; "Recommended" list keep surfacing the installer exe itself, so a user who
; double-clicks it again expects Autowright to open — not a second full
; install with its progress window. `preInit` runs first in .onInit, before
; the running-app check, so an app that is already open is simply brought
; forward (the §9 single-instance lock) rather than killed and re-extracted.
;
; Every real install path is untouched: a different or missing installed
; version, a missing exe, a silent run (/S) and electron-updater's own run
; (--updated) all fall through to the normal install. A same-version repair
; is uninstall-then-install, which is what a one-click installer expects.
!macro preInit
  ${ifNot} ${Silent}
  ${AndIfNot} ${isUpdated}
    ${If} ${RunningX64}
      SetRegView 64
    ${EndIf}
    ; perMachine is false (§3): both keys live under HKCU, written by the
    ; previous run of this same installer script.
    ReadRegStr $R0 HKCU "${UNINSTALL_REGISTRY_KEY}" "DisplayVersion"
    ReadRegStr $R1 HKCU "${INSTALL_REGISTRY_KEY}" "InstallLocation"
    ${if} $R0 == "${VERSION}"
    ${andIf} $R1 != ""
    ${andIf} ${FileExists} "$R1\${APP_EXECUTABLE_FILENAME}"
      ${StdUtils.ExecShellAsUser} $0 "$R1\${APP_EXECUTABLE_FILENAME}" "open" ""
      ; A Quit inside .onInit otherwise reports "aborted by script" (2); this
      ; hand-off is the successful outcome.
      SetErrorLevel 0
      Quit
    ${endIf}
  ${endIf}
!macroend
