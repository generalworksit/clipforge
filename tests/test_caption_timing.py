"""Caption timebase checks using generated media, not a speech-accuracy benchmark."""
import subprocess
import tempfile
import unittest
from pathlib import Path
import numpy as np
import soundfile as sf
from app import video, captions
from app import main, jobs
from fastapi.testclient import TestClient
from unittest.mock import patch


class CaptionTimingTests(unittest.TestCase):
    def test_api_requeues_caption_outputs_and_rebuilds_on_request(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(jobs,"JOBS_DIR",Path(tmp)/"jobs"), patch.object(jobs,"submit"):
            client=TestClient(main.app,base_url="http://127.0.0.1:8000")
            st=jobs.create("subtitles",{}, {"preset":"bold","position":"bottom","burn":True})
            job=jobs.Job(st["id"])
            for stage in job.status["stages"].values(): stage["state"]="done"
            jobs._finish(job)
            r=client.post(f"/api/jobs/{job.id}/caption-timing",data={"offset_ms":-300,"rebuild":"1"})
            self.assertEqual(r.status_code,200,r.text)
            current=jobs.read_status(job.id)
            self.assertEqual(current["options"]["caption_offset_ms"],-300)
            for name in ("transcribe","export","burn"): self.assertEqual(current["stages"][name]["state"],"pending")
            self.assertEqual(client.post(f"/api/jobs/{job.id}/caption-timing",data={"offset_ms":0}).status_code,409)
            client.close()

    def test_pause_splits_single_words_and_highlight_stops_at_speech_end(self):
        words=[{"text":"Hello","start":.2,"end":.5},{"text":"again","start":2,"end":2.5}]
        self.assertEqual(len(captions.group_words(words)),2)
        ass=captions.build_ass(words,3,"bold","bottom")
        self.assertIn("0:00:00.20,0:00:00.50",ass)
        self.assertNotIn("0:00:00.20,0:00:02.00",ass)

    def test_offset_is_bounded_and_does_not_mutate_source(self):
        words=[{"text":"Hello","start":.2,"end":.5},{"text":"again","start":2,"end":2.5}]
        earlier=captions.shift_words(words,-400,3)
        self.assertEqual(earlier[0]["start"],0)
        self.assertAlmostEqual(earlier[0]["end"],.1)
        self.assertEqual(words[0]["start"],.2)
        self.assertEqual(captions.shift_words(words,-1000,3)[0]["text"],"again")

    def test_delayed_audio_keeps_initial_silence(self):
        with tempfile.TemporaryDirectory() as tmp:
            src, wav = Path(tmp)/"delayed.mp4", Path(tmp)/"audio.wav"
            subprocess.run(["ffmpeg","-v","error","-y","-f","lavfi","-i","color=black:s=320x180:r=30:d=2",
                "-itsoffset","0.7","-f","lavfi","-i","sine=frequency=700:duration=1",
                "-c:v","libx264","-c:a","aac",str(src)],check=True,capture_output=True)
            video.extract_audio(src,wav,lambda _:None)
            samples,rate=sf.read(wav)
            onset=np.flatnonzero(np.abs(samples)>.03)[0]/rate
            self.assertAlmostEqual(onset,.7,delta=.05,msg="WAV must retain the source audio delay")


if __name__ == "__main__": unittest.main()
