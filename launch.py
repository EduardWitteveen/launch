#!/usr/bin/env python3
"""Launch: start, stop and list environments on this machine, one at a time.

Each environment is a directory under environments/ with an environment.json
(what the Launcher shows) and a run.sh (start | stop, written in bash) that
starts it with Docker step by step and writes the Launcher's progress file.
The Launcher (launcher/launcher.py) runs this command for its buttons.

  python launch.py                       the environments you can start
  python launch.py <id>                  start one (it fixes nothing itself)
  python launch.py --stop <id>           stop one
  python launch.py --status [--json]     which environments run, and where
  python launch.py --list [--json]       the catalogue (--json: for the Launcher)
  python launch.py --fix <id>            fix the known problems Launch can fix
  python launch.py --check <id> [--json] only check whether this machine can

A start checks first and never fixes anything. Its exit codes: 0 started (or
already starting), 3 a problem (the start did not begin; --fix <id> solves
the ones Launch can fix), 4 a precondition (cannot run on this computer),
5 another environment runs (stop it first). --yes is accepted and ignored.

Works on Windows (Docker Desktop and Git for Windows' bash) and on Linux or
macOS (docker and bash). State lives in LAUNCH_STATE (default: the user's
local application data, under launch/).

Messages are in Dutch: the people who use this are Dutch civil servants.

SPDX-License-Identifier: EUPL-1.2
"""
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

ROOT = os.path.dirname(os.path.abspath(__file__))
ENVS = os.path.join(ROOT, "environments")
WINDOWS = os.name == "nt"
# No console window flashing up for every docker or bash call.
NO_WINDOW = 0x08000000 if WINDOWS else 0


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
    for env in catalog():
        if env["id"] == eid:
            return env
    ids = ", ".join(e["id"] for e in catalog())
    sys.exit(f"launch: onbekende omgeving '{eid}'. Kies uit: {ids}")


# --- the processes that start an environment ----------------------------------

def paths(eid):
    d = state_dir()
    return {"pid": os.path.join(d, f"{eid}.pid"), "failed": os.path.join(d, f"{eid}.failed"),
            "progress": os.path.join(d, f"{eid}.progress.json"), "log": os.path.join(d, f"{eid}.log")}


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
        sys.exit("launch: Git for Windows (bash) is niet gevonden; installeer Git for Windows.")
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


def supervise(eid):
    """The background process of a start: runs the start, then records how it went."""
    p = paths(eid)
    with open(p["pid"], "w") as f:
        f.write(str(os.getpid()))
    for k in ("failed",):
        if os.path.exists(p[k]):
            os.remove(p[k])
    rc = run_env(eid, "start")
    if rc != 0:
        open(p["failed"], "w").close()
    elif os.path.exists(p["progress"]):
        os.remove(p["progress"])
    os.remove(p["pid"])


def spawn_supervisor(eid):
    args = [sys.executable, os.path.abspath(__file__), "--supervise", eid]
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
    fixable (launch --fix can fix it too)."""
    needs = env.get("needs") or {}
    need = needs.get("memory_gib", 0)
    if needs.get("platform") == "dawo-usb":
        # Not Docker on this computer at all: nothing else here applies.
        return [item("platform", "precondition",
                     f"{env['title']} werkt nog alleen vanaf de DAWO-USB-SSD (Kubernetes in een virtuele machine); "
                     "in Launch op deze computer komt het later.")]
    items = []
    have = machine_memory_gib()
    if have is not None and have + 0.5 < need:
        items.append(item("machine-memory", "precondition",
                          f"Deze computer heeft {have:.0f} GB geheugen; {env['title']} heeft er ongeveer {need} nodig.",
                          data={"have": round(have), "need": need}))
    docker = docker_info()
    if docker["up"] is not True:
        # The other checks need Docker: stop here.
        items.append(item("runtime-down", "problem",
                          "Docker draait niet. Start Docker Desktop en probeer het dan opnieuw." if docker["up"] is False
                          else "Docker antwoordt niet. Start Docker Desktop opnieuw en probeer het dan nog eens."))
        return items
    mem = docker["mem"]
    if mem is not None and mem + 0.5 < need:
        items.append(item("docker-memory", "problem",
                          f"Docker heeft {mem:.0f} GB geheugen; {env['title']} heeft er ongeveer {need} nodig "
                          "(zie de README: het geheugen van Docker vergroten).", data={"have": round(mem), "need": need}))
    items += busy_ports(env)
    for c in failing_checks(env):
        code = "check:" + c.get("id", "")
        if has_fix(c):
            items.append(item(code, "fixable", c["problem"], fix=c["fix"].get("text", c["problem"])))
        else:
            items.append(item(code, "problem", c["problem"]))
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
        which = ("Poort " if len(ports) == 1 else "De poorten ") + ", ".join(map(str, ports))
        if name:
            text = (f"{which} {'is' if len(ports) == 1 else 'zijn'} bezet door de container '{name}', "
                    f"die niet bij Launch hoort; {env['title']} heeft {'die' if len(ports) == 1 else 'ze'} "
                    f"nodig. Stop die container eerst (bijvoorbeeld: docker stop {name}).")
        else:
            text = (f"{which} {'is' if len(ports) == 1 else 'zijn'} bezet door een ander programma op deze "
                    f"computer; {env['title']} heeft {'die' if len(ports) == 1 else 'ze'} nodig. "
                    "Sluit dat programma eerst.")
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
#     "problem": "what is wrong, for the person",
#     "fix": {
#       "text": "what the fix changes on this computer (shown before it runs)",
#       "run": ["../../tools/hostnamen.ps1", "identity"],
#       "os": ["windows"]                              optional: where it works
#     }
#   }]
#
# Paths in "command" and "run" are relative to the environment's directory;
# .ps1 runs with PowerShell, .sh with bash, anything else as a program. A start
# never runs a fix: it names the problem and `launch --fix <id>`, which runs
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

def cmd_start(eid):
    env = env_of(eid)
    if running_pid(eid):
        print(f"De {env['title']}-omgeving wordt al gestart.")
        return 0
    # It never fixes anything itself: it names what is wrong and stops.
    items = check(env)
    pre = [i for i in items if i["kind"] == "precondition"]
    if pre:
        print("Kan niet draaien op deze computer:\n- " + "\n- ".join(i["text"] for i in pre))
        return 4
    if items:
        print("Niet gestart. Eerst dit oplossen:\n- " + "\n- ".join(i["text"] for i in items))
        if any(i["kind"] == "fixable" for i in items):
            print(f"Oplossen: python launch.py --fix {eid}")
        return 3
    # One at a time: another one that runs is the person's to stop, not ours.
    for other in catalog():
        if other["id"] == eid:
            continue
        st = status_of(other)
        if st["state"] in ("starting", "ready", "failed"):
            print(f"De {other['title']}-omgeving draait; stop die eerst (python launch.py --stop {other['id']}).")
            return 5
        if st["state"] == "stopped" and st["containers"] is True and other.get("project"):
            cmd_stop(other["id"], quiet=True)  # leftovers that do not answer would hold the ports
    spawn_supervisor(eid)
    print(f"De {env['title']}-omgeving wordt gestart. Volg het met: python launch.py --status")
    print(f"Daarna te openen op {env['url']}")
    return 0


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
        print(f"De {env['title']}-omgeving draait nog: stoppen lukte niet. De details staan in {eid}.log.")
        return 1
    if not quiet:
        print(f"De {env['title']}-omgeving is gestopt.")
    return 0


def main(argv):
    as_json = "--json" in argv
    argv = [a for a in argv if a != "--json"]
    argv = [a for a in argv if a != "--yes"]  # accepted and ignored: a start fixes nothing
    if not argv or argv[0] == "--list":
        envs = catalog()
        if as_json:
            print(json.dumps({"schema": 1, "environments": envs}, ensure_ascii=False))
        else:
            for e in envs:
                print(f"{e['id']:<16} {e['title']} ({e.get('version', '')})")
        return 0
    if argv[0] == "--status":
        out = [status_of(e) for e in catalog()]
        if as_json:
            print(json.dumps(out, ensure_ascii=False))
        else:
            words = {"ready": "klaar op", "starting": "wordt gestart", "failed": "starten mislukt", "stopped": "niet actief",
                     "unknown": "onbekend"}
            for s, e in zip(out, catalog()):
                print(f"{e['title']}: {words[s['state']]}" + (f" {e['url']}" if s["state"] == "ready" else ""))
        return 0
    if argv[0] == "--check":
        items = check(env_of(argv[1]))
        print(json.dumps({"ok": not items, "items": items}, ensure_ascii=False)
              if as_json else ("Alles in orde." if not items else "\n".join(i["text"] for i in items)))
        return 0 if not items else 1
    if argv[0] == "--fix":
        failed = fix(env_of(argv[1]))
        print("Opgelost." if not failed else "Niet gelukt (geen toestemming gegeven?). Probeer het opnieuw.")
        return 0 if not failed else 1
    if argv[0] == "--stop":
        return cmd_stop(argv[1])
    if argv[0] == "--supervise":
        supervise(argv[1])
        return 0
    if argv[0].startswith("-"):
        print(__doc__)
        return 2
    return cmd_start(argv[0])


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
