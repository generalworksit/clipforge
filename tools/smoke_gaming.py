"""Opt-in real Whisper + FFmpeg check on the original generated monitor fixture."""
import json
import os
import sys
import uuid
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
os.environ["HF_HUB_OFFLINE"]="1"
from app import jobs, layouts, video
from app.config import ROOT, CONFIG

source=Path(sys.argv[1]).resolve() if len(sys.argv)>1 else None
if not source or not source.is_file():
    raise SystemExit("Pass a permitted local video containing English speech (at least 15 seconds).")
out=ROOT/"artifacts"/("gaming-smoke-"+uuid.uuid4().hex[:8])
out.mkdir(parents=True)
CONFIG["whisper"].update(model="small",device="cpu",compute_type="int8")
with patch.object(jobs,"JOBS_DIR",out/"jobs"):
    st=jobs.create("layout",{"type":"file","stored_as":"source.mp4"},
        {"layout":{**layouts.DEFAULT,"resolution":720},"preset":"bold","translate":False,"start":0,"end":15},
        title="Generated speech · gaming export validation")
    job=jobs.Job(st["id"])
    jobs.link_or_copy(source,job.dir/"source.mp4")
    print("Rendering 15 seconds and transcribing with cached Whisper small on CPU",flush=True)
    jobs.run_job(job)
    status=jobs.read_status(job.id)
    assert status["state"]=="done"
    transcript=json.loads((job.dir/"transcript.json").read_text(encoding="utf-8"))
    words=[w for s in transcript["segments"] for w in s["words"]]
    assert len(words)>=10 and all(0<=w["start"]<=w["end"]<=15.5 for w in words)
    info=video.probe(job.dir/"captioned.mp4")
    assert (info.width,info.height)==(720,1280) and abs(info.duration-15)<.1 and info.has_audio
    geometry=json.loads((job.dir/"layout.json").read_text(encoding="utf-8"))
    assert f"\\pos(360,{geometry['caption_y']})" in (job.dir/"captions.ass").read_text(encoding="utf-8")
    report={"fixture":"caller-supplied permitted local speech; no live provider used", "word_count":len(words),
            "language":transcript["language"],"dimensions":[info.width,info.height],"duration":info.duration,
            "caption_y":geometry["caption_y"],"transcript":" ".join(w["text"] for w in words),
            "output":str(job.dir/"captioned.mp4")}
    (out/"report.json").write_text(json.dumps(report,indent=2),encoding="utf-8")
    print(json.dumps(report,indent=2),flush=True)
