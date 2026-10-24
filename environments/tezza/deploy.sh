#!/usr/bin/env bash
# Deploys the Tezza environment (#261) in the guest: Contezza's own demo setup,
# tezza-packaging at a pinned commit (git.contezza.nl/community/tezza-packaging,
# its docker-compose.yml and resources/), the complete product as Contezza
# ships it (Alfresco with Tezza, Share, Keycloak, LDAP, GZAC, Open Zaak,
# Notificaties, Objecten), plus our override file
# (environments/tezza/compose/override.yaml). Runs in the Ubuntu guest as root,
# one phase at a time, called by the host runner
# (hosts/appliance/live-tezza.nix). Every phase is idempotent. Nothing of Tezza
# is in the image: everything is downloaded from Contezza when it starts.
#
#   deploy.sh --phase <name>    park | docker | source | pull | up | ready | catalog | setup
#   deploy.sh --stop            stop the containers (the other environment)
#   deploy.sh --dry-run --phase <name>   print what would run
#
# Inputs come from the environment, set by the host from the manifest:
#   TZ_REPO TZ_REV TZ_OVERRIDE DOCKER_URL DOCKER_SHA COMPOSE_URL COMPOSE_SHA
#   TZ_SWF_URL TZ_SWF_SHA (optional: Súdwest-Fryslân's case types, #286)
#
# Experimental and unofficial. Not for production.
# SPDX-License-Identifier: EUPL-1.2
set -euo pipefail

DRY_RUN=0
PHASE=""
STOP=0
while [[ $# -gt 0 ]]; do
  case "$1" in
    --dry-run) DRY_RUN=1 ;;
    --phase) PHASE="${2:?--phase needs a name}"; shift ;;
    --stop) STOP=1 ;;
    *) echo "deploy.sh: unknown argument $1" >&2; exit 2 ;;
  esac
  shift
done

STATE_DIR="${TZ_STATE_DIR:-/var/lib/dawo-appliance-tezza}"
SRC_DIR="${STATE_DIR}/tezza-packaging"
DOWNLOADS="${STATE_DIR}/downloads"
DOCKER_DIR="${DAWO_DOCKER_DIR:-/opt/dawo-docker}" # overridable for tests outside the guest
# Every service of upstream's compose (the maintainer, 24 October: the
# product as Contezza ships it; without GZAC, Tezza registers no cases) but
# app-tezza-akten, which does not start in upstream's setup either (see the
# override). Upstream has no profiles: the services are named. Tezza at :8082,
# the Admin Tools at :8084, Share at :8081, Open Zaak at :8000, GZAC at :9091.
SERVICES=(platform platform-ass platform-db platform-activemq platform-transform-core-aio
          identity identity-db openldap share
          app-tezza app-tezza-admin app-tezza-rm app-tezza-reports
          authorization-profiles authorization-profiles-db
          gzac-backend gzac-db gzac-frontend personen-mock
          openzaak openzaak-celery openzaak-db openzaak-redis
          notificaties notificaties-celery notificaties-db notificaties-rabbitmq notificaties-redis
          objecten objecten-celery objecten-db objecten-redis
          objecttypen objecttypen-db objecttypen-redis)
# The compose project (the directory name).
PROJECT="tezza-packaging"
DOCKER_SOCK="${DAWO_DOCKER_SOCK:-unix:///run/dawo-docker.sock}"

info() { echo "    $*"; }
log() { echo "==> $*"; }
die() { echo "deploy.sh: $*" >&2; exit 1; }
run() { # run CMD...: print it in a dry run, else run it
  if [[ "${DRY_RUN}" -eq 1 ]]; then printf 'DRY-RUN:'; printf ' %q' "$@"; echo; else "$@"; fi
}

download_verified() { # URL SHA256 DEST
  local url="$1" sha="$2" dest="$3"
  if [[ "${DRY_RUN}" -eq 1 ]]; then echo "DRY-RUN: curl -fsSL ${url} -o ${dest}; sha256 must be ${sha}"; return 0; fi
  mkdir -p "$(dirname "${dest}")"
  if [[ -f "${dest}" ]] && printf '%s  %s\n' "${sha}" "${dest}" | sha256sum -c --quiet - >/dev/null 2>&1; then
    info "already downloaded and verified: ${dest}"; return 0
  fi
  curl -fsSL --retry 3 "${url}" -o "${dest}.tmp"
  printf '%s  %s\n' "${sha}" "${dest}.tmp" | sha256sum -c --quiet - >/dev/null 2>&1 \
    || { rm -f "${dest}.tmp"; die "SHA-256 mismatch for ${url} (expected ${sha}); refusing to continue"; }
  mv -f "${dest}.tmp" "${dest}"
  info "downloaded and verified: ${dest}"
}

compose() {
  # Two images at a time: all at once choked a slow network (see the Valtimo
  # driver). Upstream's compose file and our override; no profiles.
  run env PATH="${DOCKER_DIR}/bin:${PATH}" DOCKER_HOST="${DOCKER_SOCK}" COMPOSE_PARALLEL_LIMIT=2 \
    docker compose --project-directory "${SRC_DIR}" -f "${SRC_DIR}/docker-compose.yml" -f "${TZ_OVERRIDE:?}" "$@"
}

# One environment at a time (ADR 0008). The containers of every other compose
# project on the shared Docker Engine (dawo-docker, D43) are stopped
# explicitly: upstream's `restart: unless-stopped` (and `always`) would
# otherwise bring them back each time the engine starts, for example after
# the guest rebooted while Nextcloud Common Ground ran. Explicitly stopped
# containers stay stopped; their volumes and data stay.
park_other_compose_projects() {
  if [[ "${DRY_RUN}" -eq 1 ]]; then
    echo "DRY-RUN: docker ps: stop the running containers of every compose project but ${PROJECT}"; return 0
  fi
  "${DOCKER_DIR}/bin/docker" -H "${DOCKER_SOCK}" info >/dev/null 2>&1 || { info "Docker Engine is not running"; return 0; }
  local others
  others="$("${DOCKER_DIR}/bin/docker" -H "${DOCKER_SOCK}" ps --format '{{.ID}} {{.Label "com.docker.compose.project"}}' \
             | awk -v me="${PROJECT}" '$2 != "" && $2 != me')"
  if [[ -z "${others}" ]]; then info "no other compose project runs"; return 0; fi
  # shellcheck disable=SC2046 # one container id per word
  "${DOCKER_DIR}/bin/docker" -H "${DOCKER_SOCK}" stop $(awk '{ print $1 }' <<<"${others}") >/dev/null
  info "stopped the containers of: $(awk '{ print $2 }' <<<"${others}" | sort -u | tr '\n' ' ')"
}

# One environment at a time (ADR 0008): Mijn Bureau runs on K3s in this guest.
# Stop K3s and its pods so they do not take the memory; it starts again at the
# guest's next boot (Mijn Bureau's start boots the guest).
phase_park() {
  log "park the other environments (K3s, other compose projects)"
  if [[ "${DRY_RUN}" -eq 1 ]]; then echo "DRY-RUN: systemctl stop k3s; /usr/local/bin/k3s-killall.sh"; park_other_compose_projects; return 0; fi
  if systemctl is-active --quiet k3s 2>/dev/null; then
    run systemctl stop k3s
  fi
  if [[ -x /usr/local/bin/k3s-killall.sh ]]; then
    run /usr/local/bin/k3s-killall.sh >/dev/null 2>&1 || true
  fi
  info "K3s is stopped"
  park_other_compose_projects
}

# Docker Engine and the compose plugin, pinned static binaries, as a unit of
# our own (dawo-docker, own socket and data root) next to K3s's containerd.
# Shared with Valtimo and Nextcloud Common Ground: the unit below MUST equal
# the one in apps/valtimo/deploy.sh (tests/test-tezza-driver.sh).
phase_docker() {
  log "Docker Engine ${DOCKER_URL##*/} and compose"
  download_verified "${DOCKER_URL:?}" "${DOCKER_SHA:?}" "${DOWNLOADS}/${DOCKER_URL##*/}"
  download_verified "${COMPOSE_URL:?}" "${COMPOSE_SHA:?}" "${DOWNLOADS}/docker-compose"
  if [[ "${DRY_RUN}" -eq 1 ]]; then echo "DRY-RUN: unpack into ${DOCKER_DIR}, install dawo-docker.service, start it"; park_other_compose_projects; return 0; fi
  if ! cmp -s "${DOCKER_DIR}/bin/.sha256" <(printf '%s\n' "${DOCKER_SHA}") 2>/dev/null; then
    rm -rf "${DOCKER_DIR:?}/bin"; mkdir -p "${DOCKER_DIR}/bin"
    tar -xzf "${DOWNLOADS}/${DOCKER_URL##*/}" -C "${DOCKER_DIR}/bin" --strip-components=1
    printf '%s\n' "${DOCKER_SHA}" > "${DOCKER_DIR}/bin/.sha256"
  fi
  mkdir -p "${DOCKER_DIR}/cli-plugins"
  install -m 0755 "${DOWNLOADS}/docker-compose" "${DOCKER_DIR}/cli-plugins/docker-compose"
  mkdir -p /root/.docker && ln -sfn "${DOCKER_DIR}/cli-plugins" /root/.docker/cli-plugins
  # BEGIN dawo-docker.service
  cat > /etc/systemd/system/dawo-docker.service.new <<EOF
[Unit]
Description=Docker Engine for the compose environments (dawo-appliance #246, #250)
After=network-online.target
Wants=network-online.target

[Service]
Environment=PATH=${DOCKER_DIR}/bin:/usr/sbin:/usr/bin:/sbin:/bin
ExecStart=${DOCKER_DIR}/bin/dockerd --host=unix:///run/dawo-docker.sock --data-root=/var/lib/dawo-docker --exec-root=/run/dawo-docker --pidfile=/run/dawo-docker.pid --max-concurrent-downloads=2
Restart=on-failure
LimitNOFILE=1048576
Delegate=yes
KillMode=process

[Install]
WantedBy=
EOF
  # END dawo-docker.service
  if ! cmp -s /etc/systemd/system/dawo-docker.service.new /etc/systemd/system/dawo-docker.service; then
    mv -f /etc/systemd/system/dawo-docker.service.new /etc/systemd/system/dawo-docker.service
    systemctl daemon-reload
    systemctl try-restart dawo-docker
  else
    rm -f /etc/systemd/system/dawo-docker.service.new
  fi
  systemctl start dawo-docker
  local _
  for _ in $(seq 1 60); do
    "${DOCKER_DIR}/bin/docker" -H unix:///run/dawo-docker.sock info >/dev/null 2>&1 && break
    sleep 1
  done
  "${DOCKER_DIR}/bin/docker" -H unix:///run/dawo-docker.sock info >/dev/null 2>&1 || die "dockerd did not start"
  info "Docker Engine runs ($("${DOCKER_DIR}/bin/docker" -H unix:///run/dawo-docker.sock version --format '{{.Server.Version}}'))"
  # The engine just started: containers of another environment with a
  # restart policy are back. Park them again.
  park_other_compose_projects
}

# Upstream's packaging at the pinned commit; the commit hash verifies the files.
phase_source() {
  log "tezza-packaging at ${TZ_REV:?}"
  if [[ "${DRY_RUN}" -eq 1 ]]; then echo "DRY-RUN: git fetch --depth 1 ${TZ_REPO:?} ${TZ_REV}; verify HEAD"; return 0; fi
  if [[ -d "${SRC_DIR}/.git" ]] && [[ "$(git -C "${SRC_DIR}" rev-parse HEAD 2>/dev/null)" == "${TZ_REV}" ]]; then
    info "already at ${TZ_REV:0:12}"; return 0
  fi
  mkdir -p "${SRC_DIR}"
  [[ -d "${SRC_DIR}/.git" ]] || git -C "${SRC_DIR}" init -q
  git -C "${SRC_DIR}" remote remove origin >/dev/null 2>&1 || true
  git -C "${SRC_DIR}" remote add origin "${TZ_REPO:?}"
  git -C "${SRC_DIR}" fetch -q --depth 1 origin "${TZ_REV}" || git -C "${SRC_DIR}" fetch -q origin
  git -C "${SRC_DIR}" -c advice.detachedHead=false checkout -q "${TZ_REV}"
  [[ "$(git -C "${SRC_DIR}" rev-parse HEAD)" == "${TZ_REV}" ]] || die "tezza-packaging is not at ${TZ_REV}"
  info "at ${TZ_REV:0:12} (verified by the commit hash)"
}

phase_pull() {
  log "pull the images of the product (by digest)"
  compose pull --quiet "${SERVICES[@]}"
}

# Upstream's README: the network is created first (it is external).
phase_up() {
  log "start Tezza (docker compose up -d: ${SERVICES[*]})"
  if [[ "${DRY_RUN}" -eq 1 ]]; then echo "DRY-RUN: docker network create -d bridge dev_network"
  elif ! "${DOCKER_DIR}/bin/docker" -H "${DOCKER_SOCK}" network inspect dev_network >/dev/null 2>&1; then
    "${DOCKER_DIR}/bin/docker" -H "${DOCKER_SOCK}" network create -d bridge dev_network >/dev/null
    info "network dev_network created"
  fi
  compose up -d "${SERVICES[@]}"
}

# Ready when Keycloak's realm, Alfresco's readiness probe and the Tezza app
# answer. Bounded: 30 minutes (the first start creates the repository).
phase_ready() {
  log "wait until Tezza is ready"
  if [[ "${DRY_RUN}" -eq 1 ]]; then echo "DRY-RUN: wait for :8181 realm tezza, :8080 Alfresco readiness, :8082, :8000 Open Zaak"; return 0; fi
  # One line a minute instead of curl's error every 5 s: the launcher shows
  # the last line, and "curl: (7) ..." told the user nothing (#268). When a
  # service does not answer in time, the log gets the containers and that
  # service's log, so the cause is on the stick.
  local timeout="${TZ_READY_TIMEOUT:-1800}" waited=0 entry url svc name
  for entry in "identity Keycloak http://localhost:8181/auth/realms/tezza" \
               "platform Alfresco http://localhost:8080/alfresco/api/-default-/public/alfresco/versions/1/probes/-ready-" \
               "app-tezza Tezza http://localhost:8082/" \
               "openzaak Open-Zaak http://localhost:8000/admin/"; do
    read -r svc name url <<<"${entry}"
    until curl -fs -o /dev/null --max-time 5 "${url}"; do
      if [[ "${waited}" -ge "${timeout}" ]]; then
        echo "== containers"; compose ps -a 2>&1 || true
        echo "== ${svc} (log, last 60 lines)"; compose logs --no-color --tail 60 "${svc}" 2>&1 || true
        die "${url} does not answer after ${waited}s"
      fi
      [[ "${waited}" -gt 0 && $((waited % 60)) -eq 0 ]] && info "waiting for ${name//-/ } (${url}), ${waited}s"
      sleep 5; waited=$((waited + 5))
    done
    info "${url} answers (${waited}s)"
  done
}

alfresco() { # alfresco METHOD PATH [JSON]: Alfresco's REST API as upstream's admin
  curl -sS -o /dev/null -w '%{http_code}' --max-time 120 -u admin:admin -X "$1" -H 'Content-Type: application/json' \
    "http://localhost:8080/alfresco/api/-default-/public/alfresco/versions/1/$2" ${3:+-d "$3"}
}

# Upstream's own first-run step ("01. Setup" in the Admin Tools: the
# webscript contezza/config/setup) stops at its first failing step, and some
# steps fail until another service is up (createObjecttypeSites asks the
# Objecttypen API). So its steps are called one by one, in its order, by their
# names in upstream's library (tezza-api.lib.js), through the JavaScript
# console the platform ships (OOTBee); a failing step is tried again. The
# Tezza app configuration comes from upstream's webscript contezza/config/tezza.
# Then the login launcher / launcher (#258): a user in upstream's LDAP (Keycloak signs
# in against it), an Alfresco administrator, an admin of Keycloak's console.
SETUP_STEPS=(createSites createRootGroups createServiceAccount createFolderTemplates createTezzaTemplates
             createZaaktypeSites createObjecttypeSites createAuditRole createPostintakeDummyData
             assignLDAPUsersToZaaktypeGroups assignLDAPUsersToGroups createRMRoles createRootRMGroups
             addRMGroupsToRMRoles assignLDAPUsersToRMGroups)
# Steps that may keep failing without stopping the setup (the Objecttypen API).
SETUP_STEPS_ZGW=(createObjecttypeSites)

# What the app checks before it lets an administrator in ("Setup vereist"
# otherwise): a site of type "beheer" in the search index.
beheer_site() {
  curl -fsS --max-time 30 -u admin:admin -H 'Content-Type: application/json'     -d '{"query":{"query":"TYPE:\"st:site\" AND ASPECT:\"tza:site\" AND =tza:siteType:\"beheer\"","language":"afts"}}'     http://localhost:8080/alfresco/api/-default-/public/search/versions/1/search 2>/dev/null     | python3 -c 'import json, sys; sys.exit(0 if json.load(sys.stdin)["list"]["entries"] else 1)' 2>/dev/null
}

# Súdwest-Fryslân's own case types (#286): its Open Zaak catalogue export
# (OpenZaakBrug, EUPL-1.2, pinned by commit and SHA-256 in the manifest),
# made importable in this Open Zaak by swf-convert.py, imported with Open
# Zaak's own command, and the newest published version of each case type
# published (swf-publish.py). Once: when the catalogue is there it is left
# alone. Tezza's setup then makes a site and groups per case type, so it runs
# again. Should any of it fail, Tezza works without these case types.
phase_catalog() {
  log "Súdwest-Fryslân's case types in Open Zaak"
  if [[ "${DRY_RUN}" -eq 1 ]]; then
    echo "DRY-RUN: curl ${TZ_SWF_URL:-<none>}; swf-convert.py; manage.py import; swf-publish.py"; return 0
  fi
  if [[ -z "${TZ_SWF_URL:-}" ]]; then info "no catalogue configured"; return 0; fi
  if compose exec -T openzaak-db psql -U openzaak -d openzaak -tAc "select 1 from catalogi_catalogus where domein = 'SWF'" 2>/dev/null | grep -q 1; then
    info "already in Open Zaak"; return 0
  fi
  # set -e does not work in a subshell that is an if's condition: run it, then look.
  local rc=0
  set +e
  ( set -e
       download_verified "${TZ_SWF_URL}" "${TZ_SWF_SHA:?}" "${DOWNLOADS}/swf-catalogus.zip"
       python3 "${STATE_DIR}/swf-convert.py" "${DOWNLOADS}/swf-catalogus.zip" "${STATE_DIR}/swf-catalogus-oz.zip" "${STATE_DIR}/selectielijst.json"
       compose cp "${STATE_DIR}/swf-catalogus-oz.zip" openzaak:/tmp/swf.zip
       compose exec -T openzaak python src/manage.py import --import-file /tmp/swf.zip
       compose exec -T openzaak python src/manage.py shell < "${STATE_DIR}/swf-publish.py"
  ); rc=$?
  set -e
  if [[ "${rc}" -eq 0 ]]; then
    rm -f "${STATE_DIR}/setup.done" # a site and groups per new case type
    info "Súdwest-Fryslân's case types are in Open Zaak"
  else
    info "Súdwest-Fryslân's case types could not be loaded; Tezza works without them"
  fi
}

phase_setup() {
  log "Tezza's setup (upstream's steps) and the login launcher / launcher"
  if [[ "${DRY_RUN}" -eq 1 ]]; then echo "DRY-RUN: jsconsole: ${SETUP_STEPS[*]}; GET contezza/config/tezza; ldapadd launcher; Alfresco admin launcher; kcadm master launcher"; return 0; fi
  # Done before only counts when the app's own check agrees (an earlier run
  # marked it done while createSites had failed: the Dell, 2026-10-18).
  if [[ -f "${STATE_DIR}/setup.done" ]] && ! beheer_site; then
    info "setup was marked done, but the 'beheer' site is missing: again"
    rm -f "${STATE_DIR}/setup.done"
  fi
  if [[ ! -f "${STATE_DIR}/setup.done" ]]; then
    # The steps check what exists already. Right after start Alfresco may
    # still refuse a write ("ConcurrencyFailureException" on the Dell,
    # 2026-10-18; then the sites and everything after them were missing and
    # the app said "Setup vereist"): up to four rounds, until every step but
    # the ZGW ones is done.
    local round failed
    for round in 1 2 3 4; do
      python3 - "${SETUP_STEPS[@]}" > "${STATE_DIR}/setup.json" <<'PY'
import json, sys
calls = "".join(f"run('{s}');\n" for s in sys.argv[1:])
script = ('<import resource="classpath:alfresco/templates/webscripts/nl/contezza/api/tezza-api.lib.js">\n'
          "var out = [];\n"
          "function run(n) { try { (eval(n))(); out.push(n + ': ok'); } catch (e) { out.push(n + ': not done (' + (e.message || e) + ')'); } }\n"
          + calls + "print(out.join('\\n'));\n")
print(json.dumps({"script": script, "template": "", "runas": "admin", "transaction": "readwrite",
                  "spaceNodeRef": "", "documentNodeRef": "", "urlargs": ""}))
PY
      curl -fsS --max-time 1800 -u admin:admin -H 'Content-Type: application/json' -d "@${STATE_DIR}/setup.json" \
        http://localhost:8080/alfresco/service/ootbee/jsconsole/execute > "${STATE_DIR}/setup.log" \
        || die "upstream's setup steps did not run"
      info "round ${round}:"
      python3 -c 'import json, sys; [print("    " + l) for l in "\n".join(json.load(open(sys.argv[1]))["printOutput"]).splitlines()]' \
        "${STATE_DIR}/setup.log" || true
      failed="$(python3 -c 'import json, sys; print(" ".join(l.split(":")[0] for l in "\n".join(json.load(open(sys.argv[1]))["printOutput"]).splitlines() if ": not done" in l and l.split(":")[0] not in sys.argv[2:]))' \
        "${STATE_DIR}/setup.log" "${SETUP_STEPS_ZGW[@]}" || echo unknown)"
      [[ -z "${failed}" ]] && break
      [[ "${round}" -eq 4 ]] && die "upstream's setup steps not done: ${failed}"
      info "not done yet: ${failed}; again in 30 s"
      sleep 30
    done
    curl -fsS -o /dev/null --max-time 300 -u admin:admin http://localhost:8080/alfresco/service/contezza/config/tezza \
      || die "upstream's app configuration (contezza/config/tezza) did not run"
    local waited=0
    until beheer_site; do
      [[ "${waited}" -ge 300 ]] && die "no site of type 'beheer' after ${waited}s: the app would say 'Setup vereist'"
      sleep 10; waited=$((waited + 10))
    done
    info "the app finds its 'beheer' site (no 'Setup vereist')"
    touch "${STATE_DIR}/setup.done"
    info "upstream's setup done"
  else
    info "upstream's setup was done before"
  fi
  # LDAP (upstream's users.ldif shape).
  if ! compose exec -T openldap ldapsearch -x -H ldap://localhost -b ou=Users,dc=contezza,dc=local -D cn=admin,dc=contezza,dc=local -w admin "(uid=launcher)" uid 2>/dev/null | grep -q '^uid: launcher'; then
    # 68: the entry is there already (the check above may not see it).
    compose exec -T openldap ldapadd -x -H ldap://localhost -D cn=admin,dc=contezza,dc=local -w admin >/dev/null <<'EOF' || [[ $? -eq 68 ]]
dn: cn=launcher,ou=Users,dc=contezza,dc=local
objectClass: person
objectClass: inetOrgPerson
objectClass: organizationalPerson
objectClass: top
cn: launcher
userpassword: launcher
givenname: Launcher
sn: Demo
mail: launcher@example.com
uid: launcher
ou: Management
EOF
  fi
  # Alfresco: the person (as LDAP sync would make it) and an administrator.
  local code
  code="$(alfresco POST people '{"id":"launcher","firstName":"Launcher","lastName":"Demo","email":"launcher@example.com","password":"launcher"}')"
  [[ "${code}" == 201 || "${code}" == 409 ]] || info "Alfresco person launcher: HTTP ${code}"
  code="$(alfresco POST groups/GROUP_ALFRESCO_ADMINISTRATORS/members '{"id":"launcher","memberType":"PERSON"}')"
  [[ "${code}" == 201 || "${code}" == 409 ]] || info "Alfresco administrators: HTTP ${code}"
  # The same groups as upstream's admin (TEZZA_CREATE_ZAKEN, the sites per
  # case type, records management): without them 'Maken' offered no
  # 'Registreer zaak' to launcher (local test of 24 October).
  local g n=0
  while IFS= read -r g; do
    g="${g%$'\r'}"
    code="$(alfresco POST "groups/${g}/members" '{"id":"launcher","memberType":"PERSON"}' || true)"
    [[ "${code}" == 201 ]] && n=$((n + 1))
  done < <(curl -fsS --max-time 120 -u admin:admin \
             "http://localhost:8080/alfresco/api/-default-/public/alfresco/versions/1/people/admin/groups?maxItems=1000" \
           | python3 -c 'import json, sys; [print(e["entry"]["id"]) for e in json.load(sys.stdin)["list"]["entries"] if e["entry"]["id"] != "GROUP_EVERYONE"]')
  info "launcher is in admin's groups (${n} added)"
  # Keycloak's own console (master realm), next to upstream's admin / admin.
  local kc=(/opt/keycloak/bin/kcadm.sh) cfg=(--config /tmp/dawo-kcadm.config)
  if compose exec -T identity "${kc[@]}" config credentials "${cfg[@]}" --server http://localhost:8181/auth \
       --realm master --user admin --password admin >/dev/null 2>&1; then
    if ! compose exec -T identity "${kc[@]}" get users "${cfg[@]}" -r master -q username=launcher -q exact=true --fields username 2>/dev/null | grep -q '"launcher"'; then
      compose exec -T identity "${kc[@]}" create users "${cfg[@]}" -r master -s username=launcher -s enabled=true >/dev/null
    fi
    compose exec -T identity "${kc[@]}" set-password "${cfg[@]}" -r master --username launcher --new-password launcher >/dev/null
    compose exec -T identity "${kc[@]}" add-roles "${cfg[@]}" -r master --uusername launcher --rolename admin >/dev/null
  fi
  # Open Zaak's admin (#268): a superuser next to upstream's admin / admin.
  compose exec -T openzaak python src/manage.py shell -c "
from django.contrib.auth import get_user_model
U = get_user_model()
u = U.objects.filter(username='launcher').first() or U(username='launcher', email='launcher@example.com')
u.is_active = u.is_staff = u.is_superuser = True
u.set_password('launcher')
u.save()" >/dev/null 2>&1 || info "Open Zaak: no launcher login (its admin keeps admin / admin)"
  info "login launcher / launcher (Tezza, Share, Admin Tools, Keycloak console, Open Zaak)"
}

# The other environment starts: stop the containers (they keep their data).
do_stop() {
  log "stop the Tezza containers"
  if [[ "${DRY_RUN}" -eq 0 ]] && ! systemctl is-active --quiet dawo-docker 2>/dev/null; then
    info "Docker Engine is not running"; return 0
  fi
  compose stop || true
  run systemctl stop dawo-docker
}

if [[ "${STOP}" -eq 1 ]]; then do_stop; exit 0; fi
case "${PHASE}" in
  park) phase_park ;;
  docker) phase_docker ;;
  source) phase_source ;;
  pull) phase_pull ;;
  up) phase_up ;;
  ready) phase_ready ;;
  catalog) phase_catalog ;;
  setup) phase_setup ;;
  *) die "unknown or missing --phase (park, docker, source, pull, up, ready, setup)" ;;
esac
