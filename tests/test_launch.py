"""Unit tests for launch.py's Docker diagnostics: the memory check and the
per-container problem detection (a missing OOM-killed, a missing container
user, an image that will not run under emulation, or a plain exit code).

No real Docker, no download, no existing container or volume is touched:
every `docker` call is mocked. Run with:

    python3 -m unittest tests/test_launch.py

SPDX-License-Identifier: EUPL-1.2
"""
import os
import subprocess
import sys
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import launch  # noqa: E402


def run_result(returncode=0, stdout="", stderr=""):
    return subprocess.CompletedProcess(args=[], returncode=returncode, stdout=stdout, stderr=stderr)


class DockerMemoryTests(unittest.TestCase):
    def test_returns_gib_from_docker_info(self):
        with mock.patch("launch.subprocess.run", return_value=run_result(0, str(12 * 2**30))):
            self.assertAlmostEqual(launch.docker_memory_gib(), 12.0)

    def test_none_when_docker_not_running(self):
        with mock.patch("launch.subprocess.run", return_value=run_result(1, "")):
            self.assertIsNone(launch.docker_memory_gib())

    def test_none_when_docker_missing(self):
        with mock.patch("launch.subprocess.run", side_effect=OSError("no such file")):
            self.assertIsNone(launch.docker_memory_gib())


class HostMemoryTests(unittest.TestCase):
    def test_darwin(self):
        with mock.patch.object(launch, "WINDOWS", False), \
             mock.patch("launch.sys.platform", "darwin"), \
             mock.patch("launch.subprocess.run", return_value=run_result(0, str(18 * 2**30))):
            self.assertAlmostEqual(launch.host_memory_gib(), 18.0)

    def test_linux_reads_proc_meminfo(self):
        meminfo = "MemTotal:       16777216 kB\nMemFree:         1000000 kB\n"
        with mock.patch.object(launch, "WINDOWS", False), \
             mock.patch("launch.sys.platform", "linux"), \
             mock.patch("builtins.open", mock.mock_open(read_data=meminfo)):
            self.assertAlmostEqual(launch.host_memory_gib(), 16777216 / 2**20)

    def test_windows_none_on_failure(self):
        fake_kernel32 = mock.Mock()
        fake_kernel32.GlobalMemoryStatusEx.return_value = 0  # failure
        fake_windll = mock.Mock(kernel32=fake_kernel32)
        with mock.patch.object(launch, "WINDOWS", True), \
             mock.patch("ctypes.windll", fake_windll, create=True):
            self.assertIsNone(launch.host_memory_gib())

    def test_none_when_command_fails(self):
        with mock.patch.object(launch, "WINDOWS", False), \
             mock.patch("launch.sys.platform", "darwin"), \
             mock.patch("launch.subprocess.run", side_effect=OSError()):
            self.assertIsNone(launch.host_memory_gib())


class ProjectContainersTests(unittest.TestCase):
    def test_parses_inspect_output(self):
        names_out = run_result(0, "tezza-packaging-notificaties-1\ntezza-packaging-platform-1\n")
        inspect_out = run_result(
            0,
            "/tezza-packaging-notificaties-1\tcreated\t0\tfalse\tunable to find user opennotificaties: "
            "no matching entries in passwd file\n"
            "/tezza-packaging-platform-1\texited\t137\ttrue\t\n")
        with mock.patch("launch.subprocess.run", side_effect=[names_out, inspect_out]):
            result = launch.project_containers("tezza-packaging")
        self.assertEqual(len(result), 2)
        self.assertEqual(result[0]["name"], "tezza-packaging-notificaties-1")
        self.assertEqual(result[0]["status"], "created")
        self.assertFalse(result[0]["oomkilled"])
        self.assertIn("no matching entries", result[0]["error"])
        self.assertEqual(result[1]["status"], "exited")
        self.assertEqual(result[1]["exitcode"], "137")
        self.assertTrue(result[1]["oomkilled"])

    def test_empty_when_no_containers(self):
        with mock.patch("launch.subprocess.run", return_value=run_result(0, "")) as run:
            result = launch.project_containers("no-such-project")
        self.assertEqual(result, [])
        run.assert_called_once()  # the inspect call is skipped when there are no names

    def test_empty_on_docker_error(self):
        with mock.patch("launch.subprocess.run", side_effect=OSError()):
            self.assertEqual(launch.project_containers("tezza-packaging"), [])


class ContainerProblemTests(unittest.TestCase):
    def test_oom_killed(self):
        c = {"name": "x", "status": "exited", "exitcode": "137", "oomkilled": True, "error": ""}
        reason = launch.container_problem(c)
        self.assertIn("137", reason)
        self.assertIn("OOMKilled", reason)

    def test_missing_container_user(self):
        c = {"name": "x", "status": "created", "exitcode": "0", "oomkilled": False,
            "error": "unable to find user opennotificaties: no matching entries in passwd file"}
        reason = launch.container_problem(c)
        self.assertIn("gebruiker", reason)
        self.assertIn("image", reason)

    def test_elf_architecture_error(self):
        c = {"name": "x", "status": "created", "exitcode": "1", "oomkilled": False,
            "error": "OCI runtime create failed: exec format error"}
        reason = launch.container_problem(c)
        self.assertIn("emulatie", reason)

    def test_plain_exit_code(self):
        c = {"name": "x", "status": "exited", "exitcode": "1", "oomkilled": False, "error": ""}
        reason = launch.container_problem(c)
        self.assertIn("exited", reason)
        self.assertIn("1", reason)

    def test_running_is_not_a_problem(self):
        c = {"name": "x", "status": "running", "exitcode": "0", "oomkilled": False, "error": ""}
        self.assertEqual(launch.container_problem(c), "")

    def test_created_without_error_is_not_a_problem_yet(self):
        # Right after `docker compose up -d`, every container is briefly
        # "created": that alone must not be flagged.
        c = {"name": "x", "status": "created", "exitcode": "0", "oomkilled": False, "error": ""}
        self.assertEqual(launch.container_problem(c), "")


class ContainerProblemsTests(unittest.TestCase):
    def test_no_project_means_no_problems(self):
        self.assertEqual(launch.container_problems({"title": "X"}), [])

    def test_names_the_container_and_the_environment(self):
        containers = [{"name": "tezza-packaging-notificaties-1", "status": "exited", "exitcode": "137",
                      "oomkilled": True, "error": ""}]
        with mock.patch("launch.project_containers", return_value=containers):
            problems = launch.container_problems({"title": "Tezza", "project": "tezza-packaging"})
        self.assertEqual(len(problems), 1)
        self.assertIn("tezza-packaging-notificaties-1", problems[0])
        self.assertIn("Tezza", problems[0])

    def test_running_container_gives_no_problem(self):
        containers = [{"name": "x", "status": "running", "exitcode": "0", "oomkilled": False, "error": ""}]
        with mock.patch("launch.project_containers", return_value=containers):
            self.assertEqual(launch.container_problems({"title": "Tezza", "project": "tezza-packaging"}), [])


class CheckMemoryTests(unittest.TestCase):
    # A minimal env (no url/checks/project) isolates the memory logic: the
    # other parts of check() (busy_ports, container_problems, failing_checks)
    # see nothing to do and make no further calls.
    ENV = {"id": "tezza", "title": "Tezza", "needs": {"memory_gib": 16}}

    def test_docker_not_running(self):
        with mock.patch("launch.docker_memory_gib", return_value=None):
            problems = launch.check(self.ENV)
        self.assertTrue(any("Docker draait niet" in p for p in problems))

    def test_enough_memory_is_not_a_problem(self):
        with mock.patch("launch.docker_memory_gib", return_value=16.0), \
             mock.patch("launch.host_memory_gib", return_value=18.0):
            problems = launch.check(self.ENV)
        self.assertEqual(problems, [])

    def test_too_little_memory_names_what_is_needed_and_host_total(self):
        with mock.patch("launch.docker_memory_gib", return_value=11.7), \
             mock.patch("launch.host_memory_gib", return_value=18.0):
            problems = launch.check(self.ENV)
        self.assertEqual(len(problems), 1)
        self.assertIn("12 GB", problems[0])
        self.assertIn("16", problems[0])
        self.assertIn("18 GB", problems[0])

    def test_too_little_memory_without_host_reading(self):
        with mock.patch("launch.docker_memory_gib", return_value=11.7), \
             mock.patch("launch.host_memory_gib", return_value=None):
            problems = launch.check(self.ENV)
        self.assertEqual(len(problems), 1)
        self.assertNotIn("fysiek", problems[0])

    def test_dawo_usb_platform_short_circuits(self):
        env = {"id": "mijn-bureau", "title": "Mijn Bureau", "needs": {"platform": "dawo-usb"}}
        with mock.patch("launch.docker_memory_gib") as docker_mem:
            problems = launch.check(env)
        docker_mem.assert_not_called()
        self.assertEqual(len(problems), 1)


class FixScheduleTests(unittest.TestCase):
    CHECK = {
        "id": "identity",
        "when": {"host_unknown": "identity"},
        "problem": "...",
        "fix": {"text": "...", "run": {"windows": ["hostnamen.ps1", "identity"], "macos": ["hostnamen.sh", "identity"]}},
    }

    def test_run_of_picks_the_current_os(self):
        with mock.patch("launch.this_os", return_value="macos"):
            self.assertEqual(launch.run_of(self.CHECK), ["hostnamen.sh", "identity"])

    def test_run_of_none_when_os_not_listed(self):
        with mock.patch("launch.this_os", return_value="linux"):
            self.assertIsNone(launch.run_of(self.CHECK))

    def test_has_fix_follows_run_of(self):
        with mock.patch("launch.this_os", return_value="windows"):
            self.assertTrue(launch.has_fix(self.CHECK))
        with mock.patch("launch.this_os", return_value="linux"):
            self.assertFalse(launch.has_fix(self.CHECK))

    def test_tezza_environment_json_has_a_fix_for_every_supported_os(self):
        env = launch.env_of("tezza")
        check = next(c for c in env["checks"] if c["id"] == "identity")
        for osname in ("windows", "macos", "linux"):
            with mock.patch("launch.this_os", return_value=osname):
                cmd = launch.run_of(check)
            self.assertTrue(cmd, f"no fix for {osname}")
            self.assertEqual(cmd[-1], "identity")


class FixTests(unittest.TestCase):
    def test_fix_runs_the_command_and_reports_what_still_fails(self):
        check_ok = {"id": "a", "fix": {"run": {"linux": ["x"]}}}
        check_still_failing = {"id": "b", "fix": {"run": {"linux": ["y"]}}}
        with mock.patch("launch.this_os", return_value="linux"), \
             mock.patch("launch.failing_checks", return_value=[check_ok, check_still_failing]), \
             mock.patch("launch.script", side_effect=lambda env, cmd: cmd), \
             mock.patch("launch.subprocess.run", return_value=run_result(0)), \
             mock.patch("launch.fails", side_effect=lambda env, c: c["id"] == "b"):
            failed = launch.fix({"id": "tezza"})
        self.assertEqual(failed, ["b"])

    def test_checks_without_a_fix_for_this_os_are_skipped(self):
        check_unfixable = {"id": "a", "fix": {"run": {"windows": ["x"]}}}
        with mock.patch("launch.this_os", return_value="linux"), \
             mock.patch("launch.failing_checks", return_value=[check_unfixable]), \
             mock.patch("launch.subprocess.run") as run:
            failed = launch.fix({"id": "tezza"})
        run.assert_not_called()
        self.assertEqual(failed, [])


if __name__ == "__main__":
    unittest.main()
