import assert from "node:assert/strict";
import {Detector, reaction, nomination, settings} from "../extension/detector.mjs";

const burst = (n = 30, text = "LMAO 😂") => Array.from({length: n}, (_, i) => ({author: `viewer-${i}`, text}));
const warm = (d) => { for (let t = 0; t <= 24; t++) d.tick(t); return d; };
assert(reaction("omegalul")); assert(reaction("ахахах")); assert(reaction("🤣")); assert(!reaction("hello everyone"));
assert.deepEqual(settings({pre: Infinity, tail: -3, max: 999, sound: "true"}), {pre: 20, tail: 5, max: 120, sensitivity: "normal", sound: false});

const d = warm(new Detector());
d.add(25, burst());
assert.equal(d.tick(25).active.start, 5, "20 seconds of lead-in");
for (let t = 26; t <= 34; t++) d.tick(t);
d.add(35, burst()); d.tick(35);
assert.equal(d.active.start, 5, "nearby bursts extend one moment");
let clip;
for (let t = 36; t <= 51; t++) clip = d.tick(t).clip || clip;
assert.equal(clip.end, 47, "tail starts at the last fresh reaction, not stale rolling-window messages"); assert.equal(clip.trigger, 25);
d.add(52, burst()); assert.equal(d.tick(52).active, null, "cooldown prevents immediate duplicate");

const spam = warm(new Detector());
for (let t = 25; t < 40; t++) { spam.add(t, burst(500).map((m) => ({...m, author: "spammer"}))); assert.equal(spam.tick(t).active, null); }
const stable = new Detector();
for (let t = 0; t < 100; t++) { stable.add(t, burst(20, "LOL")); assert.equal(stable.tick(t).active, null, "ordinary busy chat stays baseline"); }
const quiet = warm(new Detector());
for (let t = 25; t < 70; t++) { quiet.add(t, [{author: `v${t}`, text: "hello"}]); assert.equal(quiet.tick(t).active, null); }
const capped = warm(new Detector({max: 45}));
for (let t = 25; t <= 50; t++) { capped.add(t, burst()); const s = capped.tick(t); if (t === 50) assert.equal(s.clip.end - s.clip.start, 45); }
const manual = new Detector(); manual.tick(0); manual.trigger(3, "Manual highlight");
assert.equal(manual.active.start, 0); assert.equal(manual.flush(4).end, 4);
const sound = warm(new Detector({sound: true}));
assert.equal(sound.tick(25, 0.3).active, null, "one loud frame needs confirmation");
assert.equal(sound.tick(26, 0.3).active.reason, "Sound spike");
const silent = warm(new Detector()); assert.equal(silent.tick(25, 0.9).active, null);
// Stable traffic, but viewers switch from conversation to reactions.
const busy = new Detector();
for (let t = 0; t < 40; t++) { busy.add(t, burst(12, `hello ${t}`).map((m,i)=>({...m,author:`v${t}-${i}`}))); busy.tick(t); }
for (let t = 40; t < 45; t++) { busy.add(t, burst(12).map((m,i)=>({...m,author:`v${t}-${i}`}))); busy.tick(t); }
assert(busy.active, "reaction share detects a moment without an increase in message rate");
assert.equal(busy.active.reason, "More viewers reacting");
assert(busy.active.evidence.signals.includes("reaction_shift"));
const weak = warm(new Detector()); weak.add(25, burst(4)); weak.tick(25);
for (let t=26;t<=40;t++) assert.equal(weak.tick(t).active,null,"one small batch cannot confirm itself through stale window messages");
const small = warm(new Detector()); small.add(25,burst(4)); small.tick(25);
small.add(26,burst(3).map(m=>({...m,author:m.author+'new'}))); small.tick(26);
assert(small.active, "a smaller chat can confirm a reaction with fresh viewers");
assert.equal(small.active.trigger,25); assert.equal(small.active.start,5,"confirmation keeps the initial lead-in");
const bot = warm(new Detector()); bot.add(25,burst(100,"!clip LOL")); bot.add(26,burst(100,"https://example.com LOL"));
assert.equal(bot.tick(26).active,null); assert.equal(bot.accepted,0);
const duplicate = warm(new Detector()); duplicate.add(25,burst(4)); duplicate.tick(25); duplicate.add(26,burst(4));
assert.equal(duplicate.tick(26).active,null,"identical repeat spam does not count as fresh confirmation");
const continuous = new Detector();
for(let t=0;t<180;t++){continuous.add(t,burst(20,`LOL ${t}`).map((m,i)=>({...m,author:`v${t}-${i}`})));assert.equal(continuous.tick(t).active,null,"consistently excited chat is ordinary baseline");}
const sparse = warm(new Detector({sensitivity:"high"})); sparse.add(25,burst(3)); sparse.tick(25); sparse.add(26,burst(2).map(m=>({...m,author:m.author+'new'})));
assert(sparse.tick(26).active,"more-moments mode recognizes smaller confirmed reactions");
for (const text of ["clip that!", "CLIP IT", "clippez ça", "сделайте клип", "это в клип!"]) assert(nomination(text),text);
for (const text of ["don't clip that", "do not clip it", "ne clippez pas ça", "не сделайте клип", "hello"]) assert(!nomination(text),text);
const requested = warm(new Detector());
requested.add(25,burst(4,"clip that"));assert.equal(requested.tick(25).active,null);
requested.add(26,burst(3,"clip it").map(m=>({...m,author:m.author+'new'})));
assert.equal(requested.tick(26).active.reason,"Viewers asked for a clip");
assert(requested.active.evidence.signals.includes("viewer_requests"));
assert.equal(requested.active.start,5,"requests preserve the initial pre-roll");
const repeatedRequest=warm(new Detector());
for(let t=25;t<40;t++){repeatedRequest.add(t,burst(50,"clip that").map(m=>({...m,author:"one-viewer"})));assert.equal(repeatedRequest.tick(t).active,null);}
const regularRequests=new Detector();
for(let t=0;t<120;t++){regularRequests.add(t,burst(4,`clip that ${t}`).map((m,i)=>({...m,author:`r${t}-${i}`})));assert.equal(regularRequests.tick(t).active,null,"ordinary requests become baseline");}
const staleRequests=warm(new Detector());staleRequests.add(25,burst(4,"clip that"));
for(let t=25;t<40;t++)assert.equal(staleRequests.tick(t).active,null,"stale requests cannot confirm themselves");
console.log("PASS: baseline, busy-chat reaction shift, fresh-message confirmation, small chats, command/link filtering, repeat spam, lead-in, merged bursts, precise quiet tail, cooldown, max duration, manual save, sound opt-in");
console.log("PASS: multilingual viewer clip requests, negation, distinct viewers, repeated-request baseline and fresh confirmation.");
