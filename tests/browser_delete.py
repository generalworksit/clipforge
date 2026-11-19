"""Real DELETE API via the UI, isolated files only; no real media or user jobs."""
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from fastapi.testclient import TestClient
from playwright.sync_api import sync_playwright, expect
from app import jobs, main


def fixture(state, title):
    status = jobs.create("live", {}, {"preset": "none"}, title=title)
    status["state"] = state
    directory = jobs.job_dir(status["id"])
    jobs._write_status(directory, status)
    (directory / "fixture.txt").write_text("Isolated deletion fixture", encoding="utf-8")
    return status["id"]


with tempfile.TemporaryDirectory() as tmp, patch.object(jobs, "JOBS_DIR", Path(tmp) / "jobs"):
    client = TestClient(main.app, base_url="http://127.0.0.1:8000")
    done = fixture("done", "Finished fixture")
    error = fixture("error", "Failed fixture")
    queued = fixture("queued", "Queued fixture")
    with sync_playwright() as p:
        browser = p.chromium.launch(channel="chrome", headless=True)
        page = browser.new_page()
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))

        def serve(route):
            request = route.request
            response = client.request(request.method, request.url, content=request.post_data_buffer,
                headers={k: v for k, v in request.headers.items() if k.lower() not in {"host", "content-length"}})
            route.fulfill(status=response.status_code, body=response.content,
                headers={k: v for k, v in response.headers.items() if k.lower() not in {"content-length", "content-encoding"}})

        page.route("http://127.0.0.1:8000/**", serve)
        page.goto("http://127.0.0.1:8000/#/live")
        expect(page.locator(".live-card")).to_have_count(3)
        expect(page.locator(f'.live-card[data-id="{queued}"] input')).to_be_disabled()
        page.locator(f'.live-card[data-id="{done}"] input').check()
        incoming = fixture("done", "New arrival")
        page.locator("#live-refresh").click()
        expect(page.locator(".live-card")).to_have_count(4)
        expect(page.locator(f'.live-card[data-id="{done}"] input')).to_be_checked()
        page.locator(f'.live-card[data-id="{error}"] input').check()
        page.once("dialog", lambda d: d.dismiss())
        page.get_by_role("button", name="Delete 2", exact=True).click()
        assert jobs.job_dir(done).exists() and jobs.job_dir(error).exists()
        page.once("dialog", lambda d: d.accept())
        page.get_by_role("button", name="Delete 2", exact=True).click()
        expect(page.locator(".live-card")).to_have_count(2)
        assert not jobs.job_dir(done).exists() and not jobs.job_dir(error).exists()
        assert jobs.job_dir(queued).exists() and jobs.job_dir(incoming).exists()

        # A busy transition after rendering must still be refused by the backend.
        status = jobs.read_status(incoming)
        status["state"] = "running"
        jobs._write_status(jobs.job_dir(incoming), status)
        page.once("dialog", lambda d: d.accept())
        page.locator(f'.live-del[data-id="{incoming}"]').click()
        expect(page.locator(f'.live-card[data-id="{incoming}"] .live-del-error')).to_contain_text("still being saved")
        assert jobs.job_dir(incoming).exists()
        status["state"] = "done"
        jobs._write_status(jobs.job_dir(incoming), status)

        page.goto("http://127.0.0.1:8000/#/library")
        expect(page.locator(".jrow")).to_have_count(2)
        expect(page.locator(f'.jrow[data-id="{queued}"] .del')).to_be_disabled()
        page.once("dialog", lambda d: d.accept())
        page.get_by_role("button", name="Delete all 1 finished", exact=True).click()
        expect(page.locator(".jrow")).to_have_count(1)
        assert jobs.job_dir(queued).exists() and not jobs.job_dir(incoming).exists()
        assert not errors, errors
        browser.close()
    client.close()
print("PASS: cancel/delete selected, live selection survives arrivals, busy transition refuses deletion, library bulk deletion preserves queued jobs (isolated files).")
