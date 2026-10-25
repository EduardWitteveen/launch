#!/usr/bin/env python3
"""Launch: start, stop and list environments on this machine, one at a time.

Each environment is a directory under environments/ with an environment.json
(what the Launcher shows) and a run.sh (start | stop, written in bash) that
starts it with Docker step by step and writes the Launcher's progress file.
The Launcher (launcher/launcher.py) runs this command for its buttons.

  python launch.py                       the environments you can start
  python launch.py <id> [--yes]          start one (another one stops first)
  python launch.py --stop <id>           stop one
  python launch.py --status [--json]     which environments run, and where
  python launch.py --list [--json]       the catalogue (--json: for the Launcher)
  python launch.py --fix <id>            fix the known problems (starting does too)
  python launch.py --check <id> [--json] only check whether this machine can

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

def answers(url):
    try:
        req = urllib.request.Request(url, method="GET")
        with urllib.request.urlopen(req, timeout=4) as r:
            return 200 <= r.status < 400
    except urllib.error.HTTPError as e:
        return 200 <= e.code < 400
    except Exception:
        return False


def progress_of(eid):
    try:
        with open(paths(eid)["progress"], encoding="utf-8") as f:
            p = json.load(f)
        return p if isinstance(p.get("step"), int) and isinstance(p.get("steps"), int) else None
    except (OSError, ValueError):
        return None


_CONTAINERS = None


def containers():
    """The running containers: [(name, compose project, host ports)], once per command."""
    global _CONTAINERS
    if _CONTAINERS is None:
        _CONTAINERS = []
        try:
            out = subprocess.run(["docker", "ps", "--format",
                                  '{{.Names}}\t{{.Label "com.docker.compose.project"}}\t{{.Ports}}'],
                                 capture_output=True, text=True, timeout=30, creationflags=NO_WINDOW)
            for line in out.stdout.splitlines() if out.returncode == 0 else []:
                name, project, ports = (line.split("\t") + ["", ""])[:3]
                published = set()
                for a, b in re.findall(r":(\d+)(?:-(\d+))?->", ports):
                    published.update(range(int(a), int(b or a) + 1))
                _CONTAINERS.append((name, project, published))
        except (OSError, subprocess.TimeoutExpired):
            pass
    return _CONTAINERS


def own_up(env):
    """Whether the environment answers, and (with a "project") with its own containers:
    something else on its port (another program, another project) is not it."""
    up = answers((env.get("health") or {}).get("url") or env["url"])
    project = env.get("project")
    return up and (not project or any(p == project for _, p, _ in containers()))


def status_of(env):
    eid = env["id"]
    up = own_up(env)
    if running_pid(eid):
        return {"id": eid, "url": env["url"], "state": "starting", "answers": up, "progress": progress_of(eid)}
    if os.path.exists(paths(eid)["failed"]):
        return {"id": eid, "url": env["url"], "state": "failed", "answers": up, "progress": progress_of(eid)}
    return {"id": eid, "url": env["url"], "state": "ready" if up else "stopped", "answers": up}


# --- checks before a start -----------------------------------------------------

def docker_memory_gib():
    """What Docker itself has to give to containers (its VM's budget on Windows
    and macOS; the host's own memory on Linux, which has no such VM)."""
    try:
        out = subprocess.run(["docker", "info", "--format", "{{.MemTotal}}"], capture_output=True, text=True, timeout=30,
                             creationflags=NO_WINDOW)
        return int(out.stdout.strip()) / 2**30 if out.returncode == 0 else None
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return None


def host_memory_gib():
    """This computer's own physical memory (not Docker's budget out of it),
    for the message when Docker has too little: is that Docker's setting, or
    does the computer itself not have enough? None if it could not be read."""
    try:
        if WINDOWS:
            import ctypes

            class _MemStatus(ctypes.Structure):
                _fields_ = [("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong),
                           ("ullTotalPhys", ctypes.c_uint64), ("ullAvailPhys", ctypes.c_uint64),
                           ("ullTotalPageFile", ctypes.c_uint64), ("ullAvailPageFile", ctypes.c_uint64),
                           ("ullTotalVirtual", ctypes.c_uint64), ("ullAvailVirtual", ctypes.c_uint64),
                           ("ullAvailExtendedVirtual", ctypes.c_uint64)]
            stat = _MemStatus()
            stat.dwLength = ctypes.sizeof(_MemStatus)
            if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(stat)):
                return None
            return stat.ullTotalPhys / 2**30
        if sys.platform == "darwin":
            out = subprocess.run(["sysctl", "-n", "hw.memsize"], capture_output=True, text=True, timeout=10)
            return int(out.stdout.strip()) / 2**30 if out.returncode == 0 else None
        with open("/proc/meminfo", encoding="utf-8") as f:
            for line in f:
                if line.startswith("MemTotal:"):
                    return int(line.split()[1]) / 2**20  # kB -> GiB
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return None
    return None


def project_containers(project):
    """Every container (any state, also one that never started) of a compose
    project: [{name, status, exitcode, oomkilled, error}], docker's own words."""
    try:
        out = subprocess.run(["docker", "ps", "-a", "--filter", f"label=com.docker.compose.project={project}",
                             "--format", "{{.Names}}"], capture_output=True, text=True, timeout=30,
                             creationflags=NO_WINDOW)
        names = [n for n in out.stdout.splitlines() if n] if out.returncode == 0 else []
        if not names:
            return []
        out = subprocess.run(["docker", "inspect", "--format",
                             "{{.Name}}\t{{.State.Status}}\t{{.State.ExitCode}}\t{{.State.OOMKilled}}\t{{.State.Error}}",
                             *names], capture_output=True, text=True, timeout=30, creationflags=NO_WINDOW)
    except (OSError, subprocess.TimeoutExpired):
        return []
    result = []
    for line in out.stdout.splitlines() if out.returncode == 0 else []:
        name, status, exitcode, oomkilled, error = (line.split("\t") + [""] * 5)[:5]
        result.append({"name": name.lstrip("/"), "status": status, "exitcode": exitcode,
                       "oomkilled": oomkilled == "true", "error": error})
    return result


def container_problem(c):
    """A Dutch sentence about why this container (from project_containers) is a
    problem, from docker's own state: never a guess beyond what docker itself
    reports. "" when it runs, or when it is merely still starting (its status
    may be "created" for a few seconds without that meaning anything is wrong)."""
    if c["oomkilled"]:
        return (f"is onverwacht gestopt (exitcode {c['exitcode']}, geheugen tekort / OOMKilled). Docker heeft "
                "mogelijk te weinig geheugen gekregen; zie de README (het geheugen van Docker vergroten)")
    error = c.get("error") or ""
    if re.search(r"no matching entries in passwd file|unable to find user", error, re.I):
        return ("kan niet starten: de gebruiker die erin moet draaien ontbreekt in de image. Dit kan een "
                "beschadigde lokale kopie van de image zijn; probeer de image opnieuw te downloaden (zie de README)")
    if re.search(r"rosetta error|invalid elf image|exec format error", error, re.I):
        return ("kan niet starten: de image lijkt niet te werken onder emulatie op dit platform (ARM64). Probeer "
                "de image opnieuw te downloaden (zie de README)")
    if c["status"] in ("exited", "dead"):
        return f"is gestopt (status {c['status']}, exitcode {c['exitcode']})"
    return ""


def container_problems(env):
    """Problems with this environment's own containers (its compose project): a
    container that is there but not running, with what docker itself knows
    about why - the detail a "curl: connection refused" never gives."""
    project = env.get("project")
    if not project:
        return []
    problems = []
    for c in project_containers(project):
        reason = container_problem(c)
        if reason:
            problems.append(f"De container '{c['name']}' van {env['title']} {reason}.")
    return problems


def check(env):
    problems = []
    if (env.get("needs") or {}).get("platform") == "dawo-usb":
        return [f"{env['title']} werkt nog alleen vanaf de DAWO-USB-SSD (Kubernetes in een virtuele machine); "
                "in Launch op deze computer komt het later."]
    mem = docker_memory_gib()
    if mem is None:
        problems.append("Docker draait niet. Start Docker Desktop en probeer het dan opnieuw.")
    else:
        need = (env.get("needs") or {}).get("memory_gib", 0)
        if mem + 0.5 < need:
            host = host_memory_gib()
            extra = f" (van {host:.0f} GB fysiek geheugen op deze computer)" if host else ""
            problems.append(f"Docker heeft {mem:.0f} GB geheugen{extra}; {env['title']} heeft er ongeveer {need} nodig "
                            "(zie de README: het geheugen van Docker vergroten).")
    return problems + busy_ports(env) + container_problems(env) + [c["problem"] for c in failing_checks(env)]


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
    """A problem per program that holds a port the environment needs. Launch's own
    environments do not count: a start stops the others first."""
    import socket
    ours = {e.get("project") for e in catalog() if e.get("project")}
    holders = {}
    for port in ports_of(env):
        with socket.socket() as s:
            s.settimeout(0.5)
            if s.connect_ex(("127.0.0.1", port)) != 0:
                continue  # free
        held = [(n, p) for n, p, published in containers() if port in published]
        if held and all(p in ours for _, p in held):
            continue
        holders.setdefault(held[0][0] if held else "", []).append(port)
    problems = []
    for name, ports in holders.items():
        which = ("Poort " if len(ports) == 1 else "De poorten ") + ", ".join(map(str, ports))
        if name:
            problems.append(f"{which} {'is' if len(ports) == 1 else 'zijn'} bezet door de container '{name}', "
                            f"die niet bij Launch hoort; {env['title']} heeft {'die' if len(ports) == 1 else 'ze'} "
                            f"nodig. Stop die container eerst (bijvoorbeeld: docker stop {name}).")
        else:
            problems.append(f"{which} {'is' if len(ports) == 1 else 'zijn'} bezet door een ander programma op deze "
                            f"computer; {env['title']} heeft {'die' if len(ports) == 1 else 'ze'} nodig. "
                            "Sluit dat programma eerst.")
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
#       "run": {"windows": ["../../tools/hostnamen.ps1", "identity"],   one command
#               "macos": ["../../tools/hostnamen.sh", "identity"],    per OS it
#               "linux": ["../../tools/hostnamen.sh", "identity"]}    works on
#     }
#   }]
#
# Paths in "command" and "run" are relative to the environment's directory;
# .ps1 runs with PowerShell, .sh with bash, anything else as a program. A start
# runs the fixes first (the Launcher says what they change and asks); `launch
# --fix <id>` runs them alone. Whether a fix worked is the check, run again.

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
        import socket
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


def run_of(c):
    """The fix's command for this OS: "run" is a {os: [command, args...]} map
    (one command per OS it works on); None where there is none for this OS."""
    return (c.get("fix") or {}).get("run", {}).get(this_os())


def has_fix(c):
    return bool(run_of(c))


def fixes_of(env):
    """The failing checks that have a fix here: [{id, problem, text}]."""
    return [{"id": c.get("id", ""), "problem": c["problem"], "text": c["fix"].get("text", c["problem"])}
            for c in failing_checks(env) if has_fix(c)]


def fix(env):
    """Run the fixes of the failing checks; return the ids that still fail."""
    failed = []
    for c in failing_checks(env):
        cmd = run_of(c)
        if not cmd:
            continue
        try:
            # A fix may ask for administrator rights; it waits for the answer.
            subprocess.run(script(env, cmd), capture_output=True, creationflags=NO_WINDOW,
                           timeout=300, stdin=subprocess.DEVNULL)
        except (OSError, subprocess.TimeoutExpired):
            pass  # nobody answered; the check below says so
        if fails(env, c):
            failed.append(c.get("id", ""))
    return failed


# --- commands ------------------------------------------------------------------

def cmd_start(eid, yes):
    env = env_of(eid)
    fixes = fixes_of(env)
    if fixes:
        print("Eerst lost Launch dit op:\n- " + "\n- ".join(f["text"] for f in fixes))
        fix(env)
    problems = check(env)
    if problems:
        print("Let op:\n- " + "\n- ".join(problems))
        if not yes:
            print("Niet gestart. Gebruik --yes om toch te starten.", file=sys.stderr)
            return 3
    # Every other environment stops, also one that does not answer (yet): its
    # containers would hold the ports this one needs.
    for other in catalog():
        if other["id"] != eid:
            if status_of(other)["state"] != "stopped":
                print(f"Eerst stoppen: de {other['title']}-omgeving (er draait één omgeving tegelijk).")
            cmd_stop(other["id"], quiet=True)
    if running_pid(eid):
        print(f"De {env['title']}-omgeving wordt al gestart.")
        return 0
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
    if answers((env.get("health") or {}).get("url") or env["url"]):
        print(f"De {env['title']}-omgeving draait nog: stoppen lukte niet. De details staan in {eid}.log.")
        return 1
    if not quiet:
        print(f"De {env['title']}-omgeving is gestopt.")
    return 0


def main(argv):
    as_json = "--json" in argv
    argv = [a for a in argv if a != "--json"]
    yes = "--yes" in argv
    argv = [a for a in argv if a != "--yes"]
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
            words = {"ready": "klaar op", "starting": "wordt gestart", "failed": "starten mislukt", "stopped": "niet actief"}
            for s, e in zip(out, catalog()):
                print(f"{e['title']}: {words[s['state']]}" + (f" {e['url']}" if s["state"] == "ready" else ""))
        return 0
    if argv[0] == "--check":
        env = env_of(argv[1])
        problems = check(env)
        fixes = fixes_of(env)
        # blocking: what no fix solves (the Launcher asks before it starts anyway).
        fixed = [f["problem"] for f in fixes]
        print(json.dumps({"ok": not problems, "problems": problems, "fixes": fixes,
                          "blocking": [p for p in problems if p not in fixed]}, ensure_ascii=False)
              if as_json else ("Alles in orde." if not problems else "\n".join(problems)))
        return 0 if not problems else 1
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
    return cmd_start(argv[0], yes)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
