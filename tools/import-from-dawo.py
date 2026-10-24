#!/usr/bin/env python3
"""Take environments over from dawo-appliance into this repository.

  python tools/import-from-dawo.py <dawo-appliance checkout> <launch-catalog.json> [id ...]

The catalogue is the one dawo-appliance generates (nix/launch.nix,
launch-catalog.json: the card data with the licence classes computed). Per
environment this writes environments/<id>/environment.json (what the card
shows) and copies the driver (apps/<id>/deploy.sh) and the compose override.
Upstream sources stay pinned by commit and checksum, as in dawo-appliance's
manifest. The run.sh next to them is written by hand: it says which steps run.

SPDX-License-Identifier: EUPL-1.2
"""
import json
import os
import shutil
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CARD_KEYS = ("title", "version", "summary", "url", "login", "links", "license", "standards",
             "licences", "components", "health", "needs", "source")


def main(dawo, catalog_path, ids):
    catalog = json.load(open(catalog_path, encoding="utf-8"))
    manifest = json.load(open(os.path.join(dawo, "manifest", "appliance-manifest.json"), encoding="utf-8"))
    for env in catalog["environments"]:
        eid = env["id"]
        if ids and eid not in ids:
            continue
        out = os.path.join(ROOT, "environments", eid)
        os.makedirs(out, exist_ok=True)
        card = {k: env[k] for k in CARD_KEYS if k in env}
        # Only what this computer can check: memory, and later host names.
        card["needs"] = {"memory_gib": (env.get("needs") or {}).get("memory_gib", 0)}
        src = env.get("source") or {}
        up = manifest.get("upstream", {}).get(src.get("manifest", ""), {})
        if up:
            card["source"] = {k: up[k] for k in ("url", "rev", "raw_url", "sha256", "path") if k in up}
        deploy = env.get("deploy") or {}
        if deploy.get("driver"):
            shutil.copyfile(os.path.join(dawo, deploy["driver"]), os.path.join(out, "deploy.sh"))
        if deploy.get("compose_override"):
            shutil.copyfile(os.path.join(dawo, env["path"], deploy["compose_override"]), os.path.join(out, "override.yaml"))
        if eid == "nextcloud-commonground":
            apps = manifest["nextcloud_commonground"]["apps"]
            with open(os.path.join(out, "apps.txt"), "w", encoding="utf-8", newline="\n") as f:
                for aid, a in apps.items():
                    f.write(f"{aid} {a['version']} {a['url']} {a['sha256']}\n")
        with open(os.path.join(out, "environment.json"), "w", encoding="utf-8", newline="\n") as f:
            json.dump(card, f, ensure_ascii=False, indent=2)
            f.write("\n")
        print(f"{eid}: {', '.join(sorted(os.listdir(out)))}")


if __name__ == "__main__":
    if len(sys.argv) < 3:
        sys.exit(__doc__)
    main(sys.argv[1], sys.argv[2], sys.argv[3:])
