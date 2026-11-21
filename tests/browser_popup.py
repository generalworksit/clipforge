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
      window.__tab = {ok:true,supported:false,reason:'Open a Twitch or YouTube live stream'};
      window.chrome = {storage:{local:{async get(){return __storage}, async set(v){Object.assign(__storage,v)}}},
        commands:{async getAll(){return [{name:'save-moment',shortcut:'Alt+Shift+C'}]}},
        runtime:{getManifest:()=>({version:'1.4.1'}),async sendMessage(m){__commands.push(m.type);
          if(m.type==='HELLO')return location.search.includes('legacy')?{ok:false,error:'Unknown command.'}:{ok:true,version:'1.4.1'};
          if(m.type==='PAIR'){
            if(__pairFails)return {ok:false,error:'Start ClipForge, then click Connect app again.'};
            __storage.token='test-only'; return {ok:true};
          }
          if(m.type==='STATUS')return __status;
          if(m.type==='TAB')return __tab;
          if(m.type==='PAIR_KEY'){
            if(m.token!=='v'.repeat(43))return {ok:false,error:'Connection key was not accepted. Click Connect app again.'};
            __storage.token=m.token;__storage.port=m.port;return {ok:true};
          }
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
    page.locator("#silent").check()
    page.wait_for_function("__storage.options.listen===false && __commands.includes('LISTEN')")
    page.locator("#timing summary").click()
    expect(page.locator("#conn-text")).to_have_text("Connected")
    assert page.locator("#start").is_disabled()
    expect(page.locator("#state-hint")).to_contain_text("Open a Twitch or YouTube live stream")
    page.evaluate("__tab={ok:true,supported:true,platform:'Twitch',title:'Late night ranked',channel:'demo'}")
    expect(page.locator("#source-title")).to_have_text("Late night ranked", timeout=5000)
    assert page.locator("#start").is_enabled()
    page.locator("#start").click()
    expect(page.locator("#state")).to_have_text("Learning this chat…")
    assert page.locator("#start").is_hidden()
    page.evaluate("""__status={...__status,warmed:true,accepted:50,unique:12,share:.6,rate:5.4,score:85,elapsed:95,buffered:90,saved:1,
      history:Array.from({length:60},(_,i)=>({s:Math.round(18+14*Math.sin(i/4)+(i>38&&i<47?55:0)),a:i>38&&i<47?1:0})),
      clips:[{at:41,reason:'Chat reaction spike',length:38,state:'sent'}]}""")
    page.wait_for_function("document.querySelectorAll('#spark rect.hot').length===8")
    expect(page.locator("#clips li")).to_have_count(1)
    expect(page.locator("#clips .why")).to_have_text("Chat reaction spike")
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
    page.locator("#port").fill("8000");page.locator("#token").fill("wrong-key");page.locator("#save").click()
    expect(page.locator("#error")).to_contain_text("not accepted")
    assert page.evaluate("__storage.token==='test-only'")
    page.locator("#token").fill("v"*43);page.locator("#save").click()
    expect(page.locator("#notice")).to_contain_text("Key verified")
    expect(page.locator("#conn-text")).to_have_text("Connected")
    page.evaluate("()=>{chrome.runtime.sendMessage=async m=>m.type==='STATUS'?__status:{ok:false,error:'Unknown command.'}}")
    page.locator("#check-connection").click()
    expect(page.locator("#error")).to_contain_text("out of sync")
    page.goto((ROOT / "extension/popup.html").as_uri()+"?legacy=1")
    expect(page.locator("#error")).to_contain_text("out of sync")
    expect(page.locator("#reload-extension")).to_be_visible()
    expect(page.locator("#pair")).to_be_disabled()
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
    assert page.evaluate("getComputedStyle(document.documentElement).minWidth === '380px'")
    assert not errors, errors
    browser.close()
print("PASS: automatic connection/error recovery, manual fallback, capture controls, settings persistence and 380px layout (mock Chrome APIs).")
