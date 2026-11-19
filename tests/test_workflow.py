r"""Regression checks: run .venv\Scripts\python.exe -m unittest discover -s tests -v."""
import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from app import captions, discovery, main, video, voice
from app.effects import EFFECTS, light_filter


WORDS = [{"text": t, "start": i * 0.6, "end": i * 0.6 + 0.4}
         for i, t in enumerate("this is a clip with a complete story".split())]


class WorkflowTests(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(main.app, base_url="http://127.0.0.1:8000")

    def test_effect_validation_and_spacing(self):
        self.assertEqual(light_filter("none", 0.5, WORDS, 6), "")
        self.assertEqual(light_filter("rgb", 0, WORDS, 6), "")
        with self.assertRaises(ValueError):
            light_filter("rgb", float("nan"), WORDS, 6)
        with self.assertRaises(ValueError):
            light_filter("bad", 0.5, WORDS, 6)
        self.assertIn("between(t,0.000,0.200)", light_filter("rgb", 0.5, WORDS, 6))

    def test_create_preserves_edit_options_without_submitting_work(self):
        with patch.object(main.jobs, "create", return_value={"id": "0123456789ab"}) as create, patch.object(main.jobs, "submit") as submit:
            response = self.client.post("/api/jobs", data={"url": "https://www.youtube.com/watch?v=abcdefghijk", "preset": "traffic", "effect": "rgb", "effect_intensity": "0.7"})
            self.assertEqual(response.status_code, 201, response.text)
            self.assertEqual(create.call_args.args[2]["effect_intensity"], 0.7)
            self.assertEqual(create.call_args.args[2]["preset"], "traffic")
            submit.assert_called_once()
        for value in ("-1", "2", "nan", "inf"):
            response = self.client.post("/api/jobs", data={"effect": "rgb", "effect_intensity": value})
            self.assertEqual(response.status_code, 400)

    def test_effect_changes_requeue_caption_stage(self):
        status = {"state": "done", "kind": "clips"}
        with patch.object(main, "_idle_job", return_value=status), patch.object(main.jobs, "requeue", return_value=status) as requeue:
            response = self.client.post("/api/jobs/0123456789ab/effects", data={"effect": "green", "effect_intensity": "0.3"})
            self.assertEqual(response.status_code, 200)
            self.assertEqual(requeue.call_args.args[1], ["caption"])
        with patch.object(main, "_idle_job", return_value={"state": "running", "kind": "clips"}):
            self.assertEqual(self.client.post("/api/jobs/0123456789ab/effects").status_code, 409)

    def test_no_captions_create_restyle_and_sidecar(self):
        with patch.object(main.jobs, "create", return_value={"id": "0123456789ab"}) as create, patch.object(main.jobs, "submit"):
            result = self.client.post("/api/jobs", data={"url": "https://www.youtube.com/watch?v=abcdefghijk", "preset": "none", "effect": "green"})
            self.assertEqual(result.status_code, 201, result.text)
            self.assertEqual(create.call_args.args[2]["preset"], "none")
            self.assertEqual(create.call_args.args[2]["effect"], "green")
        status = {"state": "done", "kind": "clips", "options": {"erase_subs": False}}
        with patch.object(main, "_idle_job", return_value=status), patch.object(main.jobs, "requeue", return_value=status) as requeue:
            result = self.client.post("/api/jobs/0123456789ab/restyle", data={"preset": "none", "position": "bottom"})
            self.assertEqual(result.status_code, 200, result.text)
            self.assertEqual(requeue.call_args.args[1], ["caption"])
            self.assertEqual(requeue.call_args.args[2]["preset"], "none")
        with tempfile.TemporaryDirectory() as temp:
            ass, srt = Path(temp) / "clip.ass", Path(temp) / "clip.srt"
            captions.write_caption_files(WORDS, 0, 5, "none", "bottom", ass, srt)
            self.assertNotIn("Dialogue:", ass.read_text(encoding="utf-8"))
            self.assertIn("this is a clip", srt.read_text(encoding="utf-8"))
            self.assertIn("Dialogue:", captions.build_ass(WORDS, 5, "bold", "bottom"))

    def test_discovery_nulls_sort_and_validation(self):
        discovery._cache.clear()
        entries = [{"id": "abcdefghijk", "title": "A", "view_count": None},
                   {"id": "12345678901", "title": "B", "view_count": 400},
                   {"id": "bad/id"}, {"id": "zzzzzzzzzzz", "live_status": "is_live"}]
        with patch.object(discovery.yt_dlp.YoutubeDL, "extract_info", return_value={"entries": entries}):
            result = self.client.get("/api/discover", params={"q": "test topic", "sort": "views"})
            self.assertEqual(result.status_code, 200)
            videos = result.json()["videos"]
            self.assertEqual([v["title"] for v in videos], ["B", "A"])
            self.assertIsNone(videos[1]["views"])
        self.assertEqual(self.client.get("/api/discover?q=x").status_code, 400)
        with patch.object(discovery.yt_dlp.YoutubeDL, "extract_info", side_effect=RuntimeError()):
            self.assertEqual(self.client.get("/api/discover?q=network-failure").status_code, 502)

    def test_real_ffmpeg_caption_and_light_exports(self):
        # Exercise libass + the actual complex filter graph, including clips without audio.
        with tempfile.TemporaryDirectory(prefix="clipforge-render-") as temp:
            directory = Path(temp)
            (directory / "fonts").mkdir()
            video.run_ffmpeg(["-y", "-f", "lavfi", "-i", "color=c=black:s=270x480:d=5:r=30", "-c:v", "libx264", str(directory / "clean.mp4")], lambda _: None)
            new_styles = ["traffic", "ice", "headline", "lime", "impact", "editorial", "none"]
            for index, preset in enumerate(new_styles):
                (directory / "captions.ass").write_text(captions.build_ass(WORDS, 5, preset, "bottom", size=(270, 480)), encoding="utf-8")
                effect = list(EFFECTS)[index % len(EFFECTS)]
                chain = light_filter(effect, 0.5, WORDS, 5)
                video.burn_captions(directory, "clean.mp4", "captions.ass", f"{preset}.mp4", lambda _: None, pre_filter=chain)
                info = video.probe(directory / f"{preset}.mp4")
                self.assertEqual((info.width, info.height), (270, 480))
                self.assertAlmostEqual(info.duration, 5, delta=0.15)

    def test_separate_intro_pauses_clear_opening_then_restarts_from_zero(self):
        with tempfile.TemporaryDirectory(prefix="clipforge-intro-") as temp:
            directory = Path(temp)
            (directory / "fonts").mkdir()
            # A red opening followed by blue makes a skipped opening detectable.
            video.run_ffmpeg(["-y", "-f", "lavfi", "-i", "color=red:s=270x480:d=0.4:r=30",
                              "-f", "lavfi", "-i", "color=blue:s=270x480:d=1.6:r=30",
                              "-f", "lavfi", "-i", "sine=frequency=440:duration=2:sample_rate=48000",
                              "-filter_complex", "[0:v][1:v]concat=n=2:v=1:a=0[v]", "-map", "[v]", "-map", "2:a",
                              "-c:v", "libx264", "-c:a", "aac", "-ac", "2", str(directory / "clip.mp4")], lambda _: None)
            def speech(_text, _voice, path):
                video.run_ffmpeg(["-y", "-f", "lavfi", "-i", "sine=frequency=880:duration=0.8:sample_rate=48000", str(path)], lambda _: None)
                return 0.8
            log = []
            with patch.object(voice, "synthesize", side_effect=speech):
                name, spoken = voice.render(directory, "test", "clip.mp4", "clip.mp4", "Watch this", voice.VOICES[0], "intro", "bottom", log.append, size=(270, 480))
            info = video.probe(directory / name)
            self.assertTrue(info.has_audio)
            self.assertAlmostEqual(info.duration, 2 + spoken + 0.45, delta=0.12)
            self.assertFalse(any("gblur" in line or "eq=brightness" in line for line in log))
            def pixel(at):
                result = subprocess.run(["ffmpeg", "-v", "error", "-ss", str(at), "-i", str(directory / name),
                                         "-vf", "crop=4:4:4:4,format=rgb24", "-frames:v", "1", "-f", "rawvideo", "pipe:1"], capture_output=True, check=True)
                return tuple(result.stdout[:3])
            for at in (0.1, 0.7, 1.35):  # paused intro, paused intro, then original opening
                red, green, blue = pixel(at)
                self.assertGreater(red, 230)
                self.assertLess(blue, 20)
            self.assertGreater(pixel(2.1)[2], 230)  # the rest of the source plays after the opening


if __name__ == "__main__":
    unittest.main()
