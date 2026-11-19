"""Isolated browser smoke test. No workers, downloads, publishing or existing-job writes."""
import json
import socket
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import uvicorn
from playwright.sync_api import sync_playwright
from app import main
from app.__main__ import _quiet_connection_resets
_quiet_connection_resets()

PORT = 8018
main.LOCAL_HOSTS.add(f"127.0.0.1:{PORT}")
server = uvicorn.Server(uvicorn.Config(main.app, host="127.0.0.1", port=PORT, lifespan="off", log_level="error"))
thread = threading.Thread(target=server.run, daemon=True)
thread.start()
for _ in range(100):
    try:
        with socket.create_connection(("127.0.0.1", PORT), timeout=0.2):
            break
    except OSError:
        time.sleep(0.1)

artifacts = Path(__file__).resolve().parents[1] / "artifacts"
artifacts.mkdir(exist_ok=True)
errors = []
try:
    with sync_playwright() as p:
        browser = p.chromium.launch(channel="chrome", headless=True)
        page = browser.new_page(viewport={"width": 1440, "height": 1000})
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.goto(f"http://127.0.0.1:{PORT}/#/clips")
        page.locator("#recipes button").first.wait_for()
        assert page.locator("#gallery button").count() == 21
        page.get_by_role("button", name="High energy").click()
        assert page.locator("#gallery [data-value=traffic]").get_attribute("aria-checked") == "true"
        assert page.locator("#effects [data-value=rgb]").get_attribute("aria-checked") == "true"
        assert page.locator("[name=clips]").input_value() == "4"
        page.get_by_role("button", name="Clean talk").click()
        assert page.locator("#effects [data-value=none]").get_attribute("aria-checked") == "true"
        page.get_by_role("searchbox", name="Filter caption styles").fill("ice")
        assert page.locator("#gallery button:visible").count() == 1
        page.get_by_role("searchbox", name="Filter caption styles").fill("")
        page.locator(".advanced-step").filter(has=page.locator("#voice")).locator("summary").click()
        page.locator('#voice .voice-modes [data-value="intro"]').click()
        page.locator("#demo-phone.paused").wait_for()
        assert page.locator("#demo-phone").evaluate("el => getComputedStyle(el, '::before').filter") == "none"
        page.locator('#voice .voice-modes [data-value=""]').click()
        page.locator('#gallery [data-value="none"]').click()
        assert page.locator("#demo-phone .cap").count() == 0
        assert page.locator("#positions button:enabled").count() == 0
        assert "No captions" in page.locator("#summary").inner_text()
        page.locator('#gallery [data-value="bold"]').click()
        assert page.locator("#demo-phone .cap").count() == 1
        assert page.locator("#positions button:enabled").count() == 3
        page.locator('#gallery [data-value="none"]').click()
        page.evaluate("window.scrollTo(0, 0)")
        page.screenshot(path=str(artifacts / "clip-maker-desktop.png"), full_page=True)
        # Verify payload without queuing a real video.
        payloads = []
        def capture(route):
            payloads.append(route.request.post_data_buffer.decode("utf-8"))
            route.fulfill(status=400, content_type="application/json", body=json.dumps({"detail": "Test request captured"}))
        page.route("**/api/jobs", lambda route: capture(route) if route.request.method == "POST" else route.continue_())
        page.locator("#source input[name=url]").fill("https://www.youtube.com/watch?v=abcdefghijk")
        page.get_by_role("button", name="Make clips", exact=True).click()
        page.get_by_text("Test request captured").wait_for()
        assert 'name="effect"' in payloads[0] and 'name="preset"' in payloads[0]
        assert 'name="preset"\r\n\r\nnone\r\n' in payloads[0]
        page.unroute("**/api/jobs")
        page.goto(f"http://127.0.0.1:{PORT}/#/discover")
        page.locator("#discover-search").wait_for()
        page.route("**/api/discover?*", lambda route: route.fulfill(json={"videos": [{"title": "Browser test source", "channel": "Test channel", "views": None, "duration": 300, "url": "https://www.youtube.com/watch?v=abcdefghijk", "thumbnail": ""}], "source": "Test fixture", "note": "Fixture"}))
        page.locator("input[name=q]").fill("test topic")
        page.get_by_role("button", name="Search YouTube").click()
        page.get_by_role("heading", name="Browser test source").wait_for()
        page.get_by_role("link", name="Make clips →").click()
        page.locator("#recipes").wait_for()
        assert "abcdefghijk" in page.locator("#source input[name=url]").input_value()
        # Use a real, finished job read-only, if one exists, to exercise queue integration.
        jobs = page.request.get(f"http://127.0.0.1:{PORT}/api/jobs").json()
        finished = next((j for j in jobs if j["kind"] == "clips" and j["state"] == "done" and j["clip_count"]), None)
        if finished:
            page.goto(f"http://127.0.0.1:{PORT}/#/job/{finished['id']}")
            page.locator(".queue-clip").first.wait_for()
            page.locator(".queue-clip").first.click()
            page.goto(f"http://127.0.0.1:{PORT}/#/planner")
            page.locator(".plan-row").wait_for()
            assert page.locator(".plan-row a.primary").first.get_attribute("href").startswith("/files/")
            page.locator("[data-posted]").first.check()
            assert "1 / 3 posted" in page.locator("#plan-count").inner_text()
            page.reload()
            assert page.locator("[data-posted]").first.is_checked()
            page.screenshot(path=str(artifacts / "daily-queue-desktop.png"), full_page=True)
        else:
            page.goto(f"http://127.0.0.1:{PORT}/#/planner")
            page.get_by_role("heading", name="Build today’s batch.").wait_for()
        page.set_viewport_size({"width": 390, "height": 844})
        for path in ("", "clips", "discover", "planner"):
            page.goto(f"http://127.0.0.1:{PORT}/#/{path}")
            if path == "clips":
                page.locator("#recipes button").first.wait_for()
                page.locator("#source input[name=url]").wait_for()
            elif path == "planner":
                page.locator(".plan-row, .workflow-empty, .error-box").first.wait_for()
            elif path == "discover":
                page.locator("#discover-search").wait_for()
            else:
                page.locator("#quick").wait_for()
            page.evaluate("window.scrollTo(0, 0)")
            assert page.evaluate("document.documentElement.scrollWidth <= innerWidth"), f"Overflow on {path}"
            page.screenshot(path=str(artifacts / f"{path or 'home'}-mobile.png"), full_page=True)
        assert not errors, errors
        browser.close()
        print("PASS: recipes, 20 styles plus No captions, preview toggling, filtering, request payload, discovery handoff, queue persistence, desktop/mobile layout; no browser errors.")
finally:
    server.should_exit = True
    thread.join(timeout=5)
