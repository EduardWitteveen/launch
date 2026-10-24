"""The Launcher: a small local web page to start, stop and open environments.

Part of Launch (ADR 0008, docs/adr/0008-launch-environments.md). It serves one
page on 127.0.0.1 with a card per environment of the catalogue and runs the
`launch` command for the buttons. Nothing in here is specific to one
workplace.

  launcher.py [--port 8790] [--launch launch] [--page launcher.html]
              [--auto-start ID | --auto-start-cycle] [--auto-start-delay 60]
              [--cycle-file FILE [--cycle-hold 300] [--cycle-gap 60]
               [--cycle-ready-timeout 5400] [--cycle-report FILE]
               [--cycle-persist-units ON-UNIT OFF-UNIT]]

With --auto-start the Launcher starts that environment by itself after the
delay (#238), so a machine can run unattended: the page shows the countdown
and a button to cancel it. It fires once, and only when nothing runs yet;
any start or stop through the page cancels it. --auto-start-cycle starts the
round that way instead.

The round ("rondgang", #251) is a regression run while nobody watches: each
environment of the catalogue in turn is started, kept up for --cycle-hold
seconds once it is ready, stopped, and after --cycle-gap seconds the next one
starts; round after round until someone stops it on the page, or starts an
environment. Stopping the environment the round is at moves it on to the
next one. Every result goes to --cycle-report. It starts at once when
--cycle-file exists; the page can switch that on and off by starting the two
--cycle-persist-units (polkit decides who may).

One start or stop at a time: while one runs, the page's buttons wait and a
second request is answered 409 (#257: a start queued behind a stop cancelled
that stop).

An environment with a notice in its catalogue entry (for example the terms of
a product whose license is not known, #261) does not start at once: its card
shows the notice for its seconds, with "Nu starten" and "Niet starten", and
then it starts. The round does the same.

Safety: it listens on 127.0.0.1 only, and every action needs the random token
that is put into the page when it is served, so another web site cannot press
the buttons (cross-site requests cannot read the page). Starting and stopping
run `launch` as the user who runs the Launcher; the system decides (polkit)
what that user may start.

Experimental and unofficial. Not for production.
SPDX-License-Identifier: EUPL-1.2
"""

import argparse
import datetime
import http.server
import os
import json
import re
import secrets
import subprocess
import sys
import threading
import time

TOKEN = secrets.token_hex(16)
ID_RE = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")


def run(args, timeout=60):
    """Run launch with args; return (exit code, stdout, stderr)."""
    try:
        p = subprocess.run([*LAUNCH_CMD, *args], capture_output=True, text=True, creationflags=NO_WINDOW,
                           timeout=timeout, stdin=subprocess.DEVNULL)
        return p.returncode, p.stdout, p.stderr
    except subprocess.TimeoutExpired:
        return 124, "", "timeout"


# One start or stop at a time (#257). The page's request does not wait for
# it; the round does.
ACTION = threading.Lock()


def act(args, timeout=300):
    """run() a start or stop while holding ACTION; the caller holds nothing."""
    with ACTION:
        rc, out, err = run(args, timeout=timeout)
    with _LOCK:
        _CACHE["at"] = 0.0
    return rc, out, err


# Starts that first show the environment's notice (#261): {id: time it starts}.
PENDING = {}


def notice_of(eid):
    """The environment's notice {title, text, seconds}, or None."""
    rc, out, _ = run(["--list", "--json"])
    for env in (json.loads(out) if rc == 0 else {}).get("environments", []):
        if env.get("id") == eid and isinstance(env.get("notice"), dict):
            return env["notice"]
    return None


def pending_worker(eid):
    """Start eid when its notice has been shown long enough, unless cancelled."""
    while True:
        with _LOCK:
            at = PENDING.get(eid)
        if at is None:
            return
        if time.time() >= at:
            break
        time.sleep(0.5)
    with _LOCK:
        if PENDING.pop(eid, None) is None:
            return
    rc, out, err = act([eid, "--yes"])
    sys.stderr.write(f"launcher: start of {eid} after its notice: exit {rc}: {(out + err).strip()[-200:]}\n")


_CACHE = {"at": 0.0, "data": []}
_LOCK = threading.Lock()

# The pending automatic start (#238): {"id", "at"} until it fires or is
# cancelled, then None. The id None means the round (#257).
AUTO = {"pending": None}


def auto_start_worker(eid, at):
    """Start eid (None: the round) at time `at`, unless cancelled or something already runs."""
    while time.time() < at:
        time.sleep(0.5)
        if AUTO["pending"] is None:
            return
    with _LOCK:
        if AUTO["pending"] is None:
            return
        AUTO["pending"] = None
        _CACHE["at"] = 0.0
    if eid is None:
        cycle_start()
        return
    busy = [e["id"] for e in environments() if e["state"] != "stopped"]
    if busy:
        sys.stderr.write(f"launcher: automatic start of {eid} skipped: {', '.join(busy)} already runs\n")
        return
    rc, out, err = act([eid, "--yes"])
    sys.stderr.write(f"launcher: automatic start of {eid}: exit {rc}: {(out + err).strip()}\n")


# The round (#251): its state for the page, and a flag to stop it.
CYCLE = {"active": False, "round": 0, "env": None, "phase": "", "until": None,
         "next": None, "last": [], "stop": threading.Event(), "skip": threading.Event()}


def cycle_report(line):
    """Append one line to the round's report (and the Launcher's log)."""
    stamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    sys.stderr.write(f"launcher: rondgang: {line}\n")
    try:
        os.makedirs(os.path.dirname(OPTS.cycle_report), exist_ok=True)
        with open(OPTS.cycle_report, "a", encoding="utf-8") as f:
            f.write(f"{stamp} {line}\n")
    except OSError:
        pass
    with _LOCK:
        CYCLE["last"] = (CYCLE["last"] + [f"{stamp[11:16]} {line}"])[-6:]


def cycle_wait(seconds, phase, eid=None, nxt=None):
    """Wait, showing the phase and the countdown; False when stopped, or
    when the user stopped the environment the round is at (CYCLE["skip"])."""
    with _LOCK:
        CYCLE.update(phase=phase, env=eid, next=nxt, until=time.time() + seconds)
    end = time.time() + seconds
    while time.time() < end:
        if CYCLE["stop"].wait(min(1.0, max(0.0, end - time.time()))):
            return False
        if CYCLE["skip"].is_set():
            return False
    return True


def env_state(eid):
    """(state, progress error) of eid now, bypassing the cache."""
    for e in environments():
        if e["id"] == eid:
            return e["state"], ((e.get("progress") or {}).get("error") or "")
    return "unknown", ""


def cycle_worker():
    """Start each environment in turn, round after round, until stopped."""
    stop = CYCLE["stop"]
    with _LOCK:
        CYCLE.update(active=True, round=0)
        AUTO["pending"] = None
    cycle_report(f"gestart (in de lucht {OPTS.cycle_hold} s, pauze {OPTS.cycle_gap} s)")
    first = True  # the first one starts at once: the user (or the automatic start) already waited
    while not stop.is_set():
        rc, out, _ = run(["--list", "--json"])
        ids = [e["id"] for e in (json.loads(out) if rc == 0 else {}).get("environments", [])]
        # The marker file may name the environments to go round (one id per
        # line, # for comments); empty: all of the catalogue.
        try:
            with open(OPTS.cycle_file, encoding="utf-8") as f:
                wanted = [w for w in (l.split("#")[0].strip() for l in f) if ID_RE.match(w) and w in ids]
        except (OSError, TypeError):
            wanted = []
        if wanted:
            ids = wanted
        with _LOCK:
            CYCLE["round"] += 1
        n = CYCLE["round"]
        done_any = False
        for i, eid in enumerate(ids):
            nxt = ids[i + 1] if i + 1 < len(ids) else (ids[0] if ids else None)
            CYCLE["skip"].clear()
            if not first and not cycle_wait(OPTS.cycle_gap, "pause", eid, nxt) and stop.is_set():
                break
            first = False
            CYCLE["skip"].clear()
            notice = notice_of(eid)
            if notice:
                # Its notice first (#261), as when the user starts it.
                secs = int(notice.get("seconds", 60))
                with _LOCK:
                    PENDING[eid] = time.time() + secs
                    _CACHE["at"] = 0.0
                cycle_wait(secs, "notice", eid, nxt)
                with _LOCK:
                    accepted = PENDING.pop(eid, None) is not None
                    _CACHE["at"] = 0.0
                if stop.is_set():
                    break
                if not accepted:
                    cycle_report(f"ronde {n} {eid}: niet gestart (de gebruiker koos 'Niet starten')")
                    continue
            with _LOCK:
                CYCLE.update(phase="starting", env=eid, next=nxt, until=None)
            t0 = time.time()
            rc, out, err = act([eid, "--yes"], timeout=600)
            if rc == 2:
                cycle_report(f"ronde {n} {eid}: overgeslagen (niet ingericht op deze machine)")
                continue
            if rc != 0:
                cycle_report(f"ronde {n} {eid}: niet gestart (exit {rc}: {(out + err).strip()[-200:]})")
                continue
            done_any = True
            result, seen_starting = None, False
            while not stop.is_set() and not CYCLE["skip"].is_set() and result is None:
                state, error = env_state(eid)
                if state == "failed":
                    result = f"MISLUKT na {int(time.time() - t0)} s: {error or 'de start is mislukt'}"
                elif state == "ready":
                    result = f"klaar na {int(time.time() - t0)} s"
                elif error:
                    result = f"MISLUKT na {int(time.time() - t0)} s: {error}"
                elif state == "starting":
                    seen_starting = True
                elif state == "stopped" and seen_starting:
                    result = f"MISLUKT na {int(time.time() - t0)} s: gestopt tijdens het starten"
                if result is None and time.time() - t0 > OPTS.cycle_ready_timeout:
                    result = f"MISLUKT: niet klaar na {OPTS.cycle_ready_timeout} s"
                if result is None:
                    cycle_wait(10, "starting", eid, nxt)
                    with _LOCK:
                        CYCLE["until"] = None
            if stop.is_set():
                # Rondgang stoppen leads back to nothing running (#284,
                # docs/launcher-behaviour.md): the environment it was
                # starting stops too.
                with _LOCK:
                    CYCLE.update(phase="stopping", env=eid, until=None)
                rc, out, err = act(["--stop", eid], timeout=600)
                cycle_report(f"ronde {n} {eid}: rondgang gestopt tijdens het starten; " + ("gestopt" if rc == 0 else f"stoppen mislukt (exit {rc})"))
                break
            if CYCLE["skip"].is_set():
                # The user stopped it on its card (#257): the round goes on.
                cycle_report(f"ronde {n} {eid}: {result or 'tijdens het starten'}; door de gebruiker gestopt, door naar de volgende")
                continue
            if result.startswith("klaar"):
                if not cycle_wait(OPTS.cycle_hold, "up", eid, nxt):
                    if stop.is_set():
                        with _LOCK:
                            CYCLE.update(phase="stopping", env=eid, until=None)
                        rc, out, err = act(["--stop", eid], timeout=600)
                        cycle_report(f"ronde {n} {eid}: {result}; rondgang gestopt; " + ("gestopt" if rc == 0 else f"stoppen mislukt (exit {rc})"))
                        break
                    cycle_report(f"ronde {n} {eid}: {result}; door de gebruiker gestopt, door naar de volgende")
                    continue
            with _LOCK:
                CYCLE.update(phase="stopping", env=eid, until=None)
            rc, out, err = act(["--stop", eid], timeout=600)
            cycle_report(f"ronde {n} {eid}: {result}; " + ("gestopt" if rc == 0 else f"stoppen mislukt (exit {rc}: {(out + err).strip()[-200:]})"))
        if not done_any and not stop.is_set():
            cycle_report("geen omgeving ingericht; rondgang stopt")
            break
    with _LOCK:
        CYCLE.update(active=False, phase="", env=None, next=None, until=None)
        _CACHE["at"] = 0.0
    cycle_report("gestopt")


def cycle_start():
    with _LOCK:
        if CYCLE["active"]:
            return False
        CYCLE["stop"] = threading.Event()
        CYCLE["skip"] = threading.Event()
        AUTO["pending"] = None
    threading.Thread(target=cycle_worker, daemon=True).start()
    return True


def cycle_stop():
    CYCLE["stop"].set()


def cycle_state():
    with _LOCK:
        st = {k: CYCLE[k] for k in ("active", "round", "env", "phase", "until", "next", "last")}
    st["available"] = bool(OPTS.cycle_file)
    st["persisted"] = bool(OPTS.cycle_file) and os.path.exists(OPTS.cycle_file)
    st["can_persist"] = bool(OPTS.cycle_persist_units)
    # The round as the automatic start that is still to come (#257), else None.
    pend = AUTO["pending"]
    st["autostart"] = {"at": pend["at"]} if pend and pend["id"] is None else None
    return st


def environments_cached(max_age=8.0):
    """environments(), at most every max_age seconds (the page polls often)."""
    with _LOCK:
        if time.monotonic() - _CACHE["at"] > max_age:
            _CACHE["data"] = environments()
            _CACHE["at"] = time.monotonic()
        return _CACHE["data"]


def environments():
    """The catalogue with, per environment, its state and its preflight."""
    rc, out, _ = run(["--list", "--json"])
    catalog = json.loads(out) if rc == 0 else {"environments": []}
    rc, out, _ = run(["--status", "--json"], timeout=120)
    states = {s["id"]: s for s in (json.loads(out) if rc == 0 and out.strip() else [])}
    result = []
    for env in catalog.get("environments", []):
        eid = env["id"]
        rc, out, _ = run(["--check", eid, "--json"], timeout=60)
        check = json.loads(out) if out.strip() else {"ok": True, "problems": []}
        result.append({
            "id": eid,
            "title": env.get("title", eid),
            "summary": env.get("summary", ""),
            "version": env.get("version", ""),
            "url": env.get("url", ""),
            "login": env.get("login", ""),
            # What runs next to it, with its own login (#260).
            "links": [{"title": l.get("title", ""), "url": l.get("url", ""), "login": l.get("login", "")}
                      for l in env.get("links", []) if isinstance(l, dict)],
            "state": states.get(eid, {}).get("state", "stopped"),
            # Whether its URL answers: after a failed start part of it may (#282).
            "answers": bool(states.get(eid, {}).get("answers")),
            # While it starts: {step, steps, label, started, since, expected,
            # error} as the environment wrote it (#232), else None.
            "progress": states.get(eid, {}).get("progress"),
            # The automatic start that is still to come (#238), else None.
            "autostart": ({"at": AUTO["pending"]["at"]}
                          if AUTO["pending"] and AUTO["pending"]["id"] == eid else None),
            # Shown before it starts (#261), and when that start comes.
            "notice": env.get("notice") if isinstance(env.get("notice"), dict) else None,
            # Licences per repository (#273): the summary and the components.
            "licences": env.get("licences") if isinstance(env.get("licences"), dict) else None,
            "components": [{"name": c.get("name", ""), "repo": c.get("repo", ""), "license": c.get("license", "")}
                           for c in env.get("components", []) if isinstance(c, dict)],
            "pending": {"at": PENDING[eid]} if eid in PENDING else None,
            "problems": check.get("problems", []),
        })
    return result


class Handler(http.server.BaseHTTPRequestHandler):
    server_version = "Launcher"

    def send(self, code, body, ctype="application/json; charset=utf-8"):
        data = body.encode("utf-8") if isinstance(body, str) else body
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        if self.path in ("/", "/index.html"):
            with open(OPTS.page, encoding="utf-8") as f:
                page = f.read().replace("__LAUNCHER_TOKEN__", TOKEN)
            self.send(200, page, "text/html; charset=utf-8")
        elif self.path == "/api/environments":
            self.send(200, json.dumps(environments_cached()))
        elif self.path == "/api/cycle":
            self.send(200, json.dumps(cycle_state()))
        else:
            self.send(404, json.dumps({"error": "not found"}))

    def do_POST(self):
        m = re.fullmatch(r"/api/cycle/(start|stop|persist-on|persist-off)", self.path)
        if m:
            if self.headers.get("X-Launcher-Token") != TOKEN:
                self.send(403, json.dumps({"error": "forbidden"}))
                return
            verb = m.group(1)
            if verb == "start":
                ok = cycle_start()
            elif verb == "stop":
                cycle_stop(); ok = True
            elif not OPTS.cycle_persist_units:
                ok = False
            else:
                unit = OPTS.cycle_persist_units[0 if verb == "persist-on" else 1]
                ok = subprocess.run(["systemctl", "start", unit], capture_output=True,
                                    timeout=60, stdin=subprocess.DEVNULL).returncode == 0
            self.send(200 if ok else 500, json.dumps({"ok": ok, **cycle_state()}))
            return
        if self.path == "/api/autostart/cancel":
            if self.headers.get("X-Launcher-Token") != TOKEN:
                self.send(403, json.dumps({"error": "forbidden"}))
                return
            with _LOCK:
                AUTO["pending"] = None
                _CACHE["at"] = 0.0
            self.send(200, json.dumps({"ok": True}))
            return
        m = re.fullmatch(r"/api/(start|start-now|cancel|stop)/([a-z0-9-]+)", self.path)
        if not m or not ID_RE.match(m.group(2)):
            self.send(404, json.dumps({"error": "not found"}))
            return
        if self.headers.get("X-Launcher-Token") != TOKEN:
            self.send(403, json.dumps({"error": "forbidden"}))
            return
        verb, eid = m.groups()
        if verb == "cancel":
            # "Niet starten" while the notice shows (#261).
            with _LOCK:
                PENDING.pop(eid, None)
                _CACHE["at"] = 0.0
            self.send(200, json.dumps({"ok": True, "code": 0, "output": ""}))
            return
        if verb in ("start", "start-now") and CYCLE["active"] and eid not in PENDING:
            cycle_stop()  # the user takes over: the round ends (#251)
        if verb == "start" and eid not in PENDING and notice_of(eid):
            # Its notice first (#261); "Nu starten" (start-now) skips the wait.
            secs = int(notice_of(eid).get("seconds", 60))
            with _LOCK:
                AUTO["pending"] = None
                PENDING[eid] = time.time() + secs
                _CACHE["at"] = 0.0
            threading.Thread(target=pending_worker, args=(eid,), daemon=True).start()
            self.send(200, json.dumps({"ok": True, "code": 0, "pending": True, "output": ""}))
            return
        if verb == "start-now" and CYCLE["active"] and eid in PENDING:
            # The round shows this notice: the round starts it now.
            with _LOCK:
                PENDING[eid] = time.time()
            CYCLE["skip"].set()
            self.send(200, json.dumps({"ok": True, "code": 0, "output": ""}))
            return
        # One start or stop at a time (#257): a second one would replace the
        # first one's systemd job ("Job ... canceled").
        if not ACTION.acquire(blocking=False):
            self.send(409, json.dumps({"ok": False, "code": 409, "busy": True,
                                       "output": "Er wordt al iets gestart of gestopt; even geduld."}))
            return
        try:
            with _LOCK:
                AUTO["pending"] = None  # the user chose; no automatic start
                PENDING.pop(eid, None)
            if verb == "stop" and CYCLE["active"] and CYCLE["env"] == eid:
                CYCLE["skip"].set()  # the round goes on with the next one (#257)
            args = [eid, "--yes"] if verb in ("start", "start-now") else ["--stop", eid]
            rc, out, err = run(args, timeout=300)
        finally:
            ACTION.release()
        with _LOCK:
            _CACHE["at"] = 0.0  # show the new state on the next refresh
        self.send(200 if rc == 0 else 500,
                  json.dumps({"ok": rc == 0, "code": rc, "output": (out + err).strip()}))

    def log_message(self, fmt, *args):
        sys.stderr.write("launcher: " + (fmt % args) + "\n")


def main():
    global OPTS, LAUNCH_CMD
    ap = argparse.ArgumentParser(description="The Launcher (ADR 0008)")
    ap.add_argument("--port", type=int, default=8790)
    here = os.path.dirname(os.path.abspath(__file__))
    # Without --launch: this repository's launch.py, with this Python.
    ap.add_argument("--launch", default=None)
    ap.add_argument("--page", default=os.path.join(here, "launcher.html"))
    ap.add_argument("--auto-start", default="")
    ap.add_argument("--auto-start-cycle", action="store_true")
    ap.add_argument("--auto-start-delay", type=int, default=60)
    # Standalone, the round is always there; this file (absent by default) makes
    # it start at once and may name the environments to go round.
    ap.add_argument("--cycle-file", default=os.path.join(STATE, "rondgang"))
    ap.add_argument("--cycle-hold", type=int, default=300)
    ap.add_argument("--cycle-gap", type=int, default=60)
    ap.add_argument("--cycle-ready-timeout", type=int, default=5400)
    ap.add_argument("--cycle-report", default=os.path.join(STATE, "rondgang.log"))
    ap.add_argument("--cycle-persist-units", nargs=2, default=None, metavar=("ON", "OFF"))
    OPTS = ap.parse_args()
    LAUNCH_CMD = [OPTS.launch] if OPTS.launch else [sys.executable, os.path.join(os.path.dirname(here), "launch.py")]
    if OPTS.auto_start and not ID_RE.match(OPTS.auto_start):
        ap.error("--auto-start: not an environment id")
    if OPTS.cycle_file and os.path.exists(OPTS.cycle_file):
        cycle_start()  # the round at once, instead of the automatic start
    elif OPTS.auto_start_cycle or OPTS.auto_start:
        eid = None if OPTS.auto_start_cycle else OPTS.auto_start
        at = time.time() + OPTS.auto_start_delay
        AUTO["pending"] = {"id": eid, "at": at}
        threading.Thread(target=auto_start_worker, args=(eid, at), daemon=True).start()
    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", OPTS.port), Handler)
    sys.stderr.write(f"launcher: http://127.0.0.1:{OPTS.port}/\n")
    httpd.serve_forever()


OPTS = None
# On Windows: no console window flashing up for every `launch` call.
NO_WINDOW = 0x08000000 if os.name == "nt" else 0
STATE = os.environ.get("LAUNCH_STATE") or (
    os.path.join(os.environ["LOCALAPPDATA"], "launch") if os.name == "nt" and os.environ.get("LOCALAPPDATA")
    else os.path.expanduser("~/.local/state/launch"))
if __name__ == "__main__":
    main()
