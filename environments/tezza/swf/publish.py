# SPDX-License-Identifier: EUPL-1.2
# Súdwest-Fryslân's case types (#286), inside Open Zaak (manage.py shell): publish the SWF case types that SWF had published, the newest
# version per identificatie only (older versions stay concept, so validity periods do not overlap),
# and the document types they use. Reads the export to know which were published.
import json, zipfile
from django.db import transaction
from openzaak.components.catalogi.models import Catalogus, ZaakType, InformatieObjectType

z = zipfile.ZipFile("/tmp/swf.zip")
published_in_export = {e["url"].rsplit("/", 1)[1] for e in json.loads(z.read("ZaakType.json")) if not e.get("concept")}
cat = Catalogus.objects.get(domein="SWF")
with transaction.atomic():
    newest = {}
    for zt in ZaakType.objects.filter(catalogus=cat):
        if str(zt.uuid) not in published_in_export:
            continue
        cur = newest.get(zt.identificatie)
        if cur is None or (zt.versiedatum, zt.datum_begin_geldigheid) > (cur.versiedatum, cur.datum_begin_geldigheid):
            newest[zt.identificatie] = zt
    iots = InformatieObjectType.objects.filter(zaaktypen__in=newest.values()).distinct()
    n_iot = iots.filter(concept=True).update(concept=False)
    n_zt = ZaakType.objects.filter(pk__in=[zt.pk for zt in newest.values()]).update(concept=False)
    print(f"published {n_zt} case types (newest version of {len(newest)} codes) and {n_iot} document types")
