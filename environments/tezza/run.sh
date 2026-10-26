#!/usr/bin/env bash
# Tezza: Contezza's tezza-packaging (the complete product, with Súdwest-
# Fryslân's case types), started step by step with deploy.sh's phases (the
# same as on the DAWO USB-SSD). See lib/run-driver.sh.
#
# SPDX-License-Identifier: EUPL-1.2
STEP_NAMES=(source pull up ready catalog setup demo cases)
STEP_LABELS=("Tezza ophalen (de demo van Contezza)" "Onderdelen downloaden (ongeveer 8 GB)" "Tezza starten"
             "Wachten tot Tezza klaar is (de eerste keer wordt alles ingericht)" "De zaaktypes van Súdwest-Fryslân laden"
             "Tezza inrichten (sites, groepen, sjablonen) en de login launcher"
             "Demozaken aanmaken (fictief: omgevingsvergunning, evenement, bezwaar)"
             "Zaakpakketten uit de eigen bron in GZAC laden")
STEP_EXPECT=(30 1800 300 600 180 300 120 180)
prepare() {
  export TZ_STATE_DIR="${STATE_DIR}" TZ_OVERRIDE="${here}/override.yaml"
  export TZ_REPO TZ_REV TZ_SWF_URL TZ_SWF_SHA
  TZ_REPO="$(cfg "e['source']['repo']")"; TZ_REV="$(cfg "e['source']['rev']")"
  TZ_SWF_URL="$(cfg "e['swf_catalogue']['url']")"; TZ_SWF_SHA="$(cfg "e['swf_catalogue']['sha256']")"
  # The catalog phase reads swf-convert.py and swf-publish.py from the state.
  cp -f "${here}/swf/convert.py" "${STATE_DIR}/swf-convert.py"
  cp -f "${here}/swf/publish.py" "${STATE_DIR}/swf-publish.py"
  # ... and the demo phase demo.py.
  cp -f "${here}/demo/demo.py" "${STATE_DIR}/demo.py"
  # Switched off at Aanpassen: the step stays (the same count), its work is skipped.
  if option_off demo; then STEP_EXPECT[6]=1; fi
  # ... and the cases phase gzac-cases.py; without a source its work is skipped.
  cp -f "${here}/../../lib/gzac-cases.py" "${STATE_DIR}/gzac-cases.py"
  if option_off zaakpakketten || [[ -z "${LAUNCH_SOURCE_ZAAKPAKKETTEN:-}" ]]; then STEP_EXPECT[7]=1; fi
}
source "$(dirname "${BASH_SOURCE[0]}")/../../lib/run-driver.sh" "$@"
