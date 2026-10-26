"""The Launcher's transitions (contract §F, docs/launcher-behaviour.md): pure
functions fed with made-up measurements; no server, no `launch`.

  python -m unittest tests/test_launcher_states.py

SPDX-License-Identifier: EUPL-1.2
"""

import importlib.util
import os
import re
import unittest

_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "launcher", "launcher.py")
_spec = importlib.util.spec_from_file_location("launcher_under_test", _PATH)
L = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(L)

T = 1761380000.0


def facts(process=False, failed=False, containers=False, answers=False, docker=True, progress=None):
    return {"process": process, "failed": failed, "containers": containers, "answers": answers,
            "docker": docker, "progress": progress}


UP = dict(containers=True, answers=True)


def rec_in(phase):
    r = L.new_record("tezza")
    r["phase"] = phase
    return r


def obs(r, f, t=T, has_project=True):
    return L.observe(r, f, t, has_project, "Tezza")


class Unknown(unittest.TestCase):
    def test_process_starting(self):
        r = rec_in("unknown")
        self.assertEqual(obs(r, facts(process=True, progress={"step": 2}))[0][1:3], ("unknown", "starting"))
        self.assertEqual(r["progress"], {"step": 2})

    def test_failed(self):
        r = rec_in("unknown")
        obs(r, facts(failed=True, progress={"error": "Stap 3 liep vast"}))
        self.assertEqual((r["phase"], r["error"]), ("failed", "Stap 3 liep vast"))

    def test_up_ready(self):
        r = rec_in("unknown")
        obs(r, facts(**UP))
        self.assertEqual(r["phase"], "ready")

    def test_known_and_down_stopped(self):
        r = rec_in("unknown")
        obs(r, facts(containers=True, answers=False))
        self.assertEqual(r["phase"], "stopped")

    def test_null_stays_unknown(self):
        r = rec_in("unknown")
        self.assertEqual(obs(r, facts(containers=None, answers=False)), [])
        self.assertEqual(obs(r, None), [])
        self.assertEqual(r["phase"], "unknown")


class Stopped(unittest.TestCase):
    def test_process_starting_at_once(self):
        r = rec_in("stopped")
        obs(r, facts(process=True))
        self.assertEqual(r["phase"], "starting")

    def test_up_twice_ready(self):
        r = rec_in("stopped")
        self.assertEqual(obs(r, facts(**UP)), [])
        self.assertEqual(r["phase"], "stopped")
        obs(r, facts(**UP), T + 3)
        self.assertEqual(r["phase"], "ready")

    def test_null_neither_confirms_nor_resets(self):
        r = rec_in("stopped")
        obs(r, facts(**UP))
        obs(r, facts(containers=None, answers=None), T + 3)
        self.assertEqual(r["phase"], "stopped")
        obs(r, facts(**UP), T + 6)
        self.assertEqual(r["phase"], "ready")

    def test_down_resets(self):
        r = rec_in("stopped")
        obs(r, facts(**UP))
        obs(r, facts(), T + 3)
        obs(r, facts(**UP), T + 6)
        self.assertEqual(r["phase"], "stopped")


class Starting(unittest.TestCase):
    def test_process_stays_with_progress(self):
        r = rec_in("starting")
        self.assertEqual(obs(r, facts(process=True, progress={"step": 4})), [])
        self.assertEqual((r["phase"], r["progress"]), ("starting", {"step": 4}))

    def test_ended_failed_at_once(self):
        r = rec_in("starting")
        obs(r, facts(failed=True, progress={"step": 4, "error": "De database start niet"}))
        self.assertEqual((r["phase"], r["error"]), ("failed", "De database start niet"))
        self.assertEqual(r["progress"]["step"], 4)

    def test_ended_failed_without_error(self):
        r = rec_in("starting")
        obs(r, facts(failed=True))
        self.assertEqual(r["error"], L.ABORTED)

    def test_ended_up_ready_at_once(self):
        r = rec_in("starting")
        obs(r, facts(**UP))
        self.assertEqual(r["phase"], "ready")

    def test_ended_down_twice_failed(self):
        r = rec_in("starting")
        obs(r, facts(containers=True, answers=False))
        self.assertEqual(r["phase"], "starting")
        obs(r, facts(containers=True, answers=False), T + 3)
        self.assertEqual((r["phase"], r["error"]), ("failed", "De start is afgebroken."))

    def test_ended_down_then_null_then_down(self):
        r = rec_in("starting")
        obs(r, facts())
        obs(r, facts(containers=None, answers=None), T + 3)
        self.assertEqual(r["phase"], "starting")
        obs(r, facts(), T + 6)
        self.assertEqual(r["phase"], "failed")

    def test_process_unknown_changes_nothing(self):
        r = rec_in("starting")
        self.assertEqual(obs(r, facts(process=None, failed=None, containers=None, answers=None)), [])
        self.assertEqual(r["phase"], "starting")


class Ready(unittest.TestCase):
    def test_docker_false_twice_stopped_with_ended(self):
        r = rec_in("ready")
        obs(r, facts(docker=False, containers=None, answers=False))
        self.assertEqual(r["phase"], "ready")
        obs(r, facts(docker=False, containers=None, answers=False), T + 3)
        self.assertEqual(r["phase"], "stopped")
        self.assertRegex(r["ended"]["text"], r"^Tezza draait niet meer \(\d\d:\d\d\): Docker is gestopt$")

    def test_containers_false_twice_stopped_with_ended(self):
        r = rec_in("ready")
        obs(r, facts(containers=False))
        self.assertEqual(r["phase"], "ready")
        obs(r, facts(containers=False), T + 3)
        self.assertEqual(r["phase"], "stopped")
        self.assertRegex(r["ended"]["text"], r"^Tezza draait niet meer \(\d\d:\d\d\)$")

    def test_one_bad_measurement_changes_nothing(self):
        # The observed bug: one failed measurement made Klaar "Uit" (bijlage #1, #2).
        r = rec_in("ready")
        obs(r, facts(containers=False))
        obs(r, facts(**UP), T + 3)
        obs(r, facts(containers=False), T + 6)
        self.assertEqual(r["phase"], "ready")

    def test_not_answering_twice(self):
        r = rec_in("ready")
        obs(r, facts(containers=True, answers=False))
        self.assertIsNone(r["not_answering_since"])
        obs(r, facts(containers=True, answers=False), T + 3)
        self.assertEqual((r["phase"], r["not_answering_since"]), ("ready", T))
        obs(r, facts(**UP), T + 6)
        self.assertIsNone(r["not_answering_since"])

    def test_not_answering_needs_a_project(self):
        r = rec_in("ready")
        obs(r, facts(containers=True, answers=False), has_project=False)
        obs(r, facts(containers=True, answers=False), T + 3, has_project=False)
        self.assertIsNone(r["not_answering_since"])

    def test_null_sets_unknown_since(self):
        r = rec_in("ready")
        obs(r, facts(docker=None, containers=None, answers=None))
        obs(r, facts(docker=None, containers=None, answers=None), T + 3)
        self.assertEqual((r["phase"], r["unknown_since"]), ("ready", T))
        obs(r, facts(**UP), T + 6)
        self.assertIsNone(r["unknown_since"])

    def test_null_between_does_not_reset(self):
        r = rec_in("ready")
        obs(r, facts(containers=False))
        obs(r, facts(containers=None, answers=None), T + 3)
        obs(r, facts(containers=False), T + 6)
        self.assertEqual(r["phase"], "stopped")


class Failed(unittest.TestCase):
    def test_process_starting(self):
        r = rec_in("failed")
        obs(r, facts(process=True))
        self.assertEqual(r["phase"], "starting")

    def test_otherwise_stays(self):
        r = rec_in("failed")
        obs(r, facts(**UP))
        obs(r, facts(**UP), T + 3)
        obs(r, facts(), T + 6)
        self.assertEqual(r["phase"], "failed")


class ActionPhasesIgnoreMeasurements(unittest.TestCase):
    def test_ignored(self):
        for phase in ("checking", "problem", "fixing", "notice", "stopping"):
            r = rec_in(phase)
            for i in range(3):
                self.assertEqual(obs(r, facts(process=True, failed=True, **UP), T + i), [])
            self.assertEqual(r["phase"], phase)


PRE = {"code": "platform", "kind": "precondition", "text": "Alleen vanaf de DAWO-USB-SSD."}
DOCKER = {"code": "runtime-down", "kind": "problem", "text": "Docker draait niet."}
HOSTS = {"code": "check:identity", "kind": "fixable", "text": "identity ontbreekt", "fix": "toevoegen"}
NOTICE = {"title": "Voorwaarden", "text": "...", "seconds": 60}


class Actions(unittest.TestCase):
    def test_check_precondition(self):
        self.assertEqual(L.check_outcome([PRE, DOCKER], NOTICE, False), ("stopped", PRE["text"]))

    def test_check_problems(self):
        self.assertEqual(L.check_outcome([DOCKER, HOSTS], NOTICE, False), ("problem", [DOCKER, HOSTS]))

    def test_check_notice(self):
        self.assertEqual(L.check_outcome([], NOTICE, False), ("notice", NOTICE))

    def test_check_notice_accepted_starts(self):
        self.assertEqual(L.check_outcome([], NOTICE, True), ("start", None))
        self.assertEqual(L.check_outcome([], None, False), ("start", None))

    def test_check_items_bad_json_is_a_problem(self):
        self.assertEqual(L.check_items("Traceback ...")[0]["kind"], "problem")
        self.assertEqual(L.check_items('{"ok": true, "items": []}'), [])

    def test_start_rc(self):
        self.assertEqual([L.start_outcome(rc) for rc in (0, 3, 4, 5, 1, 124)],
                         ["starting", "recheck", "stopped", "other-running", "failed", "failed"])

    def test_retry_same_first_code(self):
        self.assertEqual(L.retry_note("runtime-down", [DOCKER, HOSTS]), "Nog niet opgelost")
        self.assertEqual(L.retry_note("runtime-down", [HOSTS]), "")
        self.assertEqual(L.retry_note("runtime-down", []), "")

    def test_fix_rc(self):
        self.assertEqual([L.fix_note(rc) for rc in (0, 124, 1)],
                         ["Opgelost", "Er is geen toestemming gegeven", "Oplossen lukte niet"])

    def test_stop_from_before_the_start_at_once(self):
        for phase in ("problem", "notice", "checking"):
            self.assertIsNone(L.refuse("stop", rec_in(phase), [], busy=True))

    def test_stop_while_running(self):
        for phase in ("starting", "ready", "failed"):
            self.assertIsNone(L.refuse("stop", rec_in(phase), [], busy=False))
            self.assertTrue(L.refuse("stop", rec_in(phase), [], busy=True))
        self.assertTrue(L.refuse("stop", rec_in("stopped"), [], busy=False))

    def test_start_refusals(self):
        other = rec_in("ready")
        other["id"] = "valtimo"
        me = rec_in("stopped")
        self.assertIsNone(L.refuse("start", me, [me], busy=False))
        self.assertTrue(L.refuse("start", me, [me, other], busy=False))
        self.assertTrue(L.refuse("start", me, [me], busy=True))
        self.assertTrue(L.refuse("start", rec_in("ready"), [], busy=False))
        self.assertIsNone(L.refuse("start", rec_in("failed"), [], busy=False))
        me["precondition"] = "alleen op Linux"
        self.assertIn("alleen op Linux", L.refuse("start", me, [me], busy=False))
        self.assertTrue(L.refuse("start", rec_in("stopped"), [], busy=False, cycle_active=True))

    def test_retry_fix_accept_phases(self):
        p = rec_in("problem")
        p["problems"] = [DOCKER]
        self.assertIsNone(L.refuse("retry", p, [], busy=False))
        self.assertTrue(L.refuse("fix", p, [], busy=False))
        p["problems"] = [HOSTS]
        self.assertIsNone(L.refuse("fix", p, [], busy=False))
        self.assertIsNone(L.refuse("accept", rec_in("notice"), [], busy=False))
        self.assertTrue(L.refuse("accept", rec_in("stopped"), [], busy=False))
        s = rec_in("stopping")
        self.assertTrue(L.refuse("retry", s, [], busy=False))
        s["stop_error"] = "Stoppen lukte niet."
        self.assertIsNone(L.refuse("retry", s, [], busy=False))

    def test_two_active(self):
        a, b = rec_in("ready"), rec_in("starting")
        self.assertTrue(L.two_active([a, b]))
        self.assertFalse(L.two_active([a, rec_in("problem")]))

    def test_set_phase_stopped_clears(self):
        r = rec_in("failed")
        r.update(error="x", log_tail="y", answering=[{"url": "u"}], progress={"step": 1})
        self.assertEqual(L.set_phase(r, "stopped", "test", T), ("tezza", "failed", "stopped", "test"))
        self.assertEqual((r["error"], r["log_tail"], r["answering"], r["progress"]), ("", "", [], None))

    def test_public_shape(self):
        env = {"title": "Tezza", "url": "http://localhost:8082/", "login": "admin",
               "links": [{"title": "Beheer", "url": "http://localhost:8083/"}], "notice": NOTICE}
        p = L.public(rec_in("ready"), env, cycle_env="tezza")
        for k in ("id", "title", "summary", "version", "url", "login", "links", "licences", "components", "phase",
                  "precondition", "ended", "autostart", "problems", "problem_note", "notice", "progress", "error",
                  "log_tail", "answering", "stop_error", "not_answering_since", "unknown_since", "cycle_here"):
            self.assertIn(k, p)
        self.assertTrue(p["cycle_here"])
        self.assertFalse([k for k in p if k.startswith("_")])
        self.assertEqual(p["options"], [])

    def test_public_texts_per_language(self):
        env = {"title": {"nl": "Tezza", "en": "Tezza (en)"}, "summary": {"en": "Only English"},
               "links": [{"title": {"nl": "Beheer"}, "url": "u"}]}
        p = L.public(rec_in("stopped"), env)
        self.assertEqual((p["title"], p["summary"], p["links"][0]["title"]), ("Tezza", "Only English", "Beheer"))


NC = {"title": "Nextcloud", "options": [
    {"id": "pipelinq", "kind": "part", "title": {"nl": "Klantcontact (Pipelinq)", "en": "Customer contact"},
     "about": {"nl": "Contactmomenten"}, "default": True},
    {"id": "portaliq", "kind": "part", "title": "Portaal", "default": False},
    {"id": "demo", "kind": "data", "title": "Demozaken", "default": True, "needs": ["pipelinq"]},
    {"id": "Bad Id", "kind": "part", "title": "x"}]}


class Options(unittest.TestCase):
    def test_view_defaults_in_dutch(self):
        v = L.options_view(NC, None)
        self.assertEqual([(o["id"], o["on"]) for o in v], [("pipelinq", True), ("portaliq", False), ("demo", True)])
        self.assertEqual(v[0], {"id": "pipelinq", "kind": "part", "title": "Klantcontact (Pipelinq)",
                                "about": "Contactmomenten", "needs": [], "default": True, "on": True,
                                "unavailable": None})
        self.assertEqual(v[2]["needs"], ["pipelinq"])
        self.assertEqual(L.public(rec_in("stopped"), NC, saved={"portaliq": True})["options"][1]["on"], True)

    def test_view_applies_needs(self):
        v = L.options_view(NC, {"pipelinq": False, "demo": True})
        self.assertEqual([(o["id"], o["on"]) for o in v], [("pipelinq", False), ("portaliq", False), ("demo", False)])

    def test_change_keeps_only_differences(self):
        self.assertEqual(L.change_options(NC, None, {"portaliq": True}), ({"portaliq": True}, None))
        self.assertEqual(L.change_options(NC, {"portaliq": True}, {"portaliq": False}), ({}, None))
        # A part off takes its data set along when shown and used, but only the part is stored...
        self.assertEqual(L.change_options(NC, None, {"pipelinq": False}), ({"pipelinq": False}, None))
        self.assertEqual([o["on"] for o in L.options_view(NC, {"pipelinq": False}) if o["id"] == "demo"], [False])
        # ... so the data set comes back with its part.
        self.assertEqual(L.change_options(NC, {"pipelinq": False}, {"pipelinq": True}), ({}, None))
        self.assertEqual([o["on"] for o in L.options_view(NC, {}) if o["id"] == "demo"], [True])
        # A data set switched off by the person stays off when its part comes back.
        self.assertEqual(L.change_options(NC, {"pipelinq": False, "demo": False}, {"pipelinq": True}),
                         ({"demo": False}, None))

    def test_a_missing_source_is_off_and_cannot_go_on(self):
        env = {"id": "v", "options": [
            {"id": "here", "kind": "data", "source": {"path": "launcher", "title": "x"}},
            {"id": "gone", "kind": "data", "title": "Processen",
             "source": {"path": "sources/no-such-source", "title": {"nl": "de processen"}}}]}
        v = L.options_view(env, {"gone": True})
        self.assertEqual([(o["id"], o["on"]) for o in v], [("here", True), ("gone", False)])
        self.assertIsNone(v[0]["unavailable"])
        self.assertEqual(v[1]["unavailable"], "Bron ontbreekt: zet de processen in sources/no-such-source")
        new, why = L.change_options(env, None, {"gone": True})
        self.assertIsNone(new)
        self.assertIn("Bron ontbreekt", why)
        self.assertEqual(L.change_options(env, None, {"gone": False}), ({"gone": False}, None))
        # launch puts it off by itself: no --without for it.
        self.assertEqual(L.option_flags(env, None), [])

    def test_change_refuses_wrong_bodies(self):
        for body in (None, {}, [], {"nosuch": True}, {"demo": "yes"}, {"Bad Id": True}):
            new, why = L.change_options(NC, None, body)
            self.assertIsNone(new, body)
            self.assertTrue(why, body)

    def test_flags(self):
        self.assertEqual(L.option_flags(NC, None), [])
        self.assertEqual(L.option_flags(NC, {"portaliq": True, "pipelinq": False}),
                         ["--with", "portaliq", "--without", "pipelinq,demo"])
        self.assertEqual(L.option_flags({"title": "x"}, {"gone": False}), [])  # forgotten options pass nothing

    def test_txt(self):
        self.assertEqual(L.txt("a"), "a")
        self.assertEqual(L.txt({"en": "b"}), "b")
        self.assertEqual(L.txt({"nl": "a", "en": "b"}), "a")
        self.assertEqual(L.txt(None), "")


class OptionsEndpoint(unittest.TestCase):
    """POST /api/options/<id> without the server: user_options() on the module's state."""

    def setUp(self):
        import tempfile
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        old = (L.STATE, dict(L.CATALOG), dict(L.RECS), dict(L.CHOICES))

        def restore():
            L.STATE = old[0]
            for d, o in ((L.CATALOG, old[1]), (L.RECS, old[2]), (L.CHOICES, old[3])):
                d.clear()
                d.update(o)
        self.addCleanup(restore)
        L.STATE = tmp.name
        L.CATALOG.clear()
        L.CATALOG["nc"] = NC
        L.RECS.clear()
        L.RECS["nc"] = rec = L.new_record("nc")
        rec["phase"] = "stopped"
        L.CHOICES.clear()

    def test_saves_and_shows(self):
        self.assertEqual(L.user_options("nc", {"pipelinq": False}), (200, {"ok": True}))
        with open(L.choices_path(), encoding="utf-8") as f:
            self.assertEqual(L.json.load(f), {"nc": {"pipelinq": False}})
        self.assertEqual(L.load_choices(), {"nc": {"pipelinq": False}})
        st = [e for e in L.state()["envs"] if e["id"] == "nc"][0]
        self.assertEqual([o["on"] for o in st["options"]], [False, False, False])
        self.assertEqual(L.user_options("nc", {"pipelinq": True, "demo": True}), (200, {"ok": True}))
        self.assertEqual(L.load_choices(), {})

    def test_only_while_stopped(self):
        for phase in ("ready", "starting", "checking", "problem", "unknown"):
            L.RECS["nc"]["phase"] = phase
            code, body = L.user_options("nc", {"portaliq": True})
            self.assertEqual(code, 409)
            self.assertIn("niet draait", body["message"])
        self.assertEqual(L.user_options("nosuch", {"a": True})[0], 404)
        L.RECS["nc"]["phase"] = "stopped"
        self.assertEqual(L.user_options("nc", {"nosuch": True})[0], 400)

    def test_user_start_passes_the_choices_round_does_not(self):
        L.CHOICES["nc"] = {"portaliq": True}
        calls = []

        def fake_run(args, timeout=60):
            calls.append(args)
            return 0, "", ""
        orig = L.run
        L.run = fake_run
        try:
            L.flow_run_start("nc", L.RECS["nc"]["gen"], "user")
            L.flow_run_start("nc", L.RECS["nc"]["gen"], "cycle")
            L.flow_run_start("nc", L.RECS["nc"]["gen"], "auto")
        finally:
            L.run = orig
        self.assertEqual(calls, [["start", "nc", "--with", "portaliq"], ["start", "nc"], ["start", "nc"]])


if __name__ == "__main__":
    unittest.main()
