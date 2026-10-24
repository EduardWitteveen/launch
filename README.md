# Launch

Met de **Launcher** start, stop en open je omgevingen op je eigen computer, één tegelijk. Een omgeving is een complete applicatie die je wilt laten zien of uitproberen. Nu zijn dat:

| Omgeving | Wat het is | Eerste start |
|---|---|---|
| **Tezza** | Het zaak- en documentbeheer van Contezza, compleet zoals Contezza het levert (tezza-packaging 4.15.2: Alfresco, GZAC, Open Zaak, Notificaties, Objecten), met de zaaktypes van Súdwest-Fryslân. | ongeveer 45 minuten (ongeveer 8 GB downloaden), daarna een paar minuten |
| **Haal Centraal** | Persoonsgegevens opvragen bij de BRP-testversie van de RvIG, met fictieve testpersonen. Klein en snel: handig om de Launcher te leren kennen. | een minuut |

Launch is een **experimenteel en onofficieel** project. Het is geen product van Contezza, de RvIG of een gemeente. Het komt voort uit [dawo-appliance](https://github.com/EduardWitteveen/dawo-appliance) en werkt ook los daarvan.

## Installeren (Windows 10/11)

1. Download **[Launch-installeren.cmd](https://github.com/EduardWitteveen/launch/releases/download/v0.1.0-rc.2/Launch-installeren.cmd)**.
2. Dubbelklik erop. Windows waarschuwt misschien ("Windows heeft uw pc beveiligd"). Kies dan *Meer informatie* → *Toch uitvoeren*.
3. Het bestand zet alles neer wat nodig is en slaat over wat er al is:
   - **Git for Windows** en **Python 3**;
   - **Docker**: is Docker Desktop er al, dan gebruikt het dat. Anders installeert het **Rancher Desktop** (gratis en open source). Kies bij de eerste start van Rancher Desktop voor *dockerd (moby)* en zet *Kubernetes* uit;
   - Launch zelf in `%LOCALAPPDATA%\Programs\Launch`;
   - een snelkoppeling **Launcher** in het Startmenu en op het bureaublad;
   - de hostnaam `identity` in het hosts-bestand. Die is nodig om in te loggen bij Tezza. Windows vraagt hiervoor één keer om beheerdersrechten.
4. Daarna gaat de Launcher open in je browser: <http://localhost:8790/>.

Is er net Git, Python of Rancher Desktop bijgekomen en doet de Launcher niets? Herstart dan de computer en open **Launcher** opnieuw.

## Gebruiken

- Open **Launcher** (Startmenu of bureaublad). Docker en de Launcher starten vanzelf als ze nog niet draaien.
- Klik bij een omgeving op **Starten**. De kaart laat zien welke stap bezig is en hoe lang het nog ongeveer duurt. Een omgeving haalt bij de eerste start zelf alles op wat ze nodig heeft.
- Is hij klaar, dan klik je op **Openen**. Met **Stoppen** zet je hem weer uit.
- Er draait één omgeving tegelijk. Zolang er een actief is, zijn de andere grijs.
- **Rondgang**: de Launcher start alle omgevingen om de beurt, laat ze een tijdje aan staan en gaat door naar de volgende. Zo zie je zonder op te letten of alles nog werkt. Je zet hem uit met *Rondgang stoppen*.

**Tezza:** log in met `launcher` / `launcher`. Dat is een demo-gebruiker met dezelfde groepen als de beheerder.

## Wat je computer nodig heeft

- Windows 10 of 11, 64-bit, met beheerdersrechten voor de installatie.
- **Geheugen**: Tezza heeft ongeveer 12 GB nodig binnen Docker. Docker (WSL) krijgt standaard de helft van het geheugen van de computer. Met 32 GB werkt het dus vanzelf. Met 16 GB moet je Docker meer geven: zet in `%USERPROFILE%\.wslconfig` onder `[wsl2]` de regel `memory=12GB` en herstart de computer. De Launcher waarschuwt op de kaart als het te weinig is.
- Schijfruimte: ongeveer 20 GB voor Tezza.
- Internet bij de eerste start.
- **Licentie Docker Desktop**: in een organisatie met meer dan 250 medewerkers of meer dan 10 miljoen euro omzet is Docker Desktop niet gratis. Rancher Desktop is wel altijd gratis. Daarom installeert Launch Rancher Desktop als er nog geen Docker is.

## Als het niet lukt

- De kaart zegt **Starten mislukt**: klik op **Opnieuw proberen**. De details staan in `%LOCALAPPDATA%\launch\<omgeving>.log`.
- **Docker draait niet**: start Docker Desktop of Rancher Desktop en wacht tot het klaar is.
- **Inloggen bij Tezza lukt niet** ("identity" onbekend): dubbelklik op `tools\hostnamen.cmd` in `%LOCALAPPDATA%\Programs\Launch`.
- **Opnieuw installeren**: voer `Launch-installeren.cmd` nog eens uit. Wat al gedownload is (Docker-images, de gegevens van Tezza) blijft bewaard.

## For developers

- `launch.py` is the command line (`python launch.py --list | --status | <id> | --stop <id> | --check <id>`), with the same JSON contract as DAWO's `launch` (`--list --json` gives `{"schema": 1, "environments": [...]}`).
- `launcher/` is the Launcher: `launcher.py` (a small HTTP server on 127.0.0.1) and `launcher.html`. Its behaviour is specified in dawo-appliance's `docs/launcher-behaviour.md`.
- An environment is a directory under `environments/` with `environment.json` (what the card shows, `health`, `needs`) and `run.sh start <progress-file> | stop`, run with Git for Windows' bash. Every image is pinned by digest and every source by commit.
- State lives in `%LOCALAPPDATA%\launch` (or `LAUNCH_STATE`).
- Browser test: `cd tests/e2e && npm install && node launcher-behaviour.js "%LOCALAPPDATA%/launch" http://localhost:8790/` (uses Chrome via playwright-core, and starts Haal Centraal for real).

## Licentie

EUPL-1.2 (zie `LICENSE`). De omgevingen hebben hun eigen licenties; die staan op de kaart in de Launcher.
