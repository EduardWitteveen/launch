<# : batch part (this file is both a cmd and a PowerShell script)
@echo off
rem Launch: install the Launcher on this computer. Double-click this file.
rem SPDX-License-Identifier: EUPL-1.2
powershell -NoProfile -ExecutionPolicy Bypass -Command "$t = Join-Path $env:TEMP 'launch-installeren.ps1'; Copy-Item -LiteralPath '%~f0' -Destination $t -Force; & $t '%~f0'"
pause
exit /b
#>
# --- PowerShell part ------------------------------------------------------------
# What it does, in order (each step is skipped when it is already there):
#   1. Git for Windows, Python 3 and a Docker engine (Rancher Desktop, free and
#      open source) through winget;
#   2. Launch itself into %LOCALAPPDATA%\Programs\Launch: from the folder this
#      file sits in (a downloaded zip) or else the release on GitHub;
#   3. a "Launcher" shortcut in the Start menu and on the desktop;
#   4. the host name "identity" (Tezza's login) in the hosts file (asks for
#      administrator rights).
# Then it opens the Launcher. Every environment fetches what it needs itself
# on its first start.
$ErrorActionPreference = "Stop"
$Release = "v0.1.0-rc.3"
$Repo = "EduardWitteveen/launch"
$Dest = Join-Path $env:LOCALAPPDATA "Programs\Launch"

function Step($t) { Write-Host ""; Write-Host "== $t" -ForegroundColor Cyan }
function Have($exe) { $null -ne (Get-Command $exe -ErrorAction SilentlyContinue | Where-Object { $_.Source -notmatch "WindowsApps" }) }
function Winget($id, $what) {
    if (-not (Have winget)) {
        throw "winget ontbreekt op deze computer. Installeer '$what' zelf en start dit bestand opnieuw."
    }
    Write-Host "$what installeren (Windows kan om toestemming vragen)..."
    winget install --id $id --exact --silent --accept-package-agreements --accept-source-agreements --disable-interactivity
    if ($LASTEXITCODE -ne 0 -and $LASTEXITCODE -ne -1978335189) {  # that one: already installed
        throw "$what installeren lukte niet (winget gaf $LASTEXITCODE)."
    }
}

Write-Host "Launch installeren" -ForegroundColor Green
Write-Host "Dit zet de Launcher op deze computer. Je kunt er omgevingen mee starten, zoals Tezza en Haal Centraal."

Step "1. Wat de Launcher nodig heeft"
$gitBash = @("C:\Program Files\Git\bin\bash.exe", (Join-Path $env:LOCALAPPDATA "Programs\Git\bin\bash.exe")) | Where-Object { Test-Path $_ }
if ($gitBash) { Write-Host "Git for Windows: er al" } else { Winget "Git.Git" "Git for Windows" }

$py = @((Join-Path $env:LOCALAPPDATA "Programs\Python\Python313\python.exe"),
        (Join-Path $env:LOCALAPPDATA "Programs\Python\Python312\python.exe"),
        "C:\Program Files\Python313\python.exe", "C:\Program Files\Python312\python.exe") | Where-Object { Test-Path $_ }
if ($py -or (Have python)) { Write-Host "Python: er al" } else { Winget "Python.Python.3.13" "Python 3" }

$dockerDesktop = Test-Path "C:\Program Files\Docker\Docker\Docker Desktop.exe"
$rancher = @((Join-Path $env:LOCALAPPDATA "Programs\Rancher Desktop\Rancher Desktop.exe"),
             "C:\Program Files\Rancher Desktop\Rancher Desktop.exe") | Where-Object { Test-Path $_ }
if ($dockerDesktop) { Write-Host "Docker: Docker Desktop is er al" }
elseif ($rancher) { Write-Host "Docker: Rancher Desktop is er al" }
else {
    Winget "SUSE.RancherDesktop" "Rancher Desktop (Docker)"
    Write-Host "Kies bij de eerste start van Rancher Desktop voor 'dockerd (moby)' en zet Kubernetes uit." -ForegroundColor Yellow
}

Step "2. Launch zelf"
$self = $args[0]
$here = if ($self) { Split-Path -Parent $self } else { $null }
if ($here -and ((Resolve-Path $here).Path.TrimEnd("\") -eq $Dest.TrimEnd("\"))) {
    throw "Start dit bestand vanuit de download, niet vanuit $Dest."
}
if (Test-Path $Dest) { Remove-Item -Recurse -Force $Dest }
New-Item -ItemType Directory -Force $Dest | Out-Null
if ($here -and (Test-Path (Join-Path $here "launch.py"))) {
    Write-Host "Uit deze map: $here"
    Copy-Item -Recurse -Force (Join-Path $here "*") $Dest -Exclude ".git", "tests"
} else {
    $zip = Join-Path $env:TEMP "launch-$Release.zip"
    $url = "https://github.com/$Repo/archive/refs/tags/$Release.zip"
    Write-Host "Downloaden: $url"
    [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
    Invoke-WebRequest -UseBasicParsing $url -OutFile $zip
    $tmp = Join-Path $env:TEMP "launch-$Release"
    if (Test-Path $tmp) { Remove-Item -Recurse -Force $tmp }
    Expand-Archive $zip $tmp
    Copy-Item -Recurse -Force (Join-Path (Get-ChildItem $tmp | Select-Object -First 1).FullName "*") $Dest
}
Write-Host "Staat nu in $Dest"

Step "3. Snelkoppelingen"
$shell = New-Object -ComObject WScript.Shell
foreach ($dir in @([Environment]::GetFolderPath("Programs"), [Environment]::GetFolderPath("Desktop"))) {
    $lnk = $shell.CreateShortcut((Join-Path $dir "Launcher.lnk"))
    $lnk.TargetPath = "powershell.exe"
    $lnk.Arguments = "-NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File `"$Dest\Launcher.ps1`""
    $lnk.WorkingDirectory = $Dest
    $lnk.IconLocation = "$env:SystemRoot\System32\shell32.dll,13"
    $lnk.Description = "Launcher: omgevingen starten, stoppen en openen"
    $lnk.Save()
}
Write-Host "Launcher staat in het Startmenu en op het bureaublad."

Step "4. De hostnaam voor het inloggen bij Tezza"
& powershell -NoProfile -ExecutionPolicy Bypass -File (Join-Path $Dest "tools\hostnamen.ps1")

Step "Klaar"
Write-Host "De Launcher gaat nu open. Later open je hem met 'Launcher' in het Startmenu of op het bureaublad."
Write-Host "Kreeg je net Git, Python of Rancher Desktop erbij? Herstart dan eerst de computer als de Launcher niets doet."
& powershell -NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File (Join-Path $Dest "Launcher.ps1")
