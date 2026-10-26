"""Unit tests for launch.py: the command line, the status facts, the check items,
the choices, the language and what a start does.
No network, Docker or real environments: subprocess, sockets and urllib are faked.

  python -m unittest tests/test_launch.py

SPDX-License-Identifier: EUPL-1.2
"""
import io
import json
import os
import socket
import subprocess
import sys
import tempfile
import unittest
import urllib.error
from contextlib import redirect_stderr, redirect_stdout
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
NC = {"id": "nc", "title": "Nextcloud", "url": "http://localhost:8609/", "options": [
    {"id": "pipelinq", "kind": "part", "title": {"nl": "Klantcontact"}, "default": True},
    {"id": "portaliq", "kind": "part", "title": "Portaal", "default": False},
    {"id": "demo", "kind": "data", "title": "Demozaken", "default": True, "needs": ["pipelinq"]},
    {"id": "more", "kind": "data", "title": "Meer", "needs": ["demo"]}]}


def ran(rc=0, out="", err=""):
    return subprocess.CompletedProcess([], rc, out, err)


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        env = mock.patch.dict(os.environ, {"LAUNCH_STATE": self.tmp.name, "LAUNCH_LANG": "nl"})
        env.start()
        self.addCleanup(env.stop)
        self.addCleanup(self.tmp.cleanup)
        launch._DOCKER = None
        launch._CONTAINERS = launch._UNSET
        self.addCleanup(setattr, launch, "_CONTAINERS", launch._UNSET)
        self.addCleanup(setattr, launch, "_DOCKER", None)
        launch.LANG = "nl"
        self.addCleanup(setattr, launch, "LANG", "en")

    def main(self, *argv):
        """launch.main(argv) -> (exit code, stdout, stderr); argparse's own exit counts too."""
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            try:
                rc = launch.main(list(argv))
            except SystemExit as e:
                rc = e.code
        return rc, out.getvalue(), err.getvalue()


class CommandLine(Base):
    def test_help_lists_the_commands_not_the_supervisor(self):
        rc, out, _ = self.main("--help")
        self.assertEqual(rc, 0)
        for c in ("list", "status", "check", "fix", "start", "stop", "exit codes"):
            self.assertIn(c, out)
        self.assertNotIn("supervise", out)

    def test_command_help_and_version(self):
        rc, out, _ = self.main("start", "--help")
        self.assertEqual(rc, 0)
        self.assertIn("--with", out)
        self.assertIn("--wait", out)
        rc, out, _ = self.main("--version")
        self.assertEqual((rc, out.strip()), (0, f"launch {launch.VERSION}"))
        self.assertRegex(launch.VERSION, r"^\d+\.\d+\.\d+(-rc\.\d+)?$")

    def test_old_flags_are_wrong_use(self):
        for argv in ([], ["--list"], ["--status"], ["--check", "tezza"], ["--fix", "tezza"], ["--stop", "tezza"],
                     ["tezza"], ["start", "tezza", "--yes"], ["check", "tezza", "--lang", "de"]):
            self.assertEqual(self.main(*argv)[0], 2, argv)

    def test_parsing(self):
        a = launch.parser().parse_args(["start", "nc", "--with", "a,b", "--with", "c", "--without", "d", "--wait"])
        self.assertEqual((a.command, a.id, launch.id_list(a.on), launch.id_list(a.off), a.wait),
                         ("start", "nc", ["a", "b", "c"], ["d"], True))
        a = launch.parser().parse_args(["status"])
        self.assertEqual((a.command, a.id, a.json), ("status", None, False))
        a = launch.parser().parse_args(["check", "tezza", "--json", "--lang", "en"])
        self.assertEqual((a.id, a.json, a.lang), ("tezza", True, "en"))

    def test_unknown_environment_is_2(self):
        with mock.patch.object(launch, "catalog", return_value=[TEZZA, HC]):
            for argv in (["start", "nosuch"], ["stop", "nosuch"], ["check", "nosuch"], ["fix", "nosuch"],
                         ["status", "nosuch"]):
                rc, out, err = self.main(*argv)
                self.assertEqual(rc, 2, argv)
                self.assertIn("onbekende omgeving 'nosuch'. Kies uit: tezza, haalcentraal", err)

    def test_list_passes_the_options_unchanged(self):
        with mock.patch.object(launch, "catalog", return_value=[NC]):
            rc, out, _ = self.main("list", "--json")
        self.assertEqual(rc, 0)
        self.assertEqual(json.loads(out), {"schema": 1, "environments": [NC]})

    def test_status_of_one_is_a_list_of_one(self):
        st = {"id": "haalcentraal", "state": "stopped"}
        with mock.patch.object(launch, "catalog", return_value=[TEZZA, HC]), \
                mock.patch.object(launch, "status_of", return_value=st) as status:
            rc, out, _ = self.main("status", "haalcentraal", "--json")
        self.assertEqual((rc, json.loads(out)), (0, [st]))
        status.assert_called_once_with(HC)


class Language(Base):
    def test_order(self):
        with mock.patch.object(launch, "os_lang", return_value="nl"):
            self.assertEqual(launch.choose_lang("en"), "en")
            with mock.patch.dict(os.environ, {"LAUNCH_LANG": "en"}):
                self.assertEqual(launch.choose_lang(), "en")
            with mock.patch.dict(os.environ, {"LAUNCH_LANG": "fr"}):
                self.assertEqual(launch.choose_lang(), "nl")
            with mock.patch.dict(os.environ, {"LAUNCH_LANG": ""}):
                self.assertEqual(launch.choose_lang(), "nl")

    def test_os_lang_elsewhere(self):
        clean = {k: "" for k in ("LC_ALL", "LC_MESSAGES", "LANG")}
        with mock.patch.object(launch, "WINDOWS", False):
            for env, want in (({"LANG": "nl_NL.UTF-8"}, "nl"), ({"LC_ALL": "en_US.UTF-8", "LANG": "nl_NL"}, "en"),
                              ({"LANG": "de_DE"}, "en"), ({}, "en")):
                with mock.patch.dict(os.environ, {**clean, **env}):
                    self.assertEqual(launch.os_lang(), want, env)

    @unittest.skipUnless(os.name == "nt", "Windows only")
    def test_os_lang_windows(self):
        import ctypes
        for langid, want in ((0x0413, "nl"), (0x0813, "nl"), (0x0409, "en")):
            with mock.patch.object(ctypes.windll.kernel32, "GetUserDefaultUILanguage", return_value=langid):
                self.assertEqual(launch.os_lang(), want)

    def test_every_message_in_both(self):
        for key, t in launch.MESSAGES.items():
            self.assertEqual(set(t), {"nl", "en"}, key)

    def test_pick(self):
        self.assertEqual(launch.pick("x"), "x")
        self.assertEqual(launch.pick({"nl": "a", "en": "b"}, "en"), "b")
        self.assertEqual(launch.pick({"nl": "a"}, "en"), "a")
        self.assertEqual(launch.pick({"fy": "c"}, "en"), "c")
        self.assertEqual(launch.pick(None), "")


class Options(Base):
    def test_defaults(self):
        self.assertEqual(launch.resolve_options(NC), {"on": ["pipelinq", "demo", "more"], "off": ["portaliq"]})
        self.assertEqual(launch.resolve_options(TEZZA), {"on": [], "off": []})

    def test_with_without_and_needs(self):
        self.assertEqual(launch.resolve_options(NC, ["portaliq"], ["more"]),
                         {"on": ["pipelinq", "portaliq", "demo"], "off": ["more"]})
        # Off goes down the chain: pipelinq -> demo -> more.
        self.assertEqual(launch.resolve_options(NC, (), ["pipelinq"]),
                         {"on": [], "off": ["pipelinq", "portaliq", "demo", "more"]})
        self.assertEqual(launch.resolve_options(NC, ["demo"], ["pipelinq"])["off"],
                         ["pipelinq", "portaliq", "demo", "more"])

    def test_wrong_use(self):
        with self.assertRaises(launch.Usage) as e:
            launch.resolve_options(NC, ["nosuch"])
        self.assertIn("Kies uit: pipelinq, portaliq, demo, more", str(e.exception))
        with self.assertRaises(launch.Usage):
            launch.resolve_options(NC, ["demo"], ["demo"])
        with self.assertRaises(launch.Usage):
            launch.resolve_options(TEZZA, (), ["demo"])

    def test_start_with_an_unknown_option_is_2_before_anything(self):
        with mock.patch.object(launch, "catalog", return_value=[NC]), \
                mock.patch.object(launch, "check") as check, \
                mock.patch.object(launch, "spawn_supervisor") as spawn:
            rc, _, err = self.main("start", "nc", "--without", "nosuch")
        self.assertEqual(rc, 2)
        self.assertIn("onbekende keuze 'nosuch'", err)
        check.assert_not_called()
        spawn.assert_not_called()

    def test_start_stores_them_and_the_supervisor_passes_them(self):
        with mock.patch.object(launch, "catalog", return_value=[NC]), \
                mock.patch.object(launch, "check", return_value=[]), \
                mock.patch.object(launch, "running_pid", return_value=0), \
                mock.patch.object(launch, "spawn_supervisor") as spawn:
            rc, _, _ = self.main("start", "nc", "--without", "demo", "--with", "portaliq")
        self.assertEqual(rc, 0)
        spawn.assert_called_once_with("nc")
        with open(launch.paths("nc")["options"], encoding="utf-8") as f:
            self.assertEqual(json.load(f), {"on": ["pipelinq", "portaliq"], "off": ["demo", "more"]})
        with mock.patch.object(launch, "run_env", return_value=0) as run_env:
            launch.supervise("nc")
        run_env.assert_called_once_with("nc", "start", {"LAUNCH_OPTIONS_ON": "pipelinq,portaliq",
                                                        "LAUNCH_OPTIONS_OFF": "demo,more"})

    def test_a_source(self):
        env = {"id": "v", "title": "Valtimo", "options": [
            {"id": "here-too", "kind": "data", "source": {"path": "tests"}},
            {"id": "gone", "kind": "data", "source": {"path": "sources/no-such-source", "title": "de processen"}}]}
        # Missing: off by default, and --with is wrong use that says where it goes.
        self.assertEqual(launch.resolve_options(env), {"on": ["here-too"], "off": ["gone"]})
        with self.assertRaises(launch.Usage) as e:
            launch.resolve_options(env, ["gone"])
        self.assertIn("zet de processen in sources/no-such-source", str(e.exception))
        # On: run.sh gets the folder.
        self.assertEqual(launch.source_env(env, {"on": ["here-too"]}),
                         {"LAUNCH_SOURCE_HERE_TOO": os.path.join(launch.ROOT, "tests")})
        self.assertEqual(launch.source_env(env, {"on": []}), {})
        # An empty folder is missing too.
        with tempfile.TemporaryDirectory(dir=launch.ROOT) as d:
            self.assertTrue(launch.source_missing({"source": {"path": os.path.basename(d)}}))

    def test_no_file_no_variables(self):
        self.assertEqual(launch.options_env("nc"), {})


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
        with mock.patch.object(launch, "catalog", return_value=[TEZZA]), \
                mock.patch.object(launch, "answers", return_value=None):
            rc, out, _ = self.main("status")
        self.assertEqual((rc, out.strip()), (0, "Tezza: onbekend"))
        with mock.patch.dict(os.environ, {"LAUNCH_LANG": "en"}), \
                mock.patch.object(launch, "catalog", return_value=[TEZZA]), \
                mock.patch.object(launch, "answers", return_value=None):
            self.assertEqual(self.main("status")[1].strip(), "Tezza: unknown")


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
        self.assertTrue(got[0]["text"].startswith("Poort 8082 is bezet door de container 'pg'"))
        launch._CONTAINERS = [("t", "tezza-packaging", {8082})]  # its own: not a problem
        with mock.patch.object(launch, "listens", return_value=True), \
                mock.patch.object(launch, "catalog", return_value=[TEZZA]):
            self.assertEqual(launch.busy_ports(TEZZA), [])

    def test_json_and_exit(self):
        item = {"code": "runtime-down", "kind": "problem", "text": "Docker draait niet."}
        pre = {"code": "platform", "kind": "precondition", "text": "DAWO"}
        for items, rc in (([], 0), ([item], 3), ([pre, item], 4)):
            with mock.patch.object(launch, "env_of", return_value=TEZZA), \
                    mock.patch.object(launch, "check", return_value=items):
                got, out, _ = self.main("check", "tezza", "--json")
            self.assertEqual(got, rc)
            self.assertEqual(json.loads(out), {"ok": not items, "items": items})

    def test_texts_in_english_and_from_a_dict(self):
        env = dict(TEZZA, title={"nl": "Tezza NL", "en": "Tezza EN"},
                   checks=[{"id": "x", "problem": {"nl": "kapot", "en": "broken"}}])
        launch.LANG = "en"
        got = self.items(env, docker_mem=4.0, failing=env["checks"])
        self.assertEqual(got[0]["text"], "Docker has 4 GB of memory; Tezza EN needs about 12 "
                                         "(see the README: giving Docker more memory).")
        self.assertEqual(got[1]["text"], "broken")

    def test_lang_flag(self):
        with mock.patch.object(launch, "env_of", return_value=TEZZA), \
                mock.patch.object(launch, "check", return_value=[]):
            self.assertEqual(self.main("check", "tezza", "--lang", "en")[1].strip(), "All is well.")
            self.assertEqual(self.main("check", "tezza", "--lang", "nl")[1].strip(), "Alles in orde.")


class Start(Base):
    def start(self, items=(), others=(), pid=0, argv=("start", "tezza")):
        with mock.patch.object(launch, "env_of", return_value=TEZZA), \
                mock.patch.object(launch, "catalog", return_value=[TEZZA, HC]), \
                mock.patch.object(launch, "check", return_value=list(items)), \
                mock.patch.object(launch, "running_pid", return_value=pid), \
                mock.patch.object(launch, "status_of", side_effect=lambda e: dict(others)[e["id"]]), \
                mock.patch.object(launch, "fix") as fix, \
                mock.patch.object(launch, "cmd_stop", return_value=0) as stop, \
                mock.patch.object(launch, "spawn_supervisor") as spawn:
            rc, out, _ = self.main(*argv)
        fix.assert_not_called()  # a start never fixes
        return rc, out, spawn, stop

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

    def test_problem_is_3(self):
        items = [{"code": "check:identity", "kind": "fixable", "text": "identity onbekend", "fix": "hosts"}]
        rc, out, spawn, _ = self.start(items)
        self.assertEqual(rc, 3)
        self.assertIn("identity onbekend", out)
        self.assertIn("Oplossen: python launch.py fix tezza", out)
        spawn.assert_not_called()

    def test_problem_without_fix_names_no_fix(self):
        rc, out, _, _ = self.start([{"code": "runtime-down", "kind": "problem", "text": "Docker draait niet."}])
        self.assertEqual(rc, 3)
        self.assertNotIn(" fix ", out)

    def test_other_running_is_5_and_not_stopped(self):
        for state in ("starting", "ready", "failed"):
            rc, out, spawn, stop = self.start(others={"haalcentraal": {"state": state, "containers": True}})
            self.assertEqual(rc, 5)
            self.assertIn("Haal Centraal draait; stop die eerst (python launch.py stop haalcentraal).", out)
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


class Wait(Base):
    def wait(self, pids, failed=False, states=("ready",)):
        if failed:
            open(launch.paths("tezza")["failed"], "w").close()
        with mock.patch.object(launch, "running_pid", side_effect=list(pids)), \
                mock.patch.object(launch, "status_of", side_effect=[{"state": s} for s in states]), \
                mock.patch.object(launch, "progress_of", return_value={"step": 2, "steps": 5, "label": "Ophalen"}), \
                mock.patch.object(launch.time, "sleep"), redirect_stdout(io.StringIO()) as out:
            return launch.wait_for(TEZZA), out.getvalue()

    def test_ready_is_0(self):
        rc, out = self.wait([42, 42, 0])
        self.assertEqual(rc, 0)
        self.assertEqual(out.count("Stap 2 van 5: Ophalen"), 1)  # a step once
        self.assertIn("Tezza is klaar: http://localhost:8082/", out)

    def test_unknown_then_ready_is_0(self):
        self.assertEqual(self.wait([0], states=("unknown", "ready"))[0], 0)

    def test_failed_is_1(self):
        self.assertEqual(self.wait([42, 0], failed=True, states=())[0], 1)
        self.assertEqual(self.wait([0], states=("stopped", "stopped", "stopped"))[0], 1)

    def test_start_wait(self):
        with mock.patch.object(launch, "env_of", return_value=TEZZA), \
                mock.patch.object(launch, "check", return_value=[]), \
                mock.patch.object(launch, "catalog", return_value=[TEZZA]), \
                mock.patch.object(launch, "running_pid", return_value=0), \
                mock.patch.object(launch, "spawn_supervisor"), \
                mock.patch.object(launch, "wait_for", return_value=1) as wait_for:
            self.assertEqual(self.main("start", "tezza", "--wait")[0], 1)
        wait_for.assert_called_once_with(TEZZA)


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
