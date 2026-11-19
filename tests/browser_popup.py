"""Popup behavior/layout with explicit mock Chrome APIs; no native capture claim."""
import json
import sys
from pathlib import Path
from playwright.sync_api import sync_playwright, expect

ROOT = Path(__file__).resolve().parents[1]
with sync_playwright() as p:
    browser = p.chromium.launch(channel="chrome", headless=True)
    page = browser.new_page(viewport={"width": 380, "height": 720})
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.add_init_script("""
      window.__storage = {port:8000,options:{}};
      window.__pairFails = true;
      window.__commands = [];
      window.__status = {recording:false,pending:0,saved:0};
      window.chrome = {storage:{local:{async get(){return __storage}, async set(v){Object.assign(__storage,v)}}},
        commands:{async getAll(){return [{name:'save-moment',shortcut:'Alt+Shift+C'}]}},
        runtime:{async sendMessage(m){__commands.push(m.type);
          if(m.type==='PAIR'){
            if(__pairFails)return {ok:false,error:'Start ClipForge, then click Connect app again.'};
            __storage.token='test-only'; return {ok:true};
          }
          if(m.type==='STATUS')return __status;
          if(m.type==='START')__status={...__status,recording:true,warmed:false,warmupRemaining:18,buffered:2};
          if(m.type==='MARK')__status={...__status,active:true,reason:'Manual highlight',tailRemaining:12};
          if(m.type==='STOP')__status={...__status,recording:false,active:false};
          return {ok:true};
        }}};
    """)
    (ROOT / "artifacts").mkdir(exist_ok=True)
    page.goto((ROOT / "extension/popup.html").as_uri())
    page.wait_for_function("document.querySelector('#connection').open")
    assert page.locator("#token").is_hidden()
    page.locator("#pair").click()
    expect(page.locator("#connection-state")).to_have_text("Needs attention")
    expect(page.locator("#error")).to_contain_text("Start ClipForge")
    page.evaluate("__pairFails=false")
    page.locator("#pair").click()
    page.wait_for_function("document.querySelector('#connection-state').textContent==='Connected'")
    assert page.evaluate("__storage.token==='test-only' && __commands.filter(c=>c==='PAIR').length===2")
    assert not page.locator("#connection").get_attribute("open")
    page.locator("[data-sensitivity=high]").click()
    page.wait_for_function("__storage.options.sensitivity==='high'")
    page.locator("#timing summary").click()
    page.locator("#pre").fill("25"); page.locator("#pre").press("Tab")
    page.wait_for_function("__storage.options.pre===25")
    page.locator("#timing summary").click()
    page.locator("#start").click()
    expect(page.locator("#state")).to_have_text("Learning this chat…")
    assert page.locator("#start").is_hidden()
    page.evaluate("__status={...__status,warmed:true,accepted:50,unique:12,share:.6,rate:5.4,score:85,elapsed:95,buffered:90}")
    expect(page.locator("#state")).to_have_text("Watching for moments")
    page.locator("#mark").click()
    expect(page.locator("#state")).to_have_text("Moment detected")
    page.screenshot(path=str(ROOT / "artifacts/live-popup-v2.png"),full_page=True)
    page.locator("#stop").click()
    expect(page.locator("#state")).to_have_text("Ready to capture")
    page.evaluate("__status={...__status,pending:2,error:'Clip kept for retry: offline'}")
    page.locator("#pending-box").wait_for(state="visible")
    assert page.locator("#error").is_visible()
    page.evaluate("__status={...__status,pending:0,error:''}")
    page.locator("#error").wait_for(state="hidden")
    page.locator("#connection > summary").click()
    page.locator("#manual > summary").click()
    page.locator("#port").fill("80"); page.locator("#save").click()
    assert "between 1024" in page.locator("#error").inner_text()
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
    assert page.evaluate("getComputedStyle(document.documentElement).minWidth === '380px'")
    assert not errors, errors
    browser.close()
print("PASS: automatic connection/error recovery, manual fallback, capture controls, settings persistence and 380px layout (mock Chrome APIs).")
