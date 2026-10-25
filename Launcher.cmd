@echo off
rem Launch: open the Launcher (starts Docker and the Launcher when needed).
rem SPDX-License-Identifier: EUPL-1.2
powershell -NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File "%~dp0Launcher.ps1"
