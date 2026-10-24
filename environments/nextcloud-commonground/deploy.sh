#!/usr/bin/env bash
# Deploys the Nextcloud Common Ground environment (#250) in the guest:
# Conduction's own local demo, dossiq-compose.yaml (ConductionNL/dossiq) at a
# pinned commit, `docker compose up`, plus our override file
# (environments/nextcloud-commonground/compose/override.yaml). Runs in the
# Ubuntu guest as root, one phase at a time, called by the host runner
# (hosts/appliance/live-nextcloud-commonground.nix). Every phase is idempotent.
#
#   deploy.sh --phase <name>    park | docker | source | pull | apps | up | ready | enable | seed
#   deploy.sh --stop            stop the containers
#   deploy.sh --dry-run --phase <name>   print what would run
#
# Inputs come from the environment, set by the host from the manifest:
#   NC_COMPOSE_URL NC_COMPOSE_SHA NC_OVERRIDE NC_APPS_FILE
#   DOCKER_URL DOCKER_SHA COMPOSE_URL COMPOSE_SHA
# NC_APPS_FILE has one line per app: "<id> <version> <url> <sha256>".
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

STATE_DIR="${NC_STATE_DIR:-/var/lib/dawo-appliance-nextcloud-commonground}"
SRC_DIR="${STATE_DIR}/dossiq"
COMPOSE_FILE="${SRC_DIR}/dossiq-compose.yaml"
DOWNLOADS="${STATE_DIR}/downloads"
DOCKER_DIR="${DAWO_DOCKER_DIR:-/opt/dawo-docker}" # overridable for tests outside the guest
DOCKER_SOCK="${DAWO_DOCKER_SOCK:-unix:///run/dawo-docker.sock}"
# Upstream's `name:` in dossiq-compose.yaml; manifest nextcloud_commonground.compose.project.
PROJECT="dossiq-demo"
PORT=8609 # upstream's DEMO_PORT default, kept
# Enable order: OpenRegister first (the others declare their registers
# against it), OpenCatalogi before Portaliq (its public portal needs it).
# The first four are upstream's own list and order.
ENABLE_ORDER=(openregister thematiq integriq dossiq opencatalogi pipelinq portaliq)

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

# Two images at a time (see the Valtimo driver): all at once choked a slow network.
docker_cli() { run env PATH="${DOCKER_DIR}/bin:${PATH}" DOCKER_HOST="${DOCKER_SOCK}" COMPOSE_PARALLEL_LIMIT=2 docker "$@"; }

compose() {
  docker_cli compose --project-directory "${SRC_DIR}" -f "${COMPOSE_FILE}" -f "${NC_OVERRIDE:?}" "$@"
}

occ() { compose exec -T -u www-data nextcloud php occ "$@"; }

# The apps file: "<id> <version> <url> <sha256>" per line.
apps_list() {
  [[ -r "${NC_APPS_FILE:?}" ]] || die "NC_APPS_FILE ${NC_APPS_FILE} is not readable"
  grep -v '^[[:space:]]*\(#\|$\)' "${NC_APPS_FILE}"
}

# One environment at a time (ADR 0008). The containers of every other compose
# project on the shared Docker Engine (dawo-docker, D43) are stopped
# explicitly: upstream's `restart: unless-stopped` (and `always`) would
# otherwise bring them back each time the engine starts, for example after
# the guest rebooted while Valtimo ran. Explicitly stopped
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
# guest's next boot (Mijn Bureau's start boots the guest). Other compose
# environments (Valtimo) are parked too, here if the engine already runs and
# again in the docker phase once it does.
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
# Shared with the Valtimo environment: the unit below MUST equal the one in
# apps/valtimo/deploy.sh (tests/test-nextcloud-commonground-driver.sh).
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
    "${DOCKER_DIR}/bin/docker" -H "${DOCKER_SOCK}" info >/dev/null 2>&1 && break
    sleep 1
  done
  "${DOCKER_DIR}/bin/docker" -H "${DOCKER_SOCK}" info >/dev/null 2>&1 || die "dockerd did not start"
  info "Docker Engine runs ($("${DOCKER_DIR}/bin/docker" -H "${DOCKER_SOCK}" version --format '{{.Server.Version}}'))"
  # The engine just started: containers of another environment with a
  # restart policy are back. Park them again.
  park_other_compose_projects
}

# Upstream's compose file at the pinned commit, by its raw URL and SHA-256
# (it is one file; its scripts are inline `configs`).
phase_source() {
  log "dossiq-compose.yaml (${NC_COMPOSE_URL:?})"
  download_verified "${NC_COMPOSE_URL}" "${NC_COMPOSE_SHA:?}" "${COMPOSE_FILE}"
}

phase_pull() {
  log "pull the images (by digest)"
  compose pull --quiet
}

# Each app's release tarball, verified against its SHA-256 from the manifest,
# unpacked into upstream's `apps` volume the way upstream's installer does it
# (staging directory, renamed to the app id, owned by www-data). Upstream's
# app-installer then finds each app present and skips it, so nothing
# unverified is installed. Runs in upstream's own app-installer service
# (its image, its volume), with another command.
phase_apps() {
  log "the apps, verified by SHA-256, into the apps volume"
  local id ver url sha specs=""
  while read -r id ver url sha; do
    [[ -n "${sha:-}" ]] || die "NC_APPS_FILE: a line lacks fields (${id:-?})"
    download_verified "${url}" "${sha}" "${DOWNLOADS}/apps/${id}-${ver}.tar.gz"
    specs+="${id}:${ver} "
  done < <(apps_list)
  # Runs in the alpine container (busybox sh): $ is for that shell.
  # shellcheck disable=SC2016
  local script='set -eu
for spec in $NC_SPECS; do
  app=${spec%%:*}; ver=${spec#*:}
  have=$(sed -n "s:.*<version>\([^<]*\)</version>.*:\1:p" "/apps/$app/appinfo/info.xml" 2>/dev/null | head -n1 || true)
  if [ "$have" = "$ver" ]; then echo "    $app $ver already in place"; continue; fi
  rm -rf /tmp/stage "/apps/$app"; mkdir -p /tmp/stage
  tar -xzf "/dl/$app-$ver.tar.gz" -C /tmp/stage
  top=$(ls /tmp/stage | head -n1)
  mv "/tmp/stage/$top" "/apps/$app"
  echo "    $app $ver unpacked (archive top directory: $top)"
done
chown -R 33:33 /apps'
  compose run --rm --no-deps -T -e "NC_SPECS=${specs% }" -v "${DOWNLOADS}/apps:/dl:ro" \
    app-installer /bin/sh -c "${script}"
}

phase_up() {
  log "start the demo (docker compose up -d)"
  compose up -d
}

# Ready when Nextcloud reports itself installed and out of maintenance mode
# (status.php). The image runs the install and upstream's post-installation
# hook (enabling the apps) before Apache starts. Bounded: 20 minutes.
phase_ready() {
  log "wait until Nextcloud is ready on :${PORT}"
  if [[ "${DRY_RUN}" -eq 1 ]]; then echo "DRY-RUN: wait for http://localhost:${PORT}/status.php: installed, not in maintenance"; return 0; fi
  local waited=0 s=""
  until s="$(curl -fsS --max-time 5 "http://localhost:${PORT}/status.php" 2>/dev/null)" \
        && grep -q '"installed":true' <<<"${s}" && grep -q '"maintenance":false' <<<"${s}"; do
    [[ "${waited}" -ge 1200 ]] && die "Nextcloud is not ready after ${waited}s (status: ${s:-no answer})"
    sleep 5; waited=$((waited + 5))
  done
  info "Nextcloud answers (${waited}s): ${s}"
}

# The apps beyond upstream's four (OpenCatalogi, Pipelinq, Portaliq) enabled
# with occ, all seven in dependency order (idempotent; it also repairs one
# that upstream's hook only warned about), then upstream's own schema repair.
# An app tarball newer than the database (a pin bump) needs `occ upgrade`.
phase_enable() {
  log "enable the apps (${ENABLE_ORDER[*]})"
  if [[ "${DRY_RUN}" -eq 0 ]] && curl -fsS --max-time 5 "http://localhost:${PORT}/status.php" 2>/dev/null | grep -q '"needsDbUpgrade":true'; then
    info "an app or Nextcloud is newer than its database: occ upgrade"
    occ upgrade
  fi
  local app
  for app in "${ENABLE_ORDER[@]}"; do
    occ app:enable "${app}"
  done
  occ openregister:schemas:dedup || true
}

# Conduction's own demo data: Dossiq's caseload and Pipelinq's dataset. Both
# commands are idempotent (they skip what is already there).
phase_seed() {
  log "demo data (dossiq:demo:seed, pipelinq:demo:seed, the apps' own example data)"
  occ dossiq:demo:seed --verify-only || true
  occ dossiq:demo:seed
  occ pipelinq:demo:seed
  add_dawo_login
  out_of_the_box
}

# Out of the box (#259): the answers each app's setup wizard asks for, so no
# wizard covers the app ("HET MOET OUT OF THE BOX WERKEN"), the example data
# the wizards' "Laden" buttons load, and no first-run screens or tours. Every
# key is the app's own (its SetupController status(), checked in the pinned
# releases); an app shows its wizard while a required step is not done.
# Idempotent: it runs on every start.
out_of_the_box() {
  log "out of the box: setup answered, example data, no first-run screens"
  local base="http://localhost:${PORT}/index.php/apps" app user
  # Nextcloud's own first-run screens and admin notices.
  for app in firstrunwizard nextcloud_announcements updatenotification survey_client support; do
    occ app:disable "${app}" >/dev/null 2>&1 || true
  done
  occ config:system:set updatechecker --type=boolean --value=false >/dev/null
  # Required steps: register settings (normally set by the apps' repair
  # step), Pipelinq's reporting currency, OpenCatalogi's scope.
  if ! occ config:app:get dossiq case_type_schema >/dev/null 2>&1 \
     || ! occ config:app:get opencatalogi listing_register >/dev/null 2>&1; then
    occ maintenance:repair >/dev/null
  fi
  occ config:app:set pipelinq currency --value=EUR >/dev/null
  occ config:app:set pipelinq receipt_company_name --value="Gemeente Voorbeeld" >/dev/null
  occ config:app:set pipelinq receipt_company_city --value="Voorbeeld" >/dev/null
  occ config:app:set pipelinq receipt_company_country --value="Nederland" >/dev/null
  occ config:app:set opencatalogi default_catalog_scope --value=public >/dev/null
  occ config:app:set opencatalogi onboarding_completed_version --value=3 >/dev/null
  # Dossiq's optional dwangsom step wants a callback secret: made here, once,
  # never in git or the image.
  if ! occ config:app:get dossiq dwangsom_callback_secret >/dev/null 2>&1; then
    occ config:app:set dossiq dwangsom_callback_secret --value="$(head -c 32 /dev/urandom | od -An -tx1 | tr -d ' \n')" >/dev/null
  fi
  occ dossiq:bezwaar:seed >/dev/null 2>&1 || true
  occ portaliq:example-site:install zuiddrecht >/dev/null 2>&1 || true
  # The wizards' own example data has no occ command: their setup action, as
  # the admin (upstream's admin / admin), once per app.
  for app in openregister integriq dossiq opencatalogi portaliq; do
    # The action loads the dataset the wizard chose (400 without one).
    occ config:app:set "${app}" demo_dataset --value=demo >/dev/null
    if [[ "$(occ config:app:get "${app}" demo_data_decided 2>/dev/null || true)" != installed ]]; then
      if [[ "${DRY_RUN}" -eq 1 ]]; then echo "DRY-RUN: POST ${base}/${app}/api/setup/action/load-demo-data"
      else
        curl -fsS -o /dev/null --max-time 600 -u admin:admin -H 'OCS-APIRequest: true' -X POST -d dataset=demo \
          "${base}/${app}/api/setup/action/load-demo-data" || info "${app}: example data not loaded (the wizard can still do it)"
      fi
    fi
    occ config:app:set "${app}" demo_data_decided --value=installed >/dev/null
  done
  # OpenCatalogi's optional federation steps (its wizard comes back as long
  # as they are open): connect to the public directory, as its button does.
  if [[ "${DRY_RUN}" -eq 0 ]]; then
    curl -fsS -o /dev/null --max-time 300 -u admin:admin -H 'OCS-APIRequest: true' -X POST \
      "${base}/opencatalogi/api/setup/action/connect-federation" || info "opencatalogi: federation not connected (offline?)"
  fi
  occ config:app:set pipelinq demo_dataset --value=demo >/dev/null
  occ config:app:set pipelinq demo_data_decided --value=seeded >/dev/null
  occ config:app:set pipelinq setup_dismissed_version --value=1 >/dev/null
  # Per user (admin and launcher): no guided tour, no support dialog, no
  # first-run dashboard.
  for user in admin launcher; do
    occ user:setting "${user}" firstrunwizard show 0 >/dev/null 2>&1 || true
    occ user:setting "${user}" dashboard firstRun 0 >/dev/null 2>&1 || true
    for app in openregister integriq opencatalogi pipelinq; do
      occ user:setting "${user}" "${app}" pref_walkthroughseenversion 999.0.0 >/dev/null
    done
    for app in dossiq portaliq; do
      occ user:setting "${user}" "${app}" pref_walkthroughcompletedversion 999.0.0 >/dev/null
    done
    for app in openregister integriq dossiq opencatalogi pipelinq portaliq; do
      occ user:setting "${user}" "${app}" pref_support-dialog-seen 1 >/dev/null
    done
  done
  # The notifications the seeding made (one per created object).
  if [[ "${DRY_RUN}" -eq 0 ]]; then
    for user in admin:admin launcher:launcher; do
      curl -fsS -o /dev/null --max-time 60 -u "${user}" -H 'OCS-APIRequest: true' -X DELETE \
        "http://localhost:${PORT}/ocs/v2.php/apps/notifications/api/v2/notifications" || true
    done
  fi
  info "setup answered, example data loaded, tours and first-run screens off"
}

# The appliance's one demo login, launcher / launcher (#258), next to upstream's
# admin / admin: a Nextcloud user in the admin group. Done again on every
# start (it is there already: only the password is set again).
add_dawo_login() {
  # Nextcloud's password policy refuses the demo password (10 characters
  # minimum, the leaked-password list and the list of the million most common
  # passwords, which has "launcher"): relaxed for this local demo (D47).
  occ config:app:set password_policy minLength --value=4 >/dev/null
  occ config:app:set password_policy enforceHaveIBeenPwned --value=0 >/dev/null
  occ config:app:set password_policy enforceNonCommonPassword --value=0 >/dev/null
  if occ user:info launcher >/dev/null 2>&1; then
    compose exec -T -u www-data -e OC_PASS=launcher nextcloud php occ user:resetpassword --password-from-env launcher >/dev/null
  else
    compose exec -T -u www-data -e OC_PASS=launcher nextcloud php occ user:add --password-from-env       --display-name="Launcher Demo" --group=admin launcher >/dev/null
  fi
  occ group:adduser admin launcher >/dev/null 2>&1 || true
  info "login launcher / launcher (group admin)"
}

# The other environment starts: stop the containers (they keep their data).
do_stop() {
  log "stop the Nextcloud Common Ground containers"
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
  apps) phase_apps ;;
  up) phase_up ;;
  ready) phase_ready ;;
  enable) phase_enable ;;
  seed) phase_seed ;;
  *) die "unknown or missing --phase (park, docker, source, pull, apps, up, ready, enable, seed)" ;;
esac
