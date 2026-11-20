"""Real FFmpeg layout geometry, trim/audio, safe API validation and subtitle placement."""
import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient
from app import jobs, main, layouts, video, captions


def make_fixture(path, audio=True):
    cmd = ["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i",
           "color=blue:s=640x360:r=30,drawbox=x=0:y=0:w=160:h=180:color=red:t=fill"]
    if audio:
        cmd += ["-f", "lavfi", "-i", "sine=frequency=500:sample_rate=48000"]
    subprocess.run(cmd + ["-t", "2", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", str(path)], check=True, capture_output=True)


class LayoutTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.store = patch.object(jobs, "JOBS_DIR", self.root/"jobs")
        self.store.start()
        self.client = TestClient(main.app, base_url="http://127.0.0.1:8000")
        self.settings = {**layouts.DEFAULT, "camera": [0,0,.25,.5], "game": [.5,0,.5,1], "share": .4,
                         "camera_fit": "cover", "game_fit": "cover", "resolution": 720}

    def tearDown(self):
        self.client.close(); self.store.stop(); self.tmp.cleanup()

    def create(self, *, preset="none", audio=True, **changes):
        source = self.root/"fixture.mp4"
        make_fixture(source, audio)
        with patch.object(jobs, "submit") as submit:
            response = self.client.post("/api/jobs", data={"kind":"layout", "layout":json.dumps(self.settings),
                "preset":preset,"start":.25,"end":1.75,**changes}, files={"file":("fixture.mp4",source.read_bytes(),"video/mp4")})
        self.assertEqual(response.status_code, 201, response.text)
        submit.assert_called_once()
        return jobs.Job(response.json()["id"])

    def pixel(self, path, x, y):
        return subprocess.run(["ffmpeg","-v","error","-i",str(path),"-frames:v","1","-vf",f"crop=2:2:{x}:{y},scale=1:1",
                               "-pix_fmt","rgb24","-f","rawvideo","pipe:1"],capture_output=True,check=True).stdout[:3]

    def test_invalid_settings_and_foreign_origin(self):
        for bad in ({"camera":[.9,0,.2,.5]}, {"share":float("nan")}, {"game":[0,0,0,1]},
                    {"mode":"x;movie=file"}, {"mirror":"false"}, {"resolution":16384}, {"extra":1}):
            response = self.client.post("/api/layouts/plan", data={"layout":json.dumps(bad),"width":640,"height":360})
            self.assertEqual(response.status_code,400,response.text)
        self.assertEqual(self.client.post("/api/layouts/plan",data={"layout":"{}","width":640,"height":360},headers={"Origin":"https://evil.example"}).status_code,403)
        self.assertEqual(self.client.post("/api/jobs",data={"kind":"layout","preset":"none","layout":"{}","start":0,"end":181}).status_code,400)

    def test_stack_real_pixels_trim_and_audio(self):
        job = self.create()
        with patch("app.transcribe.transcribe") as transcribe:
            jobs.run_job(job)
            transcribe.assert_not_called()
        self.assertEqual(jobs.read_status(job.id)["state"],"done")
        path = job.dir/"gaming.mp4"
        info = video.probe(path)
        self.assertEqual((info.width,info.height,info.has_audio),(720,1280,True))
        self.assertAlmostEqual(info.duration,1.5,delta=.08)
        self.assertGreater(self.pixel(path,360,150)[0],220)
        self.assertGreater(self.pixel(path,360,1000)[2],220)
        self.assertTrue((job.dir/"source.mp4").exists())
        self.assertEqual(json.loads((job.dir/"layout.json").read_text())["settings"]["game"],[.5,0,.5,1])

    def test_game_only_without_audio(self):
        self.settings["mode"]="game"
        job=self.create(audio=False)
        jobs.run_job(job)
        self.assertFalse(video.probe(job.dir/"gaming.mp4").has_audio)
        self.assertGreater(self.pixel(job.dir/"gaming.mp4",360,150)[2],220)

    def test_overlay_and_swap(self):
        self.settings.update(mode="pip",share=.4)
        job=self.create()
        jobs.run_job(job)
        self.assertGreater(self.pixel(job.dir/"gaming.mp4",100,150)[0],220)
        self.assertGreater(self.pixel(job.dir/"gaming.mp4",500,1100)[2],220)
        self.settings.update(mode="stack",order="game")
        second=self.create()
        jobs.run_job(second)
        self.assertGreater(self.pixel(second.dir/"gaming.mp4",360,150)[2],220)
        self.assertGreater(self.pixel(second.dir/"gaming.mp4",360,1100)[0],220)

    def test_caption_render_with_explicit_fixture_transcript(self):
        job=self.create(preset="bold")
        # This tests timing/compositing, not speech recognition accuracy.
        transcript={"language":"en","segments":[{"start":0,"end":1.4,"text":"Hello world","words":[
            {"start":.1,"end":.6,"text":"Hello"},{"start":.7,"end":1.3,"text":"world"}]}]}
        with patch("app.transcribe.transcribe",return_value=transcript):
            jobs.run_job(job)
        ass=(job.dir/"captions.ass").read_text(encoding="utf-8")
        self.assertIn(r"\pos(360,512)",ass)
        self.assertIn("Hello",(job.dir/"subtitles.srt").read_text())
        self.assertAlmostEqual(video.probe(job.dir/"captioned.mp4").duration,1.5,delta=.08)

    def test_keep_whole_region_never_stretches(self):
        self.settings["game_fit"]="contain"
        job=self.create()
        jobs.run_job(job)
        self.assertLess(max(self.pixel(job.dir/"gaming.mp4",5,520)),30)
        self.assertGreater(self.pixel(job.dir/"gaming.mp4",360,950)[2],220)


if __name__ == "__main__":
    unittest.main()
