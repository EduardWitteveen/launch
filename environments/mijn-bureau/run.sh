#!/usr/bin/env bash
# Mijn Bureau runs on Kubernetes (K3s) in the DAWO USB-SSD's virtual machine;
# Launch cannot start it with Docker on this computer yet. The card is there
# so it is visible, and says so (environment.json, needs.platform).
#
# SPDX-License-Identifier: EUPL-1.2
case "${1:-}" in
  stop) exit 0 ;;
  start)
    echo "Mijn Bureau werkt nog alleen vanaf de DAWO-USB-SSD (Kubernetes); in Launch op deze computer komt het later." >&2
    [[ -n "${2:-}" ]] && printf '{"step":1,"steps":1,"label":"Mijn Bureau","started":%d,"since":%d,"expected":1,"rest":0,"detail":"","activity":%d,"error":"Mijn Bureau werkt nog alleen vanaf de DAWO-USB-SSD."}\n' \
      "$(date +%s)" "$(date +%s)" "$(date +%s)" > "$2"
    exit 1 ;;
  *) echo "usage: run.sh start <progress-file> | stop" >&2; exit 2 ;;
esac
