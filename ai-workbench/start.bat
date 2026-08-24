@echo off
rem v1.0 2026-08-08 Launch the "AI workbench" desktop shortcut (Windows Terminal,
rem                 7 hermes tabs: 3x agy / 2x claude / 2x codex).
rem                 Called by the aiwork:// protocol, registered at
rem                 HKCU\Software\Classes\aiwork.
rem
rem NOTE: keep this file pure ASCII. The shortcut file name contains CJK
rem       characters, so it is matched with an ASCII wildcard instead of being
rem       hard-coded -- writing the CJK path here would be mangled by the cmd
rem       codepage (950 vs 65001). The wildcard also means the shortcut can be
rem       edited (more tabs, different colors) without touching this file.

setlocal
set "FOUND="
for %%f in ("%USERPROFILE%\Desktop\000_AI*.lnk") do (
  set "FOUND=1"
  start "" "%%f"
)

if not defined FOUND (
  echo [ERROR] Shortcut not found: %USERPROFILE%\Desktop\000_AI*.lnk
  echo Put the AI workbench shortcut back on the desktop, or fix the pattern above.
  pause
  exit /b 1
)
