#!/usr/bin/env bash
# Informatiebeheer: start or stop the demos XENA, MIRA and ELSA with Docker,
# step by step, and write the Launcher's progress file as Haal Centraal does
# ({step, steps, label, started, since, expected, rest, detail, activity, error}).
#
#   run.sh start <progress-file>     exit 0 when the start page answers
#   run.sh stop
#
# The demos are fetched once, at the commits in environment.json, and checked
# against their sha256; a file that does not match is not used.
# INFORMATIEBEHEER_PORT is the page's port (default 18796). DOCKER is the
# docker command (default: docker, or docker.exe in WSL).
#
# SPDX-License-Identifier: EUPL-1.2
set -u
cd "$(dirname "${BASH_SOURCE[0]}")"
DOCKER="${DOCKER:-}"
if [[ -z "${DOCKER}" ]]; then
  if docker version >/dev/null 2>&1; then DOCKER=docker; else DOCKER=docker.exe; fi
fi
py="${LAUNCH_PYTHON:-python3}"
port="${INFORMATIEBEHEER_PORT:-18796}"
state="${LAUNCH_STATE:-${HOME}/.local/state/launch}"
# In Windows form (C:/Users/...), as Docker takes it in a volume (lib/run-driver.sh).
command -v cygpath >/dev/null 2>&1 && state="$(cygpath -m "${state}")"
site="${state}/informatiebeheer/site"
dc() { INFORMATIEBEHEER_PORT="${port}" INFORMATIEBEHEER_SITE="${site}" "${DOCKER}" compose -f compose.yaml "$@"; }

if [[ "${1:-}" == stop ]]; then
  dc stop >/dev/null 2>&1
  exit 0
fi
[[ "${1:-}" == start && -n "${2:-}" ]] || { echo "usage: run.sh start <progress-file> | stop" >&2; exit 2; }
prog="$2"
labels=("De demo's ophalen (XENA, MIRA, ELSA)" "Informatiebeheer starten" "Wachten tot de pagina antwoordt")
expect=(10 10 5)
since=$(date +%s)
write() { # write INDEX DETAIL [ERROR]
  local i=$1 rest=0 j
  for ((j = i + 1; j < 3; j++)); do rest=$((rest + expect[j])); done
  local err=""; [[ -n "${3:-}" ]] && err=",\"error\":\"$3\""
  printf '{"step":%d,"steps":3,"label":"%s","started":%d,"since":%d,"expected":%d,"rest":%d,"detail":"%s","activity":%d%s}\n' \
    "$((i + 1))" "${labels[$i]}" "${started}" "${since}" "${expect[$i]}" "${rest}" "$2" "$(date +%s)" "${err}" > "${prog}.new"
  mv -f "${prog}.new" "${prog}"
}
# fetch: each demo's index.html at its commit into <site>/<id>/, checked; one already
# there with the right sha256 is kept.
fetch() {
  "${py}" - "environment.json" "${site}" <<'PY'
import hashlib, json, os, sys, urllib.request
env, site = json.load(open(sys.argv[1], encoding="utf-8")), sys.argv[2]
for d in env["demos"]:
    path = os.path.join(site, d["id"], "index.html")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    try:
        with open(path, "rb") as f:
            if hashlib.sha256(f.read()).hexdigest() == d["sha256"]:
                print(f"{d['id']}: er al")
                continue
    except OSError:
        pass
    raw = d["repo"].replace("https://github.com/", "https://raw.githubusercontent.com/") + f"/{d['rev']}/index.html"
    data = urllib.request.urlopen(raw, timeout=120).read()
    if hashlib.sha256(data).hexdigest() != d["sha256"]:
        sys.exit(f"{d['id']}: de download klopt niet met de vastgelegde versie ({raw})")
    with open(path + ".new", "wb") as f:
        f.write(data)
    os.replace(path + ".new", path)
    print(f"{d['id']}: opgehaald ({len(data) // 1024} kB)")
PY
}
answers() { curl -fs -o /dev/null --max-time 3 "http://localhost:${port}/xena/"; }
step() { # step INDEX DETAIL COMMAND...: the step, at least 2 s so it can be seen
  local i=$1 detail=$2; shift 2
  started=$(date +%s); write "$i" "${detail}"
  if [[ "${INFORMATIEBEHEER_FAIL_AT:-}" == "$((i + 1))" ]]; then
    sleep 2; write "$i" "${detail}" "Deze stap is mislukt (opzettelijk, voor de test)."; return 1
  fi
  "$@" >> "${prog}.log" 2>&1 || { write "$i" "${detail}" "Deze stap is mislukt."; return 1; }
  sleep $((started + 2 - $(date +%s) > 0 ? started + 2 - $(date +%s) : 0))
}
wait_answer() { local t; for t in $(seq 1 60); do answers && return 0; sleep 1; done; return 1; }

step 0 "drie losse pagina's, vastgelegde versies" fetch || exit 1
step 1 "docker compose up" dc up -d || exit 1
step 2 "de startpagina en XENA" wait_answer || exit 1
rm -f "${prog}"
