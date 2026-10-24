# Launch: what every environment's run.sh shares. A run.sh sets
#
#   STEP_NAMES the driver's phases, in order (deploy.sh --phase <name>)
#   STEP_LABELS what the Launcher shows per phase (Dutch)
#   STEP_EXPECT the typical seconds per phase (for the time left)
#   prepare()  exports the driver's inputs (its pins, state dir, override)
#
# and then sources this file with "$@". The compose project its containers
# carry (for stop, and for the Launcher's "klaar") is environment.json's
# "project". It runs on Windows (Git for Windows' bash with Docker Desktop or
# Rancher Desktop) and on Linux.
#
#   run.sh start <progress-file>   the phases one by one, with a progress file
#   run.sh stop                    stop the environment's containers
#
# SPDX-License-Identifier: EUPL-1.2
set -u
here="$(cd "$(dirname "${BASH_SOURCE[1]}")" && pwd)"
py="${LAUNCH_PYTHON:-python3}"
# cfg EXPR: a value from this environment's environment.json (e = the object).
cfg() { "${py}" -c "import json,sys; e=json.load(open(sys.argv[1], encoding='utf-8')); print($1)" "${here}/environment.json"; }
PROJECT="$(cfg "e['project']")"

state="${LAUNCH_STATE:-${HOME}/.local/state/launch}"
# In Windows form (C:/Users/...): bash and Docker both take it, and Git Bash
# leaves it alone, also inside "-v <dir>:/dl:ro" (a /c/... path there broke).
command -v cygpath >/dev/null 2>&1 && state="$(cygpath -m "${state}")"
STATE_DIR="${state}/$(basename "${here}")"
mkdir -p "${STATE_DIR}"
# The drivers take the docker binary from <dir>/bin and the connection as -H:
# this computer's Docker (Docker Desktop, Rancher Desktop, or docker on Linux).
docker_bin="$(command -v docker || true)"
[[ -n "${docker_bin}" ]] || { echo "Docker is niet gevonden. Installeer Docker Desktop of Rancher Desktop." >&2; exit 1; }
export DAWO_DOCKER_DIR="$(dirname "$(dirname "${docker_bin}")")"
export DAWO_DOCKER_SOCK="$(docker context inspect --format '{{.Endpoints.docker.Host}}' 2>/dev/null)"
# Git Bash would turn paths inside the containers (/tmp/..., /bin/sh, ...) into
# Windows paths; this computer's own paths start with /c/ and still convert.
export MSYS2_ARG_CONV_EXCL="/tmp/;/opt/;/bin/;/usr/;/var/;/etc/;/run/"
# Git for Windows checks files out with CRLF (core.autocrlf): a container's
# shell script then fails with "cannot execute: required file not found"
# (Open Formulieren's database init in Valtimo). Upstream's files as they are.
export GIT_CONFIG_COUNT=1 GIT_CONFIG_KEY_0=core.autocrlf GIT_CONFIG_VALUE_0=false
deploy=(bash "${here}/deploy.sh")

if [[ "${1:-}" == stop ]]; then
  # Not deploy.sh --stop: that stops the appliance's Docker unit (systemctl),
  # which a laptop does not have. Stop this environment's containers; their
  # data stays.
  echo "==> stop the containers of ${PROJECT}"
  ids="$(docker ps -q --filter "label=com.docker.compose.project=${PROJECT}")"
  # shellcheck disable=SC2086 # one container id per word
  [[ -z "${ids}" ]] || docker stop -t 30 ${ids} >/dev/null
  echo "    stopped"
  exit 0
fi
[[ "${1:-}" == start && -n "${2:-}" ]] || { echo "usage: run.sh start <progress-file> | stop" >&2; exit 2; }
prog="$2"
prepare

since=$(date +%s)
log="${prog%.json}.steps.log"
write() { # write INDEX [ERROR]
  local i=$1 rest=0 j detail err=""
  for ((j = i + 1; j < ${#STEP_NAMES[@]}; j++)); do rest=$((rest + STEP_EXPECT[j])); done
  detail="$(grep -v '^[[:space:]]*$' "${log}" 2>/dev/null | grep -vE '^ (Image|Container|Volume|Network) ' | tail -n 1 | cut -c1-200 | tr -d '"\\')"
  [[ -n "${2:-}" ]] && err=",\"error\":\"$2\""
  printf '{"step":%d,"steps":%d,"label":"%s","started":%d,"since":%d,"expected":%d,"rest":%d,"detail":"%s","activity":%d%s}\n' \
    "$((i + 1))" "${#STEP_NAMES[@]}" "${STEP_LABELS[$i]}" "${started}" "${since}" "${STEP_EXPECT[$i]}" "${rest}" "${detail}" "$(date +%s)" "${err}" > "${prog}.new"
  mv -f "${prog}.new" "${prog}"
}
: > "${log}"
for i in "${!STEP_NAMES[@]}"; do
  started=$(date +%s); write "$i"
  ( while sleep 5; do write "$i"; done ) & ticker=$!
  "${deploy[@]}" --phase "${STEP_NAMES[$i]}" >> "${log}" 2>&1; rc=$?
  kill "${ticker}" 2>/dev/null; wait "${ticker}" 2>/dev/null
  if [[ ${rc} -ne 0 ]]; then
    write "$i" "Deze stap is mislukt. De details staan in $(basename "${here}").log."
    cat "${log}" >&2
    exit 1
  fi
done
rm -f "${prog}"
exit 0
