#!/usr/bin/env python3
"""Launch: start, stop and list environments on this machine, one at a time.

Each environment is a directory under environments/ with an environment.json
(what the Launcher shows) and a run.sh (start | stop, written in bash) that
starts it with Docker step by step and writes the Launcher's progress file.
The Launcher (launcher/launcher.py) runs this command for its buttons.

  launch list [--json]                    the environments in environments/
  launch status [<id>] [--json]           what runs, and its state
  launch check <id> [--json] [--lang nl]  preconditions and problems, without starting
  launch fix <id>                         fix what Launch can fix
  launch start <id> [--with X,Y] [--without X,Y] [--wait]
  launch stop <id>

The command line is docs/launcher-behaviour.md section 4. A start checks
first and never fixes anything. Exit codes: 0 done, 1 failed, 2 wrong use
(an unknown option or environment), 3 a problem (the start did not begin;
`fix <id>` solves the ones Launch can fix), 4 a precondition (cannot run on
this computer), 5 busy (another environment runs). `start` returns at once;
with --wait it waits until the environment is ready (0) or failed (1).

Choices: an environment.json may have "options" (parts and sets of demo
data, each with a default). --with/--without switch them on or off; a data
set that needs a part that is off is off too. The resolved set is stored in
<state>/<id>.options.json, and the start's background process passes it to
run.sh as LAUNCH_OPTIONS_ON and LAUNCH_OPTIONS_OFF (comma lists). A choice
with a "source" needs a folder under sources/ (never in git); without it the
choice is off, and when it is on run.sh gets its path as LAUNCH_SOURCE_<ID>.

Messages are in the computer's language (Dutch or English): --lang, else
LAUNCH_LANG, else the language of the operating system, else English.
Texts in environment.json are a string or {"nl": ..., "en": ...}.

Works on Windows (Docker Desktop and Git for Windows' bash) and on Linux or
macOS (docker and bash). State lives in LAUNCH_STATE (default: the user's
local application data, under launch/).

SPDX-License-Identifier: EUPL-1.2
"""
import argparse
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

# The release this is; Launch-installeren.cmd downloads the tag v<VERSION>.
VERSION = "0.1.0-rc.4"

ROOT = os.path.dirname(os.path.abspath(__file__))
ENVS = os.path.join(ROOT, "environments")
WINDOWS = os.name == "nt"
# No console window flashing up for every docker or bash call.
NO_WINDOW = 0x08000000 if WINDOWS else 0
ID_RE = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")
# How a message names a command of its own.
CMD = "python launch.py"


# --- language ------------------------------------------------------------------

LANGS = ("nl", "en")
LANG = "en"

# Every text for the person, per language (AGENTS.md: plain and short).
MESSAGES = {
    "unknown-env": {"nl": "launch: onbekende omgeving '{id}'. Kies uit: {ids}",
                    "en": "launch: unknown environment '{id}'. Choose from: {ids}"},
    "no-bash": {"nl": "launch: Git for Windows (bash) is niet gevonden; installeer Git for Windows.",
                "en": "launch: Git for Windows (bash) was not found; install Git for Windows."},
    "unknown-option": {"nl": "launch: onbekende keuze '{opt}' voor {title}. Kies uit: {ids}",
                       "en": "launch: unknown choice '{opt}' for {title}. Choose from: {ids}"},
    "no-options": {"nl": "launch: {title} heeft geen keuzes ('{opt}').",
                   "en": "launch: {title} has no choices ('{opt}')."},
    "no-source": {"nl": "launch: bron ontbreekt voor '{opt}': zet {what} in {path}",
                  "en": "launch: the source of '{opt}' is missing: put {what} in {path}"},
    "option-twice": {"nl": "launch: '{opt}' staat bij --with en bij --without.",
                     "en": "launch: '{opt}' is in both --with and --without."},
    "platform": {"nl": "{title} werkt nog alleen vanaf de DAWO-USB-SSD (Kubernetes in een virtuele machine); "
                       "in Launch op deze computer komt het later.",
                 "en": "{title} only runs from the DAWO USB SSD for now (Kubernetes in a virtual machine); "
                       "Launch on this computer will follow later."},
    "machine-memory": {"nl": "Deze computer heeft {have:.0f} GB geheugen; {title} heeft er ongeveer {need} nodig.",
                       "en": "This computer has {have:.0f} GB of memory; {title} needs about {need}."},
    "runtime-down": {"nl": "Docker draait niet. Start Docker Desktop en probeer het dan opnieuw.",
                     "en": "Docker is not running. Start Docker Desktop and try again."},
    "runtime-silent": {"nl": "Docker antwoordt niet. Start Docker Desktop opnieuw en probeer het dan nog eens.",
                       "en": "Docker does not answer. Restart Docker Desktop and try again."},
    "docker-memory": {"nl": "Docker heeft {have:.0f} GB geheugen; {title} heeft er ongeveer {need} nodig "
                            "(zie de README: het geheugen van Docker vergroten).",
                      "en": "Docker has {have:.0f} GB of memory; {title} needs about {need} "
                            "(see the README: giving Docker more memory)."},
    "port-container-1": {"nl": "Poort {ports} is bezet door de container '{name}', die niet bij Launch hoort; "
                               "{title} heeft die nodig. Stop die container eerst (bijvoorbeeld: docker stop {name}).",
                         "en": "Port {ports} is taken by the container '{name}', which is not part of Launch; "
                               "{title} needs it. Stop that container first (for example: docker stop {name})."},
    "port-container-n": {"nl": "De poorten {ports} zijn bezet door de container '{name}', die niet bij Launch hoort; "
                               "{title} heeft ze nodig. Stop die container eerst (bijvoorbeeld: docker stop {name}).",
                         "en": "The ports {ports} are taken by the container '{name}', which is not part of Launch; "
                               "{title} needs them. Stop that container first (for example: docker stop {name})."},
    "port-program-1": {"nl": "Poort {ports} is bezet door een ander programma op deze computer; {title} heeft die "
                             "nodig. Sluit dat programma eerst.",
                       "en": "Port {ports} is taken by another program on this computer; {title} needs it. "
                             "Close that program first."},
    "port-program-n": {"nl": "De poorten {ports} zijn bezet door een ander programma op deze computer; {title} heeft "
                             "ze nodig. Sluit dat programma eerst.",
                       "en": "The ports {ports} are taken by another program on this computer; {title} needs them. "
                             "Close that program first."},
    "already-starting": {"nl": "{title} wordt al gestart.", "en": "{title} is already starting."},
    "cannot-run": {"nl": "Kan niet draaien op deze computer:", "en": "Cannot run on this computer:"},
    "not-started": {"nl": "Niet gestart. Eerst dit oplossen:", "en": "Not started. Solve this first:"},
    "fix-hint": {"nl": "Oplossen: {cmd} fix {id}", "en": "To fix it: {cmd} fix {id}"},
    "other-running": {"nl": "{title} draait; stop die eerst ({cmd} stop {id}).",
                      "en": "{title} is running; stop it first ({cmd} stop {id})."},
    "starting": {"nl": "{title} wordt gestart. Volg het met: {cmd} status {id}",
                 "en": "{title} is starting. Follow it with: {cmd} status {id}"},
    "open-at": {"nl": "Daarna te openen op {url}", "en": "Then open {url}"},
    "step": {"nl": "Stap {step} van {steps}: {label}", "en": "Step {step} of {steps}: {label}"},
    "ready-at": {"nl": "{title} is klaar: {url}", "en": "{title} is ready: {url}"},
    "start-failed": {"nl": "Starten van {title} is mislukt. De details staan in {id}.log.",
                     "en": "Starting {title} failed. The details are in {id}.log."},
    "still-running": {"nl": "{title} draait nog: stoppen lukte niet. De details staan in {id}.log.",
                      "en": "{title} still runs: stopping failed. The details are in {id}.log."},
    "stopped": {"nl": "{title} is gestopt.", "en": "{title} has stopped."},
    "all-well": {"nl": "Alles in orde.", "en": "All is well."},
    "fixed": {"nl": "Opgelost.", "en": "Fixed."},
    "fix-failed": {"nl": "Niet gelukt (geen toestemming gegeven?). Probeer het opnieuw.",
                   "en": "That did not work (no permission given?). Try again."},
    "state-ready": {"nl": "klaar op {url}", "en": "ready on {url}"},
    "state-starting": {"nl": "wordt gestart", "en": "starting"},
    "state-failed": {"nl": "starten mislukt", "en": "start failed"},
    "state-stopped": {"nl": "niet actief", "en": "stopped"},
    "state-unknown": {"nl": "onbekend", "en": "unknown"},
}


def os_lang():
    """The language of the operating system's user interface: nl or en."""
    if WINDOWS:
        try:
            import ctypes
            return "nl" if ctypes.windll.kernel32.GetUserDefaultUILanguage() & 0x3FF == 0x13 else "en"  # LANG_DUTCH
        except (AttributeError, OSError):
            return "en"
    for k in ("LC_ALL", "LC_MESSAGES", "LANG"):
        if os.environ.get(k):
            return "nl" if os.environ[k].lower().startswith("nl") else "en"
    return "en"


def choose_lang(arg=None):
    """--lang, else LAUNCH_LANG, else the operating system's language, else en."""
    for v in (arg, os.environ.get("LAUNCH_LANG", "").strip().lower()[:2]):
        if v in LANGS:
            return v
    return os_lang()


def msg(key, **kw):
    t = MESSAGES[key]
    return (t.get(LANG) or t["en"]).format(**kw)


def pick(text, lang=None):
    """A text of environment.json (a string or {"nl": ..., "en": ...}) in the language, else nl, else the first."""
    if isinstance(text, dict):
        return text.get(lang or LANG) or text.get("nl") or next((v for v in text.values() if v), "")
    return text if isinstance(text, str) else ""


def title(env):
    return pick(env.get("title")) or env["id"]


class Usage(Exception):
    """Wrong use: exit code 2, the message on stderr."""


# --- the catalogue ---------------------------------------------------------------

def state_dir():
    base = os.environ.get("LAUNCH_STATE") or (
        os.path.join(os.environ.get("LOCALAPPDATA", os.path.expanduser("~")), "launch") if WINDOWS
        else os.path.join(os.path.expanduser("~"), ".local", "state", "launch"))
    os.makedirs(base, exist_ok=True)
    return base


def catalog():
    """The environments in the order of environments/catalog.json."""
    with open(os.path.join(ENVS, "catalog.json"), encoding="utf-8") as f:
        order = json.load(f)["environments"]
    # LAUNCH_ENVIRONMENTS="a,b": only these, in this order (the browser test
    # puts Haal Centraal first, so the round does not start Tezza).
    if os.environ.get("LAUNCH_ENVIRONMENTS"):
        order = [e for e in os.environ["LAUNCH_ENVIRONMENTS"].split(",") if e in order]
    out = []
    for eid in order:
        with open(os.path.join(ENVS, eid, "environment.json"), encoding="utf-8") as f:
            env = json.load(f)
        env["id"] = eid
        out.append(env)
    return out


def env_of(eid):
    envs = catalog()
    for env in envs:
        if env["id"] == eid:
            return env
    raise Usage(msg("unknown-env", id=eid, ids=", ".join(e["id"] for e in envs)))


# --- choices (docs section 6) ---------------------------------------------------

def options_of(env):
    """The valid options of environment.json: [{"id", "kind", "default", "needs", ...}]."""
    return [o for o in env.get("options") or [] if isinstance(o, dict) and ID_RE.match(str(o.get("id", "")))]


def source_path(o):
    """The folder a choice's "source" names, absolute (relative to Launch); None without a source."""
    src = o.get("source")
    if not isinstance(src, dict) or not isinstance(src.get("path"), str) or not src["path"].strip():
        return None
    return os.path.normpath(os.path.join(ROOT, src["path"]))


def source_missing(o):
    """True when the choice needs a source and its folder is not there or is empty."""
    p = source_path(o)
    if p is None:
        return False
    try:
        return not os.listdir(p)
    except OSError:
        return True


def source_env(env, chosen):
    """LAUNCH_SOURCE_<ID> for each choice that is on and has a source."""
    on = set(chosen.get("on", []))
    return {"LAUNCH_SOURCE_" + o["id"].upper().replace("-", "_"): source_path(o)
            for o in options_of(env) if o["id"] in on and source_path(o)}


def resolve_options(env, on=(), off=()):
    """The defaults, then --with (on) and --without (off), then off what needs a part
    that is off or a source that is missing: {"on": [...], "off": [...]}, each option
    in exactly one. Raises Usage for an id that is not an option of env, and for --with
    on a choice whose source is missing."""
    opts = options_of(env)
    ids = [o["id"] for o in opts]
    for opt in list(on) + list(off):
        if opt not in ids:
            raise Usage(msg("unknown-option", opt=opt, title=title(env), ids=", ".join(ids)) if ids
                        else msg("no-options", opt=opt, title=title(env)))
    for opt in on:
        if opt in off:
            raise Usage(msg("option-twice", opt=opt))
    for o in opts:
        if o["id"] in on and source_missing(o):
            raise Usage(msg("no-source", opt=o["id"], what=pick(o["source"].get("title")) or o["id"],
                            path=o["source"]["path"]))
    want = {o["id"]: o.get("default", True) is not False and not source_missing(o) for o in opts}
    want.update({i: True for i in on})
    want.update({i: False for i in off})
    changed = True
    while changed:  # a chain of needs settles in a few passes
        changed = False
        for o in opts:
            needs = o.get("needs") or []
            if want[o["id"]] and any(want.get(n) is False for n in needs if isinstance(n, str)):
                want[o["id"]] = False
                changed = True
    return {"on": [i for i in ids if want[i]], "off": [i for i in ids if not want[i]]}


def id_list(value):
    """--with a,b (also given twice) -> ["a", "b"]."""
    return [i.strip() for v in value or [] for i in v.split(",") if i.strip()]


# --- the processes that start an environment ----------------------------------

def paths(eid):
    d = state_dir()
    return {"pid": os.path.join(d, f"{eid}.pid"), "failed": os.path.join(d, f"{eid}.failed"),
            "progress": os.path.join(d, f"{eid}.progress.json"), "log": os.path.join(d, f"{eid}.log"),
            "options": os.path.join(d, f"{eid}.options.json")}


def alive(pid):
    if pid <= 0:
        return False
    if WINDOWS:
        import ctypes
        k = ctypes.windll.kernel32
        h = k.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
        if not h:
            return False
        code = ctypes.c_ulong()
        ok = k.GetExitCodeProcess(h, ctypes.byref(code))
        k.CloseHandle(h)
        return bool(ok) and code.value == 259  # STILL_ACTIVE
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def running_pid(eid):
    try:
        with open(paths(eid)["pid"]) as f:
            pid = int(f.read().strip() or 0)
    except (OSError, ValueError):
        return 0
    return pid if alive(pid) else 0


def bash():
    """Git for Windows' bash on Windows (not WSL's bash.exe), else bash."""
    if os.environ.get("LAUNCH_BASH"):
        return os.environ["LAUNCH_BASH"]
    if WINDOWS:
        for p in (r"C:\Program Files\Git\bin\bash.exe", r"C:\Program Files (x86)\Git\bin\bash.exe",
                  os.path.join(os.environ.get("LOCALAPPDATA", ""), r"Programs\Git\bin\bash.exe")):
            if os.path.exists(p):
                return p
        sys.exit(msg("no-bash"))
    return shutil.which("bash") or "/bin/bash"


def run_env(eid, verb, extra_env=None):
    """Run environments/<id>/run.sh <verb> [progress file]; return its exit code."""
    p = paths(eid)
    env = dict(os.environ)
    env["LAUNCH_PYTHON"] = sys.executable
    env["LAUNCH_STATE"] = state_dir()
    env["PATH"] = os.path.join(ROOT, "bin") + os.pathsep + env.get("PATH", "")
    env.update(extra_env or {})
    # For the Launcher's browser test: <state>/<id>-fail-at makes that step fail.
    try:
        with open(os.path.join(state_dir(), f"{eid}-fail-at")) as f:
            env[f"{eid.upper().replace('-', '_')}_FAIL_AT"] = f.read().strip()
    except OSError:
        pass
    args = [bash(), os.path.join(ENVS, eid, "run.sh"), verb] + ([p["progress"]] if verb == "start" else [])
    with open(p["log"], "a", encoding="utf-8", errors="replace") as log:
        log.write(f"\n== {time.strftime('%Y-%m-%d %H:%M:%S')} {verb}\n")
        log.flush()
        return subprocess.call(args, stdout=log, stderr=subprocess.STDOUT, env=env, cwd=os.path.join(ENVS, eid),
                               creationflags=NO_WINDOW)


def options_env(eid):
    """LAUNCH_OPTIONS_ON/OFF (and LAUNCH_SOURCE_<ID>) for run.sh from <state>/<id>.options.json;
    {} without it (all defaults)."""
    try:
        with open(paths(eid)["options"], encoding="utf-8") as f:
            o = json.load(f)
        out = {"LAUNCH_OPTIONS_ON": ",".join(o.get("on", [])), "LAUNCH_OPTIONS_OFF": ",".join(o.get("off", []))}
    except (OSError, ValueError, AttributeError, TypeError):
        return {}
    try:
        out.update(source_env(env_of(eid), o))
    except Usage:
        pass
    return out


def supervise(eid):
    """The background process of a start: runs the start, then records how it went."""
    p = paths(eid)
    with open(p["pid"], "w") as f:
        f.write(str(os.getpid()))
    for k in ("failed",):
        if os.path.exists(p[k]):
            os.remove(p[k])
    rc = run_env(eid, "start", options_env(eid))
    if rc != 0:
        open(p["failed"], "w").close()
    elif os.path.exists(p["progress"]):
        os.remove(p["progress"])
    os.remove(p["pid"])


def spawn_supervisor(eid):
    args = [sys.executable, os.path.abspath(__file__), "supervise", eid]
    kw = {"stdin": subprocess.DEVNULL, "stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL, "cwd": ROOT}
    if WINDOWS:
        kw["creationflags"] = 0x00000008 | 0x00000200 | 0x08000000  # DETACHED, NEW_PROCESS_GROUP, NO_WINDOW
    else:
        kw["start_new_session"] = True
    proc = subprocess.Popen(args, **kw)
    # The pid file is the supervisor's own; wait until it is there.
    for _ in range(50):
        if running_pid(eid):
            return
        time.sleep(0.1)
    with open(paths(eid)["pid"], "w") as f:
        f.write(str(proc.pid))


def kill_tree(pid):
    if WINDOWS:
        subprocess.call(["taskkill", "/PID", str(pid), "/T", "/F"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                        creationflags=NO_WINDOW)
    else:
        try:
            os.killpg(pid, 15)
        except OSError:
            pass


# --- state -------------------------------------------------------------------
#
# Every fact is True, False or None: None is "do not know" (no answer in time),
# which the Launcher must not read as "off" (docs/launcher-behaviour.md, rule 2).

def listens(port, timeout=1.0):
    """Whether something listens on 127.0.0.1:port: True, False (refused) or None (no answer in time)."""
    with socket.socket() as s:
        if WINDOWS:
            # Windows retries a refused connect for ~2 s; without SYN retransmissions
            # (SIO_TCP_INITIAL_RTO) a closed port is refused at once.
            import ctypes

            class Rto(ctypes.Structure):
                _fields_ = [("Rtt", ctypes.c_ushort), ("MaxSynRetransmissions", ctypes.c_ubyte)]
            rto, n = Rto(0xFFFF, 0xFE), ctypes.c_ulong()  # unspecified RTT, no SYN retransmissions
            ctypes.windll.ws2_32.WSAIoctl(ctypes.c_void_p(s.fileno()), ctypes.c_ulong(0x98000011), ctypes.byref(rto),
                                          ctypes.sizeof(rto), None, 0, ctypes.byref(n), None, None)
        s.settimeout(timeout)
        try:
            s.connect(("127.0.0.1", port))
            return True
        except socket.timeout:
            return None
        except OSError:
            return False


def answers(url):
    """True: the url answers 2xx/3xx; False: refused or an error status; None: no answer in time."""
    u = urllib.parse.urlsplit(url or "")
    if u.hostname in ("localhost", "127.0.0.1"):
        # A closed port: False at once, not after a GET that tries ::1 and 127.0.0.1 in turn.
        up = listens(u.port or (443 if u.scheme == "https" else 80))
        if up is not True:
            return up
    try:
        req = urllib.request.Request(url, method="GET")
        with urllib.request.urlopen(req, timeout=4) as r:
            return 200 <= r.status < 400
    except urllib.error.HTTPError as e:
        return 200 <= e.code < 400
    except urllib.error.URLError as e:
        return None if isinstance(e.reason, (socket.timeout, TimeoutError)) else False
    except (socket.timeout, TimeoutError):
        return None
    except Exception:
        return False


def progress_of(eid):
    try:
        with open(paths(eid)["progress"], encoding="utf-8") as f:
            p = json.load(f)
        return p if isinstance(p.get("step"), int) and isinstance(p.get("steps"), int) else None
    except (OSError, ValueError):
        return None


_DOCKER = None


def docker_info():
    """The Docker daemon, once per command: {"up": True|False|None, "mem": GiB or None}.
    False: clearly not running (no docker CLI, or it cannot connect); None: no answer in time."""
    global _DOCKER
    if _DOCKER is None:
        try:
            out = subprocess.run(["docker", "info", "--format", "{{.ServerVersion}}\t{{.MemTotal}}"],
                                 capture_output=True, text=True, timeout=10, creationflags=NO_WINDOW)
            version, mem = (out.stdout.strip().split("\t") + [""])[:2]
            if out.returncode == 0 and version:
                _DOCKER = {"up": True, "mem": int(mem) / 2**30 if mem.isdigit() else None}
            else:
                said = (out.stderr or "") + (out.stdout or "")
                clear = re.search(r"connect|daemon|pipe|not running", said, re.I)
                _DOCKER = {"up": False if clear else None, "mem": None}
        except OSError:
            _DOCKER = {"up": False, "mem": None}
        except subprocess.TimeoutExpired:
            _DOCKER = {"up": None, "mem": None}
    return _DOCKER


_UNSET = object()
_CONTAINERS = _UNSET


def containers():
    """The running containers: [(name, compose project, host ports)], once per command;
    None when docker ps failed or Docker does not answer in time."""
    global _CONTAINERS
    if _CONTAINERS is _UNSET:
        up = docker_info()["up"]
        if up is False:
            _CONTAINERS = []  # no Docker, no containers
        elif up is None:
            _CONTAINERS = None  # docker ps would hang the same way
        else:
            _CONTAINERS = None
            try:
                out = subprocess.run(["docker", "ps", "--format",
                                      '{{.Names}}\t{{.Label "com.docker.compose.project"}}\t{{.Ports}}'],
                                     capture_output=True, text=True, timeout=15, creationflags=NO_WINDOW)
                if out.returncode == 0:
                    _CONTAINERS = []
                    for line in out.stdout.splitlines():
                        name, project, ports = (line.split("\t") + ["", ""])[:3]
                        published = set()
                        for a, b in re.findall(r":(\d+)(?:-(\d+))?->", ports):
                            published.update(range(int(a), int(b or a) + 1))
                        _CONTAINERS.append((name, project, published))
            except (OSError, subprocess.TimeoutExpired):
                pass
    return _CONTAINERS


def forget_measurements():
    """Measure Docker and the containers again (a long --wait)."""
    global _DOCKER, _CONTAINERS
    _DOCKER, _CONTAINERS = None, _UNSET


def own_containers(env, up):
    """Whether the environment's own compose project runs: something else on its port
    (another program, another project) is not it. Without a "project": whether it answers."""
    project = env.get("project")
    if not project:
        return up
    cs = containers()
    return None if cs is None else any(p == project for _, p, _ in cs)


def status_of(env):
    eid = env["id"]
    up = answers((env.get("health") or {}).get("url") or env["url"])
    own = own_containers(env, up)
    process = bool(running_pid(eid))
    failed = os.path.exists(paths(eid)["failed"])
    if process:
        state = "starting"
    elif failed:
        state = "failed"
    elif own is True and up is True:
        state = "ready"
    elif own is None or up is None:
        state = "unknown"
    else:
        state = "stopped"
    return {"id": eid, "url": env["url"], "state": state, "process": process, "failed": failed,
            "containers": own, "answers": up, "docker": docker_info()["up"],
            "progress": progress_of(eid) if process or failed else None}


# --- checks before a start -----------------------------------------------------

def machine_memory_gib():
    """The physical memory of this computer in GiB, or None."""
    try:
        if WINDOWS:
            import ctypes

            class Status(ctypes.Structure):
                _fields_ = [("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong)] + [
                    (n, ctypes.c_ulonglong) for n in ("ullTotalPhys", "ullAvailPhys", "ullTotalPageFile",
                                                      "ullAvailPageFile", "ullTotalVirtual", "ullAvailVirtual",
                                                      "ullAvailExtendedVirtual")]
            st = Status()
            st.dwLength = ctypes.sizeof(Status)
            if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(st)):
                return None
            return st.ullTotalPhys / 2**30
        return os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES") / 2**30
    except (OSError, ValueError, AttributeError):
        return None


def item(code, kind, text, **extra):
    return {"code": code, "kind": kind, "text": text, **extra}


def check(env):
    """What stands in the way of a start, in the order of docs section 3.2: [{code, kind, text, ...}].
    kind: precondition (cannot run on this computer), problem (the person fixes it),
    fixable (launch fix can fix it too). The texts are in LANG."""
    needs = env.get("needs") or {}
    need = needs.get("memory_gib", 0)
    name = title(env)
    if needs.get("platform") == "dawo-usb":
        # Not Docker on this computer at all: nothing else here applies.
        return [item("platform", "precondition", msg("platform", title=name))]
    items = []
    have = machine_memory_gib()
    if have is not None and have + 0.5 < need:
        items.append(item("machine-memory", "precondition", msg("machine-memory", have=have, title=name, need=need),
                          data={"have": round(have), "need": need}))
    docker = docker_info()
    if docker["up"] is not True:
        # The other checks need Docker: stop here.
        items.append(item("runtime-down", "problem",
                          msg("runtime-down" if docker["up"] is False else "runtime-silent")))
        return items
    mem = docker["mem"]
    if mem is not None and mem + 0.5 < need:
        items.append(item("docker-memory", "problem", msg("docker-memory", have=mem, title=name, need=need),
                          data={"have": round(mem), "need": need}))
    items += busy_ports(env)
    for c in failing_checks(env):
        code = "check:" + c.get("id", "")
        problem = pick(c.get("problem"))
        if has_fix(c):
            items.append(item(code, "fixable", problem, fix=pick(c["fix"].get("text")) or problem))
        else:
            items.append(item(code, "problem", problem))
    return items


def ports_of(env):
    """The ports on this computer the environment's addresses use (its url, health,
    links on localhost) and needs.ports."""
    ports = set((env.get("needs") or {}).get("ports", []))
    for url in [env.get("url"), (env.get("health") or {}).get("url")] + [l.get("url") for l in env.get("links", [])]:
        u = urllib.parse.urlsplit(url or "")
        if u.hostname in ("localhost", "127.0.0.1"):
            ports.add(u.port or (443 if u.scheme == "https" else 80))
    return sorted(ports)


def busy_ports(env):
    """A port-in-use item per program that holds a port the environment needs. Launch's own
    environments do not count: a start refuses while another one runs."""
    ours = {e.get("project") for e in catalog() if e.get("project")}
    holders = {}
    for port in ports_of(env):
        if not listens(port, 0.5):
            continue  # free (or no answer: not held either)
        held = [(n, p) for n, p, published in containers() or [] if port in published]
        if held and all(p in ours for _, p in held):
            continue
        holders.setdefault(held[0][0] if held else "", []).append(port)
    problems = []
    for name, ports in holders.items():
        key = ("port-container-" if name else "port-program-") + ("1" if len(ports) == 1 else "n")
        text = msg(key, ports=", ".join(map(str, ports)), name=name, title=title(env))
        problems.append(item("port-in-use", "problem", text, data={"ports": ports, "holder": name or None}))
    return problems


# --- known problems and their fixes ---------------------------------------------
#
# An environment lists its known problems in environment.json, each with how to
# see it, what to tell, and (optionally) the fix that solves it:
#
#   "checks": [{
#     "id": "identity",
#     "when": {"host_unknown": "identity"}            this name does not resolve
#           | {"command": ["check.sh", "arg"]},       or: this exits non-zero
#     "problem": "what is wrong, for the person",     (or {"nl": ..., "en": ...})
#     "fix": {
#       "text": "what the fix changes on this computer (shown before it runs)",
#       "run": ["../../tools/hostnamen.ps1", "identity"],
#       "os": ["windows"]                              optional: where it works
#     }
#   }]
#
# Paths in "command" and "run" are relative to the environment's directory;
# .ps1 runs with PowerShell, .sh with bash, anything else as a program. A start
# never runs a fix: it names the problem and `launch fix <id>`, which runs
# them (the Launcher says first what they change). Whether a fix worked is the
# check, run again.

def this_os():
    return "windows" if WINDOWS else "macos" if sys.platform == "darwin" else "linux"


def script(env, cmd):
    """The command line for cmd (a list, its first item a path in the environment)."""
    path = os.path.normpath(os.path.join(ENVS, env["id"], cmd[0]))
    if path.endswith(".ps1"):
        return ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", path, *cmd[1:]]
    if path.endswith(".sh"):
        return [bash(), path, *cmd[1:]]
    return [path, *cmd[1:]]


def fails(env, c):
    """Whether check c finds its problem on this machine."""
    when = c.get("when") or {}
    if "host_unknown" in when:
        try:
            socket.gethostbyname(when["host_unknown"])
            return False
        except OSError:
            return True
    if "command" in when:
        try:
            return subprocess.run(script(env, when["command"]), capture_output=True, creationflags=NO_WINDOW,
                                  timeout=60, stdin=subprocess.DEVNULL).returncode != 0
        except (OSError, subprocess.TimeoutExpired):
            return True
    return False


def failing_checks(env):
    return [c for c in env.get("checks", []) if isinstance(c, dict) and fails(env, c)]


def has_fix(c):
    f = c.get("fix") or {}
    return bool(f.get("run")) and this_os() in f.get("os", [this_os()])


def fix(env):
    """Run the fixes of the failing checks; return the ids that still fail."""
    failed = []
    for c in failing_checks(env):
        if not has_fix(c):
            continue
        try:
            # A fix may ask Windows for administrator rights; it waits for the answer.
            subprocess.run(script(env, c["fix"]["run"]), capture_output=True, creationflags=NO_WINDOW,
                           timeout=300, stdin=subprocess.DEVNULL)
        except (OSError, subprocess.TimeoutExpired):
            pass  # nobody answered; the check below says so
        if fails(env, c):
            failed.append(c.get("id", ""))
    return failed


# --- commands ------------------------------------------------------------------

def check_rc(items):
    """The exit code of a check: 4 a precondition, 3 a problem, 0 all well."""
    if any(i["kind"] == "precondition" for i in items):
        return 4
    return 3 if items else 0


def cmd_list(as_json):
    envs = catalog()
    if as_json:
        print(json.dumps({"schema": 1, "environments": envs}, ensure_ascii=False))
    else:
        for e in envs:
            print(f"{e['id']:<24} {title(e)} ({pick(e.get('version'))})")
    return 0


def cmd_status(eid, as_json):
    envs = [env_of(eid)] if eid else catalog()
    out = [status_of(e) for e in envs]
    if as_json:
        print(json.dumps(out, ensure_ascii=False))
        return 0
    for s, e in zip(out, envs):
        text = msg("state-" + s["state"], url=e["url"])
        p = s["progress"]
        if s["state"] == "starting" and p:
            text += " (" + msg("step", step=p["step"], steps=p["steps"], label=p.get("label", "")) + ")"
        print(f"{title(e)}: {text}")
    return 0


def cmd_check(eid, as_json):
    items = check(env_of(eid))
    if as_json:
        print(json.dumps({"ok": not items, "items": items}, ensure_ascii=False))
    elif not items:
        print(msg("all-well"))
    else:
        print("\n".join("- " + i["text"] for i in items))
        if any(i["kind"] == "fixable" for i in items):
            print(msg("fix-hint", cmd=CMD, id=eid))
    return check_rc(items)


def cmd_fix(eid):
    failed = fix(env_of(eid))
    print(msg("fixed") if not failed else msg("fix-failed"))
    return 0 if not failed else 1


def cmd_start(eid, on=(), off=(), wait=False):
    env = env_of(eid)
    chosen = resolve_options(env, on, off)  # wrong use first, before anything is measured
    if running_pid(eid):
        print(msg("already-starting", title=title(env)))
        return wait_for(env) if wait else 0
    # It never fixes anything itself: it names what is wrong and stops.
    items = check(env)
    pre = [i for i in items if i["kind"] == "precondition"]
    if pre:
        print(msg("cannot-run") + "\n- " + "\n- ".join(i["text"] for i in pre))
        return 4
    if items:
        print(msg("not-started") + "\n- " + "\n- ".join(i["text"] for i in items))
        if any(i["kind"] == "fixable" for i in items):
            print(msg("fix-hint", cmd=CMD, id=eid))
        return 3
    # One at a time: another one that runs is the person's to stop, not ours.
    for other in catalog():
        if other["id"] == eid:
            continue
        st = status_of(other)
        if st["state"] in ("starting", "ready", "failed"):
            print(msg("other-running", title=title(other), cmd=CMD, id=other["id"]))
            return 5
        if st["state"] == "stopped" and st["containers"] is True and other.get("project"):
            cmd_stop(other["id"], quiet=True)  # leftovers that do not answer would hold the ports
    # The supervisor is a separate process: it reads the choices from this file.
    with open(paths(eid)["options"], "w", encoding="utf-8") as f:
        json.dump(chosen, f)
    spawn_supervisor(eid)
    print(msg("starting", title=title(env), cmd=CMD, id=eid))
    if not wait:
        print(msg("open-at", url=env["url"]))
        return 0
    return wait_for(env)


def wait_for(env, interval=3.0):
    """--wait: until the start process ends; 0 when it is ready then, else 1."""
    eid, shown = env["id"], None
    while running_pid(eid):
        p = progress_of(eid)
        if p and (p["step"], p.get("label")) != shown:
            shown = (p["step"], p.get("label"))
            print(msg("step", step=p["step"], steps=p["steps"], label=p.get("label", "")), flush=True)
        time.sleep(interval)
    if not os.path.exists(paths(eid)["failed"]):
        for _ in range(3):  # "do not know" is asked again, a few times
            forget_measurements()
            if status_of(env)["state"] == "ready":
                print(msg("ready-at", title=title(env), url=env["url"]))
                return 0
            time.sleep(interval)
    print(msg("start-failed", title=title(env), id=eid))
    return 1


def cmd_stop(eid, quiet=False):
    env = env_of(eid)
    pid = running_pid(eid)
    if pid:
        kill_tree(pid)
    run_env(eid, "stop")
    p = paths(eid)
    for k in ("pid", "failed", "progress"):
        if os.path.exists(p[k]):
            os.remove(p[k])
    # A stop that left it running must say so: the Launcher shows the error
    # instead of waiting for a stop that never comes.
    if answers((env.get("health") or {}).get("url") or env["url"]) is True:
        print(msg("still-running", title=title(env), id=eid))
        return 1
    if not quiet:
        print(msg("stopped", title=title(env)))
    return 0


EXIT_CODES = """exit codes:
  0  done
  1  failed
  2  wrong use (an unknown option or environment)
  3  a problem; the start did not begin (also: Docker does not run)
  4  a precondition: cannot run on this computer
  5  busy: another environment runs"""


def parser():
    ap = argparse.ArgumentParser(
        prog="launch", formatter_class=argparse.RawDescriptionHelpFormatter, epilog=EXIT_CODES,
        description="Start, stop and check the environments in environments/, one at a time.")
    ap.add_argument("--version", action="version", version=f"launch {VERSION}")
    sub = ap.add_subparsers(dest="command", metavar="<command>", required=True)

    def command(name, text):
        return sub.add_parser(name, help=text, description=text, epilog=EXIT_CODES,
                              formatter_class=argparse.RawDescriptionHelpFormatter)

    p = command("list", "the environments in environments/")
    p.add_argument("--json", action="store_true", help="for the Launcher: {schema, environments}")
    p = command("status", "what runs, and its state")
    p.add_argument("id", nargs="?", metavar="<id>", help="only this environment")
    p.add_argument("--json", action="store_true", help="for the Launcher: a list of facts per environment")
    p = command("check", "preconditions and problems, without starting")
    p.add_argument("id", metavar="<id>")
    p.add_argument("--json", action="store_true", help="for the Launcher: {ok, items}")
    p.add_argument("--lang", choices=LANGS, help="the language of the texts (default: the computer's)")
    p = command("fix", "fix what Launch can fix")
    p.add_argument("id", metavar="<id>")
    p = command("start", "start an environment (it fixes nothing itself)")
    p.add_argument("id", metavar="<id>")
    p.add_argument("--with", dest="on", action="append", metavar="X,Y", help="switch these choices on")
    p.add_argument("--without", dest="off", action="append", metavar="X,Y", help="switch these choices off")
    p.add_argument("--wait", action="store_true", help="wait until it is ready (0) or failed (1)")
    p = command("stop", "stop an environment and clean up")
    p.add_argument("id", metavar="<id>")
    # The background process of a start; not for people (no help: not listed).
    p = sub.add_parser("supervise")
    p.add_argument("id", metavar="<id>")
    return ap


def main(argv):
    global LANG
    args = parser().parse_args(argv)  # wrong use: argparse exits with 2
    LANG = choose_lang(getattr(args, "lang", None))
    try:
        if args.command == "list":
            return cmd_list(args.json)
        if args.command == "status":
            return cmd_status(args.id, args.json)
        if args.command == "check":
            return cmd_check(args.id, args.json)
        if args.command == "fix":
            return cmd_fix(args.id)
        if args.command == "start":
            return cmd_start(args.id, id_list(args.on), id_list(args.off), args.wait)
        if args.command == "stop":
            return cmd_stop(args.id)
        supervise(args.id)
        return 0
    except Usage as e:
        print(e, file=sys.stderr)
        return 2


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(errors="replace")  # a console that cannot show "één" gets "?", not a traceback
    sys.exit(main(sys.argv[1:]))
