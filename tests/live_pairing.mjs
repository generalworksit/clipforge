// Native Chrome APIs are mocked here; test the security/connection contract.
import fs from 'node:fs';
import vm from 'node:vm';
import assert from 'node:assert/strict';
let handler, recording = false, healthy = true, validServer=true, setupFails = false, created = 0, injection;
let saved = {port:8000}, updates = new Set();
const token='a'.repeat(43), origin='chrome-extension://'+'b'.repeat(32)+'/';
const chrome={
  runtime:{getURL:p=>origin+p,getContexts:async()=>[{}],onMessage:{addListener:f=>handler=f},sendMessage:async()=>({recording})},
  storage:{local:{get:async()=>saved,set:async v=>{saved={...saved,...v}}},session:{get:async()=>({}),remove:async()=>{}}},
  tabs:{onRemoved:{addListener(){}},onUpdated:{addListener:f=>updates.add(f),removeListener:f=>updates.delete(f)},query:async()=>[],create:async v=>{created++;assert.equal(v.url,'http://127.0.0.1:8000/#/live');return {id:7}},get:async()=>({status:'complete'})},
  scripting:{executeScript:async v=>{injection=v;return [{result:{token,port:8000}}]}},
  commands:{onCommand:{addListener(){}}}
};
const fetch=async url=>url.endsWith('/setup')?{ok:!setupFails,json:async()=>({port:8000})}:{ok:healthy,json:async()=>({ok:validServer,version:1})};
vm.runInNewContext(fs.readFileSync(new URL('../extension/background.js',import.meta.url),'utf8'),{chrome,URL,AbortSignal,fetch,setTimeout,clearTimeout});
const send=(msg,sender={url:origin+'popup.html'})=>new Promise(resolve=>{const wait=handler(msg,sender,resolve);if(!wait)resolve(undefined)});
assert.equal(await send({type:'PAIR'},{url:'https://evil.example',tab:{id:1}}),undefined);
assert.equal((await send({type:'PAIR',port:80})).ok,false);
recording=true;assert.equal((await send({type:'PAIR'})).ok,false);assert.equal(created,0);
recording=false;setupFails=true;assert.equal((await send({type:'PAIR'})).ok,false);assert.equal(created,0);
setupFails=false;healthy=false;assert.equal((await send({type:'PAIR'})).ok,false);assert.equal(saved.token,undefined);
healthy=true;const r=await send({type:'PAIR'});assert.equal(r.ok,true);assert.equal(r.token,undefined);assert.equal(saved.token,token);
assert.equal(injection.world,'MAIN');assert.equal(injection.target.tabId,7);assert.equal(updates.size,0);
assert.equal((await send({type:'HELLO'})).version,'1.4.1');
const original=saved.token;
healthy=false;
assert.equal((await send({type:'PAIR_KEY',port:8000,token:'z'.repeat(43)})).ok,false);
assert.equal(saved.token,original,'failed manual pairing retains working credentials');
assert.equal(saved.connectionCheck.phase,'error');
healthy=true;validServer=false;
assert.equal((await send({type:'PAIR_KEY',port:8000,token:'z'.repeat(43)})).ok,false);
assert.equal(saved.token,original,'HTTP 200 from another app is not a successful connection');
validServer=true;
assert.equal((await send({type:'PAIR_KEY',port:8000,token:'z'.repeat(43)})).ok,true);
assert.equal(saved.token,'z'.repeat(43));assert.equal(saved.connectionCheck.phase,'connected');
recording=true;
assert.equal((await send({type:'PAIR_KEY',port:8000,token:'x'.repeat(43)})).ok,false);
assert.equal(saved.token,'z'.repeat(43));
const pageResult=await vm.runInNewContext(`(${injection.func.toString()})('http://127.0.0.1:8000')`,{location:{origin:'https://evil.example',pathname:'/'},document:{title:'ClipForge'}});
assert(pageResult.error,'navigation to an untrusted page cannot request the token');
console.log('PASS: automatic pairing, validation, active-capture guard, foreign sender rejection, failed health retains credentials, no token response (mock Chrome APIs).');
