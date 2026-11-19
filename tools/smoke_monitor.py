"""Opt-in real media/Whisper smoke test, generated owned fixture; no YouTube calls."""
import json
import os
import subprocess
import sys
import time
import uuid
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
os.environ["HF_HUB_OFFLINE"] = "1"
from app import jobs, video, voice
from app.config import ROOT, CONFIG
from app.monitoring.buffer import RollingBuffer
from app.monitoring.detector import Detector
from app.monitoring import processing

out = ROOT / "artifacts" / ("monitor-smoke-" + uuid.uuid4().hex[:8])
out.mkdir(parents=True)
assert voice.model_ready(), "Local Kokoro model is needed for this generated fixture."
print("Generating original speech and video fixture", flush=True)
voice.synthesize("We are testing the automatic highlight recorder. Watch the action in the middle of the screen. Something surprising is about to happen. That was an incredible moment! Let's see that again. The clip should keep the setup and the full reaction.", voice.VOICE_BY_ID["af_heart"], out/"speech.wav")
subprocess.run(["ffmpeg","-v","error","-y","-f","lavfi","-i","testsrc2=s=640x360:r=25","-stream_loop","-1","-i",str(out/"speech.wav"),"-t","65","-c:v","libx264","-preset","ultrafast","-g","50","-c:a","aac",str(out/"source.mp4")],check=True,capture_output=True)
buffer = RollingBuffer(out/"buffer", min_free=0)
detector = Detector({"pre":20,"tail":5,"max":60})
CONFIG["whisper"].update(model="small", device="cpu", compute_type="int8")
# Offline cached small model on CPU avoids competing with an existing GPU job.
CONFIG["video"].update(width=720,height=1280)
job_id=None; at=0; event=None
try:
    buffer.start(out/"source.mp4",realtime=True)
    deadline=time.monotonic()+65
    print("Recording at live speed; injecting a labelled synthetic reaction at 26 seconds", flush=True)
    while time.monotonic()<deadline and not event:
        segments=buffer.refresh()
        end=segments[-1].end if segments else 0
        while at+1<=end:
            at+=1
            messages=[{"author":f"fixture-{i}","text":"LOL 😂"} for i in range(20)] if at==26 else []
            result=detector.call(at=at,messages=messages)
            if result.get("clip"):
                event=result["clip"];break
        time.sleep(.2)
    assert event and buffer.process.poll() is None, "Clip must be detected while source is still recording"
    monitor={"id":"fixture", "url":"https://www.youtube.com/watch?v=abcdefghijk", "aspect":"vertical","preset":"bold","translate":False}
    with patch.object(jobs,"JOBS_DIR",out/"jobs"),patch.object(jobs,"submit"):
        job_id=processing.enqueue(buffer,event,monitor,{"title":"Generated footage · local monitor validation", "creator":"ClipForge test"})
        buffer.discard()
        print("Exporting real MP4, transcribing with cached Whisper small/CPU, and rendering captions", flush=True)
        jobs.run_job(jobs.Job(job_id))
        st=jobs.read_status(job_id);directory=jobs.job_dir(job_id)
        assert st["state"]=="done"
        mi=video.probe(directory/"captioned.mp4")
        assert (mi.width,mi.height)==(720,1280) and abs(mi.duration-25)<.3
        transcript=json.loads((directory/"transcript.json").read_text(encoding="utf-8"))
        words=[w for s in transcript["segments"] for w in s["words"]]
        assert len(words)>20 and all(0<=w["start"]<=w["end"]<=mi.duration+.5 for w in words)
        said=" ".join(w["text"] for w in words).lower()
        expected=("automatic", "highlight", "recorder", "screen", "surprising", "incredible", "setup", "reaction")
        assert sum(word in said for word in expected)>=5, f"Transcript does not match the known speech: {said}"
        assert "-->" in (directory/"subtitles.srt").read_text(encoding="utf-8")
        report={"fixture":"original generated video and speech; synthetic chat", "provider_tested":False,"detected_while_recording":True,"event":event,"duration":mi.duration,"width":mi.width,"height":mi.height,"word_count":len(words),"language":transcript["language"],"whisper_model":transcript["model"],"output":str(directory/"captioned.mp4")}
        (out/"report.json").write_text(json.dumps(report,indent=2),encoding="utf-8")
        print(json.dumps(report,indent=2),flush=True)
finally:
    detector.close()
    if buffer.directory.exists():buffer.discard()
