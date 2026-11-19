"""Real dashboard + local API in an isolated store; no live provider or capture claimed."""
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from fastapi.testclient import TestClient
from playwright.sync_api import sync_playwright, expect
from app import main, jobs
from app.monitoring import store
from app.monitoring.service import supervisor

ROOT = Path(__file__).resolve().parents[1]
with tempfile.TemporaryDirectory() as tmp, patch.object(store, "DATA", Path(tmp)/"monitor"), patch.object(jobs, "JOBS_DIR", Path(tmp)/"jobs"):
    client = TestClient(main.app, base_url="http://127.0.0.1:8000")
    supervisor.start()
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(channel="chrome", headless=True)
            page = browser.new_page(viewport={"width":1440,"height":1000})
            errors=[]; page.on("pageerror", lambda e: errors.append(str(e)))
            def serve(route):
                req=route.request
                reply=client.request(req.method, req.url, content=req.post_data_buffer, headers={k:v for k,v in req.headers.items() if k.lower() not in {"host","content-length"}})
                route.fulfill(status=reply.status_code, body=reply.content, headers={k:v for k,v in reply.headers.items() if k.lower() not in {"content-length","content-encoding"}})
            page.route("http://127.0.0.1:8000/**", serve)
            page.goto("http://127.0.0.1:8000/#/monitor")
            expect(page.locator("#monitor-key-status")).to_have_text("Setup needed")
            page.locator('#monitor-form input[name=url]').fill("https://www.youtube.com/watch?v=abcdefghijk")
            page.locator('#monitor-form select[name=preset]').select_option("none")
            page.locator("#monitor-save").click()
            expect(page.get_by_role("button",name="Start monitoring",exact=True)).to_be_visible()
            page.get_by_role("button",name="Start monitoring",exact=True).click()
            expect(page.locator(".monitor-state")).to_have_text("Connection needed",timeout=12000)
            page.get_by_role("button",name="Pause",exact=True).click()
            expect(page.get_by_role("button",name="Edit",exact=True)).to_be_visible()
            page.get_by_role("button",name="Edit",exact=True).click()
            page.locator('#monitor-form select[name=sensitivity]').select_option("high")
            page.locator("#monitor-save").click()
            page.reload()
            expect(page.locator(".monitor-card")).to_have_count(1)
            page.get_by_role("button",name="Edit",exact=True).click()
            expect(page.locator('#monitor-form select[name=sensitivity]')).to_have_value("high")
            expect(page.locator('#monitor-form select[name=preset]')).to_have_value("none")
            page.locator("#monitor-cancel").click()
            page.evaluate("window.scrollTo(0,0)")
            (ROOT/"artifacts").mkdir(exist_ok=True)
            page.screenshot(path=str(ROOT/"artifacts/monitor-desktop.png"), full_page=True)
            page.set_viewport_size({"width":390,"height":844})
            page.evaluate("window.scrollTo(0,0)")
            assert page.evaluate("document.documentElement.scrollWidth <= innerWidth"), "Mobile overflow"
            page.screenshot(path=str(ROOT/"artifacts/monitor-mobile.png"), full_page=True)
            page.get_by_role("button",name="Remove",exact=True).click()
            expect(page.locator(".monitor-card")).to_have_count(0)
            assert not errors, errors
            browser.close()
    finally:
        supervisor.close(); client.close()
print("PASS: actual local API, add/start/setup-needed/pause/edit/persist/delete, desktop/mobile, no JS errors. No provider connection used.")
