#!/usr/bin/env bash
# Haal Centraal (#284): start or stop the environment with Docker, step by
# step, and write the Launcher's progress file as the other environments do
# ({step, steps, label, started, since, expected, rest, detail, activity,
# error}). It starts in seconds, so the Launcher can be tried quickly.
#
#   run.sh start <progress-file>     exit 0 when Haal Centraal answers
#   run.sh stop
#
# HAALCENTRAAL_FAIL_AT=<n> makes step n fail on purpose (for the Launcher's
# browser test: "Starten mislukt", "Opnieuw proberen"). HAALCENTRAAL_PORT is
# the page's port (default 18795). DOCKER is the docker command (default:
# docker, or docker.exe in WSL without Docker's WSL integration).
#
# SPDX-License-Identifier: EUPL-1.2
set -u
cd "$(dirname "${BASH_SOURCE[0]}")"
DOCKER="${DOCKER:-}"
if [[ -z "${DOCKER}" ]]; then
  if docker version >/dev/null 2>&1; then DOCKER=docker; else DOCKER=docker.exe; fi
fi
port="${HAALCENTRAAL_PORT:-18795}"
dc() { HAALCENTRAAL_PORT="${port}" "${DOCKER}" compose -f compose.yaml "$@"; }

if [[ "${1:-}" == stop ]]; then
  dc stop >/dev/null 2>&1
  exit 0
fi
[[ "${1:-}" == start && -n "${2:-}" ]] || { echo "usage: run.sh start <progress-file> | stop" >&2; exit 2; }
prog="$2"
labels=("Onderdelen downloaden" "Haal Centraal starten" "Wachten tot de BRP antwoordt")
expect=(5 5 10)
since=$(date +%s)
write() { # write INDEX DETAIL [ERROR]
  local i=$1 rest=0 j
  for ((j = i + 1; j < 3; j++)); do rest=$((rest + expect[j])); done
  local err=""; [[ -n "${3:-}" ]] && err=",\"error\":\"$3\""
  printf '{"step":%d,"steps":3,"label":"%s","started":%d,"since":%d,"expected":%d,"rest":%d,"detail":"%s","activity":%d%s}\n' \
    "$((i + 1))" "${labels[$i]}" "${started}" "${since}" "${expect[$i]}" "${rest}" "$2" "$(date +%s)" "${err}" > "${prog}.new"
  mv -f "${prog}.new" "${prog}"
}
answers() {
  curl -fs -o /dev/null --max-time 3 -X POST -H 'Content-Type: application/json' \
    -d '{"type":"RaadpleegMetBurgerservicenummer","burgerservicenummer":["999993653"],"fields":["naam"]}' \
    "http://localhost:${port}/haalcentraal/api/brp/personen"
}
step() { # step INDEX DETAIL COMMAND...: the step, at least 2 s so it can be seen
  local i=$1 detail=$2; shift 2
  started=$(date +%s); write "$i" "${detail}"
  if [[ "${HAALCENTRAAL_FAIL_AT:-}" == "$((i + 1))" ]]; then
    sleep 2; write "$i" "${detail}" "Deze stap is mislukt (opzettelijk, voor de test)."; return 1
  fi
  "$@" >> "${prog}.log" 2>&1 || { write "$i" "${detail}" "Deze stap is mislukt."; return 1; }
  sleep $((started + 2 - $(date +%s) > 0 ? started + 2 - $(date +%s) : 0))
}
wait_answer() { local t; for t in $(seq 1 60); do answers && return 0; sleep 1; done; return 1; }

step 0 "de BRP-mock en nginx" dc pull -q || exit 1
step 1 "docker compose up" dc up -d || exit 1
step 2 "testpersoon 999993653 opvragen" wait_answer || exit 1
rm -f "${prog}"
