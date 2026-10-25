"""The Launcher: a small local web page to start, stop and open environments.

Part of Launch (ADR 0008, docs/adr/0008-launch-environments.md). It serves one
page on 127.0.0.1 with a card per environment of the catalogue and runs the
`launch` command for the buttons. Nothing in here is specific to one
workplace. Its behaviour is docs/launcher-behaviour.md; the code follows it.

  launcher.py [--port 1900] [--launch launch] [--page launcher.html]
              [--auto-start ID | --auto-start-cycle] [--auto-start-delay 60]
              [--cycle-file FILE [--cycle-hold 300] [--cycle-gap 60]
               [--cycle-ready-timeout 5400] [--cycle-report FILE]
               [--cycle-persist-units ON-UNIT OFF-UNIT]]

The state (#284): a background thread measures (`launch status --json`
every few seconds, the catalogue and the preconditions every minute) and keeps
a record per environment with its phase. The page's GET /api/state answers at
once from that record; a request never runs `launch`. A measurement that
fails is "unknown", and unknown changes nothing: the phase changes by an
action, or when two measurements in a row agree (rule 2). The transitions are
pure functions (observe(), check_outcome(), ...) so tests can run them with
made-up facts. Every transition goes to stderr.

Choices (docs §3.1, 1C6): an environment's "options" (parts and demo data)
are in /api/state with the remembered choice; POST /api/options/<id> changes
them while it is stopped, kept in <state>/choices.json. A start by a person
passes them to `launch start` as --with/--without; the round and the
automatic start use the defaults.

Every `launch` call gets LAUNCH_LANG=nl: the page is in Dutch.

Actions (POST /api/<verb>/<id>: start, retry, fix, accept, stop) answer at
once after setting the new phase; the work runs in a thread. One action at a
time (#257: a start queued behind a stop cancelled that stop): a second one is
answered 409.

With --auto-start the Launcher starts that environment by itself after the
delay (#238), so a machine can run unattended: the page shows the countdown
and a button to cancel it. It fires once, and only when nothing runs yet; any
action through the page cancels it. A problem stays on the card until someone
comes; an environment whose notice nobody accepted does not start by itself.
--auto-start-cycle starts the round that way instead.

The round ("rondgang", #251) is a regression run while nobody watches: each
environment of the catalogue in turn is started, kept up for --cycle-hold
seconds once it is ready, stopped, and after --cycle-gap seconds the next one
starts; round after round until someone stops it on the page. It skips (and
reports) an environment that cannot run here, has a problem, or has a notice
nobody accepted. Every result goes to --cycle-report. It starts at once when
--cycle-file exists; the page can switch that on and off by starting the two
--cycle-persist-units (polkit decides who may).

An environment with a notice in its catalogue entry (for example the terms of
a product whose license is not known, #261) shows it before its first start;
"Akkoord en starten" is remembered per environment and version
(<state>/accepted.json).

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
import json
import os
import re
import secrets
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

TOKEN = secrets.token_hex(16)
ID_RE = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")

PHASES = ("unknown", "stopped", "checking", "problem", "fixing", "notice", "starting", "ready", "failed", "stopping")
# Phases in which an environment holds the machine: no other one may start.
ACTIVE = ("checking", "problem", "fixing", "notice", "starting", "ready", "failed", "stopping")
# Phases that count for TWEE ACTIEF (§3.11): something runs.
RUNNING = ("starting", "ready", "failed", "stopping")
ABORTED = "De start is afgebroken."


# --- the transitions (pure: no launch, no clock, no files) --------------------

def new_record(eid):
    """The record of one environment before its first measurement."""
    return {"id": eid, "phase": "unknown", "precondition": None, "ended": None,
            "problems": [], "problem_note": "", "notice": None, "progress": None,
            "error": "", "output": "", "log_tail": "", "answering": [], "stop_error": "",
            "not_answering_since": None, "unknown_since": None,
            # internal: the pending confirmation, the action generation, when
            # the phase last changed, and whether an action works on it now
            "_confirm": None, "gen": 0, "changed_at": 0.0, "acting": False}


def set_phase(rec, to, why, now):
    """Move rec to phase `to`; returns the transition (id, from, to, why)."""
    frm = rec["phase"]
    rec["phase"] = to
    rec["_confirm"] = None
    rec["changed_at"] = now
    rec["not_answering_since"] = None
    rec["unknown_since"] = None
    if to in ("stopped", "starting"):
        rec.update(problems=[], problem_note="", notice=None, error="", output="", log_tail="",
                   answering=[], stop_error="")
    if to == "stopped":
        rec["progress"] = None
    return (rec["id"], frm, to, why)


def _confirm(rec, key, now):
    """One more measurement that says `key`; True when it is the second in a row."""
    c = rec["_confirm"]
    if c and c["key"] == key:
        c["n"] += 1
    else:
        rec["_confirm"] = c = {"key": key, "n": 1, "first": now}
    return c["n"] >= 2


def up_of(facts):
    """True when its own containers run and it answers, False when either clearly
    does not, None when that is not known."""
    c, a = facts.get("containers"), facts.get("answers")
    if c is True and a is True:
        return True
    if c is False or a is False:
        return False
    return None


def ended_text(title, now, docker=False):
    """1E: "Tezza draait niet meer (09:41)[: Docker is gestopt]"."""
    t = f"{title} draait niet meer ({time.strftime('%H:%M', time.localtime(now))})"
    return t + (": Docker is gestopt" if docker else "")


def start_error(facts):
    """F1: the error the start wrote, else that it was aborted."""
    return ((facts.get("progress") or {}).get("error") or "").strip() or ABORTED


def observe(rec, facts, now, has_project=True, title=None):
    """Apply one measurement (contract §A fields; None = not known) to rec.

    Returns the list of transitions (id, from, to, why). A system transition
    needs two measurements in a row that agree; a measurement that is not
    known neither confirms nor resets. The start process ending (process
    false) is a clear event and counts at once. Phases an action drives
    (checking, problem, fixing, notice, stopping) ignore measurements."""
    facts = facts or {}
    title = title or rec["id"]
    phase = rec["phase"]
    proc, failed = facts.get("process"), facts.get("failed")
    up = up_of(facts)
    out = []

    if phase == "unknown":
        if proc is True:
            out.append(set_phase(rec, "starting", "the start process runs", now))
            rec["progress"] = facts.get("progress")
        elif failed is True:
            out.append(set_phase(rec, "failed", "the last start failed", now))
            rec["progress"] = facts.get("progress")
            rec["error"] = start_error(facts)
        elif up is True:
            out.append(set_phase(rec, "ready", "its containers run and it answers", now))
        elif facts.get("containers") is not None and facts.get("answers") is not None:
            out.append(set_phase(rec, "stopped", "it does not run", now))

    elif phase == "stopped":
        if proc is True:
            out.append(set_phase(rec, "starting", "the start process runs (started outside the Launcher)", now))
            rec["progress"] = facts.get("progress")
        elif up is True:
            if _confirm(rec, "ready", now):
                out.append(set_phase(rec, "ready", "it runs and answers, twice (started outside the Launcher)", now))
        elif up is False:
            rec["_confirm"] = None

    elif phase == "starting":
        if proc is True:
            rec["_confirm"] = None
            rec["progress"] = facts.get("progress")
        elif proc is False:
            if failed is True:
                rec["progress"] = facts.get("progress") or rec["progress"]
                err = start_error(facts)
                out.append(set_phase(rec, "failed", f"the start failed: {err}", now))
                rec["error"] = err
            elif failed is False:
                if up is True:
                    out.append(set_phase(rec, "ready", "the start ended and it answers", now))
                elif up is False:
                    if _confirm(rec, "aborted", now):
                        out.append(set_phase(rec, "failed", "the start ended and it does not run, twice", now))
                        rec["error"] = ABORTED

    elif phase == "ready":
        docker, cont, ans = facts.get("docker"), facts.get("containers"), facts.get("answers")
        if docker is False:
            if _confirm(rec, "docker-stopped", now):
                out.append(set_phase(rec, "stopped", "Docker is not running, twice", now))
                rec["ended"] = {"at": now, "text": ended_text(title, now, docker=True)}
                return out
        elif cont is False:
            if _confirm(rec, "containers-gone", now):
                out.append(set_phase(rec, "stopped", "its containers are gone, twice", now))
                rec["ended"] = {"at": now, "text": ended_text(title, now)}
                return out
        elif cont is True and ans is False and has_project:
            if _confirm(rec, "not-answering", now) and rec["not_answering_since"] is None:
                rec["not_answering_since"] = rec["_confirm"]["first"]
        elif cont is True and ans is True:
            rec["_confirm"] = None
        if ans is True:
            rec["not_answering_since"] = None
        if docker is None or cont is None or ans is None:
            if rec["unknown_since"] is None:
                rec["unknown_since"] = now
        else:
            rec["unknown_since"] = None

    elif phase == "failed":
        if proc is True:
            out.append(set_phase(rec, "starting", "the start process runs again", now))
            rec["progress"] = facts.get("progress")

    return out


def check_items(out):
    """The items of `launch check <id> --json` output (contract §B); a
    check that gives no JSON is one problem."""
    try:
        data = json.loads(out)
        items = data.get("items")
        if isinstance(items, list):
            return [i for i in items if isinstance(i, dict)]
    except (ValueError, AttributeError, TypeError):
        pass
    return [{"code": "check-failed", "kind": "problem", "text": "De controle lukte niet. Probeer het opnieuw."}]


def preconditions_of(items):
    """1C5: the precondition texts joined, or None."""
    texts = [i.get("text", "") for i in items if i.get("kind") == "precondition"]
    return " ".join(t for t in texts if t) or None


def check_outcome(items, notice, accepted):
    """What follows a check: ("stopped", precondition text), ("problem", items),
    ("notice", notice) or ("start", None)."""
    pre = preconditions_of(items)
    if pre:
        return "stopped", pre
    problems = [i for i in items if i.get("kind") in ("problem", "fixable")]
    if problems:
        return "problem", problems
    if notice and not accepted:
        return "notice", notice
    return "start", None


def start_outcome(rc):
    """What follows `launch start <id>` with exit code rc (contract §C)."""
    return {0: "starting", 3: "recheck", 4: "stopped", 5: "other-running"}.get(rc, "failed")


def retry_note(prev_code, problems):
    """P4: "Nog niet opgelost" when the first problem is the same one as before."""
    return "Nog niet opgelost" if problems and prev_code and problems[0].get("code") == prev_code else ""


def fix_note(rc):
    """§3.3: the note on PROBLEEM after `launch fix` (124: timed out)."""
    if rc == 0:
        return "Opgelost"
    if rc == 124:
        return "Er is geen toestemming gegeven"
    return "Oplossen lukte niet"


def refuse(verb, rec, recs, busy, cycle_active=False):
    """None when verb may run on rec now, else the Dutch reason (a 409)."""
    phase = rec["phase"]
    if cycle_active:
        return "De rondgang loopt; stop die eerst."
    if verb == "start":
        if phase not in ("stopped", "failed"):
            return "Starten kan nu niet."
        if phase == "stopped" and rec.get("precondition"):
            return "Kan niet draaien op deze computer, omdat " + rec["precondition"]
        if any(r["phase"] in ACTIVE for r in recs if r["id"] != rec["id"]):
            return "Er draait al een andere omgeving; stop die eerst."
    elif verb == "retry":
        if not (phase == "problem" or (phase == "stopping" and rec.get("stop_error"))):
            return "Opnieuw proberen kan nu niet."
    elif verb == "fix":
        if phase != "problem" or not any(i.get("kind") == "fixable" for i in rec.get("problems", [])[:1]):
            return "Oplossen kan nu niet."
    elif verb == "accept":
        if phase != "notice":
            return "Akkoord geven kan nu niet."
    elif verb == "stop":
        if phase in ("problem", "notice", "checking"):
            return None  # nothing runs yet: at once, even while the check runs
        if not (phase in ("starting", "ready", "failed") or (phase == "stopping" and rec.get("stop_error"))):
            return "Stoppen kan nu niet."
    else:
        return "Onbekende handeling."
    if busy:
        return "Er loopt al een handeling; even geduld."
    return None


def two_active(recs):
    """§3.11: more than one environment runs."""
    return sum(1 for r in recs if r["phase"] in RUNNING) > 1


def txt(v, lang="nl"):
    """A text of environment.json (a string or {"nl": ..., "en": ...}) in lang, else nl, else the first."""
    if isinstance(v, dict):
        return v.get(lang) or v.get("nl") or next((x for x in v.values() if isinstance(x, str) and x), "")
    return v if isinstance(v, str) else ""


# --- choices (1C6, contract stage 2 §3; pure) ----------------------------------

def env_options(env):
    """The valid options of an environment.json."""
    return [o for o in env.get("options") or [] if isinstance(o, dict) and ID_RE.match(str(o.get("id", "")))]


def default_of(o):
    return o.get("default", True) is not False


def apply_needs(opts, on):
    """Off what needs a part that is off (a chain settles in a few passes)."""
    on = dict(on)
    changed = True
    while changed:
        changed = False
        for o in opts:
            if on.get(o["id"]) and any(on.get(n) is False for n in o.get("needs") or [] if isinstance(n, str)):
                on[o["id"]] = False
                changed = True
    return on


def options_view(env, saved):
    """The options for /api/state, `on` the remembered choice (saved: {opt: bool}), else the default."""
    opts = env_options(env)
    saved = saved if isinstance(saved, dict) else {}
    on = apply_needs(opts, {o["id"]: saved[o["id"]] if isinstance(saved.get(o["id"]), bool) else default_of(o)
                            for o in opts})
    return [{"id": o["id"], "kind": o.get("kind") if o.get("kind") in ("part", "data") else "part",
             "title": txt(o.get("title")) or o["id"], "about": txt(o.get("about")),
             "needs": [n for n in o.get("needs") or [] if isinstance(n, str)],
             "default": default_of(o), "on": on[o["id"]]} for o in opts]


def change_options(env, saved, body):
    """POST /api/options/<id>: (the new remembered choices, None) or (None, the Dutch reason).
    The remembered choices are only what the person chose that differs from the default;
    needs is applied when they are shown and used, not stored, so data that went off with
    its part comes back with it."""
    opts = env_options(env)
    ids = [o["id"] for o in opts]
    if not isinstance(body, dict) or not body:
        return None, "Er is geen keuze meegestuurd."
    bad = [str(k) for k, v in body.items() if k not in ids or not isinstance(v, bool)]
    if bad:
        return None, "Onbekende keuze: " + ", ".join(bad) + "."
    saved = saved if isinstance(saved, dict) else {}
    on = {o["id"]: saved[o["id"]] if isinstance(saved.get(o["id"]), bool) else default_of(o) for o in opts}
    on.update(body)
    return {o["id"]: on[o["id"]] for o in opts if on[o["id"]] != default_of(o)}, None


def option_flags(env, saved):
    """`launch start` flags for the remembered choices that differ from the default."""
    view = options_view(env, saved)
    on = [v["id"] for v in view if v["on"] and not v["default"]]
    off = [v["id"] for v in view if not v["on"] and v["default"]]
    return (["--with", ",".join(on)] if on else []) + (["--without", ",".join(off)] if off else [])


def public(rec, env, cycle_env=None, autostart=None, saved=None):
    """The record as the page gets it (contract §D); saved: its remembered choices."""
    links = [{"title": txt(l.get("title")), "url": l.get("url", ""), "login": txt(l.get("login"))}
             for l in env.get("links", []) if isinstance(l, dict)]
    return {
        "id": rec["id"],
        "title": txt(env.get("title")) or rec["id"],
        "summary": txt(env.get("summary")),
        "version": txt(env.get("version")),
        "url": env.get("url", ""),
        "login": txt(env.get("login")),
        "options": options_view(env, saved),
        # What runs next to it, with its own login (#260).
        "links": links,
        # Licences per repository (#273): the summary and the components.
        "licences": env.get("licences") if isinstance(env.get("licences"), dict) else None,
        "components": [{"name": c.get("name", ""), "repo": c.get("repo", ""), "license": c.get("license", "")}
                       for c in env.get("components", []) if isinstance(c, dict)],
        "phase": rec["phase"],
        "precondition": rec["precondition"],
        "ended": rec["ended"],
        "autostart": autostart,
        "problems": rec["problems"],
        "problem_note": rec["problem_note"],
        "notice": ({"title": txt(rec["notice"].get("title")), "text": txt(rec["notice"].get("text"))}
                   if isinstance(rec["notice"], dict) else None),
        "progress": rec["progress"],
        "error": rec["error"],
        "log_tail": rec["log_tail"] or rec["output"],
        "answering": rec["answering"],
        "stop_error": rec["stop_error"],
        "not_answering_since": rec["not_answering_since"],
        "unknown_since": rec["unknown_since"],
        "cycle_here": cycle_env == rec["id"],
    }


# --- running launch -----------------------------------------------------------

def run(args, timeout=60):
    """Run launch with args; return (exit code, stdout, stderr); 124 on timeout."""
    # launch writes UTF-8 too (on Windows a pipe would otherwise get cp1252: "één" became "��n"),
    # and in Dutch: its texts end up on the page.
    env = dict(os.environ, PYTHONIOENCODING="utf-8", LAUNCH_LANG="nl")
    try:
        p = subprocess.run([*LAUNCH_CMD, *args], capture_output=True, text=True, encoding="utf-8",
                           errors="replace", creationflags=NO_WINDOW, timeout=timeout, stdin=subprocess.DEVNULL,
                           env=env)
        return p.returncode, p.stdout or "", p.stderr or ""
    except subprocess.TimeoutExpired:
        return 124, "", "timeout"
    except OSError as e:
        return 127, "", str(e)


def answers(url):
    """True when url answers 2xx/3xx, False when refused or an error, None
    when there is no answer in time (as launch's answers(), contract §A)."""
    try:
        u = urllib.parse.urlsplit(url)
        host, port = u.hostname or "", u.port or (443 if u.scheme == "https" else 80)
        if host in ("localhost", "127.0.0.1"):
            try:
                socket.create_connection(("127.0.0.1", port), timeout=1).close()
            except socket.timeout:
                return None
            except OSError:
                return False
        with urllib.request.urlopen(url, timeout=4) as r:
            return 200 <= r.status < 400
    except urllib.error.HTTPError as e:
        return 200 <= e.code < 400
    except (socket.timeout, TimeoutError):
        return None
    except Exception:
        return False


def log_tail(eid, lines=30):
    """F3: the last lines of <state>/<id>.log."""
    try:
        with open(os.path.join(STATE, f"{eid}.log"), "rb") as f:
            f.seek(0, 2)
            f.seek(max(0, f.tell() - 65536))
            text = f.read().decode("utf-8", errors="replace")
        return "\n".join(text.splitlines()[-lines:])
    except OSError:
        return ""


# --- the state in memory ------------------------------------------------------

_LOCK = threading.RLock()
# One action at a time (#257). The page's request does not wait for it.
ACTION = threading.Lock()
WAKE = threading.Event()  # measure now
CATALOG = {}  # id -> catalogue entry
RECS = {}  # id -> record, in catalogue order
SEQ = {"n": 0}
CHOICES = {}  # env id -> {option id: bool}, as in <state>/choices.json

# The pending automatic start (#238): {"id", "at"} until it fires or is
# cancelled, then None. The id None means the round (#257).
AUTO = {"pending": None}

# The round (#251): its state for the page, and a flag to stop it.
CYCLE = {"active": False, "round": 0, "env": None, "phase": "", "until": None,
         "next": None, "last": [], "stop": threading.Event()}


def bump():
    with _LOCK:
        SEQ["n"] += 1


def log(trans):
    for eid, frm, to, why in trans:
        sys.stderr.write(f"launcher: {eid}: {frm} -> {to} ({why})\n")


def transition(eid, to, why, **fields):
    """Set eid's phase (and fields) from an action; logs and bumps."""
    with _LOCK:
        rec = RECS[eid]
        t = set_phase(rec, to, why, time.time())
        rec.update(fields)
        bump()
    log([t])
    WAKE.set()


def update(eid, **fields):
    with _LOCK:
        RECS[eid].update(fields)
        bump()


def stale(eid, gen):
    """True when another action took over eid since this one began."""
    with _LOCK:
        return RECS[eid]["gen"] != gen


def choices_path():
    return os.path.join(STATE, "choices.json")


def load_choices():
    """<state>/choices.json: {env id: {option id: bool}}, the ones that differ from the default."""
    try:
        with open(choices_path(), encoding="utf-8") as f:
            data = json.load(f)
        return {k: v for k, v in data.items() if isinstance(v, dict)} if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def user_options(eid, body):
    """POST /api/options/<id>: (200, {"ok": true}) or (4xx, {"ok": false, "message"})."""
    with _LOCK:
        rec = RECS.get(eid)
        if rec is None:
            return 404, {"ok": False, "message": "Onbekende omgeving."}
        if rec["phase"] != "stopped":
            return 409, {"ok": False, "message": "Aanpassen kan alleen als de omgeving niet draait."}
        new, why = change_options(CATALOG.get(eid, {}), CHOICES.get(eid), body)
        if why:
            return 400, {"ok": False, "message": why}
        data = dict(CHOICES)
        if new:
            data[eid] = new
        else:
            data.pop(eid, None)
        try:
            os.makedirs(STATE, exist_ok=True)
            with open(choices_path(), "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=1)
        except OSError as e:
            sys.stderr.write(f"launcher: {eid}: could not store the choices: {e}\n")
            return 500, {"ok": False, "message": "Bewaren lukte niet."}
        CHOICES.clear()
        CHOICES.update(data)
        bump()
    sys.stderr.write(f"launcher: {eid}: choices {json.dumps(new)}\n")
    return 200, {"ok": True}


def accepted_path():
    return os.path.join(STATE, "accepted.json")


def is_accepted(eid):
    try:
        with open(accepted_path(), encoding="utf-8") as f:
            return CATALOG.get(eid, {}).get("version", "") in json.load(f).get(eid, [])
    except (OSError, ValueError, AttributeError):
        return False


def accept(eid):
    """Remember "Akkoord" for eid and its version (§3.4)."""
    try:
        with open(accepted_path(), encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        data = {}
    if not isinstance(data, dict):
        data = {}
    v = CATALOG.get(eid, {}).get("version", "")
    data[eid] = sorted(set(data.get(eid, [])) | {v})
    try:
        os.makedirs(STATE, exist_ok=True)
        with open(accepted_path(), "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=1)
    except OSError as e:
        sys.stderr.write(f"launcher: {eid}: could not store the acceptance: {e}\n")


def notice_of(eid):
    n = CATALOG.get(eid, {}).get("notice")
    return n if isinstance(n, dict) else None


def refresh_catalog():
    """Read the catalogue (`launch list --json`); keeps the old one on failure."""
    rc, out, _ = run(["list", "--json"])
    try:
        envs = [e for e in json.loads(out).get("environments", []) if isinstance(e, dict) and ID_RE.match(e.get("id", ""))]
    except (ValueError, AttributeError):
        sys.stderr.write(f"launcher: the catalogue could not be read (exit {rc})\n")
        return False
    with _LOCK:
        CATALOG.clear()
        CATALOG.update({e["id"]: e for e in envs})
        old = dict(RECS)
        RECS.clear()
        for e in envs:
            RECS[e["id"]] = old.get(e["id"]) or new_record(e["id"])
        bump()
    return True


def state():
    """GET /api/state (contract §D), from memory."""
    with _LOCK:
        pend = AUTO["pending"]
        recs = list(RECS.values())
        return {
            "seq": SEQ["n"],
            "now": time.time(),
            "two_active": two_active(recs),
            "cycle": cycle_state(),
            "busy": ACTION.locked(),
            "envs": [public(r, CATALOG.get(r["id"], {}), CYCLE["env"] if CYCLE["active"] else None,
                            {"at": pend["at"]} if pend and pend["id"] == r["id"] else None, CHOICES.get(r["id"]))
                     for r in recs],
        }


# --- the background measurements ----------------------------------------------

def parts_answering(eid):
    """F2: the parts (main url + links) that answer."""
    env = CATALOG.get(eid, {})
    parts = [{"title": txt(env.get("title")) or eid, "url": env.get("url", ""), "login": txt(env.get("login"))}]
    parts += [{"title": txt(l.get("title")), "url": l.get("url", ""), "login": txt(l.get("login"))}
              for l in env.get("links", []) if isinstance(l, dict)]
    return [p for p in parts if p["url"] and answers(p["url"]) is True]


def measure_once():
    t0 = time.time()
    rc, out, err = run(["status", "--json"], timeout=60)
    try:
        data = json.loads(out)
        facts = {s["id"]: s for s in data if isinstance(s, dict) and "id" in s}
    except (ValueError, TypeError, KeyError):
        sys.stderr.write(f"launcher: status unknown (exit {rc}: {(out + err).strip()[-200:]})\n")
        facts = {}
    now = time.time()
    failed = []
    with _LOCK:
        for eid, rec in RECS.items():
            if rec["acting"] or rec["changed_at"] > t0:
                continue  # an action works on it, or changed it while we measured
            before = json.dumps(public(rec, CATALOG.get(eid, {})), sort_keys=True, default=str)
            env = CATALOG.get(eid, {})
            trans = observe(rec, facts.get(eid), now, bool(env.get("project")), txt(env.get("title")) or eid)
            log(trans)
            if rec["phase"] == "failed":
                failed.append(eid)
            if json.dumps(public(rec, env), sort_keys=True, default=str) != before:
                bump()
    for eid in failed:
        parts, tail = parts_answering(eid), log_tail(eid)
        with _LOCK:
            rec = RECS.get(eid)
            if rec and rec["phase"] == "failed" and (rec["answering"] != parts or rec["log_tail"] != tail):
                rec["answering"], rec["log_tail"] = parts, tail
                bump()


def monitor():
    """Measure every ~3 s, the catalogue every 60 s."""
    last_list = time.time()
    while True:
        if time.time() - last_list > 60:
            refresh_catalog()
            last_list = time.time()
        t0 = time.time()
        try:
            measure_once()
        except Exception as e:  # the monitor must not die
            sys.stderr.write(f"launcher: measuring failed: {e!r}\n")
        WAKE.wait(max(0.5, 3.0 - (time.time() - t0)))
        WAKE.clear()


def precondition_pass():
    """Each environment's preconditions (1C5), at start and every 60 s while nothing is busy."""
    while True:
        if not ACTION.locked():
            for eid in list(RECS):
                rc, out, _ = run(["check", eid, "--json", "--lang", "nl"], timeout=60)
                if rc == 124:
                    continue
                pre = preconditions_of(check_items(out))
                with _LOCK:
                    if eid in RECS and RECS[eid]["precondition"] != pre:
                        RECS[eid]["precondition"] = pre
                        bump()
        time.sleep(60)


# --- the action flows (run while holding ACTION) -------------------------------

def do_check(eid):
    rc, out, _ = run(["check", eid, "--json", "--lang", "nl"], timeout=120)
    items = check_items(out)
    update(eid, precondition=preconditions_of(items))
    return items


def after_check(eid, gen, items, mode="user", note=""):
    """Go on from a check: stopped, problem, notice or the start itself."""
    if stale(eid, gen):
        return
    outcome, data = check_outcome(items, notice_of(eid), is_accepted(eid))
    if outcome == "stopped":
        transition(eid, "stopped", "it cannot run on this computer", precondition=data)
    elif outcome == "problem":
        transition(eid, "problem", f"{len(data)} problem(s), first {data[0].get('code', '')}",
                   problems=data, problem_note=note)
    elif outcome == "notice" and mode != "user":
        transition(eid, "stopped", "its notice is not accepted; not started by itself")
    elif outcome == "notice":
        transition(eid, "notice", "its notice is not accepted yet", notice=data)
    else:
        flow_run_start(eid, gen, mode)


def flow_start(eid, gen, mode="user"):
    """Starten (and Opnieuw proberen on MISLUKT): check, then go on."""
    after_check(eid, gen, do_check(eid), mode)


def flow_run_start(eid, gen, mode="user"):
    """`launch start`: a person's start with the remembered choices, the round
    and the automatic start with the defaults."""
    if RECS[eid]["phase"] != "starting":
        transition(eid, "starting", "the check is fine; starting")
    with _LOCK:
        flags = option_flags(CATALOG.get(eid, {}), CHOICES.get(eid)) if mode == "user" else []
    rc, out, err = run(["start", eid, *flags], timeout=300)
    if stale(eid, gen):
        return
    text = (out + err).strip()
    outcome = start_outcome(rc)
    if outcome == "starting":
        sys.stderr.write(f"launcher: {eid}: launch {' '.join(['start', eid, *flags])}: exit 0\n")
        WAKE.set()
    elif outcome == "recheck":
        items = do_check(eid)
        if not [i for i in items if i.get("kind") in ("problem", "fixable", "precondition")]:
            items = [{"code": "start-refused", "kind": "problem", "text": text or "De start is niet begonnen."}]
        after_check(eid, gen, items, mode)
    elif outcome == "stopped":
        transition(eid, "stopped", f"launch start {eid}: exit 4 (precondition)", precondition=text or None)
    elif outcome == "other-running":
        transition(eid, "problem", f"launch start {eid}: exit 5 (another one runs)",
                   problems=[{"code": "other-running", "kind": "problem", "text": text}], problem_note="")
    else:
        transition(eid, "failed", f"launch start {eid}: exit {rc}", error="De start is mislukt.", output=text)


def flow_retry(eid, gen):
    """Opnieuw proberen on PROBLEEM: check again."""
    with _LOCK:
        probs = RECS[eid]["problems"]
        prev = probs[0].get("code") if probs else None
    items = do_check(eid)
    outcome, data = check_outcome(items, notice_of(eid), is_accepted(eid))
    after_check(eid, gen, items, note=retry_note(prev, data) if outcome == "problem" else "")


def flow_fix(eid, gen):
    rc, out, err = run(["fix", eid], timeout=240)
    if stale(eid, gen):
        return
    note = fix_note(rc)
    transition(eid, "problem", f"launch fix {eid}: exit {rc}", problem_note=note,
               output=(out + err).strip() if rc else "")


def flow_accept(eid, gen):
    accept(eid)
    flow_run_start(eid, gen)


def flow_stop(eid, gen):
    rc, out, err = run(["stop", eid], timeout=600)
    if stale(eid, gen):
        return rc
    if rc == 0:
        transition(eid, "stopped", f"launch stop {eid}: exit 0")
    else:
        lines = (out + err).strip().splitlines()
        with _LOCK:
            RECS[eid]["stop_error"] = (lines[-1] if lines else "") or "Stoppen lukte niet."
            RECS[eid]["output"] = (out + err).strip()
            bump()
        sys.stderr.write(f"launcher: {eid}: stopping failed (exit {rc})\n")
    return rc


FLOWS = {"start": ("checking", flow_start), "fix": ("fixing", flow_fix),
         "accept": ("starting", flow_accept), "stop": ("stopping", flow_stop)}


def done_acting(eid):
    """The action on eid ended; a measurement begun before now is ignored."""
    with _LOCK:
        RECS[eid]["acting"] = False
        RECS[eid]["changed_at"] = time.time()
    WAKE.set()


def begin(eid, verb, mode="user"):
    """Start an action on eid; the caller holds ACTION, the thread releases it.
    Returns the generation."""
    with _LOCK:
        rec = RECS[eid]
        rec["gen"] += 1
        gen = rec["gen"]
        if verb == "retry" and rec["phase"] == "stopping":
            verb = "stop"
        if verb == "retry":
            phase, fn = "checking", flow_retry
        else:
            phase, fn = FLOWS[verb]
        if verb == "stop":
            rec["stop_error"] = ""
        rec["acting"] = True  # the monitor leaves it alone until the action ends
    transition(eid, phase, f"{verb} ({mode})")

    def work():
        try:
            if fn is flow_start:
                fn(eid, gen, mode)
            else:
                fn(eid, gen)
        except Exception as e:
            sys.stderr.write(f"launcher: {eid}: {verb} broke: {e!r}\n")
            if not stale(eid, gen):
                transition(eid, "failed", f"{verb} broke", error="Er ging iets mis in de Launcher.", output=repr(e))
        finally:
            done_acting(eid)
            ACTION.release()
    threading.Thread(target=work, daemon=True).start()
    return gen


def user_action(verb, eid):
    """A button on a card: (200, {"ok": true}) or (409, {"ok": false, "message"})."""
    with _LOCK:
        rec = RECS.get(eid)
        if rec is None:
            return 404, {"ok": False, "message": "Onbekende omgeving."}
        msg = refuse(verb, rec, list(RECS.values()), ACTION.locked(), CYCLE["active"])
        if msg:
            return 409, {"ok": False, "message": msg}
        # Every user action cancels the automatic start and clears every 1E.
        AUTO["pending"] = None
        for r in RECS.values():
            r["ended"] = None
        if verb == "stop" and rec["phase"] in ("problem", "notice", "checking"):
            rec["gen"] += 1  # a check that still runs is ignored
            t = set_phase(rec, "stopped", "Stoppen before the start", time.time())
            bump()
            log([t])
            return 200, {"ok": True}
        if not ACTION.acquire(blocking=False):
            return 409, {"ok": False, "message": "Er loopt al een handeling; even geduld."}
    begin(eid, verb)
    return 200, {"ok": True}


# --- the automatic start and the round ----------------------------------------

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
        bump()
    if eid is None:
        cycle_start()
        return
    with _LOCK:
        rec = RECS.get(eid)
        msg = "not in the catalogue" if rec is None else refuse("start", rec, list(RECS.values()), ACTION.locked(), CYCLE["active"])
        if not msg and notice_of(eid) and not is_accepted(eid):
            msg = "its notice is not accepted"
        if not msg and not ACTION.acquire(blocking=False):
            msg = "another action runs"
    if msg:
        sys.stderr.write(f"launcher: automatic start of {eid} skipped: {msg}\n")
        return
    sys.stderr.write(f"launcher: automatic start of {eid}\n")
    begin(eid, "start", mode="auto")


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
        bump()


def cycle_wait(seconds, phase, eid=None, nxt=None):
    """Wait, showing the phase and the countdown; False when the round is stopped."""
    with _LOCK:
        CYCLE.update(phase=phase, env=eid, next=nxt, until=time.time() + seconds if seconds else None)
        bump()
    return not CYCLE["stop"].wait(seconds)


def cycle_action(eid, fn, *args):
    """Run one flow for the round, synchronously, holding ACTION."""
    while not ACTION.acquire(timeout=1.0):
        if CYCLE["stop"].is_set():
            return False
    with _LOCK:
        RECS[eid]["gen"] += 1
        RECS[eid]["acting"] = True
        gen = RECS[eid]["gen"]
    try:
        fn(eid, gen, *args)
        return True
    finally:
        done_acting(eid)
        ACTION.release()


def cycle_stop_env(eid, n, what):
    with _LOCK:
        CYCLE.update(phase="stopping", env=eid, until=None)
    transition(eid, "stopping", "the round stops it")
    rc = cycle_action_force(eid)
    cycle_report(f"ronde {n} {eid}: {what}; " + ("gestopt" if rc == 0 else f"stoppen mislukt (exit {rc})"))


def cycle_action_force(eid):
    """Stop eid for the round, also after the round was told to stop."""
    ACTION.acquire()
    with _LOCK:
        RECS[eid]["gen"] += 1
        RECS[eid]["acting"] = True
        gen = RECS[eid]["gen"]
    try:
        return flow_stop(eid, gen)
    finally:
        done_acting(eid)
        ACTION.release()


def cycle_one(eid, n, nxt):
    """One environment of the round; True when it was started."""
    stop = CYCLE["stop"]
    with _LOCK:
        rec = RECS.get(eid)
        if rec is None:
            return False
        pre = rec["precondition"]
        others = [r["id"] for r in RECS.values() if r["id"] != eid and r["phase"] in ACTIVE]
        CYCLE.update(phase="starting", env=eid, next=nxt, until=None)
        bump()
    if pre:
        cycle_report(f"ronde {n} {eid}: overgeslagen (kan niet op deze computer: {pre})")
        return False
    if others:
        cycle_report(f"ronde {n} {eid}: overgeslagen ({', '.join(others)} draait)")
        return False
    if rec["phase"] not in ("stopped", "failed", "unknown"):
        cycle_report(f"ronde {n} {eid}: overgeslagen (fase {rec['phase']})")
        return False
    transition(eid, "checking", "the round starts it")
    t0 = time.time()
    if not cycle_action(eid, flow_start, "cycle"):
        transition(eid, "stopped", "the round stopped")
        return False
    with _LOCK:
        phase, rec = RECS[eid]["phase"], RECS[eid]
        problems, pre, err = list(rec["problems"]), rec["precondition"], rec["error"]
    if phase == "problem":
        transition(eid, "stopped", "the round skips a problem")
        cycle_report(f"ronde {n} {eid}: overgeslagen (probleem: {problems[0].get('text', '') if problems else ''})")
        return False
    if phase == "stopped":
        why = f"kan niet op deze computer: {pre}" if pre else "voorwaarden nog niet geaccepteerd"
        cycle_report(f"ronde {n} {eid}: overgeslagen ({why})")
        return False
    if phase == "failed":
        cycle_stop_env(eid, n, f"niet gestart ({err})")
        return True
    result = None
    while result is None and not stop.is_set():
        with _LOCK:
            phase, err = RECS[eid]["phase"], RECS[eid]["error"]
        if phase == "ready":
            result = f"klaar na {int(time.time() - t0)} s"
        elif phase == "failed":
            result = f"MISLUKT na {int(time.time() - t0)} s: {err or 'de start is mislukt'}"
        elif phase == "stopped":
            result = f"MISLUKT na {int(time.time() - t0)} s: gestopt tijdens het starten"
        elif time.time() - t0 > OPTS.cycle_ready_timeout:
            result = f"MISLUKT: niet klaar na {OPTS.cycle_ready_timeout} s"
        else:
            stop.wait(3)
    if stop.is_set():
        # Rondgang stoppen leads back to nothing running (#284).
        cycle_stop_env(eid, n, f"{result or 'tijdens het starten'}; rondgang gestopt")
        return True
    if result.startswith("klaar") and not cycle_wait(OPTS.cycle_hold, "up", eid, nxt):
        cycle_stop_env(eid, n, f"{result}; rondgang gestopt")
        return True
    if phase == "stopped":
        cycle_report(f"ronde {n} {eid}: {result}")
        return True
    cycle_stop_env(eid, n, result)
    return True


def cycle_worker():
    """Start each environment in turn, round after round, until stopped."""
    stop = CYCLE["stop"]
    with _LOCK:
        CYCLE.update(active=True, round=0)
        AUTO["pending"] = None
        bump()
    cycle_report(f"gestart (in de lucht {OPTS.cycle_hold} s, pauze {OPTS.cycle_gap} s)")
    first = True  # the first one starts at once: the user (or the automatic start) already waited
    while not stop.is_set():
        refresh_catalog()
        with _LOCK:
            ids = list(RECS)
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
            if not first and not cycle_wait(OPTS.cycle_gap, "pause", eid, nxt):
                break
            first = False
            done_any = cycle_one(eid, n, nxt) or done_any
            if stop.is_set():
                break
        if not done_any and not stop.is_set():
            cycle_report("geen omgeving kon starten; rondgang stopt")
            break
    with _LOCK:
        CYCLE.update(active=False, phase="", env=None, next=None, until=None)
        bump()
    cycle_report("gestopt")


def cycle_start():
    with _LOCK:
        if CYCLE["active"] or any(r["phase"] in ACTIVE for r in RECS.values()):
            return False
        CYCLE["stop"] = threading.Event()
        CYCLE["active"] = True  # at once, so a second click is refused
        AUTO["pending"] = None
        for r in RECS.values():
            r["ended"] = None
        bump()
    threading.Thread(target=cycle_worker, daemon=True).start()
    return True


def cycle_stop():
    CYCLE["stop"].set()


def cycle_state():
    with _LOCK:
        st = {k: CYCLE[k] for k in ("active", "round", "env", "phase", "until", "next", "last")}
        pend = AUTO["pending"]
    st["available"] = bool(OPTS and OPTS.cycle_file)
    st["persisted"] = bool(OPTS and OPTS.cycle_file) and os.path.exists(OPTS.cycle_file)
    st["can_persist"] = bool(OPTS and OPTS.cycle_persist_units)
    # The round as the automatic start that is still to come (#257), else None.
    st["autostart"] = {"at": pend["at"]} if pend and pend["id"] is None else None
    return st


# --- the web server -----------------------------------------------------------

class Handler(http.server.BaseHTTPRequestHandler):
    server_version = "Launcher"

    def send(self, code, body, ctype="application/json; charset=utf-8"):
        data = (json.dumps(body, ensure_ascii=False) if isinstance(body, (dict, list)) else body)
        data = data.encode("utf-8") if isinstance(data, str) else data
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
        elif self.path == "/api/state":
            self.send(200, state())
        else:
            self.send(404, {"error": "not found"})

    def do_POST(self):
        cyc = re.fullmatch(r"/api/cycle/(start|stop|persist-on|persist-off)", self.path)
        act = re.fullmatch(r"/api/(start|retry|fix|accept|stop|options)/([a-z0-9-]+)", self.path)
        if not (cyc or act or self.path == "/api/autostart/cancel") or (act and not ID_RE.match(act.group(2))):
            self.send(404, {"error": "not found"})
            return
        if self.headers.get("X-Launcher-Token") != TOKEN:
            self.send(403, {"error": "forbidden"})
            return
        if self.path == "/api/autostart/cancel":
            with _LOCK:
                AUTO["pending"] = None
                for r in RECS.values():
                    r["ended"] = None
                bump()
            self.send(200, {"ok": True})
        elif cyc:
            verb = cyc.group(1)
            if verb == "start":
                ok = cycle_start()
                msg = "" if ok else "De rondgang kan nu niet starten; stop eerst wat draait."
            elif verb == "stop":
                cycle_stop()
                ok, msg = True, ""
            elif not OPTS.cycle_persist_units:
                ok, msg = False, "Dat kan op deze computer niet."
            else:
                unit = OPTS.cycle_persist_units[0 if verb == "persist-on" else 1]
                ok = subprocess.run(["systemctl", "start", unit], capture_output=True,
                                    timeout=60, stdin=subprocess.DEVNULL).returncode == 0
                msg = "" if ok else "Dat lukte niet."
                bump()
            self.send(200 if ok else 409, {"ok": ok, **({"message": msg} if msg else {})})
        elif act.group(1) == "options":
            try:
                n = int(self.headers.get("Content-Length") or 0)
                body = json.loads(self.rfile.read(min(n, 65536)).decode("utf-8")) if n else None
            except (ValueError, UnicodeDecodeError):
                body = None
            self.send(*user_options(act.group(2), body))
        else:
            code, body = user_action(*act.groups())
            self.send(code, body)

    def log_message(self, fmt, *args):
        if "/api/state" not in (args[0] if args else ""):  # the page polls every 2 s
            sys.stderr.write("launcher: " + (fmt % args) + "\n")


def main():
    global OPTS, LAUNCH_CMD
    ap = argparse.ArgumentParser(description="The Launcher (ADR 0008)")
    ap.add_argument("--port", type=int, default=1900)  # 1900: the municipality code of Súdwest-Fryslân
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
    if not OPTS.launch:
        LAUNCH_CMD = [sys.executable, os.path.join(os.path.dirname(here), "launch.py")]
    elif OPTS.launch.endswith(".py"):
        LAUNCH_CMD = [sys.executable, OPTS.launch]  # a script, also on Windows
    else:
        LAUNCH_CMD = [OPTS.launch]
    if OPTS.auto_start and not ID_RE.match(OPTS.auto_start):
        ap.error("--auto-start: not an environment id")
    CHOICES.update(load_choices())
    refresh_catalog()
    measure_once()  # the first answer of /api/state already has the phases
    threading.Thread(target=monitor, daemon=True).start()
    threading.Thread(target=precondition_pass, daemon=True).start()
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
LAUNCH_CMD = ["launch"]
# On Windows: no console window flashing up for every `launch` call.
NO_WINDOW = 0x08000000 if os.name == "nt" else 0
STATE = os.environ.get("LAUNCH_STATE") or (
    os.path.join(os.environ["LOCALAPPDATA"], "launch") if os.name == "nt" and os.environ.get("LOCALAPPDATA")
    else os.path.expanduser("~/.local/state/launch"))
if __name__ == "__main__":
    main()
