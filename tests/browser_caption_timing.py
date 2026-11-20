"""Actual correction UI/API/FFmpeg; explicit transcript fixture, no ASR claim."""
import json
import sys
import socket
import tempfile
import threading
import time
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import uvicorn
from playwright.sync_api import sync_playwright,expect
from app import main,jobs,video
from test_layouts import make_fixture

ROOT=Path(__file__).resolve().parents[1];PORT=8025;BASE=f"http://127.0.0.1:{PORT}"
main.LOCAL_HOSTS.add(f"127.0.0.1:{PORT}")
with tempfile.TemporaryDirectory() as tmp,patch.object(jobs,"JOBS_DIR",Path(tmp)/"jobs"),patch.object(jobs,"submit"):
    job=jobs.Job(jobs.create("subtitles",{"type":"upload","stored_as":"source.mp4"},{"preset":"bold","position":"bottom","burn":True})["id"])
    make_fixture(job.dir/"source.mp4")
    transcript={"language":"en","model":"fixture","device":"fixture","segments":[{"start":.7,"end":1.6,"text":"Hello world","words":[{"text":"Hello","start":.7,"end":1},{"text":"world","start":1.2,"end":1.6}]}]}
    (job.dir/"transcript.json").write_text(json.dumps(transcript))
    job.status["stages"]["transcribe"]["state"]="done";job.save();jobs.run_job(job)
    assert jobs.read_status(job.id)["state"]=="done"
    server=uvicorn.Server(uvicorn.Config(main.app,host="127.0.0.1",port=PORT,lifespan="off",log_level="error"))
    thread=threading.Thread(target=server.run,daemon=True);thread.start()
    for _ in range(50):
        try:
            with socket.create_connection(("127.0.0.1",PORT),timeout=.2):break
        except OSError:time.sleep(.1)
    try:
        with sync_playwright() as p:
            browser=p.chromium.launch(channel="chrome",headless=True);page=browser.new_page()
            errors=[];page.on("pageerror",lambda e:errors.append(str(e)))
            page.goto(f"{BASE}/#/job/{job.id}")
            page.get_by_text("Adjust caption timing",exact=True).click()
            page.locator('#caption-timing-form [name=offset_ms]').fill("300")
            page.get_by_role("button",name="Apply timing",exact=True).click()
            page.wait_for_function("document.getElementById('caption-timing-form')===null")
            assert jobs.read_status(job.id)["state"]=="queued"
            jobs.run_job(jobs.Job(job.id));page.reload()
            expect(page.locator('#caption-timing-form [name=offset_ms]')).to_have_value("300")
            assert "00:00:01,000" in (job.dir/"subtitles.srt").read_text()
            assert "0:00:01.00,0:00:01.30" in (job.dir/"captions.ass").read_text()
            assert video.probe(job.dir/"captioned.mp4").has_audio
            assert json.loads((job.dir/"transcript.json").read_text())==transcript
            assert not errors,errors;browser.close()
    finally:server.should_exit=True;thread.join(timeout=5)
print("PASS: caption timing UI, real re-export, synchronized SRT/ASS adjustment, original transcript retained (explicit speech fixture).")
