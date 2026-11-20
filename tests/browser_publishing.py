"""Real UI/local API/media; account identity fixture only. No OAuth or upload requests."""
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
from app import main,jobs
from app.publishing import store,youtube
from test_layouts import make_fixture

ROOT=Path(__file__).resolve().parents[1];PORT=8024;BASE=f"http://127.0.0.1:{PORT}"
main.LOCAL_HOSTS.add(f"127.0.0.1:{PORT}")
with tempfile.TemporaryDirectory() as tmp,patch.object(jobs,"JOBS_DIR",Path(tmp)/"jobs"),patch.object(store,"DATA",Path(tmp)/"publishing"),patch.object(youtube,"account",return_value={"connected":True,"configured":True,"channel_id":"fixture-channel","title":"Test channel"}),patch.object(youtube.requests,"post") as external:
    j=jobs.Job(jobs.create("live",{},{})["id"]);media=j.dir/"highlight.mp4";make_fixture(media);j.output(media,"Generated video","video");jobs._finish(j)
    server=uvicorn.Server(uvicorn.Config(main.app,host="127.0.0.1",port=PORT,lifespan="off",log_level="error"))
    thread=threading.Thread(target=server.run,daemon=True);thread.start()
    for _ in range(50):
        try:
            with socket.create_connection(("127.0.0.1",PORT),timeout=.2):break
        except OSError:time.sleep(.1)
    try:
        with sync_playwright() as p:
            browser=p.chromium.launch(channel="chrome",headless=True);page=browser.new_page(viewport={"width":1440,"height":1000})
            errors=[];page.on("pageerror",lambda e:errors.append(str(e)))
            page.goto(f"{BASE}/#/publish?from={j.id}&file=highlight.mp4")
            expect(page.locator("#publish-account")).to_have_text("Test channel")
            page.locator('#publish-draft [name=title]').fill("Review this generated clip")
            page.get_by_role("button",name="Save draft",exact=True).click()
            expect(page.locator("#publish-items video")).to_have_count(1)
            item=store.all_items()[0];assert item["state"]=="draft"
            page.once("dialog",lambda dialog:dialog.dismiss());page.get_by_role("button",name="Approve upload",exact=True).click()
            assert store.get(item["id"])["state"]=="draft"
            page.get_by_role("button",name="Edit",exact=True).click()
            page.locator('#publish-draft [name=title]').fill("Edited title")
            page.locator('#publish-draft [name=privacy]').select_option("public")
            future=time.time()+3600
            page.locator('#publish-draft [name=schedule]').fill(time.strftime("%Y-%m-%dT%H:%M",time.localtime(future)))
            page.get_by_role("button",name="Save draft",exact=True).click()
            expect(page.locator("#publish-items h3")).to_have_text("Edited title")
            page.once("dialog",lambda dialog:dialog.accept());page.get_by_role("button",name="Approve upload",exact=True).click()
            expect(page.get_by_role("button",name="Approve upload",exact=True)).to_have_count(0)
            item=store.get(item["id"]);assert item["state"]=="approved" and item["publish_at"]>time.time()
            page.reload();expect(page.locator("#publish-items h3")).to_have_text("Edited title")
            page.evaluate("window.scrollTo(0,0)");page.screenshot(path=str(ROOT/"artifacts/publishing-desktop.png"),full_page=True)
            page.set_viewport_size({"width":390,"height":844});assert page.evaluate("document.documentElement.scrollWidth<=innerWidth")
            page.screenshot(path=str(ROOT/"artifacts/publishing-mobile.png"),full_page=True)
            page.once("dialog",lambda dialog:dialog.accept());page.get_by_role("button",name="Cancel",exact=True).click()
            expect(page.locator("#publish-items video")).to_have_count(0)
            assert media.exists();external.assert_not_called();assert not errors,errors
            browser.close()
    finally:server.should_exit=True;thread.join(timeout=5)
print("PASS: real publishing draft/preview/edit/schedule, approval cancellation/confirmation, persistence and mobile layout; Google account is a fixture and no upload occurred.")
