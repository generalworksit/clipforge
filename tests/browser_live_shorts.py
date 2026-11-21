"""Live-to-Shorts UI, real selection/rendering, explicit transcript fixture (no ASR claim)."""
import json
import socket
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import uvicorn
from playwright.sync_api import sync_playwright,expect
from app import main,jobs,video,transcribe,moments
from app.config import CONFIG
from test_layouts import make_fixture

ROOT=Path(__file__).resolve().parents[1];PORT=8027;BASE=f"http://127.0.0.1:{PORT}"
main.LOCAL_HOSTS.add(f"127.0.0.1:{PORT}")
def transcript_fixture(audio,out_json,duration,*args,**kwargs):
    text="Watch the action now. That was an incredible win! I cannot believe that happened. What a surprising moment."
    tokens=(text+" "+text).split();words=[{"text":w,"start":i*.65,"end":i*.65+.5} for i,w in enumerate(tokens)]
    data={"language":"en","model":"fixture","device":"fixture","duration":duration,"segments":[{"start":0,"end":duration,"text":text,"words":words}]}
    out_json.write_text(json.dumps(data));return data

with tempfile.TemporaryDirectory() as tmp,patch.object(jobs,"JOBS_DIR",Path(tmp)/"jobs"),patch.object(jobs,"submit"),patch.object(transcribe,"transcribe",side_effect=transcript_fixture),patch.object(moments,"get_provider",return_value=(None,"Explicit heuristic test")),patch.dict(CONFIG["video"],{"width":720,"height":1280}):
    source=jobs.Job(jobs.create("live",{}, {"preset":"none"},title="Generated stream fixture")["id"])
    make_fixture(Path(tmp)/"short.mp4")
    subprocess.run(["ffmpeg","-v","error","-y","-stream_loop","14","-i",str(Path(tmp)/"short.mp4"),"-c","copy",str(source.dir/"highlight.mp4")],check=True,capture_output=True)
    source.output(source.dir/"highlight.mp4","Captured moment","video",duration=30,width=640,height=360);jobs._finish(source)
    server=uvicorn.Server(uvicorn.Config(main.app,host="127.0.0.1",port=PORT,lifespan="off",log_level="error"));thread=threading.Thread(target=server.run,daemon=True);thread.start()
    for _ in range(50):
        try:
            with socket.create_connection(("127.0.0.1",PORT),timeout=.2):break
        except OSError:time.sleep(.1)
    try:
        with sync_playwright() as p:
            browser=p.chromium.launch(channel="chrome",headless=True);page=browser.new_page(viewport={"width":1440,"height":1000});errors=[]
            page.on("pageerror",lambda e:errors.append(str(e)))
            page.goto(BASE+"/#/live")
            expect(page.locator("#live-export-preset")).to_have_value("bold")
            page.locator("#live-inbox").get_by_role("link",name="Find highlights",exact=True).click()
            expect(page.locator('#live-make-form [name=clips]')).to_have_value("1")
            expect(page.locator('#live-source [aria-checked=true]')).to_have_count(1)
            page.locator("#live-make-form").get_by_role("button",name="Find highlights",exact=True).click()
            expect(page.locator("#live-make-status")).to_contain_text("Added to the queue")
            queued=jobs.list_jobs("clips");assert len(queued)==1
            jid=queued[0]["id"];options=queued[0]["options"]
            assert options["workflow"]=="live" and options["preset"]=="bold" and options["framing"]=="blur" and options["mode"]=="gaming"
            jobs.run_job(jobs.Job(jid));result=jobs.read_status(jid);assert result["state"]=="done",result.get("error")
            assert result["clips"]
            media=jobs.job_dir(jid)/result["clips"][0]["files"]["captioned"]
            info=video.probe(media);assert (info.width,info.height)==(720,1280) and info.has_audio
            assert (jobs.job_dir(jid)/result["clips"][0]["files"]["srt"]).stat().st_size>0
            page.locator("#live-refresh").click()
            expect(page.locator("#live-shorts-results video")).to_have_count(len(result["clips"]))
            expect(page.locator("#live-shorts-results")).to_contain_text("9:16")
            page.screenshot(path=str(ROOT/"artifacts/live-shorts-desktop.png"),full_page=True)
            page.locator("#live-inbox").get_by_role("link",name="Make captioned Short").click()
            expect(page.locator("#gaming-captions")).to_have_value("bold")
            expect(page.locator("#gaming-export")).to_be_enabled()
            page.locator("#gaming-guide-play").click()
            assert page.locator(".gaming-guide").evaluate("el=>el.classList.contains('guide-paused')")
            page.locator("#gaming-guide-play").click()
            expect(page.locator("#gaming-guide-play")).to_have_text("Replay guide",timeout=12000)
            page.locator("#gaming-guide-play").click()
            expect(page.locator("#gaming-guide-play")).to_have_text("Pause guide")
            page.emulate_media(reduced_motion="reduce");page.reload()
            expect(page.locator("#gaming-guide-play")).to_be_hidden()
            assert page.locator(".guide-camera-move").evaluate("el=>getComputedStyle(el).animationName")=="none"
            page.screenshot(path=str(ROOT/"artifacts/gaming-guide-desktop.png"),full_page=True)
            page.set_viewport_size({"width":390,"height":844})
            assert page.evaluate("document.documentElement.scrollWidth<=innerWidth")
            page.screenshot(path=str(ROOT/"artifacts/gaming-guide-mobile.png"),full_page=True)
            page.goto(BASE+"/#/live");expect(page.locator("#live-shorts-results video")).to_have_count(len(result["clips"]))
            assert page.evaluate("document.documentElement.scrollWidth<=innerWidth")
            assert not errors,errors;browser.close()
    finally:server.should_exit=True;thread.join(timeout=5)
print("PASS: captured-source handoff, real heuristic highlights and 720x1280 captioned/audio export, inbox results, gaming caption defaults, guide pause/replay/reduced motion and mobile layout. Speech transcript is an explicit fixture.")
