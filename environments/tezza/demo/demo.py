"""Demo data for Tezza: a few made-up cases of Súdwest-Fryslân's case types.

  python3 demo.py

Tezza's own setup makes only the configuration (sites, groups, templates);
this adds cases to try it with: an Omgevingsvergunning, an Evenementenvergunning
and a Bezwaarschrift, each with a status, an applicant (a test person of the
BRP mock, also in Haal Centraal) and documents. Everything is made up.

A case is made the way Tezza's own "Registreer zaak" makes it: upstream's
action zaakCreate (the case in Open Zaak and its folder in the case type's
site). The status and the applicant go into Open Zaak through the ZGW API
and into the folder the way upstream's notification callbacks do it
(callback_zaken_status_create.js, callback_zaken_rol_create.js; their
zaak_create callback cannot make a folder in 4.15.2, so Open Zaak's
notifications are not relied on). A document is uploaded into the folder and
registered with upstream's action createEnkelvoudiginformatieobject.

Once: a case whose description is in Open Zaak already is left alone. Talks to
the ports on this machine: Alfresco :8080, Open Zaak :8000 (as Tezza's own
client, with the host name Tezza uses, so the URLs are the same).

SPDX-License-Identifier: EUPL-1.2
"""
import base64
import hashlib
import hmac
import json
import sys
import time
import urllib.error
import urllib.request
import uuid

ALFRESCO = "http://localhost:8080/alfresco"
ADMIN = "Basic " + base64.b64encode(b"admin:admin").decode()
OZ, OZ_HOST = "http://localhost:8000", "openzaak.local:8000"
# Tezza's own client for Open Zaak (alfresco-global.properties of tezza-packaging).
CLIENT, SECRET = "clientid_alfresco_openzaak", "secret_alfresco_openzaak"
RSIN = "823288444"  # Súdwest-Fryslân, the catalogue's RSIN

# The cases. Persons: test persons of the BRP mock (personen-mock 2.7.0).
CASES = [
    {"zaaktype": "B1210", "omschrijving": "Dakkapel aan de achterzijde, Marktstraat 12, Sneek",
     "toelichting": "Plaatsen van een dakkapel op het achterdakvlak.", "startdatum": "2026-09-14",
     "status": ["In behandeling genomen"],
     "persoon": {"inpBsn": "999993653", "voornamen": "Suzanne", "geslachtsnaam": "Moulin"},
     "documenten": [("Aanvraag", "Aanvraag omgevingsvergunning dakkapel", "inkomend"),
                    ("Tekening", "Bouwtekening dakkapel", "inkomend"),
                    ("Ontvangstbevestiging", "Ontvangstbevestiging aanvraag", "uitgaand")]},
    {"zaaktype": "B1210", "omschrijving": "Bouw van een schuur, Hegedyk 3, Wommels",
     "toelichting": "Vrijstaande schuur van 30 m2 achter de woning.", "startdatum": "2026-10-06",
     "status": ["Geregistreerd"],
     "persoon": {"inpBsn": "999995078", "voornamen": "Christina Maria", "geslachtsnaam": "Maassen"},
     "documenten": [("Aanvraag", "Aanvraag omgevingsvergunning schuur", "inkomend")]},
    {"zaaktype": "B0056", "omschrijving": "Dorpsfeest Workum, 12 tot en met 14 juni 2027",
     "toelichting": "Feesttent op het Merk, muziek tot 01:00 uur, circa 1.500 bezoekers.", "startdatum": "2026-10-01",
     "status": ["In behandeling genomen"],
     "persoon": {"inpBsn": "999990627", "voornamen": "Stephan", "geslachtsnaam": "Janssen"},
     "documenten": [("Aanvraag vergunning", "Aanvraag evenementenvergunning dorpsfeest", "inkomend"),
                    ("Aanvraag vergunning - Situatietekening", "Situatietekening feestterrein", "inkomend"),
                    ("Aanvraag vergunning - Veiligheidsplan", "Veiligheidsplan dorpsfeest", "inkomend")]},
    {"zaaktype": "B0056", "omschrijving": "Kortebaandraverij IJlst, 21 augustus 2027",
     "toelichting": "Draverij op de Galamagracht, afsluiting van de weg van 12:00 tot 18:00 uur.", "startdatum": "2026-10-20",
     "status": ["Geregistreerd"],
     "persoon": {"inpBsn": "999993653", "voornamen": "Suzanne", "geslachtsnaam": "Moulin"},
     "documenten": [("Aanvraag vergunning", "Aanvraag evenementenvergunning draverij", "inkomend")]},
    {"zaaktype": "B0756", "omschrijving": "Bezwaar tegen de omgevingsvergunning Marktstraat 12, Sneek",
     "toelichting": "De buren maken bezwaar: de dakkapel ontneemt hun tuin het zonlicht.", "startdatum": "2026-10-12",
     "status": ["In behandeling genomen"],
     "persoon": {"inpBsn": "999995078", "voornamen": "Christina Maria", "geslachtsnaam": "Maassen"},
     "documenten": [("Bezwaar", "Bezwaarschrift buren Marktstraat 12", "inkomend"),
                    ("Bestreden besluit", "Bestreden besluit omgevingsvergunning", "intern"),
                    ("Uitnodiging hoorzitting", "Uitnodiging hoorzitting", "uitgaand")]},
]
TEXT = ("Dit is een fictief document van de demo van Launch.",
        "Alle namen, adressen en gegevens zijn verzonnen.")


# --- Open Zaak (ZGW API) ------------------------------------------------------

def b64(b):
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


def token():
    head = b64(json.dumps({"alg": "HS256", "typ": "JWT"}).encode())
    body = b64(json.dumps({"iss": CLIENT, "client_id": CLIENT, "iat": int(time.time()),
                           "user_id": "launcher", "user_representation": "Launcher Demo"}).encode())
    sig = b64(hmac.new(SECRET.encode(), f"{head}.{body}".encode(), hashlib.sha256).digest())
    return f"{head}.{body}.{sig}"


def zgw(method, url, body=None):
    if url.startswith("http://" + OZ_HOST):  # only the address: query values keep Tezza's host
        url = OZ + url[len("http://" + OZ_HOST):]
    req = urllib.request.Request(url, method=method, data=None if body is None else json.dumps(body).encode(),
                                 headers={"Authorization": "Bearer " + token(), "Host": OZ_HOST,
                                          "Content-Type": "application/json",
                                          "Accept-Crs": "EPSG:4326", "Content-Crs": "EPSG:4326"})
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=120) as r:
                return json.loads(r.read() or b"null")
        except (ConnectionError, urllib.error.URLError) as e:
            if isinstance(e, urllib.error.HTTPError) or attempt == 2:
                raise
            time.sleep(5)  # Open Zaak drops a connection now and then


def zgw_all(url):
    out = []
    while url:
        page = zgw("GET", url)
        out += page["results"]
        url = page.get("next")
    return out


# --- Alfresco -------------------------------------------------------------------

def js(script):
    """Run script in Alfresco's JavaScript console (OOTBee) as admin; return what it prints."""
    body = json.dumps({"script": '<import resource="classpath:alfresco/templates/webscripts/nl/contezza/api/tezza-api.lib.js">\n'
                                 + script, "template": "", "runas": "admin", "transaction": "readwrite",
                       "spaceNodeRef": "", "documentNodeRef": "", "urlargs": ""}).encode()
    req = urllib.request.Request(ALFRESCO + "/service/ootbee/jsconsole/execute", data=body,
                                 headers={"Content-Type": "application/json", "Authorization": ADMIN})
    with urllib.request.urlopen(req, timeout=600) as r:
        return "\n".join(json.load(r).get("printOutput", []))


def upload(folder_id, name, data):
    """Upload a PDF into the folder; return the new node's id."""
    boundary = uuid.uuid4().hex
    body = (f"--{boundary}\r\nContent-Disposition: form-data; name=\"filedata\"; filename=\"{name}\"\r\n"
            "Content-Type: application/pdf\r\n\r\n").encode() + data + f"\r\n--{boundary}--\r\n".encode()
    req = urllib.request.Request(f"{ALFRESCO}/api/-default-/public/alfresco/versions/1/nodes/{folder_id}/children",
                                 data=body, headers={"Authorization": ADMIN,
                                                     "Content-Type": f"multipart/form-data; boundary={boundary}"})
    with urllib.request.urlopen(req, timeout=120) as r:
        return json.load(r)["entry"]["id"]


def pdf(title, lines):
    """A one-page PDF with a title and some lines (Helvetica)."""
    def esc(s):
        return s.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
    text = [f"BT /F1 18 Tf 72 760 Td ({esc(title)}) Tj ET"]
    for i, line in enumerate(lines):
        text.append(f"BT /F1 11 Tf 72 {720 - 18 * i} Td ({esc(line)}) Tj ET")
    stream = "\n".join(text).encode("latin-1", "replace")
    objs = [b"<< /Type /Catalog /Pages 2 0 R >>",
            b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] /Contents 4 0 R "
            b"/Resources << /Font << /F1 5 0 R >> >> >>",
            b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"\nendstream",
            b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>"]
    out, offsets = bytearray(b"%PDF-1.4\n"), []
    for i, o in enumerate(objs, 1):
        offsets.append(len(out))
        out += b"%d 0 obj\n" % i + o + b"\nendobj\n"
    xref = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objs) + 1)
    out += b"".join(b"%010d 00000 n \n" % o for o in offsets)
    out += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (len(objs) + 1, xref)
    return bytes(out)


# --- the cases --------------------------------------------------------------------

def by_name(items, names, key="omschrijving"):
    for n in names:
        for it in items:
            if it[key].strip().lower() == n.lower():
                return it
    return None


def make(case, zaaktype, existing, iots):
    if case["omschrijving"] in existing:
        print(f"    er al: {case['omschrijving']}")
        return
    zt = zaaktype["url"]
    # The case and its folder, as Tezza's "Registreer zaak" makes them.
    out = js(f"""
var a = actions.create("zaakCreate");
a.parameters["zaaktype"] = {json.dumps(zt)};
a.parameters["bronorganisatie"] = "{RSIN}";
a.parameters["verantwoordelijkeOrganisatie"] = "{RSIN}";
a.parameters["omschrijving"] = {json.dumps(case["omschrijving"])};
a.parameters["toelichting"] = {json.dumps(case["toelichting"])};
a.parameters["startdatum"] = {json.dumps(case["startdatum"])};
a.execute(companyhome);
var n = a.parameters["result"];
n.properties["sys:locale"] = "nl"; n.save();
print(JSON.stringify({{id: n.id, url: "" + n.properties["tza:zaakUrl"], identificatie: "" + n.properties["tza:identificatie"]}}));
""")
    zaak = json.loads(out.strip().splitlines()[-1])
    # The status (the SWF catalogue numbers its statuses alphabetically, so
    # zaakCreate's first status is often not the one a new case has).
    st = by_name(zgw_all(f"{OZ}/catalogi/api/v1/statustypen?zaaktype={zt}"), case["status"])
    status = zgw("POST", f"{OZ}/zaken/api/v1/statussen", {
        "zaak": zaak["url"], "statustype": st["url"], "datumStatusGezet": case["startdatum"] + "T10:00:00Z",
        "statustoelichting": "Demo"})["url"] if st else None
    # The applicant.
    rt = zgw_all(f"{OZ}/catalogi/api/v1/roltypen?zaaktype={zt}&omschrijvingGeneriek=initiator")
    # Tezza shows the applicant as voorletters + geslachtsnaam.
    persoon = {**case["persoon"], "voorletters": "".join(v[0] + "." for v in case["persoon"]["voornamen"].split())}
    rol = zgw("POST", f"{OZ}/zaken/api/v1/rollen", {
        "zaak": zaak["url"], "betrokkeneType": "natuurlijk_persoon", "roltype": rt[0]["url"],
        "roltoelichting": "Aanvrager", "betrokkeneIdentificatie": persoon})["url"] if rt else None
    # Into the folder, the way upstream's callbacks do it.
    js(f"""
var folder = search.findNode("workspace://SpacesStore/{zaak['id']}");
var statusUrl = {json.dumps(status)}, rolUrl = {json.dumps(rol)};
if (statusUrl) {{
  var st = ztc.statustypeRead(zrc.statusRead(statusUrl).statustype);
  folder.properties["tza:zaakstatus"] = st.omschrijving; folder.save();
}}
if (rolUrl) {{
  var rol = zrc.rolRead(rolUrl);
  var action = actions.create("rolCreate");
  action.parameters["betrokkeneType"] = rol.betrokkeneType.toLowerCase();
  action.parameters["roltype"] = rol.roltype;
  action.parameters["roltoelichting"] = rol.roltoelichting;
  action.parameters["indicatieMachtiging"] = rol.indicatieMachtiging;
  action.parameters["betrokkene"] = rol.betrokkene;
  action.parameters["betrokkeneIdentificatie"] = rol.betrokkeneIdentificatie;
  action.parameters["rol"] = rol.url;
  action.execute(folder.nodeRef);
}}
""")
    # The documents.
    iots = [iots[u] for u in zaaktype.get("informatieobjecttypen", []) if u in iots]
    n = 0
    for iot_name, titel, richting in case["documenten"]:
        iot = by_name(iots, [iot_name]) or by_name(iots, ["Overig stuk inkomend", "Overig"])
        if not iot:
            continue
        doc = upload(zaak["id"], titel + ".pdf", pdf(titel, [case["omschrijving"], "", *TEXT]))
        js(f"""
var f = search.findNode("workspace://SpacesStore/{doc}");
var a = actions.create("createEnkelvoudiginformatieobject");
a.parameters["informatieobjecttype"] = {json.dumps(iot["url"])};
a.parameters["titel"] = {json.dumps(titel)};
a.parameters["status"] = "definitief";
a.parameters["taal"] = "dut";
a.parameters["richting"] = "{richting}";
a.parameters["documentvertrouwelijkheid"] = "openbaar";
a.parameters["destination"] = search.findNode("workspace://SpacesStore/{zaak['id']}").nodeRef;
a.execute(f);
""")
        n += 1
    print(f"    {zaak['identificatie']}: {case['omschrijving']} ({st['omschrijving'] if st else 'status?'}, "
          f"{n} documenten)")


def main():
    catalogus = zgw("GET", f"{OZ}/catalogi/api/v1/catalogussen?domein=SWF")["results"]
    if not catalogus:
        print("    geen zaaktypes van Súdwest-Fryslân: geen demozaken")
        return 0
    zaaktypen = {}
    for z in zgw_all(f"{OZ}/catalogi/api/v1/zaaktypen?catalogus={catalogus[0]['url']}&status=definitief"):
        # The newest published version of each case type.
        if z["identificatie"] not in zaaktypen or z["versiedatum"] > zaaktypen[z["identificatie"]]["versiedatum"]:
            zaaktypen[z["identificatie"]] = z
    iots = {i["url"]: i for i in zgw_all(f"{OZ}/catalogi/api/v1/informatieobjecttypen?catalogus={catalogus[0]['url']}")}
    failed = 0
    for case in CASES:
        zt = zaaktypen.get(case["zaaktype"])
        if not zt:
            print(f"    zaaktype {case['zaaktype']} niet gevonden: {case['omschrijving']} overgeslagen")
            continue
        existing = {z["omschrijving"] for z in zgw_all(f"{OZ}/zaken/api/v1/zaken?zaaktype={zt['url']}")}
        try:
            make(case, zt, existing, iots)
        except (OSError, KeyError, ValueError, TypeError) as e:
            failed += 1
            detail = e.read().decode()[:300] if isinstance(e, urllib.error.HTTPError) else str(e)
            print(f"    niet gelukt: {case['omschrijving']}: {detail}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
