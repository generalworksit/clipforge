"""Real Chrome MediaRecorder -> bridge -> FFmpeg, with synthetic capture input.

Chrome's tabCapture permission gesture and live platform DOM remain manual checks.
No user streams, existing jobs, browser profile or real pairing key are touched.
"""
import json
import socket
import sys
import tempfile
import threading
import time
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import uvicorn
from fastapi.staticfiles import StaticFiles
from playwright.sync_api import sync_playwright, expect
from app import jobs, live, main, video
from app.__main__ import _quiet_connection_resets

_quiet_connection_resets()
ROOT = Path(__file__).resolve().parents[1]
PORT = 8019
BASE = f"http://127.0.0.1:{PORT}"
main.LOCAL_HOSTS.add(f"127.0.0.1:{PORT}")
main.app.mount("/__extension", StaticFiles(directory=ROOT / "extension"))
artifacts = ROOT / "artifacts"
artifacts.mkdir(exist_ok=True)

with tempfile.TemporaryDirectory() as tmp, patch.object(jobs, "JOBS_DIR", Path(tmp) / "jobs"), \
        patch.object(live, "TOKEN_PATH", Path(tmp) / ".live-token"), patch.object(jobs, "submit") as submitted:
    key = live.pairing_key()
    server = uvicorn.Server(uvicorn.Config(main.app, host="127.0.0.1", port=PORT, lifespan="off", log_level="error"))
    thread = threading.Thread(target=server.run, daemon=True); thread.start()
    for _ in range(100):
        try:
            with socket.create_connection(("127.0.0.1", PORT), timeout=0.2): break
        except OSError: time.sleep(0.1)
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(channel="chrome", headless=True, args=["--autoplay-policy=no-user-gesture-required"])
            context = browser.new_context(viewport={"width": 1440, "height": 1000})
            page = context.new_page()
            errors = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.add_init_script("""
              window.__handler = null; window.__offline = true; window.__loseResponse = false;
              window.chrome = {runtime: {onMessage: {addListener(fn) { window.__handler = fn; }},
                async sendMessage() { return CREDENTIALS; }}};
              window.__command = (type, data = {}) => new Promise((resolve) => window.__handler({target: 'recorder', type, ...data}, {}, resolve));
              const nativeFetch = window.fetch.bind(window);
              window.fetch = async (...args) => {
                if (String(args[0]).includes('/api/live/capture')) {
                  if (window.__offline) throw Error('Simulated disconnected ClipForge');
                  const response = await nativeFetch(...args);
                  if (window.__loseResponse) { window.__loseResponse = false; throw Error('Simulated lost acknowledgement'); }
                  return response;
                }
                return nativeFetch(...args);
              };
              navigator.mediaDevices.getUserMedia = async () => {
                const source = document.createElement('canvas'); source.width = 640; source.height = 360;
                const c = source.getContext('2d'); let frame = 0;
                function draw() {
                  c.fillStyle = frame < 90 ? '#f02020' : '#2040f0'; c.fillRect(0, 0, 640, 360);
                  c.fillStyle = '#fff'; c.font = '40px sans-serif'; c.fillText('LIVE CAPTURE TEST ' + frame++, 30, 180);
                }
                draw(); window.__draw = setInterval(draw, 33);
                const ac = new AudioContext(), osc = ac.createOscillator(), dest = ac.createMediaStreamDestination();
                osc.frequency.value = 440; osc.connect(dest); osc.start();
                const stream = source.captureStream(30); stream.addTrack(dest.stream.getAudioTracks()[0]);
                return stream;
              };
            """.replace("CREDENTIALS", json.dumps({"token": key, "port": PORT})))
            page.goto(BASE + "/__extension/offscreen.html")
            page.wait_for_function("typeof window.__handler === 'function'")
            result = page.evaluate("""async () => {
              window.__player = {x: 0, y: 0, w: 1, h: 1, aspect: 16/9, playing: true, visible: true, url: 'https://www.twitch.tv/example'};
              const r = await __command('START', {player: __player, sourceUrl: __player.url, title: 'Synthetic live capture', streamId: 'test', config: {options: {pre: 20, tail: 12}}});
              window.__heartbeat = setInterval(() => __command('CHAT', {topFrame: true, player: __player, messages: []}), 500);
              return r;
            }""")
            assert result.get("ok"), result
            assert page.evaluate("__command('LISTEN',{enabled:false})")["ok"]
            page.wait_for_function("async () => (await __command('STATUS')).audio_level > .002")
            assert not page.evaluate("__command('STATUS')")["listening"], "Speaker playback must be silent while recording audio"
            page.wait_for_timeout(1500)
            assert page.evaluate("__command('MARK')")["ok"]
            page.wait_for_timeout(4200)
            assert page.evaluate("__command('STOP')")["ok"]
            page.wait_for_function("async () => (await __command('STATUS')).pending === 1")
            page.wait_for_function("async () => (await __command('STATUS')).error.includes('retry')")
            assert not page.evaluate("__command('STATUS')")["recording"]
            page.reload(); page.wait_for_function("typeof window.__handler === 'function'")
            assert page.evaluate("__command('STATUS')")["pending"] == 1, "IndexedDB must survive recorder reload"
            page.evaluate("window.__offline = false; window.__loseResponse = true")
            page.evaluate("__command('RETRY')")
            assert page.evaluate("__command('STATUS')")["pending"] == 1
            page.evaluate("__command('RETRY')")
            page.wait_for_function("async () => (await __command('STATUS')).pending === 0")
            assert submitted.call_count == 1, "Lost acknowledgement must not duplicate a clip job"
            all_jobs = jobs.list_jobs(kind="live")
            assert len(all_jobs) == 1
            job_id = all_jobs[0]["id"]
            jobs.run_job(jobs.Job(job_id))
            st = jobs.read_status(job_id)
            assert st["state"] == "done"
            info = video.probe(jobs.job_dir(job_id) / "highlight.mp4")
            assert info.width == 1280 and info.height == 720 and info.has_audio, info
            video.extract_audio(jobs.job_dir(job_id)/"highlight.mp4",jobs.job_dir(job_id)/"test-audio.wav",lambda _:None)
            from app.audio import load_wav
            assert abs(load_wav(jobs.job_dir(job_id)/"test-audio.wav")).max()>.02, "Silent listening must retain audible recorded samples"
            assert abs(info.duration - (st["source"]["capture"]["end"] - st["source"]["capture"]["start"])) < 0.4, info
            # Reaching the six-clip limit during recording must stop without a retry loop
            # or dropping the final in-memory highlight. Drain it after space is freed.
            result = page.evaluate("""async () => {
              const player = {x:0,y:0,w:1,h:1,aspect:16/9,playing:true,visible:true,url:'https://www.twitch.tv/example'};
              const r = await __command('START', {player, sourceUrl:player.url,title:'Queue limit test',streamId:'test',config:{options:{}}});
              window.__heartbeat = setInterval(() => __command('CHAT', {topFrame:true,player,messages:[]}), 500);
              await new Promise((resolve) => {
                const open = indexedDB.open('clipforge-live', 1);
                open.onsuccess = () => { const tx = open.result.transaction('pending','readwrite');
                  for(let i=0;i<6;i++) tx.objectStore('pending').put({id:'limit-'+i,metadata:{},parts:[]});
                  tx.oncomplete = () => {open.result.close();resolve();}; };
              });
              return r;
            }""")
            assert result["ok"], result
            page.wait_for_timeout(300)
            page.evaluate("__command('MARK')")
            page.wait_for_timeout(1200)
            page.evaluate("__command('STOP')")
            page.wait_for_function("async () => (await __command('STATUS')).pending === 7")
            page.evaluate("""() => new Promise((resolve) => {
              const open=indexedDB.open('clipforge-live',1);open.onsuccess=()=>{const tx=open.result.transaction('pending','readwrite');
                tx.objectStore('pending').clear();tx.oncomplete=()=>{open.result.close();resolve()};};
            })""")
            page.evaluate("__command('RETRY')")
            page.wait_for_function("async () => (await __command('STATUS')).pending === 0")
            page.wait_for_function("async () => (await fetch('/api/jobs?kind=live').then(r => r.json())).length === 2")
            assert len(jobs.list_jobs(kind="live")) == 2
            # Inbox uses real API state and the real exported video.
            page.goto(BASE + "/#/live")
            page.locator(".live-card video").wait_for()
            page.wait_for_function("document.querySelector('.live-card video').readyState >= 1")
            assert page.get_by_role("link", name="Download MP4").count() == 1
            expect(page.locator("#live-inbox .live-card").get_by_role("link", name="Make captioned Short")).to_have_attribute("href", f"#/gaming?from={job_id}&file=highlight.mp4&preset=bold")
            page.get_by_role("button", name="Show pairing key").click()
            page.locator("#live-key-box").wait_for(state="visible")
            assert page.locator("#live-key").input_value() == key
            page.locator(".live-setup summary").click()
            page.screenshot(path=str(artifacts / "live-inbox-desktop.png"), full_page=True)
            page.set_viewport_size({"width": 390, "height": 844})
            assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
            page.screenshot(path=str(artifacts / "live-inbox-mobile.png"), full_page=True)
            page.locator(".live-card").filter(has=page.locator("video")).get_by_role("link", name="Review / edit").click()
            page.get_by_role("heading", name="Clean MP4 · CapCut ready").wait_for()
            assert page.locator("#crumb").inner_text() == "Live clips"
            page.goto(BASE + "/#/live")
            page.locator(".live-card video").wait_for()
            expect(page.locator("#activity")).to_be_empty()
            queued_id = next(j["id"] for j in jobs.list_jobs(kind="live") if j["id"] != job_id)
            expect(page.locator(f'.live-del[data-id="{queued_id}"]')).to_be_disabled()
            delete_button = page.locator(f'.live-del[data-id="{job_id}"]')
            page.once("dialog", lambda dialog: dialog.dismiss())
            delete_button.click()
            assert jobs.job_dir(job_id).exists(), "Cancelling must keep the video"
            page.once("dialog", lambda dialog: dialog.accept())
            delete_button.click()
            expect(delete_button).to_have_count(0)
            expect(page.locator("#live-count")).to_have_text("1 received · auto-refreshes")
            assert not jobs.job_dir(job_id).exists(), "Confirming must delete the isolated fixture"
            # Chat observer against DOM fixtures for both platforms, no network requests.
            for url, markup, append in [
                ("https://www.twitch.tv/example", '<div data-a-target="chat-line-message"><span data-a-target="chat-message-username">Old</span><span data-a-target="chat-line-message-body">LOL</span></div>',
                 '<div data-a-target="chat-line-message"><span data-a-target="chat-message-username">New</span><span data-a-target="chat-line-message-body">lmao<img alt="KEKW"></span></div>'),
                ("https://www.youtube.com/live_chat?v=example", '<yt-live-chat-text-message-renderer><span id="author-name">Old</span><span id="message">LOL</span></yt-live-chat-text-message-renderer>',
                 '<yt-live-chat-text-message-renderer><span id="author-name">New</span><span id="message">lmao<img alt="😂"></span></yt-live-chat-text-message-renderer>')]:
                fixture = context.new_page()
                fixture.route("**/*", lambda route, request, body=markup: route.fulfill(body=body, content_type="text/html"))
                fixture.goto(url)
                fixture.evaluate("window.__sent = []; window.chrome = {runtime: {onMessage: {addListener(fn) {window.__chat = fn}}, sendMessage(m) {window.__sent.push(m); return Promise.resolve()}}}")
                fixture.add_script_tag(path=str(ROOT / "extension/content.js"))
                fixture.evaluate("__chat({type:'START_CHAT'}, {}, () => {})")
                fixture.evaluate("html => document.body.insertAdjacentHTML('beforeend', html)", append)
                fixture.wait_for_function("__sent.some(m => m.messages.length)")
                messages = fixture.evaluate("__sent.flatMap(m => m.messages)")
                assert len(messages) == 1 and "lmao" in messages[0]["text"] and messages[0]["author"] != "New"
                assert ("KEKW" if "twitch" in url else "😂") in messages[0]["text"]
                fixture.close()
            assert not errors, errors
            browser.close()
            print("PASS: real MediaRecorder/audio, offline retry/deduplication, FFmpeg MP4, inbox/editor/mobile, delete confirmation/busy protection, empty idle header, Twitch/YouTube DOM fixtures.")
            print("LIMIT: capture input and chat DOM are synthetic; native extension permission and real-stream validation remain manual.")
    finally:
        server.should_exit = True
        thread.join(timeout=5)
