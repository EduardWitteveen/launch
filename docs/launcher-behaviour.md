# Het gedrag van de Launcher

Dit document beschrijft hoe de Launcher (`launcher/launcher.py` en `launcher/launcher.html`) en het commando `launch` zich moeten gedragen: welke fasen er zijn, wat de gebruiker in elke fase ziet en kan doen, en waar elke knop heen gaat. De code volgt dit document. Klopt de code niet met dit document, dan is de code fout (of moet dit document eerst worden aangepast).

Dit is het ontwerp van 25 oktober 2026. De huidige code doet het nog niet zo; wat er nu misgaat staat in de bijlage. De browsertest `tests/e2e/launcher-behaviour.js` moet op dit ontwerp worden aangepast.

## In het kort

**Wat de Launcher is.** Een pagina met één kaart per omgeving. Je kiest er een, start hem, gebruikt hem en stopt hem weer. Er draait er maar één tegelijk.

1. **Je opent de pagina (OVERZICHT).** Je ziet de kaarten, elk met een knop **Starten**. Kan een omgeving op deze computer niet draaien, dan kun je niet op Starten drukken en staat erbij waarom. Onder **Aanpassen** kun je vinkjes uitzetten, bijvoorbeeld de demodata of een app die je niet wilt.
2. **Je drukt op Starten.** Launch controleert eerst of alles in orde is.
   - Is er een **probleem** (bijvoorbeeld: Docker draait niet), dan zie je dat, één probleem tegelijk. Je lost het op en drukt op **Opnieuw proberen**. Kan Launch het zelf, dan staat er ook **Los het op voor mij**.
   - Heeft het product **voorwaarden**, dan lees je die en druk je op **Akkoord**. Dat hoeft maar één keer.
3. **De omgeving wordt gestart (STARTEN).** Je ziet bij welke stap het is en hoe lang het nog duurt.
4. **Het is klaar (KLAAR).** De omgeving opent vanzelf in een nieuw venster. Op de kaart staat een lijstje om alles nog eens te openen, met de inloggegevens.
5. **Je drukt op Stoppen (STOPPEN).** Alles wordt opgeruimd en je bent terug bij 1.

Gaat er tijdens het starten iets mis (**MISLUKT**), dan zie je wat, met **Opnieuw proberen** of **Stoppen**.

Drie afspraken overal:
- Je ziet alleen een melding als er iets mis is.
- Na een probleem gaat Launch niet vanzelf door; jij drukt op Opnieuw proberen.
- **Stoppen** brengt je altijd terug naar het begin.

Daarnaast:
- **Rondgang:** alle omgevingen om de beurt starten, als test, zonder dat er iemand bij is.
- **Commandline:** hetzelfde in een terminal, in het Engels (`launch start tezza`). Je gebruikt óf de Launcher óf de commandline, niet allebei tegelijk.

## 1. Regels

1. **Eén omgeving tegelijk.** Draait er een, dan zijn de andere kaarten grijs, zonder knoppen.
2. **Wat je ziet, is waar.** Een meting die mislukt, is "weet niet", en "weet niet" verandert niets aan wat je ziet. Een toestand verandert door een handeling, of pas als twee metingen achter elkaar hetzelfde zeggen.
3. **Waarschuwingen, fouten en voorwaarden verschijnen pas als er iets mis is.** Wat in orde is, staat er niet.
4. **Na een probleem gaat Launch nooit vanzelf verder.** Jij drukt op **Opnieuw proberen**, en dat herhaalt wat net misging.
5. **Wat Launch kan oplossen, kun je ook zelf oplossen.**
6. **Stoppen** is overal dezelfde handeling: opruimen, en terug naar OVERZICHT. De knop staat op elke pagina, behalve op OPLOSSEN en op STOPPEN zelf. Er is geen "Annuleren".
7. **Na een klik wordt de pagina meteen grijs**, met in het midden wat er gebeurt ("Tezza wordt gestopt…"), tot de volgende fase er staat. Lukt de overgang niet, dan komt de pagina terug zoals hij was, met wat er misging. Een klik die alleen een venster opent, maakt niets grijs.
8. **De server is de baas.** De pagina tekent alleen wat de Launcher zegt; twee tabbladen tonen hetzelfde.
9. **In gewone woorden.** Een programmafout of exit-code staat er alleen achter "Details tonen".

## 2. Soorten afwijkingen

| Soort | Wanneer je het ziet | Voorbeeld |
|---|---|---|
| **Voorwaarde** (kan hier niet) | altijd, op OVERZICHT: Starten uitgeschakeld, met de reden | alleen vanaf de DAWO-USB-SSD; de computer heeft te weinig geheugen |
| **Probleem, door jou op te lossen** | na Starten, op PROBLEEM | Docker draait niet; Docker kreeg te weinig geheugen; een poort is bezet |
| **Probleem, door Launch op te lossen** | na Starten, op PROBLEEM | de naam `identity` ontbreekt in het hosts-bestand |
| **Fout tijdens de rit** | tijdens STARTEN, op MISLUKT | een stap loopt vast |

Een bekende fout wordt vóór de start gecontroleerd. Wat tijdens de rit misgaat, is altijd MISLUKT; herkent Launch het, dan staat er in woorden wat het is.

## 3. De fasen

De start: **OVERZICHT → controle → PROBLEEM (n van m) ⇄ OPLOSSEN → MELDING → STARTEN → KLAAR of MISLUKT → STOPPEN → OVERZICHT**

Alle fasen: OVERZICHT, PROBLEEM, OPLOSSEN, MELDING, STARTEN, KLAAR, MISLUKT, STOPPEN, RONDGANG, AFTELLEN, TWEE ACTIEF, GEEN LAUNCHER, LADEN.

### 3.1 OVERZICHT: kiezen en starten

**Doel:** je kiest welke omgeving je wilt proberen, en start hem.

- **1A** Kop: **Launcher**, met één zin uitleg.
- **1B** Eén kaart per omgeving in `environments/`.
- **1C** Op een kaart:
  - **1C1** de naam
  - **1C2** in één zin wat je ermee kunt
  - **1C3** de licentieregel, alleen als niet alles open source is
  - **1C4** **Starten**
  - **1C5** alleen als een voorwaarde niet klopt: "Kan niet draaien op deze computer, omdat …"; 1C4 is dan uitgeschakeld
  - **1C6** **Aanpassen** (dichtgeklapt): vinkjes voor de onderdelen en voor elke set demodata. Standaard staat alles aan (behalve wat `environment.json` standaard uit zet). Zet je een onderdeel uit, dan gaat de demodata die het nodig heeft ook uit. De Launcher onthoudt je keuze.
  - **1C7** alleen als je iets hebt aangepast: "Aangepast: zonder klantcontact"
- **1D** Het vak Rondgang, met **Rondgang nu starten**.
- **1E** Alleen na een onverwacht einde: "Tezza draait niet meer (09:41): Docker is gestopt", tot je op een knop drukt.

**Verder:** Starten → de controle → PROBLEEM, MELDING of STARTEN.

### 3.2 PROBLEEM (n van m): eerst dit oplossen

**Doel:** de start kan nog niet; je ziet waarom en lost het op.

- **P1** "Probleem 1 van 2". Draait de onderlaag (Docker) niet, dan staat alleen dat probleem er, zonder teller: de rest kan Launch dan niet controleren.
- **P2** Wat er mis is, en wat je eraan kunt doen.
- **P3** Kan Launch het oplossen: wat Launch dan verandert, en de knop **Los het op voor mij** → OPLOSSEN.
- **P4** **Opnieuw proberen**: controleert alles opnieuw.
  - Dit probleem is weg → het volgende probleem; komt er een bij, dan telt de teller mee ("2 van 3").
  - Alles is weg → MELDING of STARTEN.
  - Het is er nog → je blijft hier, met "Nog niet opgelost".
- **P5** **Stoppen** → OVERZICHT (er draait nog niets, dus meteen).

Volgorde: de onderlaag, het geheugen, de poorten, dan de controles uit `environment.json` in hun volgorde.

### 3.3 OPLOSSEN: Launch verandert iets

- **O1** "Bezig met oplossen". Vraagt Windows om toestemming, dan staat erbij: "klik op Ja; het venster kan achter de browser staan".
- **O2** Geen knoppen.

**Daarna:** terug naar PROBLEEM, met "Opgelost" en **Opnieuw proberen**, of met wat er misging. Geeft niemand binnen een paar minuten toestemming: "Er is geen toestemming gegeven".

### 3.4 MELDING: de voorwaarden van een product

- **M1** De titel en de tekst van de melding.
- **M2** **Akkoord en starten** → STARTEN.
- **M3** **Stoppen** → OVERZICHT.

Er is geen aftellen: akkoord geef je zelf. Het akkoord geldt per omgeving en per versie; daarna komt de melding niet meer.

### 3.5 STARTEN: het wordt klaargezet

**Doel:** je ziet dat je omgeving wordt gestart, hoe ver het is en hoe lang het nog duurt.

- **2A** De kaart: **Wordt gestart…**
- **2B** Stap *n* van *m*, met een balk voor de hele start.
- **2C** De stap nu, in woorden, met een balk voor die stap.
- **2D** Hoe lang de stap al duurt, en de schatting ("nog ongeveer 6 min, klaar rond 09:14"). Duurt de stap langer dan de vorige keer, dan geen schatting.
- **2E** "Nu: …": wat er op dit moment gebeurt.
- **2F** **Stoppen** → STOPPEN: de start wordt afgebroken en opgeruimd.
- De andere kaarten en het vak Rondgang zijn grijs.

Sluit je de browser, dan gaat de start door. Open je de pagina weer, dan sta je in de fase waarin de omgeving nu is.

**Verder:** gelukt → KLAAR; vastgelopen of afgebroken → MISLUKT.

### 3.6 KLAAR: gebruiken

**Doel:** je gebruikt de omgeving.

- **3A** De kaart: **Klaar**.
- **3B** De onderdelen: het hoofdonderdeel bovenaan, daaronder de rest (beheer, archief, …), elk met hoe je inlogt. Elke klik opent een nieuw venster; zo krijg je een gesloten venster terug.
- **3C** **Stoppen** → STOPPEN.
- **3D** Alleen als het zo is: "Tezza antwoordt al 2 min niet" (de containers draaien, maar twee metingen achter elkaar geen antwoord), of "Docker antwoordt even niet" (langer dan een minuut "weet niet").
- De andere kaarten en het vak Rondgang zijn grijs.

**Bij aankomst** opent het tabblad dat op Starten drukte het hoofdonderdeel in een nieuw venster. Andere tabbladen, een start uit een terminal en de rondgang openen niets. Blokkeert de browser het venster, dan vraagt hij eenmalig of pop-ups van de Launcher mogen; 3B staat er altijd.

**Vanzelf:** Docker zegt duidelijk dat hij niet draait, of de containers zijn twee metingen achter elkaar weg → OVERZICHT met 1E.

### 3.7 MISLUKT: de start liep vast

- **F1** **Starten mislukt**: wat er misging, in woorden, en bij welke stap ("Bij stap 4 van 7 (Tezza starten): …").
- **F2** "Deze onderdelen antwoorden", met de lijst uit 3B; alleen als er iets antwoordt.
- **F3** **Details tonen** (dichtgeklapt): de laatste regels van het logboek, op de pagina zelf.
- **F4** **Opnieuw proberen**: de hele start opnieuw, vanaf de controle.
- **F5** **Stoppen** → STOPPEN.

### 3.8 STOPPEN: opruimen

- **S1** **Wordt gestopt…**, met wat er nu gebeurt.
- **S2** Geen knoppen.

**Daarna:** gelukt → OVERZICHT, zonder melding. Niet gelukt → je blijft hier, met "Stoppen lukte niet", wat er misging, en **Opnieuw proberen** (nog een keer stoppen).

De vensters van de omgeving blijven open.

### 3.9 RONDGANG: alles om de beurt, als test

- **R1** "Ronde 3 · Tezza is klaar, nog 4 min · daarna Valtimo".
- **R2** De laatste drie uitslagen ("09:12 Haal Centraal: klaar na 12 s").
- **R3** **Rondgang stoppen** → alles stopt → OVERZICHT. Dit is de enige knop.
- **R4** De kaart van de omgeving waar de rondgang is, toont haar fase (STARTEN of KLAAR) zonder knoppen; de lijst met onderdelen werkt wel.
- **R5** De andere kaarten zijn grijs.

De rondgang start met alles aan (niet met wat je bij Aanpassen koos), opent geen vensters, en slaat over wat een voorwaarde mist, een probleem heeft of voorwaarden heeft die nog niemand accepteerde. Alles komt in het verslag.

### 3.10 AFTELLEN: automatische start

OVERZICHT, met op één kaart (of in het vak Rondgang):

- **A1** "Start automatisch over 45 s".
- **A2** **Niet automatisch starten**.

Elke klik zet het aftellen uit. Loopt de automatische start tegen een probleem aan, dan blijft PROBLEEM staan tot er iemand komt. Een omgeving met voorwaarden die nog niemand accepteerde, start niet automatisch.

### 3.11 TWEE ACTIEF: iets is buiten Launch om gestart

- **T1** "Er draaien twee omgevingen. Stop er één."
- **T2** Beide kaarten tonen **Stoppen**. Launch stopt niet zelf.

### 3.12 GEEN LAUNCHER: de pagina krijgt al 10 s geen antwoord

- **L1** Gele balk: "De Launcher antwoordt niet sinds 09:12. Start de Launcher opnieuw."
- **L2** De rest van de pagina is grijs, zonder knoppen.

Antwoordt de Launcher weer, dan staat de pagina meteen in de juiste fase.

### 3.13 LADEN

"Even geduld…", alleen als het eerste antwoord langer dan 1 s duurt. De Launcher heeft de toestand altijd klaar (hij meet op de achtergrond), dus normaal zie je dit niet.

## 4. De commandline

In het Engels, met subcommando's zoals `git` en `docker`.

```
launch list [--json]                              the environments in environments/
launch status [<id>] [--json]                     what runs, and its state
launch check <id> [--json] [--lang nl]            preconditions and problems, without starting
launch fix <id>                                   fix what Launch can fix
launch start <id> [--with X,Y] [--without X,Y] [--wait]
launch stop <id>
launch --help | launch <command> --help | launch --version
```

| Exitcode | Betekenis |
|---|---|
| 0 | gelukt |
| 1 | mislukt |
| 2 | verkeerd gebruik (onbekende optie of omgeving) |
| 3 | probleem; de start is niet begonnen (ook: de onderlaag draait niet) |
| 4 | voorwaarde: kan niet op deze computer |
| 5 | bezet: de Launcher draait, of er loopt al een handeling |

- `start` lost niets zelf op: bij een probleem noemt hij het, en `launch fix <id>`. Er is geen `--yes` (er valt niets te bevestigen). Draait er een andere omgeving, dan start hij niet: "Tezza is running; stop it first" (exitcode 5).
- `start` keert meteen terug ("starting"); met `--wait` wacht hij tot de omgeving klaar of mislukt is.
- `--with` en `--without` zetten keuzes uit `environment.json` aan of uit; zonder staat alles op de standaard. Alleen de Launcher onthoudt keuzes.
- `status`: `stopped`, `starting`, `ready` (met of hij antwoordt), `failed`, `stopping`, `fixing`, `unknown`.
- `check --json`: per afwijking een vaste code met de gegevens (bijvoorbeeld `port-in-use`, poort 8082, door "postgres"), de soort (voorwaarde, probleem, door Launch op te lossen) en een tekst in de gevraagde taal.
- Meldingen in de taal van de computer, anders Engels. De Launcher kiest zelf de taal van de pagina (later: die van de browser).

## 5. Eén tegelijk: locks

Twee file locks van het besturingssysteem in de map van Launch. Ze verdwijnen vanzelf als het proces stopt of crasht; er blijft nooit een lock achter.

- **"De Launcher draait":** de Launcher houdt hem vast zolang hij draait. `start`, `stop` en `fix` uit een terminal weigeren dan (exitcode 5); `list`, `status` en `check` mogen wel.
- **"Er loopt een handeling":** een start, stop of fix houdt hem vast zolang die loopt, ook als de Launcher intussen stopt. Een tweede `launch` weigert; een herstarte Launcher ziet hem en staat meteen in de juiste fase.

## 6. Wat erbij komt in `environment.json`

- **Onderlaag:** wat de omgeving nodig heeft om te draaien (Docker, een VM, …). Draait die niet, dan is dat een probleem van die omgeving.
- **Voorwaarden:** het platform (`needs.platform`), en het geheugen dat de computer minstens moet hebben.
- **Keuzes:** onderdelen en sets demodata, elk met een naam, een uitleg, een standaard (aan of uit), en bij demodata het onderdeel dat ze nodig heeft. `list --json` geeft ze door aan de Launcher.
- **Teksten per taal** (`{"nl": "…"}`), te beginnen met alleen `nl`.

## Bijlage: wat er nu misgaat (25 oktober 2026)

Waargenomen: Tezza draaide al uren en de containers stonden er gewoon. Toch meldde de Launcher om 08:22:01–08:22:25 dat Tezza "Niet actief" was, en de pagina zette alle kaarten weer aan. Docker zag in die tijd geen enkele container stoppen of starten.

| # | Wat | Waar | Tegen |
|---|---|---|---|
| 1 | Eén mislukte meting van het antwoord (time-out 4 s) of een mislukte `docker ps` maakt van Klaar meteen "Uit" | `launch.py` `own_up`, `status_of` | regel 2 |
| 2 | Lukt `launch --status` niet, dan wordt **elke** omgeving "Uit" | `launcher.py` `environments()`: `states = {}` → `"stopped"` | regel 2 |
| 3 | Eén verversing duurt ~15 s (lijst, status ~8,7 s, en vijf checks na elkaar) en gebeurt onder `_LOCK`, in de aanvraag van de pagina; de cache van 8 s is korter dan de verversing zelf | `environments_cached` | regel 7, §3.13 |
| 4 | "Wordt gestopt" en "wordt gestart" na een klik zijn alleen een gok van de pagina (`INTENT`, twee minuten); een tweede tabblad of herladen ziet ze niet | `launcher.html` `view()` | regel 7, 8 |
| 5 | Het verzoek Starten/Stoppen wacht op `launch` (tot 300 s) en houdt `ACTION` vast | `do_POST` | regel 7 |
| 6 | Een start die zonder uitslag stopt (pid weg, geen `failed`) terwijl de containers half draaien, wordt "Klaar" of "Uit", afhankelijk van het antwoord | `status_of` | regel 2 |
| 7 | Een probleem staat al vóór de start als waarschuwing op elke kaart; "Docker draait niet" staat op elke kaart | `check()`, `launcher.html` | regel 3 |
| 8 | `launch <id>` voert fixes uit zonder te vragen, en `--yes` start ondanks problemen (de Launcher geeft altijd `--yes` mee) | `cmd_start` | regel 4 |
| 9 | Bij een gestopte omgeving kost de meting ~2 s per `localhost`-poort die dicht is (Windows probeert een geweigerde verbinding opnieuw) | `answers()` | §3.13 |
