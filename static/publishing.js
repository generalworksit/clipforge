/* The publishing queue contains local snapshots. Every item needs explicit approval. */
async function renderPublishing(params={}) {
  setNav("publish");clearTimeout(pollTimer);
  view.innerHTML=`<section class="monitor-page"><header class="page-head"><p class="eyebrow">YouTube publishing</p><h1>Review. Schedule. Publish.</h1><p class="lede">Prepare clips here. Only clips you approve can upload.</p></header>
  <p id="publish-error" class="error-box" role="alert" hidden></p><p id="publish-notice" role="status"></p>
  <div class="monitor-layout"><aside><details class="live-setup" id="publish-connection"><summary>YouTube connection <span id="publish-account">Checking…</span></summary>
  <form id="publish-client"><label class="field">Google Desktop OAuth JSON<input name="file" type="file" accept=".json" required></label><button class="ghost">Save OAuth app</button></form>
  <p class="hint-s">Create a Desktop OAuth client with YouTube Data API enabled. This connects your publishing channel separately from live monitoring.</p><a href="https://console.cloud.google.com/apis/credentials" target="_blank" rel="noopener">Google API credentials ↗</a>
  <div class="live-actions"><button class="primary small" id="publish-connect">Connect YouTube</button><button class="ghost small" id="publish-disconnect">Disconnect</button></div><a id="publish-auth-link" target="_blank" rel="noopener" hidden>Continue with Google ↗</a></details>
  <form id="publish-limit"><label class="field">Maximum uploads per 24 hours<input name="daily_limit" type="number" min="1" max="20" value="4" required></label><button class="ghost small">Save limit</button></form>
  <form id="publish-draft" class="monitor-form"><h2 id="publish-form-title">Prepare a clip</h2><label class="field">Video<select name="source" required><option value="">Choose a finished MP4…</option></select></label>
  <label class="field">Title<input name="title" type="text" maxlength="100" required></label><label class="field">Description<textarea name="description" maxlength="5000" rows="3"></textarea></label>
  <label class="field">Visibility<select name="privacy"><option value="private">Private</option><option value="unlisted">Unlisted</option><option value="public">Public</option></select></label>
  <label class="field">Publish time (your local time)<input name="schedule" type="datetime-local"></label><p class="hint-s">A schedule requires Public. YouTube keeps it private until that time. Leave empty to upload with the selected visibility.</p>
  <label class="check"><input name="made_for_kids" type="checkbox">Made for kids</label><label class="check"><input name="synthetic" type="checkbox">Contains realistic altered or synthetic content</label>
  <div class="live-actions"><button class="primary">Save draft</button><button id="publish-reset" class="ghost" type="button">New draft</button></div></form>
  <p class="hint-s">ClipForge must be running to upload. Once a scheduled upload completes, YouTube handles its publication. Uploaded items are managed in YouTube Studio.</p></aside>
  <div><div class="section-head"><h2>Review queue</h2><span id="publish-count" class="muted"></span></div><div id="publish-items"></div></div></div></section>`;
  const root=view.querySelector(".monitor-page"),$=id=>root.querySelector(`#publish-${id}`),form=$("draft");
  let state,editing=null,version="",sources=[],loaded=false,busy=false;
  const failure=e=>{if(root.isConnected){$("error").textContent=e.message;$("error").hidden=false;}};
  const notice=text=>{$("notice").textContent=text;$("error").hidden=true;};
  const json=(path,body,method="POST")=>api("/api/publishing"+path,{method,headers:{"Content-Type":"application/json"},...(body?{body:JSON.stringify(body)}:{})});
  const perform=async(fn)=>{try{await fn();await refresh();}catch(e){failure(e);}};
  $("client").onsubmit=e=>{e.preventDefault();perform(async()=>{await api("/api/publishing/youtube/client",{method:"POST",body:new FormData(e.currentTarget)});$("client").reset();notice("OAuth app saved. Connect your YouTube channel.");});};
  $("connect").onclick=()=>perform(async()=>{const r=await json("/youtube/connect");$("auth-link").href=r.url;$("auth-link").hidden=false;notice("Continue with Google to choose your channel.");});
  $("disconnect").onclick=()=>perform(async()=>{await json("/youtube/disconnect");notice("Channel disconnected. Manage remaining OAuth permissions in your Google account.");});
  $("limit").onsubmit=e=>{e.preventDefault();perform(async()=>{await json("/settings",{daily_limit:Number($("limit").elements.daily_limit.value)},"PUT");notice("Upload limit saved.");});};
  const reset=()=>{editing=null;form.reset();form.elements.source.disabled=false;$("form-title").textContent="Prepare a clip";};
  $("reset").onclick=reset;
  form.elements.source.onchange=()=>{const s=sources[Number(form.elements.source.value)];if(s&&!editing)form.elements.title.value=s.title.slice(0,100);};
  form.onsubmit=e=>{e.preventDefault();perform(async()=>{const source=sources[Number(form.elements.source.value)];if(!source||form.elements.source.value==="")throw Error("Choose a video.");
    const schedule=form.elements.schedule.value;const body={job_id:source.job,file:source.file,title:form.elements.title.value,description:form.elements.description.value,privacy:form.elements.privacy.value,
      publish_at:schedule?new Date(schedule).getTime()/1000:null,made_for_kids:form.elements.made_for_kids.checked,synthetic:form.elements.synthetic.checked};
    await json(editing?`/items/${editing}`:"/items",body,editing?"PUT":"POST");reset();notice("Draft saved locally. Review it below before approving an upload.");});};
  $("items").onclick=async e=>{const button=e.target.closest("button[data-action]");if(!button||button.disabled)return;
    const item=state.items.find(x=>x.id===button.dataset.id);if(!item)return;const action=button.dataset.action;
    if(action==="edit"){
      const index=sources.findIndex(s=>s.job===item.job_id&&s.file===item.file);if(index<0)return failure(Error("The original library entry is missing. The reviewed copy is still available below."));
      editing=item.id;form.elements.source.value=index;form.elements.source.disabled=true;$("form-title").textContent="Edit draft";
      for(const key of ["title","description","privacy"])form.elements[key].value=item[key];
      for(const key of ["made_for_kids","synthetic"])form.elements[key].checked=item[key];
      if(item.publish_at){const d=new Date(item.publish_at*1000);form.elements.schedule.value=new Date(d.getTime()-d.getTimezoneOffset()*60000).toISOString().slice(0,16);}else form.elements.schedule.value="";
      form.scrollIntoView({behavior:"smooth"});return;
    }
    button.disabled=true;
    await perform(async()=>{
      if(action==="approve"){
        const channel=state.account;
        if(!channel.connected)throw Error("Connect the channel you want to publish to first.");
        const destination=item.publish_at?`public at ${new Date(item.publish_at*1000).toLocaleString()}`:item.privacy;
        if(!confirm(`Upload “${item.title}” to ${channel.title} (${destination})? This approves this exact video.`))return;
        await json(`/items/${item.id}/approve`,{consent:true,channel_id:channel.channel_id});notice("Upload approved. ClipForge will send it within your upload limit.");
      }else if(action==="cancel"){
        const question=item.has_session?"Have you checked YouTube Studio for this upload? Removing this queue entry will NOT delete any video already on YouTube.":"Cancel this draft and remove its publishing copy? Your original clip stays in the library.";
        if(!confirm(question))return;
        await json(`/items/${item.id}/cancel`,{checked_remote:!!item.has_session});notice("Queue entry removed. Any video already on YouTube stays there.");
      }
    });button.disabled=false;
  };
  async function refresh(){
    if(!root.isConnected||busy)return;clearTimeout(pollTimer);busy=true;
    try{
      const data=await api("/api/publishing");if(!root.isConnected)return;state=data;
      $("account").textContent=data.account.connected?data.account.title:data.account.configured?"Ready to connect":"Setup needed";
      $("connect").disabled=!data.account.configured;$("disconnect").disabled=!data.account.connected;
      if(data.account.connected)$("auth-link").hidden=true;
      if(!loaded){$("limit").elements.daily_limit.value=data.settings.daily_limit;$("connection").open=!data.account.connected;loaded=true;}
      $("count").textContent=`${data.items.length} items · ${data.settings.daily_limit} uploads / 24 h`;
      const next=JSON.stringify([data.items,data.account]);if(version===next)return;version=next;
      $("items").innerHTML=data.items.length?data.items.map(item=>{
        const editable=["draft","needs_review"].includes(item.state),done=item.state==="uploaded",cancelled=item.state==="cancelled";
        return `<article class="live-card" style="margin-bottom:16px">${cancelled?"":`<video controls preload="metadata" src="/api/publishing/items/${item.id}/media#t=0.1" style="max-height:360px"></video>`}<div class="live-card-body"><p class="hint-s">${esc(item.state.replaceAll("_"," "))} · ${esc(item.publish_at?new Date(item.publish_at*1000).toLocaleString():item.privacy)}</p><h3>${esc(item.title)}</h3>${item.error?`<p class="error-box">${esc(item.error)}</p>`:""}<div class="live-actions">${editable?`<button class="primary small" data-action="approve" data-id="${item.id}">${item.state==="draft"?"Approve upload":"Resume reviewed upload"}</button><button class="ghost small" data-action="edit" data-id="${item.id}" ${item.has_session?"disabled":""}>Edit</button>`:""}${!done&&!cancelled?`<button class="ghost small" data-action="cancel" data-id="${item.id}" ${item.state==="uploading"||(item.has_session&&item.state!=="needs_review")?"disabled":""}>${item.has_session?"Remove after checking Studio":"Cancel"}</button>`:""}${done?`<a href="https://www.youtube.com/watch?v=${encodeURIComponent(item.video_id)}" target="_blank" rel="noopener">Open video ↗</a>`:""}</div></div></article>`;
      }).join(""):'<p class="monitor-empty">No drafts yet. Choose a finished clip to prepare its upload.</p>';
    }catch(e){failure(e);}finally{busy=false;if(root.isConnected)pollTimer=setTimeout(refresh,3000);}
  }
  try{
    const library=await api("/api/jobs");if(!root.isConnected)return;
    for(const job of library.filter(j=>j.state==="done")){
      const files=new Map((job.outputs||[]).filter(o=>o.media==="video"&&o.file.endsWith(".mp4")).map(o=>[o.file,o.label]));
      for(const clip of job.clips||[])for(const key of ["voiced","captioned","clean"])if(clip.files?.[key])files.set(clip.files[key],clip.title||key);
      for(const [file,label] of files)sources.push({job:job.id,file,title:job.title||label,label:`${job.title||"Clip"} · ${label}`});
    }
    form.elements.source.innerHTML='<option value="">Choose a finished MP4…</option>'+sources.map((s,i)=>`<option value="${i}">${esc(s.label)}</option>`).join("");
    const selected=sources.findIndex(s=>s.job===params.from&&s.file===params.file);if(selected>=0){form.elements.source.value=selected;form.elements.source.onchange();}
  }catch(e){failure(e);}
  await refresh();
}
