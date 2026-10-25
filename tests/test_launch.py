"""Unit tests for launch.py: the status facts, the check items and what a start does.
No network, Docker or real environments: subprocess, sockets and urllib are faked.

  python -m unittest tests/test_launch.py

SPDX-License-Identifier: EUPL-1.2
"""
import io
import os
import socket
import subprocess
import sys
import tempfile
import unittest
import urllib.error
from contextlib import redirect_stdout
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import launch  # noqa: E402

TEZZA = {"id": "tezza", "title": "Tezza", "url": "http://localhost:8082/", "health": {"url": "http://localhost:8082/"},
         "project": "tezza-packaging", "needs": {"memory_gib": 12},
         "checks": [{"id": "identity", "when": {"host_unknown": "identity"}, "problem": "identity onbekend",
                     "fix": {"text": "hosts aanpassen", "run": ["fix.ps1"]}},
                    {"id": "other", "when": {"host_unknown": "other"}, "problem": "other onbekend"}]}
HC = {"id": "haalcentraal", "title": "Haal Centraal", "url": "http://localhost:18795/", "project": "haalcentraal",
      "needs": {"memory_gib": 1}}
MB = {"id": "mijn-bureau", "title": "Mijn Bureau", "url": "https://bureaublad.dawo.internal",
      "needs": {"memory_gib": 16, "platform": "dawo-usb"}}


def ran(rc=0, out="", err=""):
    return subprocess.CompletedProcess([], rc, out, err)


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        env = mock.patch.dict(os.environ, {"LAUNCH_STATE": self.tmp.name})
        env.start()
        self.addCleanup(env.stop)
        self.addCleanup(self.tmp.cleanup)
        launch._DOCKER = None
        launch._CONTAINERS = launch._UNSET
        self.addCleanup(setattr, launch, "_CONTAINERS", launch._UNSET)
        self.addCleanup(setattr, launch, "_DOCKER", None)


class Answers(Base):
    def test_refused_is_false_without_a_get(self):
        with mock.patch.object(launch, "listens", return_value=False), \
                mock.patch("urllib.request.urlopen") as get:
            self.assertIs(launch.answers("http://localhost:8082/"), False)
            get.assert_not_called()

    def test_socket_timeout_is_none(self):
        with mock.patch.object(launch, "listens", return_value=None):
            self.assertIsNone(launch.answers("http://localhost:8082/"))

    def test_get_outcomes(self):
        resp = mock.MagicMock()
        resp.__enter__.return_value.status = 302
        cases = [(resp, True),
                 (urllib.error.HTTPError("u", 503, "x", {}, None), False),
                 (urllib.error.HTTPError("u", 401, "x", {}, None), False),
                 (urllib.error.HTTPError("u", 301, "x", {}, None), True),
                 (urllib.error.URLError(socket.timeout("timed out")), None),
                 (TimeoutError(), None),
                 (urllib.error.URLError(ConnectionRefusedError()), False),
                 (ConnectionResetError(), False)]
        for outcome, want in cases:
            kw = {"return_value": outcome} if outcome is resp else {"side_effect": outcome}
            with mock.patch.object(launch, "listens", return_value=True), \
                    mock.patch("urllib.request.urlopen", **kw):
                self.assertIs(launch.answers("http://localhost:8082/"), want, outcome)

    def test_listens_on_a_closed_port_is_fast(self):
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            port = s.getsockname()[1]  # bound, not listening: refused
        self.assertIs(launch.listens(port), False)


class Docker(Base):
    def info(self, **kw):
        with mock.patch("subprocess.run", **kw):
            return launch.docker_info()

    def test_up(self):
        self.assertEqual(self.info(return_value=ran(0, "28.1.1\t8589934592\n")), {"up": True, "mem": 8.0})

    def test_cannot_connect_is_false(self):
        r = ran(1, "", "error during connect: open //./pipe/dockerDesktopLinuxEngine: not found")
        self.assertIs(self.info(return_value=r)["up"], False)

    def test_no_cli_is_false(self):
        self.assertIs(self.info(side_effect=FileNotFoundError())["up"], False)

    def test_timeout_is_none(self):
        self.assertIsNone(self.info(side_effect=subprocess.TimeoutExpired("docker", 10))["up"])

    def test_once_per_command(self):
        with mock.patch("subprocess.run", return_value=ran(0, "28\t1")) as run:
            launch.docker_info()
            launch.docker_info()
        self.assertEqual(run.call_count, 1)

    def test_containers_follow_docker(self):
        launch._DOCKER = {"up": False, "mem": None}
        self.assertEqual(launch.containers(), [])
        launch._DOCKER, launch._CONTAINERS = {"up": None, "mem": None}, launch._UNSET
        self.assertIsNone(launch.containers())
        launch._DOCKER, launch._CONTAINERS = {"up": True, "mem": 8}, launch._UNSET
        with mock.patch("subprocess.run", side_effect=subprocess.TimeoutExpired("docker", 15)):
            self.assertIsNone(launch.containers())
        launch._CONTAINERS = launch._UNSET
        with mock.patch("subprocess.run", return_value=ran(0, "pg\tother\t0.0.0.0:8082-8083->5432/tcp\n")):
            self.assertEqual(launch.containers(), [("pg", "other", {8082, 8083})])


class Status(Base):
    def status(self, env, up, cs, docker=True, pid=0, failed=False):
        launch._DOCKER = {"up": docker, "mem": 8}
        launch._CONTAINERS = cs
        if failed:
            open(launch.paths(env["id"])["failed"], "w").close()
        with mock.patch.object(launch, "answers", return_value=up), \
                mock.patch.object(launch, "running_pid", return_value=pid):
            return launch.status_of(env)

    def test_facts_and_state(self):
        own, other = [("t", "tezza-packaging", set())], [("x", "other", set())]
        self.assertEqual(self.status(TEZZA, True, own)["state"], "ready")
        self.assertEqual(self.status(TEZZA, True, other)["state"], "stopped")  # another program on its port
        self.assertEqual(self.status(TEZZA, False, own)["state"], "stopped")
        self.assertEqual(self.status(TEZZA, None, own)["state"], "unknown")
        self.assertEqual(self.status(TEZZA, True, None)["state"], "unknown")
        self.assertEqual(self.status(TEZZA, None, None, pid=42)["state"], "starting")
        self.assertEqual(self.status(TEZZA, False, [], failed=True)["state"], "failed")

    def test_fields(self):
        s = self.status(TEZZA, None, None, docker=None)
        self.assertEqual(s, {"id": "tezza", "url": "http://localhost:8082/", "state": "unknown", "process": False,
                             "failed": False, "containers": None, "answers": None, "docker": None,
                             "progress": None})

    def test_without_project_containers_is_answers(self):
        self.assertIs(self.status(MB, True, None)["containers"], True)
        self.assertEqual(self.status(MB, False, None)["state"], "stopped")

    def test_human_unknown(self):
        launch._DOCKER = {"up": None, "mem": None}
        launch._CONTAINERS = None
        f = io.StringIO()
        with mock.patch.object(launch, "catalog", return_value=[TEZZA]), \
                mock.patch.object(launch, "answers", return_value=None), redirect_stdout(f):
            self.assertEqual(launch.main(["--status"]), 0)
        self.assertEqual(f.getvalue().strip(), "Tezza: onbekend")


class Check(Base):
    def items(self, env, docker=True, docker_mem=16.0, machine=32.0, busy=(), failing=()):
        launch._DOCKER = {"up": docker, "mem": docker_mem}
        with mock.patch.object(launch, "machine_memory_gib", return_value=machine), \
                mock.patch.object(launch, "busy_ports", return_value=list(busy)), \
                mock.patch.object(launch, "failing_checks", return_value=list(failing)), \
                mock.patch.object(launch, "this_os", return_value="windows"):
            return launch.check(env)

    def test_all_well(self):
        self.assertEqual(self.items(TEZZA), [])

    def test_platform_only(self):
        self.assertEqual([i["code"] for i in self.items(MB, docker=False)], ["platform"])
        self.assertEqual(self.items(MB)[0]["kind"], "precondition")

    def test_order_and_kinds(self):
        port = {"code": "port-in-use", "kind": "problem", "text": "p", "data": {"ports": [8082], "holder": "pg"}}
        got = self.items(TEZZA, docker_mem=8 * 1.0, machine=8.0, busy=[port], failing=TEZZA["checks"])
        self.assertEqual([(i["code"], i["kind"]) for i in got],
                         [("machine-memory", "precondition"), ("docker-memory", "problem"),
                          ("port-in-use", "problem"), ("check:identity", "fixable"), ("check:other", "problem")])
        self.assertEqual(got[0]["data"], {"have": 8, "need": 12})
        self.assertEqual(got[1]["data"], {"have": 8, "need": 12})
        self.assertEqual(got[3]["fix"], "hosts aanpassen")
        self.assertNotIn("fix", got[4])

    def test_runtime_down_stops_there(self):
        got = self.items(TEZZA, docker=False, machine=8.0, failing=TEZZA["checks"])
        self.assertEqual([i["code"] for i in got], ["machine-memory", "runtime-down"])
        self.assertIn("Docker draait niet", got[1]["text"])
        self.assertEqual([i["code"] for i in self.items(TEZZA, docker=None)], ["runtime-down"])

    def test_busy_ports_items(self):
        launch._CONTAINERS = [("pg", "other", {8082})]
        with mock.patch.object(launch, "listens", return_value=True), \
                mock.patch.object(launch, "catalog", return_value=[TEZZA]):
            got = launch.busy_ports(TEZZA)
        self.assertEqual([(i["code"], i["data"]) for i in got], [("port-in-use", {"ports": [8082], "holder": "pg"})])
        launch._CONTAINERS = [("t", "tezza-packaging", {8082})]  # its own: not a problem
        with mock.patch.object(launch, "listens", return_value=True), \
                mock.patch.object(launch, "catalog", return_value=[TEZZA]):
            self.assertEqual(launch.busy_ports(TEZZA), [])

    def test_json_and_exit(self):
        item = {"code": "runtime-down", "kind": "problem", "text": "Docker draait niet."}
        for items, rc in (([], 0), ([item], 1)):
            f = io.StringIO()
            with mock.patch.object(launch, "env_of", return_value=TEZZA), \
                    mock.patch.object(launch, "check", return_value=items), redirect_stdout(f):
                self.assertEqual(launch.main(["--check", "tezza", "--json"]), rc)
            self.assertEqual(launch.json.loads(f.getvalue()), {"ok": not items, "items": items})


class Start(Base):
    def start(self, items=(), others=(), pid=0, argv=("tezza",)):
        f = io.StringIO()
        with mock.patch.object(launch, "env_of", return_value=TEZZA), \
                mock.patch.object(launch, "catalog", return_value=[TEZZA, HC]), \
                mock.patch.object(launch, "check", return_value=list(items)), \
                mock.patch.object(launch, "running_pid", return_value=pid), \
                mock.patch.object(launch, "status_of", side_effect=lambda e: dict(others)[e["id"]]), \
                mock.patch.object(launch, "fix") as fix, \
                mock.patch.object(launch, "cmd_stop", return_value=0) as stop, \
                mock.patch.object(launch, "spawn_supervisor") as spawn, redirect_stdout(f):
            rc = launch.main(list(argv))
        fix.assert_not_called()  # a start never fixes
        return rc, f.getvalue(), spawn, stop

    STOPPED = {"haalcentraal": {"state": "stopped", "containers": False}}

    def test_starts(self):
        rc, _, spawn, stop = self.start(others=self.STOPPED)
        self.assertEqual(rc, 0)
        spawn.assert_called_once_with("tezza")
        stop.assert_not_called()

    def test_precondition_is_4(self):
        rc, out, spawn, _ = self.start([{"code": "machine-memory", "kind": "precondition", "text": "te weinig"},
                                        {"code": "runtime-down", "kind": "problem", "text": "docker"}])
        self.assertEqual(rc, 4)
        self.assertIn("te weinig", out)
        spawn.assert_not_called()

    def test_problem_is_3_also_with_yes(self):
        items = [{"code": "check:identity", "kind": "fixable", "text": "identity onbekend", "fix": "hosts"}]
        rc, out, spawn, _ = self.start(items, argv=("tezza", "--yes"))
        self.assertEqual(rc, 3)
        self.assertIn("identity onbekend", out)
        self.assertIn("Oplossen: python launch.py --fix tezza", out)
        spawn.assert_not_called()

    def test_problem_without_fix_names_no_fix(self):
        rc, out, _, _ = self.start([{"code": "runtime-down", "kind": "problem", "text": "Docker draait niet."}])
        self.assertEqual(rc, 3)
        self.assertNotIn("--fix", out)

    def test_other_running_is_5_and_not_stopped(self):
        for state in ("starting", "ready", "failed"):
            rc, out, spawn, stop = self.start(others={"haalcentraal": {"state": state, "containers": True}})
            self.assertEqual(rc, 5)
            self.assertIn("De Haal Centraal-omgeving draait; stop die eerst (python launch.py --stop haalcentraal).",
                          out)
            spawn.assert_not_called()
            stop.assert_not_called()

    def test_unknown_other_does_not_block(self):
        rc, _, spawn, _ = self.start(others={"haalcentraal": {"state": "unknown", "containers": None}})
        self.assertEqual(rc, 0)
        spawn.assert_called_once()

    def test_already_starting_is_0(self):
        rc, out, spawn, _ = self.start(pid=42)
        self.assertEqual(rc, 0)
        self.assertIn("wordt al gestart", out)
        spawn.assert_not_called()


class Stop(Base):
    def test_unknown_answer_is_not_still_running(self):
        with mock.patch.object(launch, "env_of", return_value=TEZZA), \
                mock.patch.object(launch, "running_pid", return_value=0), \
                mock.patch.object(launch, "run_env", return_value=0), \
                mock.patch.object(launch, "answers", return_value=None), redirect_stdout(io.StringIO()):
            self.assertEqual(launch.cmd_stop("tezza"), 0)
        with mock.patch.object(launch, "env_of", return_value=TEZZA), \
                mock.patch.object(launch, "running_pid", return_value=0), \
                mock.patch.object(launch, "run_env", return_value=0), \
                mock.patch.object(launch, "answers", return_value=True), redirect_stdout(io.StringIO()):
            self.assertEqual(launch.cmd_stop("tezza"), 1)


if __name__ == "__main__":
    unittest.main()
