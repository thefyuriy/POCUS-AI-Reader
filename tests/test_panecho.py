# SPDX-License-Identifier: AGPL-3.0-only
"""PanEcho plumbing: window selection, result formatting, aggregation, background jobs and the API (fake engine)."""
import time
import unittest

from pocus_ai_reader import panecho
from pocus_ai_reader.jobs import Cancelled, JobManager
from pocus_ai_reader.library import LibraryError
from tests.support import ServerFixture, fake_mp4

TASKS = panecho.load_tasks()
BY_NAME = {t["name"]: t for t in TASKS}


class WindowTests(unittest.TestCase):
    def test_short_video_is_one_window(self):
        self.assertEqual(panecho.select_windows(10), [(0, 10)])
        self.assertEqual(panecho.select_windows(16), [(0, 16)])

    def test_windows_are_evenly_spaced_and_in_range(self):
        w = panecho.select_windows(100)
        self.assertEqual(len(w), 4)
        self.assertEqual(w[0], (0, 16))
        self.assertEqual(w[-1], (84, 100))
        self.assertTrue(all(b - a == 16 for a, b in w))

    def test_just_over_clip_len_has_no_duplicate_windows(self):
        w = panecho.select_windows(18)
        self.assertEqual(len(w), len(set(w)))
        self.assertTrue(all(0 <= a and b <= 18 for a, b in w))


class TaskTableTests(unittest.TestCase):
    def test_forty_tasks_with_required_fields(self):
        self.assertEqual(len(TASKS), 40)
        for t in TASKS:
            self.assertIn(t["type"], {"regression", "binary_classification", "multi-class_classification"})
            self.assertTrue(t["label"])
            if t["type"] == "binary_classification":
                self.assertTrue(t["positive"])
            if t["type"] == "multi-class_classification":
                self.assertGreaterEqual(len(t["classes"]), 3)

    def test_wording_of_positive_labels(self):
        self.assertEqual(BY_NAME["RVSystolicFunction"]["positive"], "Decreased")
        self.assertEqual(BY_NAME["RASize"]["positive"], "Dilated")


class FormatTests(unittest.TestCase):
    def raw(self):
        out = {}
        for t in TASKS:
            n = {"regression": 1, "binary_classification": 1}.get(t["type"], len(t["classes"]))
            out[t["name"]] = [0.5] * n
        return out

    def test_every_task_is_formatted_by_type(self):
        res = panecho.format_outputs([self.raw()], TASKS)
        self.assertEqual(set(res), set(BY_NAME))
        self.assertEqual(res["EF"], {"value": 0.5})
        self.assertEqual(res["RASize"], {"p": 0.5})
        self.assertEqual(set(res["RVSize"]["probs"]), set(BY_NAME["RVSize"]["classes"]))

    def test_gls_sign_is_flipped(self):
        self.assertEqual(panecho.format_outputs([{"GLS": [17.0]}], TASKS)["GLS"], {"value": -17.0})

    def test_windows_are_averaged(self):
        res = panecho.format_outputs([{"EF": [50.0]}, {"EF": [60.0]}], TASKS)
        self.assertEqual(res["EF"]["value"], 55.0)

    def test_non_finite_values_are_dropped(self):
        res = panecho.format_outputs([{"EF": [float("nan")], "TAPSE": [2.0]}], TASKS)
        self.assertNotIn("EF", res)
        self.assertIn("TAPSE", res)


class AggregateTests(unittest.TestCase):
    def test_mean_over_clips(self):
        a = {"tasks": {"EF": {"value": 50.0}, "RASize": {"p": 0.2}, "RVSize": {"probs": {c: 1 / 3 for c in BY_NAME["RVSize"]["classes"]}}}}
        b = {"tasks": {"EF": {"value": 60.0}, "RASize": {"p": 0.4}}}
        res = panecho.aggregate([a, b], TASKS)
        self.assertEqual(res["clips"], 2)
        self.assertAlmostEqual(res["tasks"]["EF"]["value"], 55.0)
        self.assertAlmostEqual(res["tasks"]["RASize"]["p"], 0.3)
        self.assertAlmostEqual(sum(res["tasks"]["RVSize"]["probs"].values()), 1.0)

    def test_empty_and_malformed_inputs(self):
        self.assertIsNone(panecho.aggregate([], TASKS))
        self.assertIsNone(panecho.aggregate(["x", None, {"tasks": 3}], TASKS))
        res = panecho.aggregate([{"tasks": {"EF": {"value": "oops"}, "TAPSE": {"value": 2.0}}}], TASKS)
        self.assertNotIn("EF", res["tasks"])
        self.assertEqual(res["tasks"]["TAPSE"]["value"], 2.0)


class JobTests(unittest.TestCase):
    def wait(self, jobs, state, timeout=5):
        end = time.time() + timeout
        while time.time() < end:
            cur = jobs.current()
            if cur and cur["state"] == state:
                return cur
            time.sleep(0.01)
        self.fail(f"job never reached {state}: {jobs.current()}")

    def test_success_failure_and_one_at_a_time(self):
        jobs = JobManager()
        gate = []

        def slow(job):
            job.update(0.5, "half")
            while not gate:
                time.sleep(0.01)

        j = jobs.start("t", "slow", slow)
        with self.assertRaises(LibraryError) as ctx:
            jobs.start("t", "second", slow)
        self.assertEqual(ctx.exception.status, 409)
        self.assertEqual(jobs.current()["message"] in ("half", ""), True)
        gate.append(1)
        self.assertEqual(self.wait(jobs, "done")["progress"], 1.0)

        def boom(job):
            raise RuntimeError("bad")

        jobs.start("t", "boom", boom)
        self.assertIn("bad", self.wait(jobs, "failed")["error"])

    def test_cancel(self):
        jobs = JobManager()

        def loop(job):
            while True:
                job.update(0.1)
                time.sleep(0.01)

        j = jobs.start("t", "loop", loop)
        jobs.cancel(j.id)
        self.wait(jobs, "cancelled")
        jobs.start("t", "again", lambda job: None)  # the slot is free again


class FakeEngine:
    def __init__(self, installed=True, fail=False):
        self.installed, self.fail, self.analyzed, self.downloads = installed, fail, [], 0

    def is_installed(self):
        return self.installed

    def status(self):
        return {"installed": self.installed, "download_mb": 505, "license": "CC BY-NC-SA 4.0 (non-commercial)", "tasks": TASKS}

    def download(self, job):
        self.downloads += 1
        job.update(0.5, "half way")
        self.installed = True

    def analyze_clip(self, path):
        if self.fail:
            raise LibraryError("This clip could not be read for analysis.", 415)
        self.analyzed.append(path.name)
        return {"model": "PanEcho", "analyzed": "2026-01-01T00:00:00+00:00", "frames": 20, "windows": 2,
                "tasks": {"EF": {"value": 55.0}, "RASize": {"p": 0.1}}}


class ApiTests(unittest.TestCase):
    def setUp(self):
        self.engine = FakeEngine()
        self.fx = ServerFixture(engine=self.engine)
        self.addCleanup(self.fx.close)
        self.c = self.fx.client
        r = self.c.upload("/api/archives", [("label", "t"), ("confirm_deidentified", "true")],
                          [("files", "A4C.mp4", fake_mp4()), ("files", "PLAX.mp4", fake_mp4()), ("files", "x.png", b"\x89PNG" + b"0" * 50)])
        self.assertEqual(r.status, 201, r.body)
        self.aid = r.json()["id"]
        self.clips = r.json()["clips"]

    def wait_job(self, state="done", timeout=5):
        end = time.time() + timeout
        while time.time() < end:
            cur = self.c.get("/api/jobs/current").json()
            if cur and cur["state"] == state:
                return cur
            time.sleep(0.02)
        self.fail(f"never {state}: {cur}")

    def test_status_lists_tasks(self):
        s = self.c.get("/api/ai/status").json()
        self.assertTrue(s["installed"])
        self.assertEqual(len(s["tasks"]), 40)

    def test_analyze_archive_skips_stills_and_stores_results(self):
        r = self.c.send_json("POST", "/api/ai/analyze", {"archive_id": self.aid})
        self.assertEqual(r.status, 202, r.body)
        self.assertEqual(r.json()["archive_id"], self.aid)
        self.wait_job()
        self.assertEqual(len(self.engine.analyzed), 2)
        a = self.c.get(f"/api/archives/{self.aid}").json()
        done = [c for c in a["clips"] if "panecho" in c["results"]]
        self.assertEqual(len(done), 2)
        self.assertEqual(a["panecho_study"]["clips"], 2)
        self.assertAlmostEqual(a["panecho_study"]["tasks"]["EF"]["value"], 55.0)
        summary = [x for x in self.c.get("/api/archives").json() if x["id"] == self.aid][0]
        self.assertEqual(summary["ai_status"], "analyzed")

    def test_analyze_one_clip_is_partial(self):
        cid = [c for c in self.clips if c["kind"] == "clip"][0]["id"]
        self.c.send_json("POST", "/api/ai/analyze", {"archive_id": self.aid, "clip_ids": [cid]})
        self.wait_job()
        summary = [x for x in self.c.get("/api/archives").json() if x["id"] == self.aid][0]
        self.assertEqual(summary["ai_status"], "partial")

    def test_only_a_still_is_rejected(self):
        cid = [c for c in self.clips if c["kind"] == "still"][0]["id"]
        r = self.c.send_json("POST", "/api/ai/analyze", {"archive_id": self.aid, "clip_ids": [cid]})
        self.assertEqual(r.status, 400)

    def test_not_installed_must_download_first(self):
        self.engine.installed = False
        r = self.c.send_json("POST", "/api/ai/analyze", {"archive_id": self.aid})
        self.assertEqual(r.status, 409)
        r = self.c.send_json("POST", "/api/ai/download", {})
        self.assertEqual(r.status, 202)
        self.wait_job()
        self.assertEqual(self.engine.downloads, 1)
        self.assertEqual(self.c.send_json("POST", "/api/ai/download", {}).status, 409)  # already installed

    def test_failure_is_reported_not_stored(self):
        self.engine.fail = True
        self.c.send_json("POST", "/api/ai/analyze", {"archive_id": self.aid})
        job = self.wait_job("failed")
        self.assertIn("could not be read", job["error"])
        a = self.c.get(f"/api/archives/{self.aid}").json()
        self.assertTrue(all("panecho" not in c["results"] for c in a["clips"]))

    def test_bad_requests(self):
        self.assertEqual(self.c.send_json("POST", "/api/ai/analyze", {}).status, 400)
        self.assertEqual(self.c.send_json("POST", "/api/ai/analyze", {"archive_id": "0" * 32}).status, 404)
        self.assertEqual(self.c.send_json("POST", "/api/ai/analyze", {"archive_id": self.aid, "clip_ids": "x"}).status, 400)

    def test_ai_routes_need_the_token(self):
        from tests.support import Client
        anon = Client(self.fx.port, cookie=False)
        self.assertEqual(anon.get("/api/ai/status").status, 401)
        self.assertEqual(anon.send_json("POST", "/api/ai/analyze", {"archive_id": self.aid}).status, 401)


if __name__ == "__main__":
    unittest.main()
