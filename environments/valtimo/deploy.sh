#!/usr/bin/env bash
# Deploys the Valtimo environment (#246) in the guest: upstream's official GZAC
# demo, gzac-docker-compose at a pinned commit, `docker compose --profile demo
# --profile zgw --profile openformulieren` (Valtimo, Open Zaak, Objecten, Open
# Notificaties and Open Formulieren, #260), plus our override file (environments/valtimo/compose/override.yaml). Runs in
# the Ubuntu guest as root, one phase at a time, called by the host runner
# (hosts/appliance/live-valtimo.nix). Every phase is idempotent.
#
#   deploy.sh --phase <name>    park | docker | source | pull | up | ready
#   deploy.sh --stop            stop the containers (the other environment)
#   deploy.sh --dry-run --phase <name>   print what would run
#
# Inputs come from the environment, set by the host from the manifest:
#   VT_REPO VT_REV VT_OVERRIDE DOCKER_URL DOCKER_SHA COMPOSE_URL COMPOSE_SHA
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

STATE_DIR="${VT_STATE_DIR:-/var/lib/dawo-appliance-valtimo}"
SRC_DIR="${STATE_DIR}/gzac-docker-compose"
DOWNLOADS="${STATE_DIR}/downloads"
DOCKER_DIR="${DAWO_DOCKER_DIR:-/opt/dawo-docker}" # overridable for tests outside the guest
# Upstream's README: "with demo data: --profile demo --profile zgw"; plus its
# openformulieren profile for the citizen form (#260). Not openklant: nothing
# in the demo uses it.
PROFILES=(demo zgw openformulieren)
PROFILE="${PROFILES[*]}"
# The compose project (the directory name); manifest valtimo.compose.project.
PROJECT="gzac-docker-compose"
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

PROFILE_ARGS=()
for p in "${PROFILES[@]}"; do PROFILE_ARGS+=(--profile "${p}"); done

compose() {
  # Two images at a time: all at once choked a slow network (TLS handshake
  # timeouts to Docker Hub in the nested test, 2026-10-17).
  run env PATH="${DOCKER_DIR}/bin:${PATH}" DOCKER_HOST="${DOCKER_SOCK}" COMPOSE_PARALLEL_LIMIT=2 \
    docker compose --project-directory "${SRC_DIR}" -f "${SRC_DIR}/docker-compose.yaml" -f "${VT_OVERRIDE:?}" \
    "${PROFILE_ARGS[@]}" "$@"
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
# Shared with the Nextcloud Common Ground environment (#250): the unit below
# MUST equal the one in apps/nextcloud-commonground/deploy.sh
# (tests/test-nextcloud-commonground-driver.sh).
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

# Upstream's compose at the pinned commit; the commit hash verifies the files.
phase_source() {
  log "gzac-docker-compose at ${VT_REV:?}"
  if [[ "${DRY_RUN}" -eq 1 ]]; then echo "DRY-RUN: git fetch --depth 1 ${VT_REPO:?} ${VT_REV}; verify HEAD"; return 0; fi
  if [[ -d "${SRC_DIR}/.git" ]] && [[ "$(git -C "${SRC_DIR}" rev-parse HEAD 2>/dev/null)" == "${VT_REV}" ]]; then
    info "already at ${VT_REV:0:12}"; return 0
  fi
  mkdir -p "${SRC_DIR}"
  [[ -d "${SRC_DIR}/.git" ]] || git -C "${SRC_DIR}" init -q
  git -C "${SRC_DIR}" remote remove origin >/dev/null 2>&1 || true
  git -C "${SRC_DIR}" remote add origin "${VT_REPO:?}"
  git -C "${SRC_DIR}" fetch -q --depth 1 origin "${VT_REV}" || git -C "${SRC_DIR}" fetch -q origin
  git -C "${SRC_DIR}" -c advice.detachedHead=false checkout -q "${VT_REV}"
  [[ "$(git -C "${SRC_DIR}" rev-parse HEAD)" == "${VT_REV}" ]] || die "gzac-docker-compose is not at ${VT_REV}"
  info "at ${VT_REV:0:12} (verified by the commit hash)"
}

phase_pull() {
  log "pull the demo images (by digest)"
  compose pull --quiet
}

phase_up() {
  log "start the demo (docker compose, profiles ${PROFILE}: up -d)"
  compose up -d
}

# Ready when Keycloak answers and the backend has finished its migrations and
# the demo cases (its readiness group includes the bootstrap), and the
# frontend answers. Bounded: 20 minutes.
phase_ready() {
  log "wait until Valtimo is ready"
  if [[ "${DRY_RUN}" -eq 1 ]]; then echo "DRY-RUN: wait for :8081/auth, the backend readiness and :80"; return 0; fi
  local waited=0 url ok
  until curl -fsS -o /dev/null --max-time 5 http://localhost:8081/auth/realms/valtimo; do
    [[ "${waited}" -ge 1200 ]] && die "Keycloak does not answer after ${waited}s"
    sleep 5; waited=$((waited + 5))
  done
  info "Keycloak answers (${waited}s)"
  # GZAC 13.4.1 answers 403 on the health groups (readiness) without a
  # token; the aggregate /actuator/health says UP once it serves (tested
  # in WSL 2026-10-17: UP at 206 s, the 7 demo cases created by then).
  until ok=""; for url in /actuator/health /management/health/readiness /api/management/health/readiness; do
          if curl -fsS --max-time 5 "http://localhost:8080${url}" 2>/dev/null | grep -q '"UP"'; then ok="${url}"; break; fi
        done; [[ -n "${ok}" ]]; do
    [[ "${waited}" -ge 1200 ]] && die "the backend is not ready after ${waited}s"
    sleep 5; waited=$((waited + 5))
  done
  info "backend ready at ${ok} (${waited}s)"
  until curl -fsS -o /dev/null --max-time 5 http://localhost/; do
    [[ "${waited}" -ge 1200 ]] && die "the frontend does not answer after ${waited}s"
    sleep 5; waited=$((waited + 5))
  done
  info "frontend answers (${waited}s)"
  # Open Zaak and the citizen form (#260): their first migrations take a few
  # minutes more.
  for url in http://localhost:8001/admin/ http://localhost:8003/leningaanvragen/; do
    until curl -sS -o /dev/null --max-time 5 -w '%{http_code}' "${url}" 2>/dev/null | grep -q '^[23]'; do
      [[ "${waited}" -ge 1800 ]] && die "${url} does not answer after ${waited}s"
      sleep 5; waited=$((waited + 5))
    done
    info "${url} answers (${waited}s)"
  done
  add_dawo_login
}

# The appliance's one demo login, launcher / launcher (#258), next to upstream's
# users: a user in upstream's realm with the same roles as its admin. Made
# through Keycloak's own admin CLI with upstream's admin account; done again
# on every start (it is there already: nothing changes).
add_dawo_login() {
  if [[ "${DRY_RUN}" -eq 1 ]]; then echo "DRY-RUN: kcadm: user launcher / launcher in realm valtimo, roles ROLE_ADMIN ROLE_USER"; return 0; fi
  local kc=(env PATH="${DOCKER_DIR}/bin:${PATH}" DOCKER_HOST="${DOCKER_SOCK}" docker exec gzac-docker-compose-gzac-keycloak
            /opt/keycloak/bin/kcadm.sh)
  local cfg=(--config /tmp/dawo-kcadm.config)
  # Keycloak's admin login comes a little after its realm: up to 2 minutes.
  local try
  for try in $(seq 1 24); do
    "${kc[@]}" config credentials "${cfg[@]}" --server http://localhost:8080/auth --realm master \
      --user admin --password admin >/dev/null 2>&1 && break
    [[ "${try}" -eq 24 ]] && { info "login launcher / launcher: Keycloak admin CLI not reachable; skipped"; return 0; }
    sleep 5
  done
  if ! "${kc[@]}" get users "${cfg[@]}" -r valtimo -q username=launcher -q exact=true --fields username 2>/dev/null | grep -q '"launcher"'; then
    "${kc[@]}" create users "${cfg[@]}" -r valtimo -s username=launcher -s enabled=true -s emailVerified=true \
      -s firstName=Launcher -s lastName=Demo -s email=launcher@example.com >/dev/null
  fi
  "${kc[@]}" set-password "${cfg[@]}" -r valtimo --username launcher --new-password launcher >/dev/null
  "${kc[@]}" add-roles "${cfg[@]}" -r valtimo --uusername launcher --rolename ROLE_ADMIN --rolename ROLE_USER >/dev/null
  info "login launcher / launcher (roles ROLE_ADMIN, ROLE_USER)"
  # Open Formulieren's admin signs in through this realm (upstream's fixture:
  # client valtimo-console), but the client only lets localhost:80/8080/4200
  # back in: add Open Formulieren's address (#260).
  local id client
  id="$("${kc[@]}" get clients "${cfg[@]}" -r valtimo -q clientId=valtimo-console --fields id 2>/dev/null \
        | python3 -c 'import json, sys; print(json.load(sys.stdin)[0]["id"])' 2>/dev/null || true)"
  [[ -n "${id}" ]] || info "Open Formulieren sign-in: client valtimo-console not found"
  client="$("${kc[@]}" get "clients/${id}" "${cfg[@]}" -r valtimo --fields redirectUris,webOrigins 2>/dev/null \
            | python3 -c '
import json, sys
c = json.load(sys.stdin)
if "http://localhost:8003/*" in c.get("redirectUris", []):
    sys.exit(0)
c["redirectUris"] = c.get("redirectUris", []) + ["http://localhost:8003/*"]
c["webOrigins"] = c.get("webOrigins", []) + ["http://localhost:8003"]
print(json.dumps(c))' || true)"
  if [[ -n "${id}" && -n "${client}" ]]; then
    env PATH="${DOCKER_DIR}/bin:${PATH}" DOCKER_HOST="${DOCKER_SOCK}" docker exec -i gzac-docker-compose-gzac-keycloak \
      /opt/keycloak/bin/kcadm.sh update "clients/${id}" "${cfg[@]}" -r valtimo -f - <<<"${client}" >/dev/null
  fi
  info "Open Formulieren may sign in through the realm (http://localhost:8003)"
  # The components underneath too (#258): a superuser launcher / launcher in each
  # Django admin (next to upstream's admin / admin) and an admin of
  # Keycloak's own console (master realm).
  local svc
  for svc in openzaak objecten-api objecttypen-api open-notificaties; do
    compose exec -T "${svc}" python src/manage.py shell -c "
from django.contrib.auth import get_user_model
U = get_user_model()
u = U.objects.filter(username='launcher').first() or U(username='launcher', email='launcher@example.com')
u.is_active = u.is_staff = u.is_superuser = True
u.set_password('launcher')
u.save()" >/dev/null 2>&1 || info "${svc}: no launcher login (its admin keeps admin / admin)"
  done
  if ! "${kc[@]}" get users "${cfg[@]}" -r master -q username=launcher -q exact=true --fields username 2>/dev/null | grep -q '"launcher"'; then
    "${kc[@]}" create users "${cfg[@]}" -r master -s username=launcher -s enabled=true >/dev/null
  fi
  "${kc[@]}" set-password "${cfg[@]}" -r master --username launcher --new-password launcher >/dev/null
  "${kc[@]}" add-roles "${cfg[@]}" -r master --uusername launcher --rolename admin >/dev/null
  info "login launcher / launcher also in Open Zaak, Objecten, Objecttypen, Open Notificaties and the Keycloak console"
}

# The other environment starts: stop the containers (they keep their data).
do_stop() {
  log "stop the Valtimo containers"
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
  *) die "unknown or missing --phase (park, docker, source, pull, up, ready)" ;;
esac
