#!/usr/bin/env bash
# Tezza: start or stop Contezza's Tezza (tezza-packaging, the complete product,
# with Súdwest-Fryslân's case types) with Docker, step by step, and write the
# Launcher's progress file. The steps are deploy.sh's (the same as on the DAWO
# USB-SSD): source, pull, up, ready, catalog, setup.
#
#   run.sh start <progress-file>
#   run.sh stop
#
# The Docker binary and connection are this machine's (Docker Desktop, Rancher
# Desktop, or docker on Linux). State in LAUNCH_STATE/tezza.
#
# SPDX-License-Identifier: EUPL-1.2
set -u
here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
py="${LAUNCH_PYTHON:-python3}"
cfg() { "${py}" -c "import json,sys; e=json.load(open(sys.argv[1], encoding='utf-8')); print($1)" "${here}/environment.json"; }

state="${LAUNCH_STATE:-${HOME}/.local/state/launch}"
command -v cygpath >/dev/null 2>&1 && state="$(cygpath -u "${state}")"
export TZ_STATE_DIR="${state}/tezza"
mkdir -p "${TZ_STATE_DIR}"
export TZ_REPO TZ_REV TZ_SWF_URL TZ_SWF_SHA
TZ_REPO="$(cfg "e['source']['repo']")"; TZ_REV="$(cfg "e['source']['rev']")"
TZ_SWF_URL="$(cfg "e['swf_catalogue']['url']")"; TZ_SWF_SHA="$(cfg "e['swf_catalogue']['sha256']")"
export TZ_OVERRIDE="${here}/override.yaml"
# deploy.sh takes the docker binary from <dir>/bin and the connection as -H.
docker_bin="$(command -v docker)"
export DAWO_DOCKER_DIR="$(dirname "$(dirname "${docker_bin}")")"
export DAWO_DOCKER_SOCK="$(docker context inspect --format '{{.Endpoints.docker.Host}}' 2>/dev/null)"
# Git Bash would turn the container's /tmp/... and /opt/... into Windows paths.
export MSYS2_ARG_CONV_EXCL="/tmp/;/opt/"
# deploy.sh's catalog phase reads swf-convert.py and swf-publish.py from the state.
cp -f "${here}/swf/convert.py" "${TZ_STATE_DIR}/swf-convert.py"
cp -f "${here}/swf/publish.py" "${TZ_STATE_DIR}/swf-publish.py"
deploy=(bash "${here}/deploy.sh")

if [[ "${1:-}" == stop ]]; then
  # Not deploy.sh --stop: that stops the appliance's Docker unit (systemctl),
  # which a laptop does not have. Stop Tezza's containers; their data stays.
  echo "==> stop the Tezza containers"
  ids="$(docker ps -q --filter label=com.docker.compose.project=tezza-packaging)"
  # shellcheck disable=SC2086 # one container id per word
  [[ -z "${ids}" ]] || docker stop -t 30 ${ids} >/dev/null
  echo "    stopped"
  exit 0
fi
[[ "${1:-}" == start && -n "${2:-}" ]] || { echo "usage: run.sh start <progress-file> | stop" >&2; exit 2; }
prog="$2"
names=(source pull up ready catalog setup)
labels=("Tezza ophalen (de demo van Contezza)" "Onderdelen downloaden (ongeveer 8 GB)" "Tezza starten"
        "Wachten tot Tezza klaar is (de eerste keer wordt alles ingericht)" "De zaaktypes van Súdwest-Fryslân laden"
        "Tezza inrichten (sites, groepen, sjablonen) en de login launcher")
expect=(30 1800 300 600 180 300)
since=$(date +%s)
log="${prog%.json}.steps.log"
write() { # write INDEX [ERROR]
  local i=$1 rest=0 j detail
  for ((j = i + 1; j < ${#names[@]}; j++)); do rest=$((rest + expect[j])); done
  detail="$(grep -v '^[[:space:]]*$' "${log}" 2>/dev/null | grep -vE '^ (Image|Container|Volume|Network) ' | tail -n 1 | cut -c1-200 | tr -d '"\\')"
  local err=""; [[ -n "${2:-}" ]] && err=",\"error\":\"$2\""
  printf '{"step":%d,"steps":%d,"label":"%s","started":%d,"since":%d,"expected":%d,"rest":%d,"detail":"%s","activity":%d%s}\n' \
    "$((i + 1))" "${#names[@]}" "${labels[$i]}" "${started}" "${since}" "${expect[$i]}" "${rest}" "${detail}" "$(date +%s)" "${err}" > "${prog}.new"
  mv -f "${prog}.new" "${prog}"
}
: > "${log}"
for i in "${!names[@]}"; do
  started=$(date +%s); write "$i"
  ( while sleep 5; do write "$i"; done ) & ticker=$!
  "${deploy[@]}" --phase "${names[$i]}" >> "${log}" 2>&1; rc=$?
  kill "${ticker}" 2>/dev/null; wait "${ticker}" 2>/dev/null
  if [[ ${rc} -ne 0 ]]; then
    write "$i" "Deze stap is mislukt. De details staan in tezza.log."
    cat "${log}" >&2
    exit 1
  fi
done
rm -f "${prog}"
