/* Replay analysis and live capture reuse the same job queue and caption renderer. */
async function mountLiveTools(host, params={}) {
  host.innerHTML=`<section class="live-tools"><div class="section-head"><div><h2>Make Shorts</h2><p class="hint-s">Find highlights in a finished replay or saved video. Vertical 9:16 and captions are ready.</p></div><a href="#/clips">Full clip maker →</a></div>
    <form id="live-make-form"><div id="live-source"></div><div class="live-export-options">
    <label class="field">Caption style<select name="preset" id="live-export-preset"></select></label>
    <label class="field">Vertical framing<select name="framing"><option value="blur">Keep whole picture · blurred fill</option><option value="crop">Fill screen · centre crop</option><option value="face">Follow the speaker</option></select></label>
    <label class="field">Clips<input name="clips" type="number" min="1" max="10" value="3" required></label>
    <button class="primary" type="submit">Find highlights</button></div><p id="live-make-error" class="error-box" role="alert" hidden></p><p id="live-make-status" role="status"></p></form>
    <p class="hint-s">For an ongoing stream, use capture above or <a href="#/monitor">Auto monitor</a>. Use Make captioned Short on a captured moment to frame webcam + gameplay.</p>
    <div class="section-head"><h3>Your Shorts</h3><span class="hint-s">Ready to review and download</span></div><div id="live-shorts-results"></div></section>`;
  const form=host.querySelector("form"),preset=form.elements.preset,status=host.querySelector("#live-make-status"),error=host.querySelector("#live-make-error");
  let opts,source,busy=false;
  if(params.clips)form.elements.clips.value=Math.min(10,Math.max(1,Number(params.clips)||3));
  const fail=e=>{error.textContent=e.message;error.hidden=false;};
  try {
    opts=await getOptions();if(!host.isConnected)return;
    preset.innerHTML=opts.presets.map(p=>`<option value="${esc(p.id)}">${esc(p.label)}</option>`).join("");preset.value="bold";
    try {const saved=JSON.parse(localStorage.getItem("cf-live-shorts")||"{}");if(opts.presets.some(p=>p.id===saved.preset))preset.value=saved.preset;if(["blur","crop","face"].includes(saved.framing))form.elements.framing.value=saved.framing;}catch{}
    source=await sourcePicker(host.querySelector("#live-source"),{prefill:params});
  }catch(e){fail(e);return;}
  host.captionPreset=()=>preset.value;
  form.onchange=()=>{try{localStorage.setItem("cf-live-shorts",JSON.stringify({preset:preset.value,framing:form.elements.framing.value}));}catch{}};
  form.onsubmit=async e=>{
    e.preventDefault();if(busy)return;const data=new FormData(form),problem=source.fill(data);
    if(problem)return fail(Error(problem));
    data.set("kind","clips");data.set("workflow","live");data.set("clip_mode","gaming");data.set("position","bottom");data.set("length","normal");
    busy=true;form.querySelector("button[type=submit]").disabled=true;error.hidden=true;status.textContent="Adding this video to the clipping queue…";
    try {const job=await api("/api/jobs",{method:"POST",body:data});if(host.isConnected){status.innerHTML=`Added to the queue. <a href="#/job/${job.id}">Watch progress →</a>`;await host.refreshShorts();}}
    catch(e){fail(e);status.textContent="";}
    finally{busy=false;form.querySelector("button[type=submit]").disabled=false;}
  };
  let version="";
  host.refreshShorts=async()=>{
    const list=await api("/api/jobs?kind=clips");if(!host.isConnected)return;
    let rows=list.filter(j=>j.options?.workflow==="live");const next=JSON.stringify(rows);if(next===version)return;
    rows=await Promise.all(rows.map(j=>j.state==="done"?api(`/api/jobs/${j.id}`):j));if(!host.isConnected)return;version=next;
    host.querySelector("#live-shorts-results").innerHTML=rows.length?rows.map(job=>`<article class="live-shorts-job"><div class="section-head"><a href="#/job/${job.id}">${esc(job.title||"Stream highlights")}</a><span class="hint-s">${esc(job.state)}</span></div>${job.error?`<p class="error-box">${esc(job.error)}</p>`:""}${job.state==="done"?`<div class="live-grid">${(job.clips||[]).map(c=>`<div class="live-card"><video controls preload="metadata" src="${fileUrl(job.id,c.files.voiced||c.files.captioned,false,job.finished)}#t=0.1"></video><div class="live-card-body"><h3>${esc(c.title||c.name)}</h3><p class="hint-s">9:16 · ${job.options.preset==="none"?"No captions":esc(job.options.preset)+" captions"}</p><a class="primary small" href="${fileUrl(job.id,c.files.voiced||c.files.captioned,true,job.finished)}">Download Short</a></div></div>`).join("")}</div>`:`<p class="hint-s">${job.state==="error"?"Open this job to review and retry.":"Detecting moments and preparing captioned exports. You can keep capturing."}</p>`}</article>`).join(""):'<p class="empty-note">Choose a replay above or send a captured moment to the clip maker below.</p>';
  };
  await host.refreshShorts();
}
