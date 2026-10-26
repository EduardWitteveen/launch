"""Loads case packages from a source into a running GZAC (Valtimo 13) (docs §6, §8.10).

  python3 gzac-cases.py <packages folder> <state folder> [--keycloak URL] [--realm R]
                        [--backend URL] [--client ID]

The defaults are Valtimo's (upstream's gzac-docker-compose): Keycloak http://localhost:8081/auth,
realm valtimo, backend http://localhost:8080/api, client valtimo-console. Tezza passes its own
(Keycloak http://identity:8181/auth, realm tezza, backend http://localhost:9090/api) and
--profiles: its roles come from Contezza's authorization profiles, which set them in Keycloak
when they are published, so the roles are made as profiles there instead.

Every folder in <packages folder> with a package.json is one case package:

  package.json   {"key", "versionTag", "title", "process", "document",
                  "roles": {"ROLE_X": "description", ...}, "demo": "demo/cases.json"}
  case/...       the case definition in Valtimo's own layout (case/definition, bpmn, dmn, form,
                 process-link, ...), imported as config/case/<key>/<version>/...
  demo/...       optional: [{"content": {...}, "steps": [{"task": "<activity id>", "variables": {...}}]}]

Per package: the roles in Keycloak (realm valtimo, given to the logins launcher and admin)
and in Valtimo's access control (the permissions of ROLE_USER), the import of the case
definition (Valtimo skips a key and version it has already), and once the demo cases: each
one is started with its content and then moved on by completing its steps' tasks in order.

Only this machine's ports. Uses the products' own demo accounts: admin / admin for Keycloak's
master realm, the login launcher / launcher (Launch's own, docs §3) for GZAC.

SPDX-License-Identifier: EUPL-1.2
"""
import argparse
import io
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
import zipfile

KEYCLOAK = "http://localhost:8081/auth"
BACKEND = "http://localhost:8080/api"
REALM = "valtimo"
CLIENT = "valtimo-console"
LOGINS = ("launcher", "admin")


def info(text):
    print(f"    {text}", flush=True)


def call(method, url, token=None, body=None, form=None, raw=None, ctype=None, ok=(200, 201, 204)):
    """(status, parsed JSON or text); body is JSON, form is urlencoded, raw is bytes with ctype."""
    headers = {"Accept": "application/json"}
    data = None
    if token:
        headers["Authorization"] = f"Bearer {token}"
    if body is not None:
        data, headers["Content-Type"] = json.dumps(body).encode(), "application/json"
    elif form is not None:
        data, headers["Content-Type"] = urllib.parse.urlencode(form).encode(), "application/x-www-form-urlencoded"
    elif raw is not None:
        data, headers["Content-Type"] = raw, ctype
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            status, text = r.status, r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        status, text = e.code, e.read().decode("utf-8", "replace")
    try:
        parsed = json.loads(text) if text else None
    except ValueError:
        parsed = text
    if ok and status not in ok:
        raise RuntimeError(f"{method} {url}: {status} {str(text)[:300]}")
    return status, parsed


def token(realm, client, user, password):
    _, t = call("POST", f"{KEYCLOAK}/realms/{realm}/protocol/openid-connect/token",
                form={"grant_type": "password", "client_id": client, "username": user, "password": password})
    return t["access_token"]


# --- roles -------------------------------------------------------------------------

def keycloak_roles(roles):
    """The realm roles, given to the logins launcher and admin where they exist (their next
    token has them)."""
    kc = token("master", "admin-cli", "admin", "admin")
    realm = f"{KEYCLOAK}/admin/realms/{REALM}"
    for name, about in roles.items():
        call("POST", f"{realm}/roles", kc, {"name": name, "description": about}, ok=(201, 409))
    reps = [call("GET", f"{realm}/roles/{urllib.parse.quote(n)}", kc)[1] for n in roles]
    for login in LOGINS:
        _, users = call("GET", f"{realm}/users?exact=true&username={login}", kc)
        if users:
            call("POST", f"{realm}/users/{users[0]['id']}/role-mappings/realm", kc,
                 [{"id": r["id"], "name": r["name"]} for r in reps])
    info(f"Keycloak: {len(roles)} roles, given to {' and '.join(LOGINS)}")


def profile_roles(url, roles):
    """Tezza: each role a published authorization profile with the logins launcher and admin."""
    tok = token(REALM, "alfresco-client", "admin", "admin")
    _, have = call("GET", f"{url}?size=1000", tok)
    have = {p["name"]: p for p in (have or {}).get("data", [])}
    users = [{"id": "launcher", "displayName": "Launcher Demo"}, {"id": "admin", "displayName": "Administrator"}]
    for name, about in roles.items():
        p = have.get(name)
        if p is None:
            _, p = call("POST", url, tok, {"name": name, "description": about, "users": users,
                                           "sites": [], "userGroups": []})
        else:
            ids = {u["id"] for u in p.get("users") or []}
            missing = [u for u in users if u["id"] not in ids]
            if missing:
                p["users"] = (p.get("users") or []) + missing
                for k in ("sites", "userGroups"):  # its update fails on a missing list
                    p.setdefault(k, [])
                call("PUT", f"{url}/{p['id']}", tok, {k: v for k, v in p.items() if k != "metadata"})
        call("POST", f"{url}/{(p or {}).get('id') or find(p, 'id')}/publish", tok)
    info(f"authorization profiles: {len(roles)} roles, published, with launcher and admin")


def valtimo_roles(tok, roles):
    """Valtimo's access control: each new role gets ROLE_USER's permissions (with its own name)."""
    try:
        _, have = call("GET", f"{BACKEND}/management/v1/roles", tok)
        have = {r.get("roleKey") for r in have or [] if isinstance(r, dict)}
        _, base = call("GET", f"{BACKEND}/management/v1/roles/ROLE_USER/permissions", tok)
    except RuntimeError as e:
        info(f"Valtimo roles: not set ({e}); the login launcher sees everything as admin")
        return
    for name in roles:
        if name not in have:
            call("POST", f"{BACKEND}/management/v1/roles", tok, {"roleKey": name}, ok=(200, 201, 409))
        perms = json.loads(json.dumps(base).replace('"ROLE_USER"', json.dumps(name)))
        for p in perms if isinstance(perms, list) else []:
            p.pop("id", None)
            p.pop("role", None)
        try:
            call("PUT", f"{BACKEND}/management/v1/roles/{urllib.parse.quote(name)}/permissions", tok, perms)
        except RuntimeError as e:
            info(f"Valtimo role {name}: permissions not set ({str(e)[:120]})")
    info(f"Valtimo: {len(roles)} roles with the permissions of ROLE_USER")


# --- the case definition ----------------------------------------------------------------

def case_zip(folder, key, version):
    """case/... as config/case/<key>/<1-0-0>/... in a zip, as Valtimo's import wants it."""
    buf = io.BytesIO()
    root = os.path.join(folder, "case")
    prefix = f"config/case/{key}/{version.replace('.', '-')}"
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for d, _, files in os.walk(root):
            for f in sorted(files):
                path = os.path.join(d, f)
                z.write(path, f"{prefix}/{os.path.relpath(path, root).replace(os.sep, '/')}")
    return buf.getvalue()


def multipart(name, filename, data):
    b = uuid.uuid4().hex
    body = (f"--{b}\r\nContent-Disposition: form-data; name=\"{name}\"; filename=\"{filename}\"\r\n"
            "Content-Type: application/zip\r\n\r\n").encode() + data + f"\r\n--{b}--\r\n".encode()
    return body, f"multipart/form-data; boundary={b}"


def import_case(tok, folder, pkg):
    body, ctype = multipart("file", f"{pkg['key']}.valtimo.zip", case_zip(folder, pkg["key"], pkg["versionTag"]))
    call("POST", f"{BACKEND}/management/v1/case/import", tok, raw=body, ctype=ctype)
    info(f"case {pkg['key']} {pkg['versionTag']} imported (a version Valtimo has already stays as it is)")


# --- demo cases -----------------------------------------------------------------------------

def find(obj, *keys):
    """The first value under one of keys, anywhere in obj."""
    if isinstance(obj, dict):
        for k in keys:
            if k in obj and obj[k] not in (None, ""):
                return obj[k]
        for v in obj.values():
            r = find(v, *keys)
            if r is not None:
                return r
    elif isinstance(obj, list):
        for v in obj:
            r = find(v, *keys)
            if r is not None:
                return r
    return None


def open_tasks(tok, pid):
    _, tasks = call("GET", f"{BACKEND}/v1/process/{pid}/tasks", tok)
    out = {}
    for t in tasks or []:
        dto = t.get("taskDto", t) if isinstance(t, dict) else {}
        key, tid = dto.get("taskDefinitionKey") or dto.get("definitionKey"), dto.get("id")
        if key and tid:
            out[key] = tid
    return out


def wait_task(tok, pid, key, seconds=90):
    """The task id of activity key once it is open (a timer that is due fires a little later)."""
    end = time.time() + seconds
    while True:
        tasks = open_tasks(tok, pid)
        if key in tasks:
            return tasks[key]
        if time.time() > end:
            raise RuntimeError(f"task '{key}' does not open (open: {', '.join(tasks) or 'none'})")
        time.sleep(2)


def demo(tok, folder, pkg, progress):
    """Each case once. progress has per case its process instance and the steps done, so a
    start that broke off goes on where it was (and makes no case twice)."""
    with open(os.path.join(folder, pkg["demo"]), encoding="utf-8") as f:
        cases = json.load(f)
    try:
        with open(progress, encoding="utf-8") as f:
            done = json.load(f)
        done = done if isinstance(done, dict) else {}
    except (OSError, ValueError):
        done = {}

    def save():
        with open(progress, "w", encoding="utf-8") as f:
            json.dump(done, f, ensure_ascii=False, indent=1)

    # Completing needs the task assigned to oneself; assigning takes the user's id in GZAC
    # (its /v1/users/<username>; Valtimo may also take the username itself).
    _, me = call("GET", f"{BACKEND}/v1/users/{LOGINS[0]}", tok, ok=None)
    me = (me or {}).get("id") if isinstance(me, dict) else None
    me = me or LOGINS[0]
    made = 0
    for c in cases:
        name = c["content"].get("titel") or c["content"].get("omschrijving") or "?"
        state = done.get(name)
        if state and state.get("steps", 0) >= len(c["steps"]):
            continue
        if state:
            pid = state["pid"]
        else:
            pid = start_case(tok, pkg, c["content"], name)
            done[name] = state = {"pid": pid, "steps": 0}
            save()
        for step in c["steps"][state["steps"]:]:
            tid = wait_task(tok, pid, step["task"])
            call("POST", f"{BACKEND}/v1/task/{tid}/assign", tok, {"assignee": me})
            call("POST", f"{BACKEND}/v1/task/{tid}/complete", tok, {"variables": step["variables"], "filesToDelete": []})
            state["steps"] += 1
            save()
        made += 1
        info(f"{name[:60]}: {len(c['steps'])} steps")
    info(f"{made} demo cases of {pkg['key']} made or finished ({len(done)} in all)")


def start_case(tok, pkg, content, name):
    """A new case with its content; its process instance id."""
    _, r = call("POST", f"{BACKEND}/v1/process-document/operation/new-document-and-start-process", tok, {
        "processDefinitionKey": pkg["process"],
        "request": {"definition": pkg["document"], "caseDefinitionKey": pkg["key"],
                    "caseDefinitionVersionTag": pkg["versionTag"], "content": content}})
    pid = find(r, "resultingProcessInstanceId", "processInstanceId")
    pid = pid.get("id") if isinstance(pid, dict) else pid
    if not pid:
        raise RuntimeError(f"{name}: no process instance in the answer ({str(r)[:200]})")
    return pid


def main(argv):
    global KEYCLOAK, BACKEND, REALM, CLIENT
    ap = argparse.ArgumentParser(prog="gzac-cases.py")
    ap.add_argument("packages")
    ap.add_argument("state")
    ap.add_argument("--keycloak", default=KEYCLOAK)
    ap.add_argument("--realm", default=REALM)
    ap.add_argument("--backend", default=BACKEND)
    ap.add_argument("--client", default=CLIENT)
    ap.add_argument("--profiles", help="Contezza's authorization profiles API (Tezza)")
    a = ap.parse_args(argv[1:])
    KEYCLOAK, BACKEND, REALM, CLIENT = a.keycloak.rstrip("/"), a.backend.rstrip("/"), a.realm, a.client
    src, state = a.packages, a.state
    pkgs = sorted(d for d in os.listdir(src) if os.path.isfile(os.path.join(src, d, "package.json")))
    if not pkgs:
        info(f"no case packages in {src}")
        return 0
    failed = []
    for name in pkgs:
        folder = os.path.join(src, name)
        with open(os.path.join(folder, "package.json"), encoding="utf-8") as f:
            pkg = json.load(f)
        print(f"  {pkg.get('title') or pkg['key']} ({name})", flush=True)
        try:
            if pkg.get("roles"):
                profile_roles(a.profiles, pkg["roles"]) if a.profiles else keycloak_roles(pkg["roles"])
            tok = token(REALM, CLIENT, LOGINS[0], "launcher")
            if pkg.get("roles"):
                valtimo_roles(tok, pkg["roles"])
            import_case(tok, folder, pkg)
            done = os.path.join(state, f"case-{pkg['key']}-{pkg['versionTag']}.demo.done")
            if pkg.get("demo") and not os.path.exists(done):
                demo(tok, folder, pkg, done.replace(".done", ".progress.json"))
                with open(done, "w") as f:
                    f.write(time.strftime("%Y-%m-%d %H:%M:%S\n"))
            elif pkg.get("demo"):
                info("demo cases: made before")
        except (RuntimeError, OSError, KeyError, ValueError) as e:
            failed.append(name)
            info(f"not loaded: {e}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
