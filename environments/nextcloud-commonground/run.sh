#!/usr/bin/env bash
# Nextcloud Common Ground: Conduction's demo (dossiq-compose.yaml) with their
# apps, started step by step with deploy.sh's phases (the same as on the DAWO
# USB-SSD). See lib/run-driver.sh.
#
# SPDX-License-Identifier: EUPL-1.2
STEP_NAMES=(source pull apps up ready enable seed)
STEP_LABELS=("Nextcloud Common Ground ophalen (de demo van Conduction)" "Onderdelen downloaden"
             "De apps van Conduction downloaden en controleren" "Nextcloud starten"
             "Wachten tot Nextcloud klaar is (de eerste keer wordt het geïnstalleerd)" "De apps aanzetten"
             "Demozaken en voorbeeldgegevens laden, instellingen klaarzetten")
STEP_EXPECT=(5 300 200 25 340 200 180)
prepare() {
  export NC_STATE_DIR="${STATE_DIR}" NC_OVERRIDE="${here}/override.yaml" NC_APPS_FILE="${here}/apps.txt"
  export NC_COMPOSE_URL NC_COMPOSE_SHA
  NC_COMPOSE_URL="$(cfg "e['source']['raw_url']")"; NC_COMPOSE_SHA="$(cfg "e['source']['sha256']")"
}
source "$(dirname "${BASH_SOURCE[0]}")/../../lib/run-driver.sh" "$@"
