"""Actual UI + API + FFmpeg on generated footage, with an isolated job store."""
import sys
import tempfile
import socket
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import uvicorn
from playwright.sync_api import sync_playwright, expect
from app import main, jobs, video
from test_layouts import make_fixture

ROOT = Path(__file__).resolve().parents[1]
PORT = 8023
BASE = f"http://127.0.0.1:{PORT}"
main.LOCAL_HOSTS.add(f"127.0.0.1:{PORT}")

@contextmanager
def serve_app():
    server=uvicorn.Server(uvicorn.Config(main.app,host="127.0.0.1",port=PORT,lifespan="off",log_level="error"))
    thread=threading.Thread(target=server.run,daemon=True);thread.start()
    try:
        for _ in range(50):
            try:
                with socket.create_connection(("127.0.0.1",PORT),timeout=.2):break
            except OSError:time.sleep(.1)
        yield
    finally:
        server.should_exit=True;thread.join(timeout=5)

with tempfile.TemporaryDirectory() as tmp, patch.object(jobs, "JOBS_DIR", Path(tmp)/"jobs"), patch.object(jobs, "submit"), serve_app():
    source = jobs.create("live", {}, {"preset":"none"}, title="Generated webcam + gameplay fixture")
    source_job = jobs.Job(source["id"])
    media = source_job.dir/"highlight.mp4"
    make_fixture(media)
    source_job.output(media, "Test footage", "video", duration=2,width=640,height=360)
    jobs._finish(source_job)
    with sync_playwright() as p:
        browser = p.chromium.launch(channel="chrome", headless=True)
        page = browser.new_page(viewport={"width":1440,"height":1080})
        errors=[]; page.on("pageerror",lambda e:errors.append(str(e)))
        page.goto(f"{BASE}/#/gaming?from={source_job.id}&file=highlight.mp4")
        expect(page.locator("#gaming-export")).to_be_enabled()
        expect(page.locator("#gaming-captions")).to_have_value("none")
        # Draw the fixture's red webcam, then isolate its blue gameplay half.
        page.locator("#gaming-source-frame").scroll_into_view_if_needed()
        canvas=page.locator("#gaming-source-frame").bounding_box()
        page.mouse.move(canvas["x"]+canvas["width"]*.02,canvas["y"]+canvas["height"]*.02);page.mouse.down()
        page.mouse.move(canvas["x"]+canvas["width"]*.27,canvas["y"]+canvas["height"]*.52,steps=8);page.mouse.up()
        assert abs(float(page.locator('[data-coord="2"]').input_value())-25)<1, "Drawn webcam width should be 25%"
        page.locator('[data-region="game"]').click()
        for i,value in enumerate((50,0,50,100)):
            page.locator(f'[data-coord="{i}"]').fill(str(value))
            page.locator(f'[data-coord="{i}"]').press("Tab")
        page.locator("#gaming-resolution").select_option("720")
        page.locator("#gaming-share").fill("40")
        page.locator("#gaming-game-fit").select_option("cover")
        page.locator("#gaming-camera-fit").select_option("cover")
        expect(page.locator("#gaming-export")).to_be_enabled()
        page.locator("#gaming-preset-name").fill("Fixture streamer")
        page.locator("#gaming-save").click()
        expect(page.locator("#gaming-message")).to_have_text("Saved layout: Fixture streamer")
        page.reload()
        expect(page.locator("#gaming-export")).to_be_enabled()
        page.locator("#gaming-presets").select_option("0")
        expect(page.locator("#gaming-resolution")).to_have_value("720")
        expect(page.locator("#gaming-share-label")).to_have_text("40%")
        expect(page.locator("#gaming-export")).to_be_enabled()
        # Desktop and mobile layouts stay within the viewport.
        artifacts=ROOT/"artifacts";artifacts.mkdir(exist_ok=True)
        page.evaluate("window.scrollTo(0,0)")
        page.screenshot(path=str(artifacts/"gaming-editor-desktop.png"),full_page=True)
        page.set_viewport_size({"width":390,"height":844})
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth"), "Mobile overflow"
        page.screenshot(path=str(artifacts/"gaming-editor-mobile.png"),full_page=True)
        page.locator("#gaming-upload").set_input_files(str(media))
        expect(page.locator("#gaming-export")).to_be_enabled()
        page.locator("#gaming-start").fill("0.25")
        page.locator("#gaming-end").fill("1.75")
        page.locator("#gaming-export").click()
        page.wait_for_function("location.hash.startsWith('#/job/') || document.getElementById('gaming-error')?.hidden === false")
        if not page.url.split('#')[-1].startswith('/job/'):
            raise AssertionError(page.locator("#gaming-error").inner_text())
        page.wait_for_url("**/#/job/*")
        result=next(j for j in jobs.list_jobs() if j["kind"]=="layout")
        jobs.run_job(jobs.Job(result["id"]))
        page.reload()
        expect(page.get_by_role("heading",name="Gaming layout · clean",exact=True)).to_be_visible()
        expect(page.locator("#crumb")).to_have_text("Gaming layout")
        expect(page.locator("#result-body video")).to_have_count(1)
        out=video.probe(jobs.job_dir(result["id"])/"gaming.mp4")
        assert (out.width,out.height)==(720,1280) and abs(out.duration-1.5)<.1 and out.has_audio
        assert media.exists(), "Original clip must remain untouched"
        # Delayed suggestions test editor coordination, not face-recognition accuracy.
        pending=[]
        page.route("**/api/layouts/auto", lambda route: pending.append(route))
        def resolve_suggestion():
            # Playwright's request event may precede dispatch of its route callback.
            for _ in range(100):
                if pending:
                    pending.pop(0).fulfill(json=suggestion)
                    return
                page.wait_for_timeout(50)
            raise AssertionError("Suggestion request was not intercepted")
        with page.expect_request("**/api/layouts/auto"):
            page.goto(f"{BASE}/#/gaming?from={source_job.id}&file=highlight.mp4")
        page.locator('[data-coord="2"]').fill("35")
        page.locator('[data-coord="2"]').press("Tab")
        suggestion={"found":True,"settings":{"mode":"pip","camera":[0,0,.25,.5]},"message":"Test suggestion"}
        resolve_suggestion()
        expect(page.locator("#gaming-auto")).to_have_text("Auto detect")
        expect(page.locator("#gaming-mode")).to_have_value("stack")
        expect(page.locator('[data-coord="2"]')).to_have_value("35.0")
        # A new source preserves edited settings and cancels the old automatic task.
        with page.expect_request("**/api/layouts/auto"):
            page.locator("#gaming-auto").click()
        page.locator("#gaming-upload").set_input_files(str(media))
        expect(page.locator("#gaming-auto")).to_have_text("Auto detect")
        expect(page.locator("#gaming-auto")).to_be_enabled()
        resolve_suggestion()
        expect(page.locator("#gaming-mode")).to_have_value("stack")
        expect(page.locator('[data-coord="2"]')).to_have_value("35.0")
        with page.expect_request("**/api/layouts/auto"):
            page.locator("#gaming-auto").click()
        resolve_suggestion()
        expect(page.locator("#gaming-mode")).to_have_value("pip")
        expect(page.locator("#gaming-auto")).to_have_text("Auto detect")
        assert not errors,errors
        browser.close()
print("PASS: local/library source, drawing regions, saved-layout persistence, desktop/mobile preview, real API submission and MP4 export (generated footage).")
print("PASS: delayed suggestion respects manual edits, source changes cancel old tasks, and explicit retry applies the new suggestion (stubbed detection response).")
