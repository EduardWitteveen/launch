@echo off
rem Launch: add the host names the environments need (asks for administrator rights).
rem SPDX-License-Identifier: EUPL-1.2
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0hostnamen.ps1"
pause
