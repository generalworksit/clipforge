"""Real local media + detector tests; provider responses below are explicit fixtures."""
import os
import tempfile
import time
import subprocess
import unittest
from pathlib import Path
from unittest.mock import patch, Mock
from fastapi.testclient import TestClient
from app import main, jobs, video
from app.monitoring import store, credentials, youtube, processing
from app.monitoring.buffer import RollingBuffer, audio_levels
from app.monitoring.detector import Detector
from app.monitoring.service import Supervisor

URL = "https://www.youtube.com/watch?v=abcdefghijk"


class MonitorTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.patches = [patch.object(store, "DATA", self.root / "monitor"), patch.object(jobs, "JOBS_DIR", self.root / "jobs")]
        for p in self.patches: p.start()
        self.client = TestClient(main.app, base_url="http://127.0.0.1:8000")

    def tearDown(self):
        for p in reversed(self.patches): p.stop()
        self.tmp.cleanup()

    def add(self, **kwargs):
        r = self.client.post("/api/monitor", json={"url": URL, **kwargs})
        self.assertEqual(r.status_code, 200, r.text)
        return r.json()["id"]

    def test_settings_auth_limits_and_encrypted_credentials(self):
        key = "test-only-never-a-real-api-key"
        self.assertEqual(self.client.post("/api/monitor/key", json={"key": key}, headers={"Origin": "https://evil.example"}).status_code, 403)
        self.assertEqual(self.client.post("/api/monitor/key", json={"key": key}).status_code, 200)
        self.assertEqual(credentials.get_key(), key)
        key_file = store.DATA / "youtube-key.dpapi"
        if os.name == "nt":  # DPAPI encrypts at rest; elsewhere the file is owner-only
            self.assertNotIn(key.encode(), key_file.read_bytes())
        else:
            self.assertEqual(key_file.stat().st_mode & 0o777, 0o600)
        first = self.add(feed="https://example.com/live.m3u8?secret=test", pre=35, preset="none")
        second = self.add()
        data = self.client.get("/api/monitor").json()
        self.assertNotIn(key, str(data)); self.assertNotIn("secret=test", str(data))
        self.assertTrue(next(c for c in data["monitors"] if c["id"] == first)["has_feed"])
        self.assertEqual(self.client.post(f"/api/monitor/{first}/toggle").status_code, 200)
        self.assertEqual(self.client.post(f"/api/monitor/{second}/toggle").status_code, 200)
        self.assertEqual(self.client.put("/api/monitor/settings",json={"max_active":5}).status_code,422)
        self.assertEqual(self.client.put("/api/monitor/settings",json={"max_active":2,"reserve_gb":4}).status_code,200)
        self.assertEqual(self.client.get("/api/monitor").json()["settings"]["reserve_gb"],4)
        self.assertEqual(self.client.delete(f"/api/monitor/{first}").status_code, 409)
        self.assertEqual(self.client.put(f"/api/monitor/{first}", json={"url": URL}).status_code, 409)
        self.client.post(f"/api/monitor/{first}/toggle")
        self.assertEqual(self.client.put(f"/api/monitor/{first}", json={"url": URL, "pre": 25}).status_code, 200)
        c = next(c for c in store.all_monitors() if c["id"] == first)
        self.assertEqual(c["pre"], 25); self.assertTrue(c["feed"])
        self.assertEqual(self.client.post("/api/monitor", json={"url": "https://www.youtube.com/@channel"}).status_code, 400)
        self.assertEqual(self.client.post("/api/monitor", json={"url": URL, "pre": 900}).status_code, 422)

    def test_resume_missing_key_reports_setup_without_network(self):
        mid = self.add(enabled=True)
        supervisor = Supervisor()
        with patch.object(youtube.requests, "get") as request:
            try:
                supervisor.start()
                deadline = time.monotonic() + 5
                while time.monotonic() < deadline and store.all_monitors()[0]["runtime"]["state"] != "needs_setup":
                    time.sleep(.1)
                self.assertEqual(store.all_monitors()[0]["runtime"]["state"], "needs_setup")
                self.assertEqual(store.all_monitors()[0]["id"], mid)
                request.assert_not_called()
            finally:
                supervisor.close()
        self.assertFalse(supervisor.watch.is_alive())

    def test_provider_contract_and_quota_guard(self):
        response = Mock(status_code=200)
        response.json.return_value = {"items": [{"id": "abcdefghijk", "snippet": {"liveBroadcastContent": "live", "title": "Fixture"}, "liveStreamingDetails": {"activeLiveChatId": "chat"}}]}
        with patch.object(youtube.requests, "get", return_value=response):
            self.assertEqual(youtube.status(URL, "fixture-key")["chat_id"], "chat")
        Request, Response = youtube.message_types()
        req = Request(live_chat_id="abc", part=["snippet"])
        self.assertTrue(req.SerializeToString().startswith(b"\x0a\x03abc"))
        reply = Response(next_page_token="resume")
        message = reply.items.add(id="message"); message.snippet.display_message = "LOL"; message.snippet.type = 1
        self.assertEqual(Response.FromString(reply.SerializeToString()).items[0].snippet.display_message, "LOL")
        store.spend(8499)
        with self.assertRaises(RuntimeError): store.spend(1)

    def test_connection_errors_are_actionable_and_never_echo_key(self):
        self.add()
        credentials.save_key("test-only-never-a-real-api-key")
        response=Mock(status_code=400)
        response.json.return_value={"error":{"message":"API key not valid. Please pass a valid API key.","errors":[{"reason":"badRequest"}]}}
        with patch.object(youtube.requests,"get",return_value=response):
            r=self.client.post("/api/monitor/key/check")
        self.assertEqual(r.status_code,400)
        self.assertIn("Google Cloud",r.json()["detail"])
        self.assertNotIn(credentials.get_key(),r.text)
        with patch.object(youtube,"status",return_value={"state":"live","chat_id":"fixture"}):
            self.assertEqual(self.client.post("/api/monitor/key/check").json(),{"state":"live","chat_available":True})

    def test_shared_detector_candidate_pre_roll_tail_and_duplicates(self):
        d = Detector({"pre": 20, "tail": 5, "max": 60})
        try:
            for second in range(1, 26): d.call(at=second, messages=[])
            burst = [{"author": f"u{i}", "text": "LMAO 😂"} for i in range(20)]
            result = d.call(at=26, messages=burst)
            self.assertIsNotNone(result["active"])
            for second in range(27, 32): result = d.call(at=second, messages=burst)
            clip = result["clip"]
            self.assertEqual(clip["start"], 6); self.assertEqual(clip["end"], 31)
            self.assertGreater(clip["evidence"]["score"], 70)
            self.assertIsNone(d.call(at=32, messages=burst)["clip"])
        finally: d.close()

    def test_real_rolling_buffer_pins_video_for_queue_and_exports(self):
        src = self.root / "source.mp4"
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc2=s=320x180:r=25", "-f", "lavfi", "-i", "sine=frequency=550:sample_rate=48000", "-t", "10", "-c:v", "libx264", "-preset", "ultrafast", "-g", "50", "-c:a", "aac", str(src)], capture_output=True, check=True)
        buffer = RollingBuffer(self.root / "buffer", seconds=5, min_free=0)
        try:
            buffer.start(src); buffer.process.wait(timeout=20)
            parts = buffer.refresh()
            self.assertGreater(parts[-1].end, 9)
            self.assertTrue(all(s.end >= parts[-1].end - 5 for s in parts))
            self.assertGreater(audio_levels(parts[-1])[0][1], .01)
            with patch.object(jobs, "submit") as submit:
                mid = self.add()
                c = store.all_monitors()[0]
                job_id = processing.enqueue(buffer, {"start": 6, "end": 9, "trigger": 7, "evidence": {"score": 80}}, c, {"title": "Generated test footage"})
                submit.assert_called_once_with(job_id)
            buffer.discard()
            jobs.run_job(jobs.Job(job_id))
            st = jobs.read_status(job_id)
            self.assertEqual(st["state"], "done")
            mi = video.probe(jobs.job_dir(job_id) / "clean.mp4")
            self.assertAlmostEqual(mi.duration, 3, delta=.15)
            self.assertEqual((mi.width, mi.height, mi.has_audio), (320,180,True))
            self.assertFalse((jobs.job_dir(job_id) / "transcript.json").exists())
        finally:
            if buffer.directory.exists(): buffer.discard()

    def test_disk_limit_and_incomplete_capture(self):
        buffer = RollingBuffer(self.root / "buffer", min_free=10**20)
        with self.assertRaises(RuntimeError): buffer.check_space()
        st = jobs.create("autoclip", {}, {"aspect": "landscape", "preset": "none", "translate": False})
        with self.assertRaisesRegex(RuntimeError, "interrupted"):
            jobs.run_job(jobs.Job(st["id"]))

    def test_crashed_export_requeues_and_incomplete_capture_does_not(self):
        opts = {"aspect":"landscape", "preset":"none", "translate":False}
        saved = jobs.create("autoclip", {"segments":["capture-0000.ts"]}, opts, state="running")
        unfinished = jobs.create("autoclip", {}, opts, state="receiving")
        with patch.object(jobs, "acquire_lock"), patch.object(jobs.threading, "Thread"), patch.object(jobs, "submit") as submit:
            jobs.start_worker()
            submit.assert_called_once_with(saved["id"])
        self.assertEqual(jobs.read_status(saved["id"])["state"], "queued")
        self.assertEqual(jobs.read_status(unfinished["id"])["state"], "error")

    def test_backpressure_rejects_new_clip_before_copying(self):
        for _ in range(10):
            jobs.create("autoclip", {}, {"aspect":"landscape", "preset":"none", "translate":False})
        buffer = Mock()
        with self.assertRaisesRegex(RuntimeError, "queue is full"):
            processing.enqueue(buffer, {}, {}, {})
        buffer.snapshot.assert_not_called()

    def test_disk_reserve_stops_provider_calls(self):
        self.add(enabled=True)
        credentials.save_key("fake-test-key-never-send-this")
        supervisor = Supervisor()
        with patch("app.monitoring.service.shutil.disk_usage", return_value=Mock(free=100)), patch.object(youtube.requests, "get") as request:
            try:
                supervisor.start()
                deadline=time.monotonic()+5
                while time.monotonic()<deadline and store.all_monitors()[0]["runtime"]["state"] != "storage_full":
                    time.sleep(.1)
                self.assertEqual(store.all_monitors()[0]["runtime"]["state"], "storage_full")
                request.assert_not_called()
            finally: supervisor.close()


if __name__ == "__main__": unittest.main()
