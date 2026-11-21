"""Real MV3 worker, offscreen document, browser storage and localhost pairing. No capture or provider login."""
import io
import socket
import sys
import tempfile
import threading
import time
import zipfile
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import uvicorn
from fastapi.testclient import TestClient
from playwright.sync_api import sync_playwright,expect
from app import main,jobs,live

ROOT=Path(__file__).resolve().parents[1];PORT=8026
main.LOCAL_HOSTS.add(f"127.0.0.1:{PORT}")
with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp,patch.object(jobs,"JOBS_DIR",Path(tmp)/"jobs"),patch.object(live,"TOKEN_PATH",Path(tmp)/"key"),patch.object(main,"PORT",PORT):
    extension=Path(tmp)/"extension";extension.mkdir()
    client=TestClient(main.app,base_url=f"http://127.0.0.1:{PORT}")
    with zipfile.ZipFile(io.BytesIO(client.get("/api/live/extension.zip").content)) as archive:archive.extractall(extension)
    client.close()
    server=uvicorn.Server(uvicorn.Config(main.app,host="127.0.0.1",port=PORT,lifespan="off",log_level="error"))
    thread=threading.Thread(target=server.run,daemon=True);thread.start()
    for _ in range(50):
        try:
            with socket.create_connection(("127.0.0.1",PORT),timeout=.2):break
        except OSError:time.sleep(.1)
    try:
        with sync_playwright() as p:
            context=p.chromium.launch_persistent_context(str(Path(tmp)/"profile"),channel="chromium",headless=True,args=[f"--disable-extensions-except={extension}",f"--load-extension={extension}"])
            try:
                worker=context.service_workers[0] if context.service_workers else context.wait_for_event("serviceworker")
                worker.evaluate("port=>chrome.storage.local.set({port})",PORT)
                eid=worker.url.split("/")[2];page=context.new_page();errors=[]
                page.on("pageerror",lambda e:errors.append(str(e)))
                page.goto(f"chrome-extension://{eid}/popup.html")
                expect(page.locator("#pair")).to_be_enabled()
                page.locator("#pair").click()
                expect(page.locator("#conn-text")).to_have_text("Connected",timeout=45000)
                page.locator("#conn").click()
                expect(page.locator("#connection-detail")).to_contain_text("Verified connection")
                assert page.evaluate("async()=>{const s=await chrome.storage.local.get('token');return /^[A-Za-z0-9_-]{43}$/.test(s.token)}")
                assert page.evaluate("async()=> (await chrome.runtime.getContexts({contextTypes:['OFFSCREEN_DOCUMENT']})).length===1")
                page.locator("#manual > summary").click();page.locator("#token").fill("z"*43);page.locator("#save").click()
                expect(page.locator("#error")).to_contain_text("not accepted")
                assert page.evaluate("async()=> (await chrome.storage.local.get('token')).token!== 'z'.repeat(43)")
                page.locator("#check-connection").click()
                expect(page.locator("#conn-text")).to_have_text("Connected")
                page.reload();expect(page.locator("#conn-text")).to_have_text("Connected")
                page.locator("#conn").click()
                page.screenshot(path=str(ROOT/"artifacts/extension-native-pairing.png"),full_page=True)
                assert main.LIVE_CONNECTION["verified_at"]
                server.should_exit=True;thread.join(timeout=5)
                page.locator("#check-connection").click()
                expect(page.locator("#error")).to_contain_text("Cannot reach ClipForge",timeout=15000)
                expect(page.locator("#conn-text")).to_have_text("Needs attention")
                assert not errors,errors
            finally:context.close()
    finally:server.should_exit=True;thread.join(timeout=5)
print("PASS: ZIP loads as a real MV3 extension; actual automatic pairing, offscreen STATUS, key verification, saved-key protection, reopen persistence and offline diagnostics. No stream capture or real provider used.")
