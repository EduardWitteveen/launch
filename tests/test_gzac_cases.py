"""Unit tests for lib/gzac-cases.py: the zip Valtimo imports, and how the
demo cases are started and moved on. A made-up package and a fake backend; no network.

  python -m unittest tests/test_valtimo_cases.py

SPDX-License-Identifier: EUPL-1.2
"""
import importlib.util
import io
import json
import os
import tempfile
import unittest
import zipfile
from contextlib import redirect_stdout
from unittest import mock

_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "lib", "gzac-cases.py")
_spec = importlib.util.spec_from_file_location("gzac_cases", _PATH)
L = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(L)

PKG = {"key": "voorbeeld", "versionTag": "1.2.0", "title": "Voorbeeld", "process": "voorbeeld",
       "document": "voorbeeld", "roles": {"ROLE_A": "a"}, "demo": "demo/cases.json"}
CASES = [{"content": {"titel": "Eerste"}, "steps": [{"task": "a", "variables": {"x": 1}},
                                                     {"task": "b", "variables": {}}]},
         {"content": {"titel": "Tweede"}, "steps": []}]


def package(root):
    d = os.path.join(root, "voorbeeld")
    for rel, text in {"package.json": json.dumps(PKG), "demo/cases.json": json.dumps(CASES),
                      "case/bpmn/voorbeeld.bpmn": "<x/>",
                      "case/case/definition/voorbeeld.case-definition.json": "{}"}.items():
        os.makedirs(os.path.dirname(os.path.join(d, rel)), exist_ok=True)
        with open(os.path.join(d, rel), "w", encoding="utf-8") as f:
            f.write(text)
    return d


class FakeBackend:
    """Answers load.call: a process instance per new case, its tasks opening one by one."""

    def __init__(self):
        self.calls, self.open = [], {}

    def __call__(self, method, url, token=None, body=None, **kw):
        self.calls.append((method, url.replace(L.BACKEND, ""), body))
        if url.endswith("/new-document-and-start-process"):
            pid = f"p{len([c for c in self.calls if c[1].endswith('start-process')])}"
            steps = next(c["steps"] for c in CASES if c["content"] == body["request"]["content"])
            self.open[pid] = [s["task"] for s in steps]
            return 200, {"resultingDocument": {"id": "d"}, "resultingProcessInstanceId": {"id": pid}}
        if "/tasks" in url:
            pid = url.split("/process/")[1].split("/")[0]
            return 200, [{"taskDto": {"id": f"{pid}-{t}", "taskDefinitionKey": t}} for t in self.open[pid][:1]]
        if url.endswith("/v1/users/launcher"):
            return 200, {"id": "u-1", "username": "launcher"}
        if url.endswith("/assign"):
            assert body == {"assignee": "u-1"}
        if url.endswith("/complete"):
            pid, task = url.split("/task/")[1].split("/")[0].split("-", 1)
            assert self.open[pid][0] == task
            self.open[pid].pop(0)
        return 200, None


class Zip(unittest.TestCase):
    def test_paths_are_valtimos_own(self):
        with tempfile.TemporaryDirectory() as root:
            z = zipfile.ZipFile(io.BytesIO(L.case_zip(package(root), "voorbeeld", "1.2.0")))
            self.assertEqual(sorted(z.namelist()), [
                "config/case/voorbeeld/1-2-0/bpmn/voorbeeld.bpmn",
                "config/case/voorbeeld/1-2-0/case/definition/voorbeeld.case-definition.json"])


class Demo(unittest.TestCase):
    def test_cases_start_and_their_steps_complete_in_order_once(self):
        fake = FakeBackend()
        with tempfile.TemporaryDirectory() as root, mock.patch.object(L, "call", fake), \
                redirect_stdout(io.StringIO()):
            d = package(root)
            progress = os.path.join(root, "progress.json")
            L.demo("t", d, PKG, progress)
            started = [c for c in fake.calls if c[1].endswith("start-process")]
            self.assertEqual([c[2]["request"]["content"]["titel"] for c in started], ["Eerste", "Tweede"])
            self.assertEqual(started[0][2]["request"]["caseDefinitionVersionTag"], "1.2.0")
            done = [(c[1], c[2]) for c in fake.calls if c[1].endswith("/complete")]
            self.assertEqual(done, [("/v1/task/p1-a/complete", {"variables": {"x": 1}, "filesToDelete": []}),
                                    ("/v1/task/p1-b/complete", {"variables": {}, "filesToDelete": []})])
            # Again: nothing new (the names made are in the progress file).
            fake.calls.clear()
            L.demo("t", d, PKG, progress)
            self.assertFalse([c for c in fake.calls if c[1].endswith("start-process")])

    def test_a_task_that_does_not_open_is_an_error(self):
        with mock.patch.object(L, "open_tasks", return_value={"other": "1"}), mock.patch.object(L.time, "sleep"):
            with self.assertRaises(RuntimeError) as e:
                L.wait_task("t", "p", "a", seconds=0)
        self.assertIn("task 'a' does not open (open: other)", str(e.exception))


class Main(unittest.TestCase):
    def test_no_packages_is_fine(self):
        with tempfile.TemporaryDirectory() as root, redirect_stdout(io.StringIO()) as out:
            self.assertEqual(L.main(["load.py", root, root]), 0)
        self.assertIn("no case packages", out.getvalue())

    def test_a_failing_package_is_1_and_named(self):
        with tempfile.TemporaryDirectory() as root, redirect_stdout(io.StringIO()) as out, \
                mock.patch.object(L, "keycloak_roles", side_effect=RuntimeError("Keycloak weg")):
            package(root)
            self.assertEqual(L.main(["load.py", root, root]), 1)
        self.assertIn("not loaded: Keycloak weg", out.getvalue())


if __name__ == "__main__":
    unittest.main()
