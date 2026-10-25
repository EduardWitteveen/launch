#!/usr/bin/env bash
# Valtimo: the official GZAC demo (gzac-docker-compose, profiles demo, zgw and
# openformulieren), started step by step with deploy.sh's phases (the same as
# on the DAWO USB-SSD). See lib/run-driver.sh.
#
# SPDX-License-Identifier: EUPL-1.2
STEP_NAMES=(source pull up ready)
STEP_LABELS=("Valtimo ophalen (de officiële demo)" "Onderdelen downloaden" "Valtimo starten"
             "Wachten tot Valtimo, Open Zaak en het burgerformulier klaar zijn")
STEP_EXPECT=(10 900 240 420)
prepare() {
  export VT_STATE_DIR="${STATE_DIR}" VT_OVERRIDE="${here}/override.yaml"
  export VT_REPO VT_REV
  VT_REPO="$(cfg "e['source']['url']")"; VT_REV="$(cfg "e['source']['rev']")"
  if option_off openformulieren; then STEP_LABELS[3]="Wachten tot Valtimo en Open Zaak klaar zijn"; fi
}
source "$(dirname "${BASH_SOURCE[0]}")/../../lib/run-driver.sh" "$@"
