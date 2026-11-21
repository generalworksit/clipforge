/* Gaming layout editor. The backend supplies the exact geometry used by FFmpeg. */
function gamingGuide() {
  return `<details class="gaming-guide" open><summary>See how it works <span>8-second guide</span></summary>
    <div class="gaming-guide-body"><svg viewBox="0 0 720 224" role="img" aria-label="Draw around the webcam, frame the gameplay, and combine both in a vertical Short.">
    <rect class="guide-screen" x="16" y="24" width="320" height="180" rx="8"/>
    <path class="guide-world" d="M20 154L100 72 146 112 220 56 332 154V200H20Z"/>
    <path class="guide-road" d="M168 124H206L256 200H108Z"/>
    <rect class="guide-car" x="171" y="151" width="35" height="20" rx="6"/>
    <g transform="translate(34 132)"><rect class="guide-camera" width="74" height="56" rx="3"/><circle class="guide-face" cx="37" cy="21" r="12"/><path class="guide-face" d="M13 56Q14 33 37 33T61 56Z"/></g>
    <rect class="guide-camera-box" x="30" y="128" width="82" height="64" rx="3"/>
    <rect class="guide-game-box" x="124" y="42" width="198" height="140" rx="3"/>
    <path class="guide-arrow" d="M380 112H494m-14-14 14 14-14 14"/>
    <rect class="guide-screen" x="551" y="5" width="123" height="214" rx="8"/>
    <g class="guide-camera-move"><rect class="guide-camera" width="74" height="56"/><circle class="guide-face" cx="37" cy="21" r="12"/><path class="guide-face" d="M13 56Q14 33 37 33T61 56Z"/></g>
    <g class="guide-game-move"><rect class="guide-game" width="198" height="140"/><path class="guide-world" d="M0 70L40 20 90 60 130 0 198 72V140H0Z"/><path class="guide-road" d="M83 61H105L148 140H40Z"/><rect class="guide-car" x="83" y="89" width="27" height="18" rx="4"/></g>
    <g class="guide-caption"><rect x="565" y="131" width="95" height="19" rx="4"/><text x="612" y="144" text-anchor="middle">WHAT A MOMENT</text></g>
    </svg><div class="gaming-guide-steps"><p><b>1. Webcam</b>Drag around the facecam.</p><p><b>2. Gameplay</b>Select Gameplay and frame the action.</p><p><b>3. Export</b>Preview the Short, then add captions.</p></div><button class="btn-s" id="gaming-guide-play" type="button">Pause guide</button></div></details>`;
}
function mountGamingGuide(root) {
  const guide=root.querySelector(".gaming-guide"),button=root.querySelector("#gaming-guide-play");
  const reduced=matchMedia("(prefers-reduced-motion: reduce)").matches;
  let ended=reduced,paused=false;
  if(reduced){button.hidden=true;return;}
  guide.querySelector(".guide-game-move").addEventListener("animationend",()=>{ended=true;button.textContent="Replay guide";});
  button.onclick=()=>{
    if(ended){guide.classList.add("guide-reset");void guide.offsetWidth;guide.classList.remove("guide-reset");ended=false;paused=false;}
    else paused=!paused;
    guide.classList.toggle("guide-paused",paused);button.textContent=paused?"Continue guide":"Pause guide";
  };
}
async function renderGaming(params = {}) {
  setNav("gaming"); clearTimeout(pollTimer);
  view.innerHTML = `<section class="gaming-page"><header class="page-head"><p class="eyebrow">Gaming / reaction</p><h1>Your webcam. Your gameplay.</h1><p class="lede">Draw the two regions, check the vertical preview, export.</p></header>
    ${gamingGuide()}
    <div class="gaming-source"><label class="field">Local video<input id="gaming-upload" type="file" accept="video/*"></label>
    <label class="field">From your library<select id="gaming-library"><option value="">Choose a saved video…</option></select></label>
    <label class="field">Video URL<div class="gaming-inline"><input id="gaming-url" type="url" placeholder="YouTube, Twitch or Kick recording"><button id="gaming-import" class="ghost">Import</button></div></label></div>
    <p id="gaming-message" role="status" class="hint-s"></p><p id="gaming-error" role="alert" class="error-box" hidden></p>
    <div class="gaming-workspace"><div class="gaming-edit"><div class="gaming-panel">
      <div class="gaming-toolbar"><div role="group" aria-label="Region to draw"><button class="btn-s" data-region="camera" aria-pressed="true">Webcam</button><button class="btn-s" data-region="game" aria-pressed="false">Gameplay</button></div><span class="gaming-tools"><button id="gaming-auto" class="primary small" disabled title="Finds the webcam and the gameplay for you">Auto detect</button><button id="gaming-full" class="ghost small">Use full frame</button></span></div>
      <canvas id="gaming-source-frame" width="960" height="540" aria-label="Source video. Drag to draw the selected region, or use the coordinate controls below."></canvas>
      <video id="gaming-video" preload="metadata" hidden playsinline></video>
      <div class="gaming-playback"><button id="gaming-play" class="btn-s" disabled>Play</button><input id="gaming-seek" type="range" aria-label="Preview time" min="0" max="1" step=".05" value="0" disabled><output id="gaming-time">0:00</output></div>
      <div class="gaming-coordinates">${["X", "Y", "Width", "Height"].map((n,i)=>`<label>${n} %<input type="number" data-coord="${i}" aria-label="Region ${n.toLowerCase()} percent" min="${i<2?0:1}" max="100" step=".1"></label>`).join("")}</div>
      <p class="hint-s">Auto detect finds the webcam for you. To fix it, draw over the webcam, then select Gameplay and frame the action.</p></div>
      <div class="gaming-panel gaming-controls">
      <label class="field">Layout<select id="gaming-mode"><option value="stack">Webcam + gameplay</option><option value="pip">Small webcam overlay</option><option value="game">Gameplay only</option></select></label>
      <label class="field">Order<select id="gaming-order"><option value="camera">Webcam on top</option><option value="game">Gameplay on top</option></select></label>
      <label class="field">Camera size <output id="gaming-share-label">38%</output><input id="gaming-share" type="range" min="20" max="55" value="38" step="1"></label>
      <label class="field">Webcam fit<select id="gaming-camera-fit"><option value="contain">Keep whole region</option><option value="cover">Fill the space</option></select></label>
      <label class="field">Gameplay fit<select id="gaming-game-fit"><option value="contain">Keep whole region / HUD</option><option value="cover">Fill the space</option></select></label>
      <label class="check"><input id="gaming-mirror" type="checkbox">Mirror webcam</label>
      <label class="field">Start (seconds)<input id="gaming-start" type="number" min="0" step=".1" value="0"></label>
      <label class="field">End (seconds)<input id="gaming-end" type="number" min="0" step=".1" value="60"></label>
      <label class="field">Captions<select id="gaming-captions"><option value="none">No captions</option></select></label>
      <label class="field">Caption position<select id="gaming-caption-position"><option value="seam">Between webcam and game</option><option value="bottom">Lower safe area</option><option value="top">Upper safe area</option></select></label>
      <label class="check"><input id="gaming-translate" type="checkbox">Translate speech to English</label>
      <label class="field">Resolution<select id="gaming-resolution"><option value="1080">1080 × 1920</option><option value="720">720 × 1280</option></select></label>
      </div><div class="gaming-panel gaming-presets"><label class="field">Saved layouts<select id="gaming-presets"><option value="">Choose a layout…</option></select></label><label class="field">Layout name<input id="gaming-preset-name" maxlength="48" placeholder="Streamer / game"></label><button id="gaming-save" class="ghost small">Save layout</button><button id="gaming-remove" class="ghost small">Remove</button></div>
      </div><aside class="gaming-preview gaming-panel"><h2>Vertical preview</h2><canvas id="gaming-preview" width="360" height="640" aria-label="Vertical composition preview"></canvas><p class="hint-s">Caption zone is a guide. Speech captions are generated on export.</p><button id="gaming-export" class="primary" disabled>Export clip</button><p class="hint-s">Up to 3 minutes · original kept</p></aside></div></section>`;
  const root = view.querySelector(".gaming-page"), $ = id => root.querySelector(`#gaming-${id}`);
  mountGamingGuide(root);
  const video = $("video"), canvas = $("source-frame"), preview = $("preview"), ctx = canvas.getContext("2d"), out = preview.getContext("2d");
  let settings = {mode:"stack",camera:[0,.6,.3,.4],game:[0,0,1,1],share:.38,order:"camera",camera_fit:"contain",game_fit:"contain",mirror:false,resolution:1080,captions_at:"seam"};
  let edits=0, region="camera", source=null, objectURL=null, renderPlan=null, sequence=0, sourceSequence=0, drag=null, submitting=false, presets=[], autoRun=null;
  const fail = message => { $("error").textContent=message; $("error").hidden=!message; };
  const message = text => { $("message").textContent=text; };
  const escape = text => String(text).replace(/[&<>"']/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
  const safeSettings = value => value && typeof value==="object" && [value.camera,value.game].every(r=>Array.isArray(r)&&r.length===4&&r.every(Number.isFinite));
  try { const saved=JSON.parse(localStorage.getItem("clipforge-gaming-layouts")||"[]"); if(Array.isArray(saved)) presets=saved.filter(p=>typeof p.name==="string"&&safeSettings(p.settings)).slice(0,20); } catch {}
  function presetList(){ $("presets").innerHTML='<option value="">Choose a layout…</option>'+presets.map((p,i)=>`<option value="${i}">${escape(p.name)}</option>`).join(""); }
  function storePresets(){ try {localStorage.setItem("clipforge-gaming-layouts",JSON.stringify(presets));presetList();return true;} catch {fail("Could not save layouts. Check browser storage permissions.");return false;} }
  function controls(){
    for(const [id,key] of [["mode","mode"],["order","order"],["camera-fit","camera_fit"],["game-fit","game_fit"],["resolution","resolution"],["caption-position","captions_at"]]) $(id).value=settings[key];
    $("share").value=Math.round(settings.share*100);$("share-label").textContent=`${Math.round(settings.share*100)}%`;$("mirror").checked=settings.mirror;
    root.querySelectorAll("[data-coord]").forEach(input=>input.value=(settings[region][Number(input.dataset.coord)]*100).toFixed(1));
    root.querySelectorAll("[data-region]").forEach(button=>button.setAttribute("aria-pressed",String(button.dataset.region===region)));
    $("order").disabled=settings.mode!=="stack";$("share").disabled=settings.mode==="game";$("mirror").disabled=settings.mode==="game";
    $("caption-position").disabled=$("captions").value==="none";$("translate").disabled=$("captions").value==="none";
  }
  async function updatePlan(){
    controls();const seq=++sequence;renderPlan=null;$("export").disabled=true;
    if(!video.videoWidth || !source)return;
    const data=new FormData();data.set("layout",JSON.stringify(settings));data.set("width",video.videoWidth);data.set("height",video.videoHeight);
    try {const plan=await api("/api/layouts/plan",{method:"POST",body:data});if(seq!==sequence||!root.isConnected)return;renderPlan=plan;$("export").disabled=submitting;fail("");}
    catch(e){if(seq===sequence&&root.isConnected)fail(e.message);}
  }
  function loadSource(url, selection){
    $("import").disabled=false;
    autoRun?.abort();autoRun=null;delete $("auto").dataset.busy;$("auto").disabled=true;$("auto").textContent="Auto detect";
    sourceSequence++; if(objectURL)URL.revokeObjectURL(objectURL);objectURL=selection.file?url:null;
    video.pause();video.removeAttribute("src");video.load();source=selection;renderPlan=null;sequence++;$("export").disabled=true;
    video.src=url;video.load();message(selection.name||"Video loaded");fail("");
  }
  video.onloadedmetadata=()=>{if(!root.isConnected)return;canvas.width=Math.min(1280,video.videoWidth);canvas.height=Math.round(canvas.width*video.videoHeight/video.videoWidth);$("seek").max=video.duration;$("seek").disabled=false;$("play").disabled=false;$("start").value=0;$("end").value=Math.round(Math.min(60,video.duration)*100)/100;video.currentTime=Math.min(.1,video.duration/2);$("auto").disabled=false;updatePlan();autoDetect(false);};
  video.onerror=()=>{if(root.isConnected){renderPlan=null;$("export").disabled=true;fail("This video cannot be previewed in your browser. Convert it to MP4 first.");}};
  $("upload").onchange=()=>{const file=$("upload").files[0];if(file){$("library").value="";loadSource(URL.createObjectURL(file),{file,name:file.name});}};
  $("library").onchange=()=>{const option=$("library").selectedOptions[0];if(!option?.value)return;$("upload").value="";loadSource(fileUrl(option.dataset.job,option.dataset.file,false),{job:option.dataset.job,fileName:option.dataset.file,name:option.textContent});};
  $("import").onclick=async()=>{
    const url=$("url").value.trim();if(!url)return fail("Paste a recorded video URL.");
    $("import").disabled=true;fail("");
    try {const data=new FormData();data.set("urls",url);data.set("mode","video");data.set("quality","1080");data.set("compatible","1");
      const response=await api("/api/downloads",{method:"POST",body:data}), id=response.jobs[0].id;
      const version=++sourceSequence;
      async function check(){if(!root.isConnected||version!==sourceSequence)return;
        try {const job=await api(`/api/jobs/${id}`);if(!root.isConnected||version!==sourceSequence)return;
          if(job.state==="error")throw Error(job.error||"Import failed.");
          if(job.state==="done"){const file=job.outputs.find(o=>o.media==="video");if(!file)throw Error("No video was downloaded.");loadSource(fileUrl(id,file.file,false),{job:id,fileName:file.file,name:job.title});$("import").disabled=false;return;}
          message(`Importing video… ${job.title||""}`);setTimeout(check,1500);
        }catch(e){fail(e.message);$("import").disabled=false;}}
      check();
    }catch(e){fail(e.message);$("import").disabled=false;}
  };
  for(const id of ["mode","order","camera-fit","game-fit","resolution","caption-position","mirror","share"]){$(id).oninput=()=>{edits++;
    settings={...settings,mode:$("mode").value,order:$("order").value,camera_fit:$("camera-fit").value,game_fit:$("game-fit").value,resolution:Number($("resolution").value),captions_at:$("caption-position").value,mirror:$("mirror").checked,share:Number($("share").value)/100};updatePlan();};}
  $("captions").onchange=controls;
  root.querySelectorAll("[data-region]").forEach(button=>button.onclick=()=>{region=button.dataset.region;controls();});
  root.querySelectorAll("[data-coord]").forEach(input=>input.onchange=()=>{edits++;settings[region][Number(input.dataset.coord)]=Number(input.value)/100;updatePlan();});
  async function autoDetect(asked=true){
    if(!source||!video.videoWidth||$("auto").dataset.busy||(!asked&&edits>0))return;
    const version=sourceSequence, before=edits, button=$("auto"), back=video.currentTime, wasPaused=video.paused;
    const run=new AbortController();autoRun=run;
    button.dataset.busy="1";button.disabled=true;button.textContent="Detecting…";fail("");message("Looking for the webcam…");
    try{
      video.pause();
      const grab=document.createElement("canvas"), w=Math.min(960,video.videoWidth);grab.width=w;grab.height=Math.round(w*video.videoHeight/video.videoWidth);
      const g=grab.getContext("2d"), data=new FormData(), d=video.duration||1;
      for(let i=0;i<8;i++){
        const at=Math.min(d-.05,d*(.08+.84*i/7));
        await new Promise((ok,bad)=>{
          const finish=error=>{clearTimeout(timer);video.removeEventListener("seeked",ready);video.removeEventListener("loadeddata",ready);run.signal.removeEventListener("abort",aborted);error?bad(error):ok();};
          const ready=()=>{if(video.readyState>=2&&!video.seeking)finish();};
          const aborted=()=>finish(new DOMException("Source changed","AbortError"));
          const timer=setTimeout(()=>finish(Error("The video did not seek. Try again.")),8000);
          video.addEventListener("seeked",ready);video.addEventListener("loadeddata",ready);run.signal.addEventListener("abort",aborted,{once:true});
          video.currentTime=Math.max(0,at);ready();
        });
        g.drawImage(video,0,0,grab.width,grab.height);
        data.append("frames",await new Promise(ok=>grab.toBlob(ok,"image/jpeg",.85)),`f${i}.jpg`);
        if(!root.isConnected||version!==sourceSequence)return;
      }
      const result=await api("/api/layouts/auto",{method:"POST",body:data,signal:run.signal});
      if(!root.isConnected||version!==sourceSequence)return;
      if(edits!==before){message("Auto detect skipped: you already changed the layout. Press Auto detect to run it again.");return;}
      if(!result.found&&!asked){message("No webcam found automatically. Draw it yourself, or keep gameplay only.");return;}
      settings={...settings,...result.settings};region=result.settings.mode==="game"?"game":"camera";
      message(result.message);
      updatePlan();
    }catch(e){if(e.name!=="AbortError"&&root.isConnected&&version===sourceSequence)fail(e.message);}
    finally{if(root.isConnected&&autoRun===run){autoRun=null;delete button.dataset.busy;button.disabled=!source;button.textContent="Auto detect";video.currentTime=back;if(!wasPaused&&edits===before)video.play().catch(()=>{});}}
  }
  $("auto").onclick=()=>autoDetect(true);
  $("full").onclick=()=>{edits++;settings[region]=[0,0,1,1];updatePlan();};
  const point=e=>{const r=canvas.getBoundingClientRect();return [Math.max(0,Math.min(1,(e.clientX-r.left)/r.width)),Math.max(0,Math.min(1,(e.clientY-r.top)/r.height))];};
  canvas.onpointerdown=e=>{if(!source||!video.videoWidth)return;edits++;drag=point(e);video.pause();canvas.setPointerCapture(e.pointerId);};
  canvas.onpointermove=e=>{if(!drag)return;const to=point(e),x=Math.min(to[0],drag[0]),y=Math.min(to[1],drag[1]);settings[region]=[x,y,Math.max(.01,Math.abs(to[0]-drag[0])),Math.max(.01,Math.abs(to[1]-drag[1]))];controls();};
  canvas.onpointerup=()=>{if(drag){drag=null;updatePlan();}};canvas.onpointercancel=()=>{drag=null;updatePlan();};
  $("play").onclick=async()=>{try{if(video.paused)await video.play();else video.pause();}catch(e){fail(e.message);}};
  $("seek").oninput=()=>{video.currentTime=Number($("seek").value);};
  $("start").onchange=()=>{const at=Number($("start").value);if(Number.isFinite(at)&&at>=0&&at<video.duration)video.currentTime=at;};
  $("save").onclick=()=>{if(!renderPlan)return fail("Load a video and check the layout first.");const name=$("preset-name").value.trim();if(!name)return fail("Give your layout a name.");presets=presets.filter(p=>p.name!==name);presets.unshift({name,settings:structuredClone(renderPlan.settings)});presets=presets.slice(0,20);if(storePresets())message(`Saved layout: ${name}`);};
  $("presets").onchange=()=>{const p=presets[Number($("presets").value)];if($("presets").value!==""&&p){edits++;settings=structuredClone(p.settings);$("preset-name").value=p.name;updatePlan();}};
  $("remove").onclick=()=>{if($("presets").value==="")return;presets.splice(Number($("presets").value),1);storePresets();};
  $("export").onclick=async()=>{
    if(!renderPlan||!source||submitting)return;submitting=true;$("export").disabled=true;fail("");
    try {const start=Number($("start").value),end=Number($("end").value);if(!Number.isFinite(start)||!Number.isFinite(end)||start<0||end<=start||end>video.duration+.05||end-start>180)throw Error("Choose a range inside the video, up to 180 seconds.");
      const data=new FormData();data.set("kind","layout");data.set("layout",JSON.stringify(renderPlan.settings));data.set("start",start);data.set("end",end);data.set("preset",$("captions").value);data.set("translate",$("translate").checked?"1":"");
      if(source.file)data.set("file",source.file);else{data.set("from_job",source.job);data.set("from_file",source.fileName);}
      message("Saving your clip…");const job=await api("/api/jobs",{method:"POST",body:data});if(root.isConnected)location.hash=`#/job/${job.id}`;
    }catch(e){if(root.isConnected)fail(e.message);}finally{submitting=false;if(root.isConnected)$("export").disabled=!renderPlan;}
  };
  function drawLayer(layer,p){
    let [sx,sy,sw,sh]=layer.source;const [x,y,w,h]=layer.destination;
    const ratio=layer.fit==="cover"?Math.max(w/sw,h/sh):Math.min(w/sw,h/sh),dw=sw*ratio,dh=sh*ratio;
    out.save();out.beginPath();out.rect(x,y,w,h);out.clip();out.fillStyle="#000";out.fillRect(x,y,w,h);
    if(layer.mirror){out.translate(2*x+w,0);out.scale(-1,1);}
    out.drawImage(video,sx,sy,sw,sh,x+(w-dw)/2,y+(h-dh)/2,dw,dh);out.restore();
  }
  function draw(){
    if(!root.isConnected){autoRun?.abort();video.pause();video.removeAttribute("src");video.load();if(objectURL)URL.revokeObjectURL(objectURL);return;}
    ctx.fillStyle="#101318";ctx.fillRect(0,0,canvas.width,canvas.height);out.fillStyle="#000";out.fillRect(0,0,preview.width,preview.height);
    if(video.readyState>=2){ctx.drawImage(video,0,0,canvas.width,canvas.height);
      for(const name of ["game","camera"]){if(name==="camera"&&settings.mode==="game")continue;const [x,y,w,h]=settings[name];ctx.save();ctx.lineWidth=name===region?4:2;ctx.strokeStyle=name==="camera"?"#7cb8ff":"#fff";ctx.setLineDash(name===region?[]:[8,6]);ctx.strokeRect(x*canvas.width,y*canvas.height,w*canvas.width,h*canvas.height);ctx.setLineDash([]);ctx.font="18px sans-serif";ctx.fillStyle="#101318";ctx.fillRect(x*canvas.width,y*canvas.height,100,26);ctx.fillStyle="#fff";ctx.fillText(name==="camera"?"Webcam":"Gameplay",x*canvas.width+6,y*canvas.height+19);ctx.restore();}
      if(renderPlan){out.save();out.scale(preview.width/renderPlan.width,preview.height/renderPlan.height);for(const layer of renderPlan.layers)drawLayer(layer,renderPlan);
        if($("captions").value!=="none"){out.font=`${renderPlan.height*.025}px sans-serif`;out.textAlign="center";out.fillStyle="#fff";out.strokeStyle="#000";out.lineWidth=5;out.strokeText("Caption zone",renderPlan.width/2,renderPlan.caption_y);out.fillText("Caption zone",renderPlan.width/2,renderPlan.caption_y);}out.restore();}
      $("seek").value=video.currentTime;$("time").textContent=fmtTime(video.currentTime);$("play").textContent=video.paused?"Play":"Pause";
    }else{ctx.fillStyle="#9da7b5";ctx.font="24px sans-serif";ctx.textAlign="center";ctx.fillText("Choose a video to start",canvas.width/2,canvas.height/2);ctx.textAlign="start";}
    requestAnimationFrame(draw);
  }
  presetList();controls();requestAnimationFrame(draw);
  try{
    const [opts,list]=await Promise.all([getOptions(),api("/api/jobs")]);if(!root.isConnected)return;
    $("captions").innerHTML=opts.presets.map(p=>`<option value="${escape(p.id)}">${escape(p.label)}</option>`).join("");$("captions").value=opts.presets.some(p=>p.id===params.preset)?params.preset:"none";controls();
    for(const job of list.filter(j=>j.state==="done"))for(const file of job.outputs||[]){if(file.media!=="video")continue;const option=document.createElement("option");option.value=`${job.id}/${file.file}`;option.dataset.job=job.id;option.dataset.file=file.file;option.textContent=`${job.title} · ${file.label}`;$("library").append(option);}
    if(params.from&&params.file){$("library").value=`${params.from}/${params.file}`;loadSource(fileUrl(params.from,params.file,false),{job:params.from,fileName:params.file,name:params.file});}
  }catch(e){if(root.isConnected)fail(e.message);}
}
