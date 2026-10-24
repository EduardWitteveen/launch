# Launch: add the host names some environments need to the hosts file.
#
# Tezza logs in through Keycloak at http://identity:8181; the browser must find
# "identity" on this computer. This adds "127.0.0.1 identity" to the hosts
# file once (it asks for administrator rights). Running it again changes
# nothing.
#
# SPDX-License-Identifier: EUPL-1.2
$ErrorActionPreference = "Stop"
$names = @("identity")
$hosts = Join-Path $env:SystemRoot "System32\drivers\etc\hosts"

$isAdmin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole(
    [Security.Principal.WindowsBuiltInRole]::Administrator)
$text = Get-Content -Raw -ErrorAction SilentlyContinue $hosts
$missing = $names | Where-Object { $text -notmatch "(?m)^\s*127\.0\.0\.1\s+([^#\r\n]*\s)?$([regex]::Escape($_))(\s|$)" }
if (-not $missing) { Write-Host "De hostnamen staan er al in ($($names -join ', '))."; exit 0 }

if (-not $isAdmin) {
    Write-Host "Windows vraagt nu om beheerdersrechten om de hostnaam '$($missing -join ', ')' toe te voegen."
    $p = Start-Process powershell -Verb RunAs -Wait -PassThru -ArgumentList @(
        "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", "`"$PSCommandPath`"")
    exit $p.ExitCode
}

Copy-Item $hosts "$hosts.bak-launch" -Force
$add = "`r`n# Launch (github.com/EduardWitteveen/launch)`r`n" + (($missing | ForEach-Object { "127.0.0.1 $_" }) -join "`r`n") + "`r`n"
Add-Content -Path $hosts -Value $add -NoNewline -Encoding ascii
Write-Host "Toegevoegd aan het hosts-bestand: $($missing -join ', ') (een kopie van het oude staat in hosts.bak-launch)."
