#!/usr/bin/env bash
# D-OmniTwin (#2): Súdwest-Fryslân's digital twin of the living environment,
# version 0.6.1 with its ready-made images, started step by step; writes the
# Launcher's progress file as Informatiebeheer does
# ({step, steps, label, started, since, expected, rest, detail, activity, error}).
#
#   run.sh start <progress-file>     exit 0 when D-OmniTwin answers
#   run.sh stop
#
# Upstream's compose files come from its repository at the commit in
# environment.json; override.yaml pins the images. It runs as compose project
# d-omnitwin-launch on port 18797 (OMNITWIN_PORT), so a D-OmniTwin of its own
# on this computer is left alone. The choices of Aanpassen (docs §7):
# LAUNCH_OPTIONS_ON/OFF with demo, keycloak and swf, and LAUNCH_SOURCE_SWF
# the folder of Súdwest-Fryslân's own data (see swf.yaml). DOCKER is the
# docker command (default: docker, or docker.exe in WSL).
#
# SPDX-License-Identifier: EUPL-1.2
set -u
here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "${here}"
DOCKER="${DOCKER:-}"
if [[ -z "${DOCKER}" ]]; then
  if docker version >/dev/null 2>&1; then DOCKER=docker; else DOCKER=docker.exe; fi
fi
py="${LAUNCH_PYTHON:-python3}"
cfg() { "${py}" -c "import json,sys; e=json.load(open(sys.argv[1], encoding='utf-8')); print($1)" environment.json; }
project="$(cfg "e['project']")"
port="${OMNITWIN_PORT:-18797}"
state="${LAUNCH_STATE:-${HOME}/.local/state/launch}"
# In Windows form (C:/Users/...), as Docker takes it (lib/run-driver.sh).
win() { if command -v cygpath >/dev/null 2>&1; then cygpath -m "$1"; else printf '%s\n' "$1"; fi; }
state="$(win "${state}")"
dir="${state}/d-omnitwin"
src="${dir}/src"

if [[ "${1:-}" == stop ]]; then
  # Every container of the project, Keycloak's too; the data stays.
  ids="$("${DOCKER}" ps -q --filter "label=com.docker.compose.project=${project}")"
  # shellcheck disable=SC2086 # one container id per word
  [[ -z "${ids}" ]] || "${DOCKER}" stop -t 30 ${ids} >/dev/null
  exit 0
fi
[[ "${1:-}" == start && -n "${2:-}" ]] || { echo "usage: run.sh start <progress-file> | stop" >&2; exit 2; }
prog="$2"
mkdir -p "${dir}"

# The choices; a caller that passes none gets environment.json's defaults.
if [[ -z "${LAUNCH_OPTIONS_ON+x}${LAUNCH_OPTIONS_OFF+x}" ]]; then
  LAUNCH_OPTIONS_ON="demo,swf" LAUNCH_OPTIONS_OFF="keycloak"
fi
on() { [[ ",${LAUNCH_OPTIONS_ON:-}," == *",$1,"* ]]; }
files=(-f "${src}/docker-compose.yml" -f "${src}/docker-compose.images.yml" -f "$(win "${here}")/override.yaml")
swfdir=""
swf=()
if on swf && [[ -n "${LAUNCH_SOURCE_SWF:-}" ]]; then
  swfdir="$(win "${LAUNCH_SOURCE_SWF}")"
  files+=(-f "$(win "${here}")/swf.yaml")
  # Each subfolder that is there, as the folder D-OmniTwin reads (swf.yaml).
  for sub in geo:GEO_DIR docs:DOCS_DIR dump:DUMP_DIR db-dump:DB_DUMP export:EXPORT_DIR; do
    [[ -d "${LAUNCH_SOURCE_SWF}/${sub%%:*}" ]] && swf+=("OMNITWIN_SWF_${sub#*:}=1")
  done
fi
# The use cases that read the source (swf.yaml), from D-OmniTwin 0.6.1's usecases/.
swf_cases=(swf-definities swf-adapter-fme swf-kompasprofiel swf-gebiedsindelingen swf-kaartlagen swf-initialisatie)
profiles=""
on keycloak && profiles=keycloak
dc() { env COMPOSE_PROFILES="${profiles}" OMNITWIN_SWF="${swfdir}" "${swf[@]}" "${DOCKER}" compose -p "${project}" --project-directory "${src}" "${files[@]}" "$@"; }

# What data the database has: D-OmniTwin loads it only at the first start, in an
# empty database, so Launch keeps the list next to the volume's creation time
# (a new volume is an empty database again). The source counts as swf@<its
# fingerprint>: new or changed files in it are loaded at the next start.
volume="${project}_pgdata"
marker="${dir}/data.txt"
created() { "${DOCKER}" volume inspect -f '{{.CreatedAt}}' "${volume}" 2>/dev/null; }
wanted=""
on demo && wanted="demo"
swfkey=""
if [[ -n "${swfdir}" ]]; then
  swfkey="swf@$("${py}" - "${LAUNCH_SOURCE_SWF}" <<'PY'
import hashlib, os, sys
h = hashlib.sha256()
for root, dirs, names in sorted(os.walk(sys.argv[1])):
    dirs.sort()
    for n in sorted(names):
        st = os.stat(os.path.join(root, n))
        h.update(f"{os.path.relpath(os.path.join(root, n), sys.argv[1])}\0{st.st_size}\0{int(st.st_mtime)}\n".encode())
print(h.hexdigest()[:12])
PY
)"
  wanted="${wanted:+${wanted},}${swfkey}"
fi
fresh=1
[[ -n "$(created)" ]] && fresh=0
loaded() { # the data the database has, or "?" when Launch does not know
  if [[ ${fresh} == 1 ]]; then return; fi
  if [[ -f "${marker}" && "$(sed -n 1p "${marker}")" == "$(created)" ]]; then sed -n 2p "${marker}"; else echo "?"; fi
}
missing() { # missing ID: wanted, and not (known to be) in the database
  local have; have="$(loaded)"
  [[ -n "$1" ]] || return 1
  [[ ",${wanted}," == *",$1,"* ]] || return 1
  [[ "${have}" == "?" ]] && return 0
  [[ ",${have}," != *",$1,"* ]]
}

labels=("D-OmniTwin ophalen (versie 0.6.1)" "Onderdelen downloaden (ongeveer 1 GB)" "D-OmniTwin starten"
        "Data laden die er nog niet is" "Wachten tot D-OmniTwin antwoordt")
expect=(20 600 60 1 60)
if [[ ${fresh} == 1 ]]; then
  labels[2]="D-OmniTwin starten en de database inrichten"
  if on demo; then labels[2]="D-OmniTwin starten en de demodata van Fryslân laden"; expect[2]=600; else expect[2]=120; fi
fi
# At a first start the init loads everything but the source without demo data.
if { [[ ${fresh} == 0 ]] && { missing demo || missing "${swfkey}"; }; } || { [[ ${fresh} == 1 && -n "${swfdir}" ]] && ! on demo; }; then
  expect[3]=300
fi
since=$(date +%s)
log="${prog%.json}.steps.log"
: > "${log}"
write() { # write INDEX [ERROR]
  local i=$1 rest=0 j detail err=""
  for ((j = i + 1; j < ${#labels[@]}; j++)); do rest=$((rest + expect[j])); done
  detail="$(grep -v '^[[:space:]]*$' "${log}" 2>/dev/null | tail -n 1 | cut -c1-200 | tr -d '"\\\r')"
  [[ -n "${2:-}" ]] && err=",\"error\":\"$2\""
  printf '{"step":%d,"steps":%d,"label":"%s","started":%d,"since":%d,"expected":%d,"rest":%d,"detail":"%s","activity":%d%s}\n' \
    "$((i + 1))" "${#labels[@]}" "${labels[$i]}" "${started}" "${since}" "${expect[$i]}" "${rest}" "${detail}" "$(date +%s)" "${err}" > "${prog}.new"
  mv -f "${prog}.new" "${prog}"
}

# fetch: upstream's repository at the commit (its compose files, Traefik's and
# Keycloak's configuration), with LF line ends: Keycloak runs its shell scripts.
fetch() {
  local repo rev; repo="$(cfg "e['source']['repo']")"; rev="$(cfg "e['source']['rev']")"
  if [[ "$(git -C "${src}" rev-parse HEAD 2>/dev/null)" == "${rev}" ]] && git -C "${src}" diff --quiet 2>/dev/null; then
    echo "versie ${rev:0:7}: er al"
  else
    rm -rf "${src}.new"
    git init -q "${src}.new" && git -C "${src}.new" config core.autocrlf false \
      && git -C "${src}.new" fetch -q --depth 1 "${repo}" "${rev}" && git -C "${src}.new" checkout -q FETCH_HEAD || return 1
    rm -rf "${src}" && mv "${src}.new" "${src}" || return 1
    echo "versie ${rev:0:7}: opgehaald"
  fi
  settings
}
# settings: D-OmniTwin's .env, written on every start. The passwords are made
# once (secrets.env): the database keeps the first ones.
settings() {
  if [[ ! -s "${dir}/secrets.env" ]]; then
    "${py}" -c "import secrets
for k in ('DATABASE_PASSWORD', 'KEYCLOAK_ADMIN_PASSWORD', 'KEYCLOAK_DB_PASSWORD'):
    print(f'{k}={secrets.token_hex(24)}')" > "${dir}/secrets.env.new" && mv -f "${dir}/secrets.env.new" "${dir}/secrets.env" || return 1
  fi
  {
    echo "# Written by Launch (environments/d-omnitwin/run.sh) on every start."
    cat "${dir}/secrets.env"
    echo "COMPOSE_PROJECT_NAME=${project}"
    echo "OMNITWIN_HTTP_PORT=${port}"
    echo "OMNITWIN_BASE_URL=http://localhost:${port}"
    echo "# Demo: admin / admin, and a yellow bar that says so."
    echo "OMNITWIN_OMGEVING=demo"
    if on demo; then echo "LOAD_SAMPLE_DATA=true"; else echo "LOAD_SAMPLE_DATA=false"; fi
    if on keycloak; then
      echo "KEYCLOAK_HOSTNAME=http://localhost:${port}/auth"
      echo "OIDC_ISSUER=http://localhost:${port}/auth/realms/omnitwin"
    fi
  } > "${src}/.env"
}
up() {
  dc up -d || return 1
  # Keycloak switched off at Aanpassen after an earlier start: stop it.
  if ! on keycloak; then
    local ids
    ids="$("${DOCKER}" ps -q --filter "label=com.docker.compose.project=${project}" --filter "label=com.docker.compose.service=keycloak")"
    # shellcheck disable=SC2086 # one container id per word
    [[ -z "${ids}" ]] || "${DOCKER}" stop -t 30 ${ids}
  fi
  if [[ ${fresh} == 1 ]]; then
    local have=""
    on demo && have="${wanted}"
    printf '%s\n%s\n' "$(created)" "${have}" > "${marker}"
  fi
}
# data: what was switched on after the first start, loaded now. A source that
# cannot be reached is skipped with a message, as at D-OmniTwin's first start.
data() {
  local have
  fresh=0
  if [[ -z "${wanted}" ]]; then echo "overgeslagen (uitgezet bij Aanpassen)"; return 0; fi
  if missing demo; then
    dc run --rm --no-deps db-init python -m omnitwin.usecases installeer --alle --inproces \
      || echo "Niet alle use cases zijn geladen (zie hierboven); D-OmniTwin werkt zonder die data."
  elif missing "${swfkey}"; then
    # Only Súdwest-Fryslân's use cases (with what they depend on), not the open data again.
    dc run --rm --no-deps db-init python -m omnitwin.usecases installeer "${swf_cases[@]}" --inproces \
      || echo "Niet alle gegevens van Súdwest-Fryslân zijn geladen (zie hierboven)."
  else
    echo "alles is er al"; return 0
  fi
  have="$(loaded)"; [[ "${have}" == "?" ]] && have=""
  # The source as it is now replaces what was loaded from it before.
  [[ -n "${swfkey}" ]] && have="$(printf '%s\n' "${have//,/$'\n'}" | grep -v '^swf@' | paste -sd, -)"
  printf '%s\n%s\n' "$(created)" "$(printf '%s\n' "${have//,/$'\n'}" "${wanted//,/$'\n'}" | grep . | sort -u | paste -sd, -)" > "${marker}"
}
answers() { curl -fs -o /dev/null --max-time 3 "http://localhost:${port}/health"; }
wait_answer() { local t; for t in $(seq 1 180); do answers && return 0; sleep 1; done; return 1; }

step() { # step INDEX COMMAND...: the step, at least 2 s so it can be seen
  local i=$1 rc ticker; shift
  started=$(date +%s); write "$i"
  ( while sleep 5; do write "$i"; done ) & ticker=$!
  if [[ "${OMNITWIN_FAIL_AT:-}" == "$((i + 1))" ]]; then
    sleep 2; rc=1; echo "opzettelijk mislukt, voor de test" >> "${log}"
  else
    "$@" >> "${log}" 2>&1; rc=$?
  fi
  kill "${ticker}" 2>/dev/null; wait "${ticker}" 2>/dev/null
  if [[ ${rc} -ne 0 ]]; then
    write "$i" "Deze stap is mislukt. De details staan in d-omnitwin.log."
    cat "${log}" >&2
    return 1
  fi
  sleep $((started + 2 - $(date +%s) > 0 ? started + 2 - $(date +%s) : 0))
  write "$i"
}

echo "==> options on: ${LAUNCH_OPTIONS_ON:-(none)}; off: ${LAUNCH_OPTIONS_OFF:-(none)}; source: ${swfdir:-(none)} ${swf[*]}" >> "${log}"
step 0 fetch || exit 1
# Only what is missing: a second start works without the internet.
step 1 dc pull --policy missing || exit 1
step 2 up || exit 1
step 3 data || exit 1
step 4 wait_answer || exit 1
rm -f "${prog}"
exit 0
