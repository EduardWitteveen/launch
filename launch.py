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
  python launch.py --check <id> [--json] only check whether this machine can

Works on Windows (Docker Desktop and Git for Windows' bash) and on Linux or
macOS (docker and bash). State lives in LAUNCH_STATE (default: the user's
local application data, under launch/).

Messages are in Dutch: the people who use this are Dutch civil servants.

SPDX-License-Identifier: EUPL-1.2
"""
import json
import os
import shutil
import subprocess
import sys
import time
import urllib.error
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


def status_of(env):
    eid = env["id"]
    up = answers((env.get("health") or {}).get("url") or env["url"])
    if running_pid(eid):
        return {"id": eid, "url": env["url"], "state": "starting", "answers": up, "progress": progress_of(eid)}
    if os.path.exists(paths(eid)["failed"]):
        return {"id": eid, "url": env["url"], "state": "failed", "answers": up, "progress": progress_of(eid)}
    return {"id": eid, "url": env["url"], "state": "ready" if up else "stopped", "answers": up}


# --- checks before a start -----------------------------------------------------

def docker_memory_gib():
    try:
        out = subprocess.run(["docker", "info", "--format", "{{.MemTotal}}"], capture_output=True, text=True, timeout=30,
                             creationflags=NO_WINDOW)
        return int(out.stdout.strip()) / 2**30 if out.returncode == 0 else None
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return None


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
            problems.append(f"Docker heeft {mem:.0f} GB geheugen; {env['title']} heeft er ongeveer {need} nodig "
                            "(zie de README: het geheugen van Docker vergroten).")
    for host in (env.get("needs") or {}).get("host_names", []):
        try:
            import socket
            socket.gethostbyname(host)
        except OSError:
            problems.append(f"De naam '{host}' is op deze computer onbekend. Voer eenmalig tools\\hostnamen.cmd uit "
                            "(als beheerder); zonder die naam kun je niet inloggen.")
    return problems


# --- commands ------------------------------------------------------------------

def cmd_start(eid, yes):
    env = env_of(eid)
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
        problems = check(env_of(argv[1]))
        print(json.dumps({"ok": not problems, "problems": problems}, ensure_ascii=False) if as_json
              else ("Alles in orde." if not problems else "\n".join(problems)))
        return 0 if not problems else 1
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
