"use strict";

async function renderMonitor() {
  setNav("monitor"); clearTimeout(pollTimer);
  view.innerHTML = `<section class="monitor-page" style="--k:var(--k-clips)">
    <header class="page-head"><p class="eyebrow">Auto monitor / YouTube</p><h1>Let the moments come to you</h1><p class="lede">Save a livestream, watch for reactions, and collect clips while the broadcast is still running.</p></header>
    <div class="monitor-overview"><span id="monitor-engine">Checking the monitor…</span><a href="#/live">Use the Chrome companion →</a></div><p class="error-box" id="monitor-storage" role="status" hidden></p>
    <div class="monitor-layout"><aside>
      <details class="live-setup" id="monitor-key-setup"><summary>YouTube connection <span id="monitor-key-status">Checking…</span></summary>
        <form id="monitor-key-form"><label class="field"><span class="label">YouTube Data API key</span><input name="key" type="password" placeholder="Google Cloud API key" autocomplete="off" minlength="15" maxlength="256" required></label><button class="ghost" type="submit">Save key securely</button><button class="ghost" type="button" id="monitor-test-key">Test connection</button></form>
        <p class="hint-s">Enable YouTube Data API v3 in your Google Cloud project. Your key is protected by Windows on this PC. This prototype uses public stream status and available chat; it does not connect through vidIQ.</p><a href="https://console.cloud.google.com/apis/library/youtube.googleapis.com" target="_blank" rel="noopener">Open API setup ↗</a>
      </details>
      <details class="live-setup"><summary>Recording limits</summary><form id="monitor-resources">
        <label class="field">Simultaneous recordings<input name="max_active" type="number" min="1" max="4" value="2" required></label>
        <label class="field">Recording quality<select name="quality"><option>480p</option><option selected>720p</option><option>1080p</option></select></label>
        <label class="field">Total buffer budget (MB)<input name="buffer_mb" type="number" min="512" max="8192" value="2048" required></label>
        <label class="field">Keep free (GiB)<input name="reserve_gb" type="number" min="2" max="100" value="8" required></label>
        <button class="ghost">Save limits</button><p class="hint-s">Applies to new recordings. Extra live streams wait for a slot. Recording is silent and includes source audio.</p></form></details>
      <form class="monitor-form" id="monitor-form"><h2 id="monitor-form-title">Add a livestream</h2>
        <label class="field"><span class="label">YouTube livestream URL</span><input name="url" type="url" placeholder="https://www.youtube.com/watch?v=…" required></label>
        <p class="hint-s">Use a specific stream link. Automatic channel discovery, Twitch and Kick are later stages.</p>
        <div class="monitor-fields"><label class="field"><span class="label">Export format</span><select name="aspect"><option value="landscape">Original</option><option value="vertical">Vertical 9:16</option></select></label>
        <label class="field"><span class="label">Captions</span><select name="preset" id="monitor-presets"><option value="none">No captions</option></select></label></div>
        <label class="field"><span class="label">Detection sensitivity</span><select name="sensitivity"><option value="low">Low · stronger reactions</option><option value="normal" selected>Balanced</option><option value="high">High · more candidates</option></select></label>
        <div class="monitor-checks"><label><input name="chat" type="checkbox" checked> Chat reactions</label><label><input name="audio" type="checkbox"> Sound spikes (experimental)</label></div>
        <details><summary>Timing & advanced settings</summary><div class="monitor-fields">
          <label class="field"><span class="label">Seconds before</span><input name="pre" type="number" min="5" max="40" value="20"></label>
          <label class="field"><span class="label">Seconds after quiet</span><input name="tail" type="number" min="5" max="25" value="12"></label>
          <label class="field"><span class="label">Maximum clip (s)</span><input name="max" type="number" min="45" max="120" value="60"></label>
          <label class="field"><span class="label">Buffer (s)</span><input name="buffer" type="number" min="120" max="180" value="150"></label>
          <label class="field"><span class="label">Move clip earlier (s)</span><input name="offset" type="number" min="0" max="30" value="0"></label>
        </div><p class="hint-s">Chat and video can arrive at different times. Increase the offset if clips start too late. Sound detects loudness changes; it cannot reliably distinguish laughter, music or ads.</p>
        <label class="monitor-check"><input name="translate" type="checkbox"> Translate speech to English</label>
        <label class="field"><span class="label">Your authorized media feed (optional)</span><input name="feed" type="url" placeholder="https://…/live.m3u8" autocomplete="off"></label>
        <label class="monitor-check"><input name="clear_feed" type="checkbox"> Remove saved feed and use Streamlink</label>
        <p class="hint-s">Without a feed, Streamlink attempts to open the livestream. Use content and a recording method you have permission to use. API access alone does not grant download permission.</p></details>
        <div class="live-actions"><button class="primary" type="submit" id="monitor-save">Save stream</button><button class="ghost" type="button" id="monitor-cancel" hidden>Cancel edit</button></div>
      </form><p class="hint-s">Streams record independently; exports share one processing queue. Keep ClipForge running and Windows awake. Enabled monitors resume after the app restarts.</p>
    </aside><div class="monitor-main"><p class="error-box" id="monitor-error" role="alert" hidden></p><p id="monitor-notice" role="status"></p>
      <div class="section-head"><h2>Streams</h2><span class="muted" id="monitor-count"></span></div><div id="monitor-streams"></div>
      <div class="section-head"><h2>Captured moments</h2><a href="#/library?kind=autoclip">Open library →</a></div><div id="monitor-clips"></div>
    </div></div></section>`;
  const form = document.getElementById("monitor-form"), streams = document.getElementById("monitor-streams"), error = document.getElementById("monitor-error");
  let state, editing = null, busy = false, previous = "", streamsVersion = "";
  const showFailure = e => {if (error.isConnected) {error.textContent=e.message;error.hidden=false;}};
  const post = (path, body, method="POST") => api(path,{method,headers:{"Content-Type":"application/json"},...(body===undefined?{}:{body:JSON.stringify(body)})});
  const notice = text => {document.getElementById("monitor-notice").textContent=text; error.hidden=true;};
  const resourceForm=document.getElementById("monitor-resources");let resourcesLoaded=false;
  resourceForm.onsubmit=async e=>{e.preventDefault();const data=Object.fromEntries(new FormData(resourceForm));for(const k of ["max_active","buffer_mb","reserve_gb"])data[k]=Number(data[k]);try{await post("/api/monitor/settings",data,"PUT");notice("Recording limits saved for new captures.");await refresh();}catch(e){showFailure(e);}};
  function reset() {editing=null;form.reset();document.getElementById("monitor-form-title").textContent="Add a livestream";document.getElementById("monitor-save").textContent="Save stream";document.getElementById("monitor-cancel").hidden=true;}
  document.getElementById("monitor-cancel").onclick=reset;
  document.getElementById("monitor-test-key").onclick=async e=>{
    const button=e.currentTarget;button.disabled=true;
    try {const r=await post("/api/monitor/key/check");if(streams.isConnected)notice(`YouTube API connected. Stream: ${r.state}. ${r.chat_available?"Live chat is available.":"No active live chat available."}`);}
    catch(e){showFailure(e);}finally{button.disabled=false;}
  };
  document.getElementById("monitor-key-form").onsubmit=async e=>{
    e.preventDefault();const f=e.currentTarget,button=f.querySelector("button");button.disabled=true;
    try {await post("/api/monitor/key",{key:f.elements.key.value});f.reset();notice("Key saved on this PC. Start a stream to check access.");await refresh();}
    catch(e){showFailure(e);}finally{button.disabled=false;}
  };
  form.onsubmit=async e=>{
    e.preventDefault();const values=Object.fromEntries(new FormData(form));
    for(const name of ["pre","tail","max","buffer","offset"])values[name]=Number(values[name]);
    for(const name of ["chat","audio","translate","clear_feed"])values[name]=form.elements[name].checked;
    const button=document.getElementById("monitor-save");button.disabled=true;
    try {await post(editing?`/api/monitor/${editing}`:"/api/monitor",values,editing?"PUT":"POST");reset();notice("Stream saved. Choose Start monitoring when ready.");await refresh();}
    catch(e){showFailure(e);}finally{button.disabled=false;}
  };
  streams.onclick=async e=>{
    const b=e.target.closest("button[data-action]");if(!b)return;
    const {id,action}=b.dataset; b.disabled=true;
    try {
      if(action==="edit") {
        const c=state.monitors.find(c=>c.id===id);editing=id;
        for(const [key,value] of Object.entries(c))if(form.elements[key]){const field=form.elements[key];if(field.type==="checkbox")field.checked=!!value;else field.value=value;}
        form.elements.feed.value="";form.elements.clear_feed.checked=false;
        document.getElementById("monitor-form-title").textContent="Edit stream settings";document.getElementById("monitor-save").textContent="Save changes";document.getElementById("monitor-cancel").hidden=false;form.elements.url.focus();
      } else {
        await post(`/api/monitor/${id}${action==="delete"?"":"/"+action}`,undefined,action==="delete"?"DELETE":"POST");
        notice(action==="mark"?"Moment marked. Capture continues until the reaction settles.":action==="delete"?"Stream removed. Existing clips are kept.":"Monitoring preference saved.");await refresh();
      }
    } catch(e){showFailure(e);}finally{if(b.isConnected)b.disabled=false;}
  };
  async function refresh() {
    if(busy||!streams.isConnected)return;busy=true;clearTimeout(pollTimer);
    try {
      const [data,clips]=await Promise.all([api("/api/monitor"),api("/api/jobs?kind=autoclip")]);if(!streams.isConnected)return;
      state=data;
      if(!resourcesLoaded&&data.settings){for(const [key,value] of Object.entries(data.settings))if(resourceForm.elements[key])resourceForm.elements[key].value=value;resourcesLoaded=true;}
      document.getElementById("monitor-engine").textContent=`${data.engine_running?"Monitor service running":"Monitor service stopped"} · ${fmtSize(data.free_bytes)} free · ${data.quota_used}/${data.quota_budget} local API budget`;
      document.getElementById("monitor-key-status").textContent=data.key_configured?"Key saved":"Setup needed";
      const reserve=data.settings?.reserve_gb||8,storage=document.getElementById("monitor-storage");storage.hidden=data.free_bytes>=reserve*1024**3;storage.textContent=`Recording needs ${reserve} GiB free on the ClipForge drive. Change the reserve in Recording limits or free some storage.`;
      if(!data.key_configured)document.getElementById("monitor-key-setup").open=true;
      const presets=document.getElementById("monitor-presets");if(presets.options.length===1){presets.innerHTML=data.presets.map(p=>`<option value="${esc(p.id)}">${esc(p.label)}</option>`).join("");}
      document.getElementById("monitor-count").textContent=`${data.monitors.filter(c=>c.runtime?.state==="recording").length}/${data.max_active} recording · ${data.monitors.filter(c=>c.enabled).length} enabled`;
      const version=JSON.stringify(data.monitors);
      if(version!==streamsVersion){streamsVersion=version;streams.innerHTML=data.monitors.length?data.monitors.map(c=>{
        const r=c.runtime||{},d=r.detector||{},points=r.history||[],peak=Math.max(1,...points),names={disabled:"Paused",needs_setup:"Connection needed",checking:"Checking status",upcoming:"Scheduled",offline:"Offline",connecting:"Opening feed",buffering:"Building buffer",recording:"Recording",retrying:"Retrying",starting:"Starting"};
        return `<article class="monitor-card"><div class="section-head"><span class="monitor-state ${r.state==="recording"?"is-recording":""}">${esc(names[r.state]||r.state)}</span><span class="hint-s">YouTube</span></div><h3>${esc(r.title||"Saved livestream")}</h3><a class="monitor-source" href="${esc(c.url)}" target="_blank" rel="noopener">${esc(c.url)}</a><p>${esc(r.detail)}</p>
          ${r.state==="recording"?`<div class="monitor-metrics"><div><strong>${fmtTime(r.buffered_seconds)}</strong><span>in buffer</span></div><div><strong>${(d.rate||0).toFixed(1)}/s</strong><span>chat messages</span></div><div><strong>${d.score||0}</strong><span>signal strength</span></div></div>`:""}
          ${points.length>1?`<svg class="monitor-chart" viewBox="0 0 300 45" role="img" aria-label="Recent chat message rate"><polyline points="${points.map((n,i)=>`${(i/(points.length-1)*300).toFixed(1)},${(42-n/peak*38).toFixed(1)}`).join(" ")}"/></svg>`:""}
          <p class="hint-s">${esc(r.chat||"Chat not connected")}${d.warmupRemaining?` · Learning baseline: ${d.warmupRemaining}s`:""}${d.active?` · Saving reaction: ${d.tailRemaining}s after quiet`:""}</p>${r.warning?`<p class="error-box">${esc(r.warning)}</p>`:""}
          <div class="live-actions"><button class="${c.enabled?"ghost":"primary"} small" data-id="${c.id}" data-action="toggle">${c.enabled?"Pause":"Start monitoring"}</button><button class="ghost small" data-id="${c.id}" data-action="mark" ${r.state!=="recording"?"disabled":""}>Save moment</button>${!c.enabled?`<button class="ghost small" data-id="${c.id}" data-action="edit">Edit</button><button class="ghost small" data-id="${c.id}" data-action="delete">Remove</button>`:""}${r.last_job?`<a href="#/job/${r.last_job}">Latest clip →</a>`:""}</div></article>`;
      }).join(""):`<div class="workflow-empty"><h3>Your first stream starts here.</h3><p>Add a YouTube livestream on the left, then start monitoring. Real connection and capture status will appear here.</p></div>`;}
      const clipVersion=JSON.stringify(clips);
      if(clipVersion!==previous){previous=clipVersion;document.getElementById("monitor-clips").innerHTML=clips.length?`<div class="live-grid">${clips.slice(0,12).map(j=>{
        const f=j.outputs?.find(f=>f.file==="captioned.mp4")||j.outputs?.find(f=>f.file==="vertical.mp4")||j.outputs?.find(f=>f.media==="video");
        return `<article class="live-card">${f?`<video controls preload="metadata" src="${fileUrl(j.id,f.file,false,j.finished)}"></video>`:`<div class="live-wait">${esc(j.state==="error"?"Needs attention":"Processing clip…")}</div>`}<div class="live-card-body"><p class="hint-s">${esc(fmtDate(j.created))} · ${esc(j.state)}</p><h3>${esc(j.title)}</h3>${j.error?`<p class="error-box">${esc(j.error)}</p>`:""}<div class="live-actions"><a class="ghost small" href="#/job/${j.id}">Review / edit</a>${f?`<a class="primary small" href="${fileUrl(j.id,f.file,true,j.finished)}">Download MP4</a>`:""}</div></div></article>`;
      }).join("")}</div>`:`<p class="monitor-empty">No captures yet. Detected moments will appear here with their actual processing status.</p>`;}
    } catch(e){showFailure(e);}finally{busy=false;if(streams.isConnected)pollTimer=setTimeout(refresh,3000);}
  }
  await refresh();
}
