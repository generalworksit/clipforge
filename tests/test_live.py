"""Local bridge auth, validation, retry deduplication and real media export."""
import io
import json
import subprocess
import tempfile
import unittest
import uuid
import zipfile
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient
from app import jobs, live, main, video


def metadata(**changes):
    return {"id": str(uuid.uuid4()), "source_url": "https://www.twitch.tv/example", "title": "Test live highlight",
            "reason": "Chat reaction spike", "start": 0.4, "end": 3.4, "trigger": 2,
            "segments": [{"start": 0, "end": 2}, {"start": 2, "end": 4}], **changes}


class LiveTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.jobs = patch.object(jobs, "JOBS_DIR", self.root / "jobs")
        self.token = patch.object(live, "TOKEN_PATH", self.root / ".live-token")
        self.jobs.start(); self.token.start()
        self.client = TestClient(main.app, base_url="http://127.0.0.1:8000")
        self.key = self.client.post("/api/live/pair").json()["token"]
        self.headers = {"Authorization": f"Bearer {self.key}", "Origin": "chrome-extension://" + "a" * 32}

    def tearDown(self):
        self.jobs.stop(); self.token.stop(); self.tmp.cleanup()

    def test_pairing_and_origin_boundaries(self):
        self.assertEqual(self.client.get("/api/live/health").status_code, 401)
        self.assertEqual(self.client.get("/api/live/health", headers=self.headers).status_code, 200)
        self.assertEqual(self.client.post("/api/live/pair", headers={"Origin": "https://evil.example"}).status_code, 403)
        self.assertEqual(self.client.post("/api/live/pair", headers=self.headers).status_code, 403)
        self.assertEqual(self.client.post("/api/jobs", headers=self.headers).status_code, 403)
        r = self.client.options("/api/live/capture", headers=self.headers)
        self.assertEqual(r.headers["access-control-allow-origin"], self.headers["Origin"])
        self.assertNotIn("access-control-allow-origin", self.client.options("/api/live/pair", headers=self.headers).headers)
        bad = self.client.post("/api/live/capture", data=b"invalid", headers={"Origin": self.headers["Origin"]})
        self.assertEqual(bad.status_code, 401)
        self.assertEqual(bad.headers["access-control-allow-origin"], self.headers["Origin"])
        self.assertEqual(self.client.get("/api/live/setup").json()["paired"], True)
        self.assertNotIn(self.key, self.client.get("/api/live/setup").text)

    def test_delete_completed_video_and_protect_busy_jobs(self):
        st = jobs.create("live", {}, {"preset":"none"}, title="Delete fixture")
        directory = jobs.job_dir(st["id"])
        media = directory / "highlight.mp4"; media.write_bytes(b"test-file-only")
        for state in ("queued", "receiving", "running"):
            st["state"] = state; jobs._write_status(directory, st)
            self.assertEqual(self.client.delete(f"/api/jobs/{st['id']}").status_code, 409)
            self.assertTrue(media.exists())
        st["state"] = "done"; jobs._write_status(directory, st)
        self.assertEqual(self.client.delete(f"/api/jobs/{st['id']}", headers={"Origin":"https://evil.example"}).status_code,403)
        self.assertEqual(self.client.delete(f"/api/jobs/{st['id']}").status_code,200)
        self.assertFalse(directory.exists())
        self.assertEqual(self.client.get(f"/api/jobs/{st['id']}").status_code,404)

    def test_bad_metadata_and_media_never_queue(self):
        cases = [metadata(start=-1), metadata(end=500), metadata(trigger=30), metadata(end=float("nan")),
                 metadata(source_url="https://evil.example/x"), metadata(id="../../file"),
                 metadata(segments=[{"start": 0, "end": 2}, {"start": 3.5, "end": 4}])]
        with patch.object(jobs, "submit") as submit:
            for meta in cases:
                r = self.client.post("/api/live/capture", data={"metadata": json.dumps(meta)},
                                     files=[("files", ("a.webm", b"bad")), ("files", ("b.webm", b"bad"))], headers=self.headers)
                self.assertEqual(r.status_code, 400, r.text)
            r = self.client.post("/api/live/capture", data={"metadata": json.dumps(metadata())},
                                 files=[("files", ("a.webm", b"bad")), ("files", ("b.webm", b"bad"))], headers=self.headers)
            self.assertEqual(r.status_code, 400)
            submit.assert_not_called()
        self.assertEqual(jobs.list_jobs(), [])

    def test_real_webm_to_mp4_and_retry_idempotency(self):
        parts = []
        for i, color in enumerate(("red", "blue")):
            path = self.root / f"source-{i}.webm"
            subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", f"color={color}:s=320x180:r=30",
                            "-f", "lavfi", "-i", f"sine=frequency={440+i*220}:sample_rate=48000", "-t", "2",
                            "-c:v", "libvpx", "-deadline", "realtime", "-c:a", "libopus", str(path)], check=True, capture_output=True)
            parts.append(("files", (path.name, path.read_bytes(), "video/webm")))
        meta = metadata()
        with patch.object(jobs, "submit") as submit:
            r = self.client.post("/api/live/capture", data={"metadata": json.dumps(meta)}, files=parts, headers=self.headers)
            self.assertEqual(r.status_code, 200, r.text)
            job_id = r.json()["id"]
            duplicate = self.client.post("/api/live/capture", data={"metadata": json.dumps(meta)}, files=parts, headers=self.headers)
            self.assertEqual(duplicate.json(), {"id": job_id, "duplicate": True})
            submit.assert_called_once_with(job_id)
        jobs.run_job(jobs.Job(job_id))
        st = jobs.read_status(job_id)
        self.assertEqual(st["state"], "done")
        out = jobs.job_dir(job_id) / "highlight.mp4"
        info = video.probe(out)
        self.assertEqual((info.width, info.height, info.has_audio), (320, 180, True))
        self.assertAlmostEqual(info.duration, 3, delta=0.15)
        # Both independently recorded segments survive assembly and source trimming.
        for at, channel in ((0.2, 0), (2.5, 2)):
            raw = subprocess.run(["ffmpeg", "-v", "error", "-ss", str(at), "-i", str(out), "-frames:v", "1",
                                  "-vf", "scale=1:1", "-f", "rawvideo", "-pix_fmt", "rgb24", "pipe:1"], capture_output=True, check=True).stdout
            self.assertGreater(raw[channel], 200)
        self.assertEqual(st["options"]["preset"], "none")
        self.assertEqual(self.client.get(f"/files/{job_id}/highlight.mp4").status_code, 200)

    def test_download_contains_complete_extension_no_key(self):
        r = self.client.get("/api/live/extension.zip")
        with zipfile.ZipFile(io.BytesIO(r.content)) as archive:
            self.assertIn("manifest.json", archive.namelist())
            self.assertIn("offscreen.js", archive.namelist())
            self.assertNotIn(".live-token", archive.namelist())
            self.assertEqual(json.loads(archive.read("manifest.json"))["manifest_version"], 3)

    def test_detection_evidence_validation(self):
        evidence = {"version": 2, "signals": ["reaction_shift"], "score": 80, "unique": 12, "reactionShare": .7, "rateLift": 1.1}
        self.assertEqual(live.validate(json.dumps(metadata(detection=evidence)), 2)["detection"], evidence)
        requested = evidence | {"signals": ["viewer_requests"]}
        self.assertEqual(live.validate(json.dumps(metadata(detection=requested)), 2)["detection"], requested)
        from fastapi import HTTPException
        for changes in ({"score":101}, {"rateLift":float("nan")}, {"signals":["made_up"]}, {"reactionShare":1.5}):
            with self.assertRaises(HTTPException):
                live.validate(json.dumps(metadata(detection=evidence | changes)), 2)


if __name__ == "__main__":
    unittest.main()
