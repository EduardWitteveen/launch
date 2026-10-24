@echo off
rem Launch: open the Launcher (starts Docker and the Launcher when needed).
powershell -NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File "%~dp0Launcher.ps1"
