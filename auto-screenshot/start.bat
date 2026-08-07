@echo off
rem v2.0 2026-08-08 all-ASCII: script renamed to auto-screenshot.ahk so cmd codepage
rem                 (950 vs 65001) can never mangle the path. Called by autoshot://
rem                 protocol, registered at HKCU\Software\Classes\autoshot.
cd /d "%~dp0"

set "AHK=C:\Users\admin\AppData\Local\Programs\AutoHotkey\v2\AutoHotkey64.exe"
if not exist "%AHK%" (
  echo AutoHotkey v2 not found: %AHK%
  pause
  exit /b 1
)
if not exist "%~dp0auto-screenshot.ahk" (
  echo Script not found: %~dp0auto-screenshot.ahk
  pause
  exit /b 1
)

start "" "%AHK%" "%~dp0auto-screenshot.ahk"
