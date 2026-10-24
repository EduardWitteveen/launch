"""Make Súdwest-Fryslân's Open Zaak catalogue export importable in Tezza's Open Zaak (1.28) (#286).

  python3 convert.py <export.zip> <out.zip> <selectielijst-cache.json>

The export (OpenZaakBrug, examples/soap/openzaak-export-catalogus-zaaktypes.zip,
EUPL-1.2) comes from an older Open Zaak. Only what the newer one requires is
changed; the content (case types, statuses, roles, results, document types)
stays as SWF has it:
  - URLs: SWF's API base (http://<host>/zgw/) becomes the path Open Zaak
    resolves (http://openzaak.local:8000/); only the path is matched;
  - InformatieObjectType.informatieobjectcategorie is required since the ZGW
    Catalogi API 1.3; the export has none: "Overig";
  - ZaakType.verantwoordelijke is required since 1.3: the catalogue's RSIN
    (the organisation responsible for it);
  - ZaakType without a doorlooptijd (one: B1291 Bezwaarschriften gemeentelijke
    belastingen): P6W, the legal term for deciding on an objection (Awb 7:10);
  - selectielijst_procestype: Open Zaak checks that each ResultaatType's
    selectielijstklasse (the national selection list) belongs to its case
    type's process type. SWF's process type is kept where it fits one of its
    results; where it fits none (4 case types) it becomes the process type of
    most of its results; a result whose class belongs to another process type
    gets the class with the same name in the case type's process type (with
    afleidingswijze "afgehandeld" one whose procestermijn is "nihil");
  - brondatum_archiefprocedure.procestermijn belongs only to afleidingswijze
    "termijn"; 32 result types have "ander_datumkenmerk" with a placeholder
    P1D, which Open Zaak refuses: emptied.

The selection list's classes are read from the public Selectielijst API
(https://selectielijst.openzaak.nl/api/v1/resultaten) once and kept in the
cache file.

SPDX-License-Identifier: EUPL-1.2
"""
import collections
import io
import json
import os
import re
import sys
import urllib.request
import zipfile

SELECTIELIJST = "https://selectielijst.openzaak.nl/api/v1/resultaten"
FIELDS = ("procesType", "naam", "nummer", "volledigNummer", "procestermijn")


def get(url):
    with urllib.request.urlopen(url, timeout=60) as r:
        return json.load(r)


def selectielijst(cache):
    if os.path.exists(cache):
        with open(cache, encoding="utf-8") as f:
            return json.load(f)
    klassen, url = {}, SELECTIELIJST
    while url:
        page = get(url)
        for r in page["results"]:
            klassen[r["url"]] = {k: r.get(k) for k in FIELDS}
        url = page.get("next")
    with open(cache, "w", encoding="utf-8") as f:
        json.dump(klassen, f)
    return klassen


def main(src, dst, cache):
    zin = zipfile.ZipFile(src)
    klassen = selectielijst(cache)

    def klasse(url):
        # A class that is not (or no longer) in the list: ask for it by its URL.
        if url and url not in klassen:
            r = get(url)
            klassen[url] = {k: r.get(k) for k in FIELDS}
        return klassen.get(url or "")

    data = {}
    for name in zin.namelist():
        if name.endswith(".json"):
            text = re.sub(r"https?://[^/\"]+/zgw/", "http://openzaak.local:8000/", zin.read(name).decode("utf-8"))
            data[name.rsplit("/", 1)[-1][:-5]] = json.loads(text)

    rsin = data["Catalogus"][0]["rsin"]
    for e in data["InformatieObjectType"]:
        if not e.get("informatieobjectcategorie"):
            e["informatieobjectcategorie"] = "Overig"

    per = collections.defaultdict(collections.Counter)
    for r in data["ResultaatType"]:
        k = klasse(r.get("selectielijstklasse"))
        if k:
            per[r["zaaktype"]][k["procesType"]] += 1

    procestype = {}
    for e in data["ZaakType"]:
        if not e.get("verantwoordelijke"):
            e["verantwoordelijke"] = rsin
        if not e.get("doorlooptijd"):
            e["doorlooptijd"] = "P6W"
        c, own = per.get(e["url"]), e.get("selectielijst_procestype")
        if c and own not in c:
            own = e["selectielijst_procestype"] = c.most_common(1)[0][0]
        procestype[e["url"]] = own

    for e in data["ResultaatType"]:
        k = klasse(e.get("selectielijstklasse"))
        want = procestype.get(e["zaaktype"])
        if k and want and k["procesType"] != want:
            afgehandeld = (e.get("brondatum_archiefprocedure") or {}).get("afleidingswijze") == "afgehandeld"
            same = sorted((u for u, v in klassen.items() if v["procesType"] == want and v["naam"] == k["naam"]),
                          key=lambda u: (afgehandeld and klassen[u].get("procestermijn") != "nihil",
                                         klassen[u]["nummer"], klassen[u].get("volledigNummer") or ""))
            alt = same or sorted((u for u, v in klassen.items() if v["procesType"] == want), key=lambda u: klassen[u]["nummer"])
            e["selectielijstklasse"] = alt[0]
        b = e.get("brondatum_archiefprocedure") or {}
        if b.get("procestermijn") and b.get("afleidingswijze") != "termijn":
            b["procestermijn"] = None

    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zout:
        for resource, entries in data.items():
            zout.writestr(f"{resource}.json", json.dumps(entries, ensure_ascii=False))
    with open(dst, "wb") as f:
        f.write(out.getvalue())
    print(f"{len(data['ZaakType'])} case types, {len(data['InformatieObjectType'])} document types")


if __name__ == "__main__":
    main(*sys.argv[1:4])
