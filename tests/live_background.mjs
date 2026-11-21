import fs from "node:fs";
import vm from "node:vm";
import assert from "node:assert/strict";
let message, shortcut, recording = true, requests = 0;
const calls = [], origin = 'chrome-extension://' + 'a'.repeat(32) + '/';
const chrome = {
  runtime: {getURL:p=>origin+p, getContexts:async()=>[{}], onMessage:{addListener:fn=>message=fn},
    sendMessage:async m=>{calls.push(m.type);return m.type==='STATUS'?{recording}:{ok:true}}},
  storage: {local:{get:async()=>({token:'test-key',port:8000}),set:async()=>{}},session:{get:async()=>({tabId:42}),remove:async()=>calls.push('REMOVE_TAB')}},
  commands:{onCommand:{addListener:fn=>shortcut=fn}},
  action:{setBadgeText:async v=>calls.push('BADGE:'+v.text)},
  tabs:{onRemoved:{addListener:()=>{}},sendMessage:async(_id,m)=>calls.push(m.type)}
};
vm.runInNewContext(fs.readFileSync(new URL('../extension/background.js',import.meta.url),'utf8'),{chrome,URL,AbortSignal,fetch:async()=>{requests++;return {ok:true,json:async()=>({ok:true,version:1})}}});
const send = (type,url=origin+'popup.html')=>new Promise(resolve=>message({type},{url},resolve));
assert((await send('CHECK')).ok);assert.equal(requests,1);
assert.equal(message({type:'CHECK'},{url:'https://www.youtube.com/watch?v=x',tab:{id:42}},()=>{}),undefined);
assert.equal(requests,1,'web pages cannot use connection controls');
await shortcut('save-moment');assert(calls.includes('MARK'));
recording=false;calls.length=0;await shortcut('save-moment');assert(!calls.includes('MARK'),'shortcut must not capture without Start');
await send('CAPTURE_ENDED',origin+'offscreen.html');assert(calls.includes('STOP_CHAT'));assert(calls.includes('BADGE:'));
console.log('PASS: connection check, sender boundary, manual shortcut only during capture, observer/badge cleanup (mock Chrome APIs).');
