#!/usr/bin/env bash
# Launch: add host names that environments need to the hosts file, on macOS
# and Linux (tools/hostnamen.ps1 does the same on Windows).
#
#   hostnamen.sh [name ...]
#
# Some environments need a name on this computer (Tezza logs in through
# Keycloak at http://identity:8181). This adds "127.0.0.1 <name>" to
# /etc/hosts for the names that are not there yet. Without names: every name
# an environment's check asks for ("when": {"host_unknown": ...} in
# environments/*/environment.json). Running it again changes nothing.
#
# Writing /etc/hosts needs root. On macOS this asks through a GUI prompt
# (osascript, the same mechanism Windows' UAC prompt plays here). On Linux it
# tries pkexec (a GUI prompt too, where polkit is set up), falling back to
# sudo (which asks in the terminal this runs in, and so does nothing useful
# when Launch starts it itself without one - experimental there).
#
# Written for bash 3.2 (macOS ships no other bash by default): no mapfile,
# no associative arrays.
#
# SPDX-License-Identifier: EUPL-1.2
set -euo pipefail
here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
py="${LAUNCH_PYTHON:-python3}"

names=("$@")
if [[ $# -eq 0 ]]; then
  names=()
  while IFS= read -r n; do
    [[ -n "${n}" ]] && names+=("${n}")
  done < <("${py}" -c "
import glob, json, os
found = set()
for p in glob.glob(os.path.join('${here}', '..', 'environments', '*', 'environment.json')):
    for c in (json.load(open(p, encoding='utf-8')).get('checks') or []):
        n = (c.get('when') or {}).get('host_unknown')
        if n:
            found.add(n)
print('\n'.join(sorted(found)))
")
fi
# Only plain host names (defensive: these end up in a shell command below).
names=("${names[@]//[^A-Za-z0-9.-]/}")

missing=()
for n in "${names[@]}"; do
  [[ -n "${n}" ]] || continue
  grep -qE "^[[:space:]]*127\.0\.0\.1[[:space:]]+([^#]*[[:space:]])?${n}([[:space:]]|\$)" /etc/hosts 2>/dev/null \
    || missing+=("${n}")
done
if [[ ${#missing[@]} -eq 0 ]]; then
  echo "Geen hostnamen nodig, of ze staan er al in."
  exit 0
fi

script="$(mktemp)"
trap 'rm -f "${script}"' EXIT
{
  echo "cp /etc/hosts /etc/hosts.bak-launch"
  printf 'printf "\\n# Launch (github.com/EduardWitteveen/launch)\\n" >> /etc/hosts\n'
  for n in "${missing[@]}"; do
    printf 'printf "127.0.0.1 %s\\n" >> /etc/hosts\n' "${n}"
  done
} > "${script}"

if [[ "$(uname -s)" == "Darwin" ]]; then
  osascript -e "do shell script \"/bin/bash '${script}'\" with administrator privileges with prompt \"Launch wil de hostnaam ${missing[*]} toevoegen aan /etc/hosts.\"" >/dev/null
elif command -v pkexec >/dev/null 2>&1; then
  pkexec /bin/bash "${script}"
else
  echo "Geen pkexec gevonden; dit vraagt mogelijk om een wachtwoord in deze terminal:"
  sudo /bin/bash "${script}"
fi
echo "Toegevoegd aan het hosts-bestand: ${missing[*]} (een kopie van het oude staat in hosts.bak-launch)."
