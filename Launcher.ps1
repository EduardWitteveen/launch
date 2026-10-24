# Launch: open the Launcher. Starts Docker when it does not run yet, starts the
# Launcher (launcher\launcher.py on http://localhost:1900/) when it does not
# run yet, and opens it in the browser. The Start menu and desktop shortcut
# made by the installer run this.
#
# SPDX-License-Identifier: EUPL-1.2
$ErrorActionPreference = "Continue"
$root = $PSScriptRoot
$port = 1900
$url = "http://localhost:$port/"
$state = Join-Path $env:LOCALAPPDATA "launch"
New-Item -ItemType Directory -Force $state | Out-Null

function Find-Python {
    foreach ($c in @(
            (Join-Path $env:LOCALAPPDATA "Programs\Python\Python313\pythonw.exe"),
            (Join-Path $env:LOCALAPPDATA "Programs\Python\Python312\pythonw.exe"),
            "C:\Program Files\Python313\pythonw.exe", "C:\Program Files\Python312\pythonw.exe")) {
        if (Test-Path $c) { return $c }
    }
    # A python on PATH, but not the Microsoft Store's placeholder.
    foreach ($n in @("pythonw.exe", "python.exe")) {
        foreach ($cmd in (Get-Command $n -All -ErrorAction SilentlyContinue)) {
            if ($cmd.Source -notmatch "WindowsApps") { return $cmd.Source }
        }
    }
    return $null
}

function Test-Up($u) {
    try { Invoke-WebRequest -UseBasicParsing -TimeoutSec 3 $u | Out-Null; return $true } catch { return $false }
}

function Test-Docker {
    $d = Get-Command docker -ErrorAction SilentlyContinue
    if (-not $d) { return $false }
    & $d.Source info --format "{{.ServerVersion}}" *> $null
    return $LASTEXITCODE -eq 0
}

Add-Type -AssemblyName System.Windows.Forms
function Say($text) { [System.Windows.Forms.MessageBox]::Show($text, "Launcher") | Out-Null }

if (Test-Up $url) { Start-Process $url; exit 0 }

$py = Find-Python
if (-not $py) { Say "Python is niet gevonden. Voer Launch-installeren.cmd opnieuw uit."; exit 1 }

# Docker: start Docker Desktop or Rancher Desktop when it does not answer yet.
if (-not (Test-Docker)) {
    $engines = @(
        "C:\Program Files\Docker\Docker\Docker Desktop.exe",
        (Join-Path $env:LOCALAPPDATA "Programs\Rancher Desktop\Rancher Desktop.exe"),
        "C:\Program Files\Rancher Desktop\Rancher Desktop.exe")
    $engine = $engines | Where-Object { Test-Path $_ } | Select-Object -First 1
    if ($engine) {
        Start-Process $engine
        # The Launcher itself waits for nothing: its cards say "Docker draait
        # niet" until Docker answers, so it opens at once.
    }
}

$env:LAUNCH_PYTHON = ($py -replace "pythonw\.exe$", "python.exe")
Start-Process -WindowStyle Hidden -FilePath $py -ArgumentList @("`"$root\launcher\launcher.py`"", "--port", "$port") `
    -RedirectStandardError (Join-Path $state "launcher.log") -RedirectStandardOutput (Join-Path $state "launcher.out")
for ($i = 0; $i -lt 40 -and -not (Test-Up $url); $i++) { Start-Sleep -Milliseconds 500 }
if (Test-Up $url) { Start-Process $url } else { Say "De Launcher start niet. Kijk in $state\launcher.log." }
