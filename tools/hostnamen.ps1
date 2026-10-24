# Launch: add host names that environments need to the hosts file.
#
#   hostnamen.ps1 [name ...]
#
# Some environments need a name on this computer (Tezza logs in through
# Keycloak at http://identity:8181). This adds "127.0.0.1 <name>" to the hosts
# file for the names that are not there yet (it asks for administrator
# rights). Without names: every name an environment's check asks for
# ("when": {"host_unknown": ...} in environments/*/environment.json; the
# installer runs it that way). Running it again changes nothing.
#
# SPDX-License-Identifier: EUPL-1.2
param([Parameter(ValueFromRemainingArguments = $true)][string[]]$Names)
$ErrorActionPreference = "Stop"
if (-not $Names) {
    $Names = @(Get-ChildItem (Join-Path $PSScriptRoot "..\environments") -Filter environment.json -Recurse |
        ForEach-Object { (Get-Content -Raw -Encoding utf8 $_.FullName | ConvertFrom-Json).checks } |
        Where-Object { $_ -and $_.when.host_unknown } | ForEach-Object { $_.when.host_unknown } | Sort-Object -Unique)
}
$names = @($Names | Where-Object { $_ -match "^[A-Za-z0-9]([A-Za-z0-9.-]*[A-Za-z0-9])?$" })
if (-not $names) { Write-Host "Geen hostnamen nodig."; exit 0 }
$hosts = Join-Path $env:SystemRoot "System32\drivers\etc\hosts"

$isAdmin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole(
    [Security.Principal.WindowsBuiltInRole]::Administrator)
$text = Get-Content -Raw -ErrorAction SilentlyContinue $hosts
$missing = $names | Where-Object { $text -notmatch "(?m)^\s*127\.0\.0\.1\s+([^#\r\n]*\s)?$([regex]::Escape($_))(\s|$)" }
if (-not $missing) { Write-Host "De hostnamen staan er al in ($($names -join ', '))."; exit 0 }

if (-not $isAdmin) {
    Write-Host "Windows vraagt nu om beheerdersrechten om de hostnaam '$($missing -join ', ')' toe te voegen."
    $p = Start-Process powershell -Verb RunAs -Wait -PassThru -ArgumentList @(
        "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", "`"$PSCommandPath`"", ($missing -join " "))
    exit $p.ExitCode
}

Copy-Item $hosts "$hosts.bak-launch" -Force
$add = "`r`n# Launch (github.com/EduardWitteveen/launch)`r`n" + (($missing | ForEach-Object { "127.0.0.1 $_" }) -join "`r`n") + "`r`n"
Add-Content -Path $hosts -Value $add -NoNewline -Encoding ascii
Write-Host "Toegevoegd aan het hosts-bestand: $($missing -join ', ') (een kopie van het oude staat in hosts.bak-launch)."
