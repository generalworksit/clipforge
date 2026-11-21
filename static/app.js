"use strict";

const view = document.getElementById("view");
let pollTimer = null;
let options = null;

const KIND_LABEL = { layout: "Gaming layout", autoclip: "Auto clips", live: "Live clips", clips: "Clips", download: "Download", subtitles: "Subtitles", convert: "Convert", voice: "AI voice" };
const KIND_ROUTE = { layout: "gaming", autoclip: "monitor", live: "live", clips: "clips", download: "download", subtitles: "subtitles", convert: "convert", voice: "voice" };

const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const cap1 = (s) => s ? s[0].toUpperCase() + s.slice(1) : "";
const fmtTime = (t) => {
  t = Math.max(0, Math.round(t || 0));
  const h = Math.floor(t / 3600), m = Math.floor((t % 3600) / 60), s = t % 60;
  return h ? `${h}:${String(m).padStart(2, "0")}:${String(s).padStart(2, "0")}` : `${m}:${String(s).padStart(2, "0")}`;
};
const fmtDate = (iso) => new Date(iso).toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" });
const fmtSize = (b) => (b == null ? "" : b >= 1e9 ? `${(b / 1e9).toFixed(1)} GB` : b >= 1e6 ? `${(b / 1e6).toFixed(b >= 1e8 ? 0 : 1)} MB` : `${Math.max(1, Math.round(b / 1e3))} KB`);
const elapsed = (a, b) => {
  if (!a) return "";
  const s = Math.max(0, Math.round(((b ? new Date(b) : new Date()) - new Date(a)) / 1000));
  return s < 60 ? `${s}s` : `${Math.floor(s / 60)}m ${String(s % 60).padStart(2, "0")}s`;
};
/* "1:23", "1:02:03" or "83.5" -> seconds */
const parseTime = (v) => {
  v = String(v).trim();
  if (!v) return NaN;
  return v.split(":").reduce((acc, part) => acc * 60 + Number(part), 0);
};
const fileUrl = (id, name, dl, bust = "") => `/files/${id}/${encodeURIComponent(name)}?${dl ? "download=true&" : ""}v=${encodeURIComponent(bust)}`;

async function api(path, init) {
  const res = await fetch(path, init);
  let body = null;
  try { body = await res.json(); } catch { /* empty body */ }
  if (!res.ok) throw new Error(errText(body) || `HTTP ${res.status}`);
  return body;
}
const errText = (body) => body && body.detail ? (Array.isArray(body.detail) ? body.detail.map((d) => d.msg).join(", ") : body.detail) : "";
async function getOptions() { return options || (options = await api("/api/options")); }

function mount(id) {
  clearTimeout(pollTimer);
  view.replaceChildren(document.getElementById(id).content.cloneNode(true));
  view.focus({ preventScroll: true });
  window.scrollTo(0, 0);
}
function setNav(name) {
  document.querySelectorAll("[data-nav]").forEach((a) => a.classList.toggle("active", a.dataset.nav === name));
}
function showError(msg) {
  const err = document.getElementById("form-error");
  err.textContent = msg;
  err.hidden = !msg;
}
function flash(btn, text) {
  const old = btn.textContent;
  btn.textContent = text; btn.classList.add("ok");
  setTimeout(() => { btn.textContent = old; btn.classList.remove("ok"); }, 1400);
}

function radioGroup(el, items, current, onPick, render, cls = "") {
  el.replaceChildren(...items.map((item) => {
    const b = document.createElement("button");
    b.type = "button";
    b.setAttribute("role", "radio");
    b.dataset.value = item.id;
    if (cls) b.className = cls;
    b.innerHTML = render(item);
    b.addEventListener("click", () => onPick(item.id));
    return b;
  }));
  const sync = (value) => el.querySelectorAll("[role=radio]").forEach((b) => b.setAttribute("aria-checked", String(b.dataset.value === value)));
  sync(current);
  return sync;
}

/* ---------------------------------------------------------------- activity (top bar) */

async function pollActivity() {
  const box = document.getElementById("activity");
  try {
    const a = await api("/api/activity");
    const runs = Object.values(a).map((w) => w.running).filter(Boolean);
    const waiting = Object.values(a).reduce((n, w) => n + w.waiting, 0);
    box.innerHTML = runs.length || waiting ? `
      ${runs.map((r) => `<a class="act" href="#/job/${r.id}" title="${esc(KIND_LABEL[r.kind] || r.kind)}, ${esc(r.stage.toLowerCase())}">
        <span class="act-dot"></span>
        <span class="act-title">${esc(r.title || "Working")}</span>
        <span class="act-pct">${Math.round(r.progress * 100)}%</span></a>`).join("")}
      ${waiting ? `<span class="act-wait">+${waiting} waiting</span>` : ""}` : "";
  } catch {
    box.innerHTML = `<span class="act-off">Not connected. Is run.bat open?</span>`;
  }
  setTimeout(pollActivity, 2500);
}

/* ---------------------------------------------------------------- live caption previews */

/* static caption sample inside the small gallery phones */
function captionSample(preset) {
  if (preset === "none") return "";
  if (preset === "solo") return `<b class="hl">how</b>`;
  const words = ["This", "is", "how", "it", "looks"];
  return words.map((w, i) => `<b class="${i === 2 ? "hl" : i < 2 ? "sung" : ""}">${w}</b>`).join(" ");
}

const DEMO_WORDS = "I quit my job to do this and nobody believed me".split(" ");
const DEMO_HOOK = "He quit his job for this. Nobody believed him.";
const TICK = 360;
const HOOK_TICKS = 8;

/* Plays a looping example of the finished clip inside `el` (a .phone or .sub-frame):
   the caption style word by word and, when a voice mode is on, the AI hook.
   getState() -> { preset, position, mode }. Returns redraw(), call it after a state change. */
function playDemo(el, getState) {
  let pos = 0;
  if (el.stopDemo) el.stopDemo();  // one loop per element
  const timer = setInterval(() => {
    if (!el.isConnected) { clearInterval(timer); return; }
    pos += 1;
    draw();
  }, TICK);
  el.stopDemo = () => clearInterval(timer);
  function draw() {
    const s = getState();
    const n = s.preset === "solo" ? 1 : 3;
    const intro = s.mode === "intro" ? HOOK_TICKS : 0;
    const cycle = intro + DEMO_WORDS.length + 3;
    const p = pos % cycle;
    el.dataset.pos = s.position;
    let html = "";
    if (p < intro) {
      el.classList.add("paused");
      html = `<div class="v-intro"><p>${esc(s.hook || DEMO_HOOK)}</p></div>${voiceChip()}`;
    } else {
      el.classList.remove("paused");
      const wi = Math.min(p - intro, DEMO_WORDS.length - 1);
      const g = Math.floor(wi / n);
      const k = wi % n;
      const words = DEMO_WORDS.slice(g * n, g * n + n);
      html = s.noCaps || s.preset === "none" ? "" : `<span class="cap s-${s.preset}">${words.map((w, i) =>
        `<b class="${i === k ? `hl${g % 2 ? " alt" : ""}` : i < k ? "sung" : ""}">${esc(w)}</b>`).join(" ")}</span>`;
      if (s.mode === "over" && p < HOOK_TICKS) {
        html += `<div class="v-bubble">${esc(s.hook || DEMO_HOOK)}</div>${voiceChip()}<span class="v-chip duck">Original <span class="vol"><i></i></span></span>`;
      }
    }
    if (s.effect && s.effect !== "none" && p >= intro) html += '<span class="light-hit" aria-hidden="true"></span>';
    el.innerHTML = html;
  }
  const voiceChip = () => `<span class="v-chip"><span class="wave"><i></i><i></i><i></i><i></i></span>AI voice</span>`;
  draw();
  return () => { pos = 0; draw(); };
}

function styleControls(opts, state, onChange = () => {}, { allowNone = false } = {}) {
  const labels = Object.fromEntries(opts.presets.map((p) => [p.id, p.label]));
  const gallery = document.getElementById("gallery");
  const syncStyle = radioGroup(gallery, opts.presets.filter((p) => allowNone || p.id !== "none"), state.preset, (id) => { state.preset = id; refresh(); },
    (p) => `<span class="phone" data-pos="${state.position}">${p.id === "none" ? "" : `<span class="cap s-${p.id}">${captionSample(p.id)}</span>`}</span>${esc(p.label)}`, "style-pick");
  const syncPos = radioGroup(document.getElementById("positions"), opts.positions.map((p) => ({ id: p })), state.position,
    (id) => { state.position = id; refresh(); }, (p) => cap1(p.id));
  function refresh() {
    syncStyle(state.preset);
    syncPos(state.position);
    document.querySelectorAll("#positions button").forEach((b) => { b.disabled = state.preset === "none"; });
    gallery.querySelectorAll(".phone").forEach((ph) => { ph.dataset.pos = state.position; });
    document.getElementById("style-name").textContent = labels[state.preset];
    onChange();
  }
  refresh();
  const filter = document.createElement("input");
  filter.type = "search"; filter.placeholder = "Find a caption style…";
  filter.setAttribute("aria-label", "Filter caption styles"); filter.className = "caption-search";
  gallery.before(filter);
  filter.addEventListener("input", () => gallery.querySelectorAll(".style-pick").forEach((b) => {
    b.hidden = !b.textContent.toLowerCase().includes(filter.value.toLowerCase());
  }));
  return labels;
}

/* AI voice hook: Off / Over the start / Separate intro, plus the voice and a Listen button.
   state: { mode: "" | "over" | "intro", voice: "<kokoro voice id>" } */
const LANG_NAME = { en: "English", fr: "Français", es: "Español", it: "Italiano", pt: "Português" };
const MODE_INFO = {
  "": ["No voice", "Clip starts straight away", `<span class="off-x"></span>`],
  over: ["Over the start", "Voice on top, original sound lowered", `<span class="clip-bar"></span><span class="voice-bar"></span><span class="duck"></span>`],
  intro: ["Separate intro", "Pause the clear first frame, narrate, then play from 0:00", `<span class="intro-bar"></span><span class="clip-bar"></span>`],
};
let previewAudio = null;

function voiceControls(host, opts, state, onChange = () => {}, { required = false } = {}) {
  host.innerHTML = `
    <div class="voice-modes" role="radiogroup" aria-label="AI voice hook"></div>
    <div class="voice-pick" hidden>
      <select aria-label="Voice"></select>
      <button type="button" class="btn-s voice-play">Listen</button>
    </div>
    <p class="voice-note" hidden></p>`;
  const modes = [...(required ? [] : [""]), ...Object.keys(opts.voice_modes)].map((id) => ({ id }));
  const pick = host.querySelector(".voice-pick");
  const select = pick.querySelector("select");
  const play = pick.querySelector(".voice-play");
  const note = host.querySelector(".voice-note");
  const groups = {};
  opts.voices.forEach((v) => (groups[v.lang] ||= []).push(v));
  select.innerHTML = Object.entries(groups).map(([lang, vs]) => `<optgroup label="${esc(LANG_NAME[lang] || lang)}">${
    vs.map((v) => `<option value="${v.id}">${esc(v.label)} · ${esc(v.desc)}${lang === "en" ? ` (${v.accent})` : ""}</option>`).join("")}</optgroup>`).join("");
  select.value = state.voice;
  select.addEventListener("change", () => { state.voice = select.value; onChange(); });

  const sync = radioGroup(host.querySelector(".voice-modes"), modes, state.mode, (id) => { state.mode = id; refresh(); }, (m) => {
    const [title, desc, art] = MODE_INFO[m.id] || [opts.voice_modes[m.id], "", ""];
    return `<span class="vm-art" aria-hidden="true">${art}</span><span><b>${esc(title)}</b><br>${esc(desc)}</span>`;
  }, "vm");
  function refresh() {
    sync(state.mode);
    pick.hidden = !state.mode;
    note.hidden = !state.mode;
    note.textContent = !state.mode ? "" :
      (required ? "The narrator reads your line at the start of the video. Runs on your PC."
        : "A narrator reads a one-line teaser written for each clip (you can edit it after). Runs on your PC.")
      + (opts.voice_ready ? "" : ` The first use downloads the voice model (${opts.voice_download_mb} MB).`);
    onChange();
  }
  play.addEventListener("click", async () => {
    if (previewAudio) previewAudio.pause();
    play.disabled = true;
    play.textContent = opts.voice_ready ? "Loading…" : "Downloading voices…";
    try {
      const res = await fetch(`/api/voices/${select.value}/preview`);
      if (!res.ok) throw new Error((await res.json().catch(() => ({}))).detail || `HTTP ${res.status}`);
      opts.voice_ready = true;
      previewAudio = new Audio(URL.createObjectURL(await res.blob()));
      play.textContent = "Playing…";
      previewAudio.addEventListener("ended", () => { play.textContent = "Listen"; });
      await previewAudio.play();
    } catch (e) { alert(`Could not play the voice: ${e.message}`); play.textContent = "Listen"; }
    play.disabled = false;
    refresh();
  });
  refresh();
}
const voiceName = (opts, id) => (opts.voices.find((v) => v.id === id) || {}).label || id;

/* What to look for: video type, moment types and framing.
   state: { clipMode, cats: [ids], framing } */
const svg = (d) => `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${d}</svg>`;
const MODE_ICON = {
  viral: svg(`<path d="M13 2 4.5 13.5H11L10 22l8.5-11.5H12z"/>`),
  youtuber: svg(`<rect x="2.5" y="6" width="13" height="12" rx="3"/><path d="m15.5 10.2 6-3.2v10l-6-3.2"/>`),
  film: svg(`<rect x="3" y="9" width="18" height="11.5" rx="2"/><path d="M3 9V5.5A1.5 1.5 0 0 1 4.5 4h15A1.5 1.5 0 0 1 21 5.5V9M8 4 6 9m7-5-2 5m7-5-2 5"/>`),
  talk: svg(`<rect x="9" y="2.5" width="6" height="11.5" rx="3"/><path d="M5.5 11a6.5 6.5 0 0 0 13 0M12 17.5V21m-3.5 0h7"/>`),
  gaming: svg(`<path d="M7 7.5h10a4.5 4.5 0 0 1 4.4 3.6l.9 4.6a2.8 2.8 0 0 1-5 2.1L15.6 16H8.4l-1.7 1.8a2.8 2.8 0 0 1-5-2.1l.9-4.6A4.5 4.5 0 0 1 7 7.5z"/><path d="M8 10.5v3m-1.5-1.5h3"/><circle cx="15.5" cy="11" r=".6" fill="currentColor"/><circle cx="17.5" cy="13" r=".6" fill="currentColor"/>`),
};
const FRAMING_INFO = { face: ["Follow speaker", "keeps faces in frame"], split: ["Two people", "stacked, top + bottom"],
  crop: ["Centre crop", "fixed, middle"], blur: ["Whole picture", "blurred bars"] };
// example source length per video type, for the preview timeline (seconds)
const MODE_EXAMPLE = { viral: [720, "12 min video"], youtuber: [1200, "20 min vlog"], film: [6000, "1 h 40 film"],
  talk: [3600, "1 h podcast"], gaming: [7200, "2 h stream"] };

function catChips(host, opts, state, onChange) {
  host.innerHTML = Object.entries(opts.categories).map(([id, c]) =>
    `<button type="button" class="cat" data-cat="${id}" aria-pressed="false"><span aria-hidden="true">${c.emoji}</span>${esc(c.label)}</button>`).join("");
  const sync = () => host.querySelectorAll(".cat").forEach((b) => b.setAttribute("aria-pressed", String(state.cats.includes(b.dataset.cat))));
  host.querySelectorAll(".cat").forEach((b) => b.addEventListener("click", () => {
    const id = b.dataset.cat;
    if (!state.cats.includes(id)) state.cats = [...state.cats, id];
    else if (state.cats.length > 1) state.cats = state.cats.filter((c) => c !== id);
    sync();
    onChange();
  }));
  sync();
  return sync;
}

function pickControls(opts, state, onChange) {
  const syncCats = catChips(document.getElementById("cats"), opts, state, onChange);
  const syncFraming = radioGroup(document.getElementById("framings"), Object.keys(opts.framings).map((id) => ({ id })), state.framing,
    (id) => { state.framing = id; syncFraming(id); onChange(); },
    (f) => `<span class="fr-art fr-${f.id}" aria-hidden="true"><i></i></span><span>${FRAMING_INFO[f.id][0]}<br><small>${FRAMING_INFO[f.id][1]}</small></span>`);
  const syncMode = radioGroup(document.getElementById("clip-modes"), Object.entries(opts.clip_modes).map(([id, m]) => ({ id, ...m })), state.clipMode,
    (id) => {
      state.clipMode = id;
      state.cats = [...opts.clip_modes[id].cats];
      state.framing = opts.clip_modes[id].framing;
      syncMode(id); syncCats(); syncFraming(state.framing);
      document.getElementById("mode-name").textContent = opts.clip_modes[id].label;
      onChange();
    },
    (m) => `<span class="mode-ico">${MODE_ICON[m.id] || ""}</span><b>${esc(m.label)}</b><small>${esc(m.hint)}</small>`, "mode-card");
  document.getElementById("mode-name").textContent = opts.clip_modes[state.clipMode].label;
}

/* Link / File / Library source picker. media: which library files to offer ("video" or "any"). */
async function sourcePicker(host, { media = "video", accept = "video/*,.mkv,.ts,.mts", prefill = {}, onChange = () => {} } = {}) {
  host.replaceChildren(document.getElementById("tpl-source").content.cloneNode(true));
  const root = host.querySelector(".source");
  const urlInput = root.querySelector("input[name=url]");
  const fileInput = root.querySelector("input[name=file]");
  const drop = root.querySelector(".drop");
  const lib = root.querySelector(".lib-pick");
  fileInput.accept = accept;
  let tab = prefill.from ? "lib" : "url";
  let libChoice = prefill.from ? { job: prefill.from, file: prefill.file, title: "" } : null;

  const setTab = (t) => {
    tab = t;
    root.querySelectorAll("[role=tab]").forEach((b) => b.setAttribute("aria-selected", String(b.dataset.tab === t)));
    root.querySelectorAll("[data-pane]").forEach((p) => { p.hidden = p.dataset.pane !== t; });
    if (t === "url") urlInput.focus();
    onChange();
  };
  root.querySelectorAll("[role=tab]").forEach((b) => b.addEventListener("click", () => setTab(b.dataset.tab)));

  const showName = () => { drop.querySelector(".drop-name").textContent = fileInput.files[0]?.name || ""; onChange(); };
  fileInput.addEventListener("change", showName);
  urlInput.addEventListener("input", onChange);
  ["dragenter", "dragover"].forEach((e) => drop.addEventListener(e, (ev) => { ev.preventDefault(); drop.classList.add("over"); }));
  ["dragleave", "drop"].forEach((e) => drop.addEventListener(e, () => drop.classList.remove("over")));
  drop.addEventListener("drop", (ev) => {
    ev.preventDefault();
    if (ev.dataTransfer.files.length) { fileInput.files = ev.dataTransfer.files; showName(); }
  });
  if (prefill.url) urlInput.value = prefill.url;

  // library: finished files from every tool, newest first
  const jobs = await api("/api/jobs");
  const files = [];
  for (const j of jobs) {
    if (j.state !== "done") continue;
    for (const o of j.outputs || []) {
      if (o.media === "video" || (media === "any" && o.media === "audio")) files.push({ job: j, o });
    }
  }
  if (!files.length) {
    lib.innerHTML = `<p class="empty-note">Nothing in the library yet. Files you download or convert show up here.</p>`;
  } else {
    lib.innerHTML = `<div class="lib-scroll" role="radiogroup" aria-label="Library file">${files.slice(0, 60).map(({ job, o }) => `
      <button type="button" role="radio" data-job="${job.id}" data-file="${esc(o.file)}" data-title="${esc(job.title || "Untitled")}">
        <strong>${esc(job.title || "Untitled")}</strong>
        <span>${esc(o.label)}${o.duration ? ` · ${fmtTime(o.duration)}` : ""} · ${fmtSize(o.size)}</span>
      </button>`).join("")}</div>`;
    const sync = () => lib.querySelectorAll("[role=radio]").forEach((b) =>
      b.setAttribute("aria-checked", String(!!libChoice && b.dataset.job === libChoice.job && b.dataset.file === libChoice.file)));
    lib.querySelectorAll("[role=radio]").forEach((b) => b.addEventListener("click", () => {
      libChoice = { job: b.dataset.job, file: b.dataset.file, title: b.dataset.title }; sync(); onChange();
    }));
    sync();
    const picked = lib.querySelector("[aria-checked=true]");
    if (picked) { libChoice.title = picked.dataset.title; picked.scrollIntoView({ block: "nearest" }); }
  }
  setTab(tab);

  return {
    /* adds the source to FormData; returns an error message or "" */
    fill(data) {
      if (tab === "url") {
        const url = urlInput.value.trim();
        if (!url) return "Paste a video link first.";
        data.append("url", url);
      } else if (tab === "file") {
        if (!fileInput.files[0]) return "Choose a file first.";
        data.append("file", fileInput.files[0]);
      } else {
        if (!libChoice) return "Pick a file from the library first.";
        data.append("from_job", libChoice.job);
        data.append("from_file", libChoice.file);
      }
      return "";
    },
    /* short description for the summary line, "" when nothing is chosen yet */
    describe() {
      if (tab === "url") {
        const u = urlInput.value.trim();
        try { return u ? new URL(u).hostname.replace(/^www\./, "") + " link" : ""; } catch { return u ? "link" : ""; }
      }
      if (tab === "file") return fileInput.files[0]?.name || "";
      return libChoice ? libChoice.title || "library file" : "";
    },
    hasUpload: () => tab === "file" && !!fileInput.files[0],
  };
}

function summary(parts) {
  const el = document.getElementById("summary");
  if (el) el.innerHTML = parts.filter(Boolean).join(" · ");
}

/* POST a job with upload progress, then open its page */
function submitJob(form, data, withUpload) {
  const xhr = new XMLHttpRequest();
  const btn = form.querySelector("button[type=submit]");
  const up = document.getElementById("upload");
  btn.disabled = true;
  showError("");
  if (withUpload) {
    up.hidden = false;
    xhr.upload.onprogress = (e) => {
      if (!e.lengthComputable) return;
      const pct = Math.round((e.loaded / e.total) * 100);
      up.querySelector("i").style.width = `${pct}%`;
      up.querySelector("span").textContent = `Uploading ${pct}%`;
    };
  }
  xhr.onload = () => {
    let body = {};
    try { body = JSON.parse(xhr.responseText); } catch { /* ignore */ }
    if (xhr.status === 201) { location.hash = `#/job/${body.id}`; return; }
    btn.disabled = false; up.hidden = true;
    showError(errText(body) || `Error ${xhr.status}`);
  };
  xhr.onerror = () => { btn.disabled = false; up.hidden = true; showError("ClipForge is not reachable. Is run.bat still open?"); };
  xhr.open("POST", "/api/jobs");
  xhr.send(data);
}

function jobState(j) {
  if (j.state === "running") return j.detail ? `Working, ${j.detail}` : "Working";
  if (j.state === "queued") return "Waiting";
  if (j.state === "error") return "Failed";
  if (j.kind === "clips") return `${j.clip_count} clip${j.clip_count === 1 ? "" : "s"}`;
  const o = (j.outputs || [])[0];
  return o ? [o.label, fmtSize((j.outputs || []).reduce((n, x) => n + (x.size || 0), 0))].filter(Boolean).join(", ") : "Done";
}

/* one row in a job list (home, tool pages, library) */
function jobRow(j, { showKind = true, actions = true } = {}) {
  const main = (j.outputs || []).find((o) => o.media !== "text") || (j.outputs || [])[0];
  const thumb = j.thumbnail
    ? `<img class="thumb" src="${esc(j.thumbnail)}" alt="" loading="lazy" referrerpolicy="no-referrer" onerror="this.replaceWith(Object.assign(document.createElement('span'),{className:'thumb ph'}))">`
    : `<span class="thumb ph k-${j.kind}"></span>`;
  const pct = j.state === "running" ? Math.round((j.progress || 0) * 100) : null;
  return `<div class="jrow" data-id="${j.id}" data-state="${j.state}">
    ${actions ? `<label class="pick" title="${BUSY_STATES.has(j.state) ? "Still working" : "Select"}"><input type="checkbox" aria-label="Select ${esc(j.title || "job")}"${BUSY_STATES.has(j.state) ? " disabled" : ""}></label>` : ""}
    ${thumb}
    <a class="jrow-main" href="#/job/${j.id}">
      <strong>${esc(j.title || "Untitled")}</strong>
      <span class="jrow-meta">${showKind ? `<span class="tag k-${j.kind}">${esc(KIND_LABEL[j.kind] || j.kind)}</span>` : ""}
        <span class="st st-${j.state}">${esc(jobState(j))}</span>
        <span class="muted">${fmtDate(j.created)}${j.size != null ? `, ${fmtSize(j.size)}` : ""}</span></span>
      ${pct !== null ? `<span class="bar"><i style="width:${pct}%"></i></span>` : ""}
    </a>
    ${actions ? `<div class="jrow-actions">
      ${j.state === "done" && main ? `<a class="btn-s main" href="${fileUrl(j.id, main.file, true, j.finished)}">Save</a>` : ""}
      ${j.state === "done" && j.kind === "clips" ? `<a class="btn-s main" href="/api/jobs/${j.id}/zip">Save all</a>` : ""}
      <button class="btn-s del" ${BUSY_STATES.has(j.state) ? "disabled title='Wait for it to finish'" : ""}>Delete</button>
    </div>` : ""}
  </div>`;
}

function wireRows(box, onDeleted) {
  box.querySelectorAll(".jrow .del").forEach((btn) => btn.addEventListener("click", async () => {
    const row = btn.closest(".jrow");
    if (!confirm("Delete this job and all its files?")) return;
    try { await api(`/api/jobs/${row.dataset.id}`, { method: "DELETE" }); row.remove(); box._sel?.delete(row.dataset.id); box._bulkSync?.(); onDeleted && onDeleted(); }
    catch (e) { alert(e.message); }
  }));
  bulkBar(box, { item: ".jrow", noun: "job", onDeleted });
}

const BUSY_STATES = new Set(["running", "queued", "receiving", "active"]);

/* Select / delete selected / delete all, for any list of job items carrying data-id + a .pick checkbox.
   Selection survives re-renders of the same list (polling) because it lives on the box. */
function bulkBar(box, { item, noun = "job", onDeleted }) {
  box._sel = box._sel || new Set();
  let bar = box.previousElementSibling;
  if (!bar || !bar.classList.contains("bulkbar")) {
    bar = document.createElement("div");
    bar.className = "bulkbar";
    bar.innerHTML = `<label class="bulk-all"><input type="checkbox" aria-label="Select all"><span>Select all</span></label>
      <span class="bulk-count" aria-live="polite"></span>
      <span class="bulk-actions"><button type="button" class="btn-s bulk-clear" hidden>Clear</button>
      <button type="button" class="btn-s del bulk-del" hidden>Delete selected</button>
      <button type="button" class="btn-s del bulk-wipe">Delete all</button></span>`;
    box.before(bar);
  }
  const items = () => [...box.querySelectorAll(`${item}[data-id]`)];
  const free = () => items().filter((el) => { const c = el.querySelector(".pick input"); return c && !c.disabled; });
  const all = bar.querySelector(".bulk-all input"), count = bar.querySelector(".bulk-count");
  const del = bar.querySelector(".bulk-del"), wipe = bar.querySelector(".bulk-wipe"), clear = bar.querySelector(".bulk-clear");
  const sync = () => {
    const ids = new Set(free().map((el) => el.dataset.id));
    for (const id of box._sel) if (!ids.has(id)) box._sel.delete(id);
    items().forEach((el) => { const c = el.querySelector(".pick input"); if (c) { c.checked = box._sel.has(el.dataset.id); el.classList.toggle("picked", c.checked); } });
    const n = box._sel.size, f = free().length;
    bar.hidden = items().length < 2 && !n;
    box.classList.toggle("selecting", n > 0);
    all.checked = n > 0 && n === f; all.indeterminate = n > 0 && n < f; all.disabled = !f;
    count.textContent = n ? `${n} selected` : `${items().length} ${noun}${items().length === 1 ? "" : "s"}`;
    del.hidden = clear.hidden = !n; wipe.hidden = !!n || !f;
    del.textContent = `Delete ${n}`; wipe.textContent = f === items().length ? "Delete all" : `Delete all ${f} finished`;
  };
  box._bulkSync = sync;
  items().forEach((el) => {
    const c = el.querySelector(".pick input");
    if (c && !c._wired) { c._wired = true; c.addEventListener("change", () => { c.checked ? box._sel.add(el.dataset.id) : box._sel.delete(el.dataset.id); sync(); }); }
  });
  if (!bar._wired) {
    bar._wired = true;
    all.addEventListener("change", () => { box._sel = new Set(all.checked ? free().map((el) => el.dataset.id) : []); sync(); });
    clear.addEventListener("click", () => { box._sel.clear(); sync(); });
    const run = async (ids, btn) => {
      if (!ids.length) return;
      if (!confirm(`Delete ${ids.length} ${noun}${ids.length === 1 ? "" : "s"} and all their files? This cannot be undone.`)) return;
      const old = btn.textContent, failed = [];
      bar.querySelectorAll("button, input").forEach((x) => (x.disabled = true));
      for (let i = 0; i < ids.length; i++) {
        btn.textContent = `Deleting ${i + 1} of ${ids.length}…`;
        try { await api(`/api/jobs/${encodeURIComponent(ids[i])}`, { method: "DELETE" }); box.querySelector(`${item}[data-id="${CSS.escape(ids[i])}"]`)?.remove(); box._sel.delete(ids[i]); }
        catch (e) { failed.push(e.message); }
      }
      bar.querySelectorAll("button, input").forEach((x) => (x.disabled = false));
      btn.textContent = old; sync();
      if (failed.length) alert(`${failed.length} could not be deleted: ${failed[0]}`);
      onDeleted && onDeleted();
    };
    del.addEventListener("click", () => run([...box._sel], del));
    wipe.addEventListener("click", () => run(free().map((el) => el.dataset.id), wipe));
  }
  sync();
}

async function recentList(kind, limit = 5) {
  const box = document.getElementById("recent-list");
  if (!box) return;
  const jobs = (await api(kind ? `/api/jobs?kind=${kind}` : "/api/jobs")).slice(0, limit);
  if (!jobs.length || !box.isConnected) return;
  document.getElementById("recent").hidden = false;
  box.innerHTML = jobs.map((j) => jobRow(j, { showKind: !kind })).join("");
  wireRows(box);
}

/* ---------------------------------------------------------------- home */

async function renderHome() {
  setNav("home");
  mount("tpl-home");
  const quick = document.getElementById("quick");
  quick.addEventListener("submit", (ev) => {
    ev.preventDefault();
    const url = quick.elements.url.value.trim();
    const to = ev.submitter?.value || "clips";
    location.hash = `#/${to}?url=${encodeURIComponent(url)}`;
  });
  playDemo(view.querySelector("[data-demo=clips]"), () => ({ preset: "bold", position: "bottom", mode: "over" }));
  const second = view.querySelector("[data-demo=clips2]");
  second.dataset.pos = "middle";
  second.innerHTML = `<span class="cap s-karaoke">${captionSample("karaoke")}</span>`;
  await recentList(null, 6);
}

/* ---------------------------------------------------------------- clip maker */

const LENGTH_EXAMPLE = { short: 22, normal: 40, long: 68 };  // seconds, for the preview timeline

async function renderClips(params) {
  setNav("clips");
  mount("tpl-clips");
  const opts = await getOptions();
  const form = document.getElementById("tool-form");
  const clipsInput = form.elements.clips;
  const autoBtn = document.getElementById("auto");
  const state = { preset: opts.default_preset, position: opts.default_position, length: "normal", mode: "", voice: opts.voices[0].id,
    clipMode: "viral", cats: [...opts.clip_modes.viral.cats], framing: opts.clip_modes.viral.framing, auto: false, effect: "none", intensity: 0.5 };
  let src = null;
  let labels = {};
  let redraw = () => {};

  function update() {
    const n = Math.min(10, Math.max(1, parseInt(clipsInput.value, 10) || 3));
    const shown = state.auto ? 3 : n;
    autoBtn.setAttribute("aria-pressed", String(state.auto));
    document.getElementById("stepper").classList.toggle("off", state.auto);
    // example timeline: a typical video of this type with the picked moments on it, tagged by kind
    const [total, what] = MODE_EXAMPLE[state.clipMode] || MODE_EXAMPLE.viral;
    const len = LENGTH_EXAMPLE[state.length];
    const tl = document.getElementById("demo-tl");
    tl.innerHTML = `<div class="tl-track">${Array.from({ length: shown }, (_, i) => {
      const at = ((i + 0.5) / shown) * total - len / 2 + Math.sin(i * 2.3) * (total / shown / 5);
      const cat = opts.categories[state.cats[i % state.cats.length]];
      return `<i style="left:${(Math.max(0, at) / total) * 100}%;width:max(7px, ${(len / total) * 100}%)"><em title="${esc(cat.label)}">${cat.emoji}</em></i>`;
    }).join("")}</div>
      <div class="tl-cap"><span>Example: ${what}</span><span>${state.auto ? "Auto, best only" : `${n} ×`} ${esc(opts.lengths[state.length])}</span></div>`;
    document.getElementById("ai-row").hidden = !document.getElementById("make-thumbs").checked;
    const phone = document.getElementById("demo-phone");
    phone.classList.toggle("fit-blur", state.framing === "blur");
    phone.classList.toggle("split-view", state.framing === "split");
    phone.dataset.effect = state.effect;
    phone.style.setProperty("--hit-strength", state.intensity);
    document.getElementById("stage-note").textContent = `${state.preset === "none" ? "No captions" : `${labels[state.preset] || ""} captions`}, ${FRAMING_INFO[state.framing][0].toLowerCase()}${state.mode ? `, ${MODE_INFO[state.mode][0].toLowerCase()}` : ""}`;
    const from = src ? src.describe() : "";
    summary([
      from ? `<b>${esc(from)}</b>` : "No video yet",
      `<b>${esc(opts.clip_modes[state.clipMode].label)}</b>${state.cats.length ? ` · ${state.cats.map((c) => esc(opts.categories[c].label.toLowerCase())).join(", ")}` : ""}`,
      `<b>${state.auto ? "Auto count" : `${n} clip${n > 1 ? "s" : ""}`}</b>, ${esc(opts.lengths[state.length])}${document.getElementById("make-thumbs").checked ? (document.getElementById("thumbs-ai").checked ? ", AI thumbnails" : ", thumbnails") : ""}`,
      `${state.preset === "none" ? "No captions" : `${esc(labels[state.preset] || "")}, ${state.position}`}${document.getElementById("erase-subs").checked ? ", old subtitles removed" : ""}`,
      state.mode ? `AI hook: ${esc(voiceName(opts, state.voice))}` : "",
      state.effect !== "none" ? `${esc(opts.effects[state.effect].label)} · ${Math.round(state.intensity * 100)}%` : "",
    ]);
    redraw();
  }

  pickControls(opts, state, update);
  if (opts.llm_problem) {
    document.getElementById("cats").insertAdjacentHTML("afterend", `<p class="note muted pick-note">Ollama is off, so picking uses the sound, the picture and YouTube's most-replayed data, plus keywords. Start Ollama to also have an AI read what is said.</p>`);
  }
  labels = styleControls(opts, state, update, { allowNone: true });
  const syncLength = radioGroup(document.getElementById("lengths"), Object.entries(opts.lengths).map(([id, secs]) => ({ id, secs })),
    state.length, (id) => { state.length = id; syncLength(id); update(); }, (l) => `${cap1(l.id)} <small>${esc(l.secs)}</small>`);
  voiceControls(document.getElementById("voice"), opts, state, update);
  redraw = playDemo(document.getElementById("demo-phone"), () => state);
  src = await sourcePicker(document.getElementById("source"), { prefill: params, onChange: update });

  form.querySelectorAll("[data-step]").forEach((b) => b.addEventListener("click", () => {
    clipsInput.value = Math.min(10, Math.max(1, (parseInt(clipsInput.value, 10) || 3) + Number(b.dataset.step)));
    state.auto = false;
    update();
  }));
  clipsInput.addEventListener("input", () => { state.auto = false; update(); });
  document.getElementById("erase-subs").addEventListener("change", update);
  document.getElementById("make-thumbs").addEventListener("change", update);
  const aiBox = document.getElementById("thumbs-ai"), aiStrength = document.getElementById("thumbs-strength");
  aiStrength.innerHTML = strengthOptions(opts, "normal");
  if (!opts.redraw.installed) {
    aiBox.disabled = aiStrength.disabled = true;
    document.getElementById("ai-note").textContent = "Not installed on this PC (needs PyTorch and diffusers, see the README).";
  } else if (!opts.redraw.model_ready) {
    document.getElementById("ai-note").textContent += ` The model (${opts.redraw.download_gb} GB) downloads the first time.`;
  }
  aiBox.addEventListener("change", update);
  autoBtn.addEventListener("click", () => { state.auto = !state.auto; update(); });
  const recipes = [
    { id: "clean", label: "Clean talk", hint: "3 clips · podcast captions · follows the speaker", preset: "podcast", count: 3, length: "normal", framing: "face", videoType: "talk" },
    { id: "energy", label: "High energy", hint: "4 clips · red / green captions · short cuts", preset: "traffic", count: 4, length: "short", framing: "crop", videoType: "viral" },
    { id: "story", label: "Story cut", hint: "2 clips · editorial captions · blurred fill", preset: "editorial", count: 2, length: "long", framing: "blur", videoType: "talk" },
  ];
  const recipeBox = document.getElementById("recipes");
  recipeBox.innerHTML = recipes.map((r) => `<button type="button" class="recipe" data-recipe="${r.id}"><strong>${r.label}</strong><small>${r.hint}</small></button>`).join("");
  recipeBox.querySelectorAll("button").forEach((button) => button.addEventListener("click", () => {
    const r = recipes.find((item) => item.id === button.dataset.recipe);
    document.querySelector(`#clip-modes [data-value="${r.videoType}"]`).click();
    document.querySelector(`#gallery [data-value="${r.preset}"]`).click();
    document.querySelector(`#framings [data-value="${r.framing}"]`).click();
    document.querySelector(`#lengths [data-value="${r.length}"]`).click();
    clipsInput.value = r.count; state.auto = false;
    recipeBox.querySelectorAll("button").forEach((b) => b.setAttribute("aria-pressed", String(b === button)));
    document.getElementById("recipe-note").textContent = `${r.label} applied. Settings below are ready to adjust.`;
    update();
  }));
  // Keep optional tuning available without making it the first thing a new user faces.
  [document.getElementById("clip-modes"), document.getElementById("voice")].forEach((host) => {
    const step = host.closest(".step"), details = document.createElement("details");
    details.className = "advanced-step";
    const heading = document.createElement("summary");
    heading.textContent = host.id === "voice" ? "Optional: AI voice hook" : "Fine-tune video type, moments and framing";
    step.before(details); details.append(heading, step);
  });
  update();

  form.addEventListener("submit", (ev) => {
    ev.preventDefault();
    const data = new FormData();
    data.append("kind", "clips");
    const problem = src.fill(data);
    if (problem) { showError(problem); return; }
    data.append("clips", state.auto ? "0" : clipsInput.value);
    data.append("preset", state.preset);
    data.append("position", state.position);
    data.append("length", state.length);
    data.append("clip_mode", state.clipMode);
    data.append("cats", state.cats.join(","));
    data.append("framing", state.framing);
    data.append("effect", state.effect);
    data.append("effect_intensity", String(state.intensity));
    data.append("erase_subs", document.getElementById("erase-subs").checked ? "1" : "");
    data.append("thumbs", document.getElementById("make-thumbs").checked ? "1" : "");
    data.append("thumbs_ai", document.getElementById("thumbs-ai").checked ? "1" : "");
    data.append("thumbs_strength", document.getElementById("thumbs-strength").value);
    if (state.mode) { data.append("voice", state.voice); data.append("voice_mode", state.mode); }
    submitJob(form, data, src.hasUpload());
  });
  await recentList("clips");
}

/* ---------------------------------------------------------------- downloader */

const QUALITY_LABEL = { best: "Best available", 2160: "4K (2160p)", 1440: "1440p", 1080: "1080p", 720: "720p", 480: "480p", 360: "360p" };

async function renderDownload(params) {
  setNav("download");
  mount("tpl-download");
  const opts = await getOptions();
  const form = document.getElementById("dl-form");
  const ta = form.elements.urls;
  const quality = document.getElementById("dl-quality");
  const state = { mode: "video" };
  quality.innerHTML = opts.qualities.map((q) => `<option value="${q}">${QUALITY_LABEL[q] || q}</option>`).join("");

  const FORMATS = [
    { id: "video", ico: "MP4", title: "Video", desc: "MP4, up to 4K" },
    { id: "audio", ico: "MP3", title: "Audio only", desc: "MP3, for music and podcasts" },
  ];
  const syncMode = radioGroup(document.getElementById("dl-mode"), FORMATS, state.mode,
    (id) => { state.mode = id; syncMode(id); applyMode(); },
    (f) => `<span class="fmt-ico">${f.ico}</span><span><b>${f.title}</b><small>${f.desc}</small></span>`, "fmt");
  const links = () => ta.value.split(/[\s,]+/).map((s) => s.trim()).filter(Boolean);
  const applyMode = () => {
    document.getElementById("dl-video-opts").hidden = state.mode !== "video";
    document.getElementById("dl-go").textContent = state.mode === "video" ? "Download" : "Download MP3";
    const n = links().length;
    summary([n ? `<b>${n} link${n > 1 ? "s" : ""}</b>` : "No links yet",
      state.mode === "video" ? `MP4, ${esc(QUALITY_LABEL[quality.value] || quality.value)}${form.elements.compatible.checked ? ", H.264" : ""}` : "MP3 audio"]);
    if (!probedUrl) emptyPreview();
  };
  quality.addEventListener("change", applyMode);
  form.elements.compatible.addEventListener("change", applyMode);
  if (params.url) ta.value = params.url;

  // preview of a single link
  let probeTimer = null, probedUrl = "";
  const preview = document.getElementById("preview");
  function emptyPreview(text) {
    const n = links().length;
    preview.innerHTML = `<div class="pv-empty">
      <p class="empty-note">${esc(text || (n > 1 ? `${n} links. Each one becomes its own download.` : "Paste one link to see the title, length and best quality before downloading."))}</p>
      <div class="pv-sample">
        <span class="pv-img ${state.mode === "video" ? "" : "tall"}"></span>
        <span><b>${state.mode === "video" ? "Video title.mp4" : "Video title.mp3"}</b>
        <span>Example · ${state.mode === "video" ? "1080p · H.264 · plays everywhere" : "Audio · tagged with title"}</span></span>
      </div></div>`;
  }
  async function runProbe() {
    const list = links();
    applyMode();
    if (list.length !== 1) { probedUrl = ""; emptyPreview(); return; }
    const url = list[0];
    if (url === probedUrl || !/^https?:\/\/\S+$/.test(url)) return;
    probedUrl = url;
    preview.innerHTML = `<p class="empty-note">Looking up the link…</p>`;
    const data = new FormData();
    data.append("url", url);
    try {
      const p = await api("/api/probe", { method: "POST", body: data });
      if (probedUrl !== url) return;
      preview.innerHTML = `
        ${p.thumbnail ? `<img class="pv-thumb" src="${esc(p.thumbnail)}" alt="" referrerpolicy="no-referrer" onerror="this.remove()">` : ""}
        <div class="pv-body">
          <span class="tag">${esc(p.site || "Link")}</span>
          <strong class="pv-title">${esc(p.title)}</strong>
          <span class="muted">${[p.uploader, p.duration ? fmtTime(p.duration) : "", p.items > 1 ? `${p.items} items` : ""].filter(Boolean).map(esc).join(" · ")}</span>
          ${p.heights.length ? `<span class="muted mono">Up to ${p.heights[0]}p</span>` : ""}
        </div>`;
      // grey out qualities the link does not have
      const max = p.heights[0] || 99999;
      [...quality.options].forEach((o) => { o.disabled = o.value !== "best" && Number(o.value) > max && p.heights.length > 0; });
      if (quality.selectedOptions[0]?.disabled) quality.value = "best";
    } catch (e) {
      if (probedUrl !== url) return;
      preview.innerHTML = `<div class="pv-error"><strong>Could not read this link</strong><p>${esc(e.message)}</p></div>`;
    }
  }
  ta.addEventListener("input", () => { clearTimeout(probeTimer); applyMode(); probeTimer = setTimeout(runProbe, 600); });
  applyMode();
  runProbe();

  form.addEventListener("submit", async (ev) => {
    ev.preventDefault();
    showError("");
    const list = links();
    if (!list.length) { showError("Paste at least one link."); return; }
    const btn = document.getElementById("dl-go");
    btn.disabled = true;
    const data = new FormData();
    data.append("urls", list.join("\n"));
    data.append("mode", state.mode);
    data.append("quality", quality.value);
    data.append("compatible", form.elements.compatible.checked ? "true" : "false");
    try {
      await api("/api/downloads", { method: "POST", body: data });
      ta.value = ""; probedUrl = "";
      applyMode();
      emptyPreview("Started. Progress is below; paste more links any time.");
      refreshList();
    } catch (e) { showError(e.message); }
    btn.disabled = false;
  });

  const listBox = document.getElementById("dl-list");
  async function refreshList() {
    clearTimeout(pollTimer);
    if (!document.body.contains(listBox)) return;
    const jobs = (await api("/api/jobs?kind=download")).slice(0, 15);
    if (!listBox.isConnected) return;
    if (jobs.length) {
      listBox.innerHTML = jobs.map((j) => jobRow(j, { showKind: false })).join("");
      wireRows(listBox);
      listBox.querySelectorAll(".jrow[data-state=done]").forEach((row) => {
        const j = jobs.find((x) => x.id === row.dataset.id);
        const v = (j.outputs || []).find((o) => o.media === "video");
        if (!v) return;
        row.querySelector(".jrow-actions").insertAdjacentHTML("afterbegin", useInLinks(j.id, v.file, true));
      });
    }
    if (jobs.some((j) => j.state === "running" || j.state === "queued")) pollTimer = setTimeout(refreshList, 1500);
  }
  refreshList();
}

/* "use this file in another tool" links */
function useInLinks(jobId, file, compact) {
  const q = `?from=${jobId}&file=${encodeURIComponent(file)}`;
  return compact
    ? `<a class="btn-s" href="#/clips${q}">Clips</a><a class="btn-s hide-sm" href="#/voice${q}">AI voice</a><a class="btn-s hide-sm" href="#/subtitles${q}">Subtitles</a><a class="btn-s hide-sm" href="#/convert${q}">Convert</a>`
    : `<a href="#/gaming${q}">Gaming layout</a><a href="#/clips${q}">Make clips</a><a href="#/voice${q}">Add AI voice</a><a href="#/subtitles${q}">Subtitles</a><a href="#/convert${q}">Convert</a>${file.endsWith('.mp4')?`<a href="#/publish${q}">Prepare upload</a>`:""}`;
}

/* ---------------------------------------------------------------- AI voice */

const SHAPE_INFO = { keep: ["As it is", "same shape as your video"], face: ["Vertical, follow faces", "keeps the speaker in frame"],
  crop: ["Vertical, centre", "fixed crop"], blur: ["Vertical, blurred bars", "whole picture, 9:16"] };
const HOOK_IDEAS = ["Wait until you see how this ends.", "Nobody expected what happened next.", "This is the moment everyone is talking about.", "Watch this until the very end."];

async function renderVoice(params) {
  setNav("voice");
  mount("tpl-voice");
  const opts = await getOptions();
  const form = document.getElementById("tool-form");
  const hookBox = document.getElementById("hook");
  const state = { mode: "over", voice: opts.voices[0].id, framing: "keep", hook: "", noCaps: true, preset: "bold", position: "bottom" };
  let src = null;
  let redraw = () => {};

  function update() {
    state.hook = hookBox.value.trim();
    document.getElementById("demo-phone").classList.toggle("fit-blur", state.framing === "blur");
    document.getElementById("hook-note").textContent = state.hook ? "" :
      "Empty: it listens to the first 3 minutes and writes a line that teases the video." +
      (opts.llm_problem ? " Ollama is off right now, so it would use a generic teaser: typing your own is better." : "");
    document.getElementById("stage-note").textContent = `${voiceName(opts, state.voice)}, ${MODE_INFO[state.mode][0].toLowerCase()}`;
    const from = src ? src.describe() : "";
    summary([
      from ? `<b>${esc(from)}</b>` : "No video yet",
      state.hook ? `“${esc(state.hook.length > 48 ? state.hook.slice(0, 46) + "…" : state.hook)}”` : "<b>Line written for you</b>",
      `${esc(voiceName(opts, state.voice))}, ${MODE_INFO[state.mode][0].toLowerCase()}`,
      SHAPE_INFO[state.framing][0],
    ]);
    redraw();
  }

  document.getElementById("hook-ideas").innerHTML = HOOK_IDEAS.map((h) => `<button type="button" class="btn-s idea">${esc(h)}</button>`).join("");
  document.querySelectorAll(".idea").forEach((b) => b.addEventListener("click", () => { hookBox.value = b.textContent; update(); }));
  hookBox.addEventListener("input", update);
  voiceControls(document.getElementById("voice"), opts, state, update, { required: true });
  const syncShape = radioGroup(document.getElementById("shapes"), Object.keys(SHAPE_INFO).map((id) => ({ id })), state.framing,
    (id) => { state.framing = id; syncShape(id); update(); }, (x) => `${SHAPE_INFO[x.id][0]} <small>${SHAPE_INFO[x.id][1]}</small>`);
  redraw = playDemo(document.getElementById("demo-phone"), () => state);
  src = await sourcePicker(document.getElementById("source"), { prefill: params, onChange: update });
  update();

  form.addEventListener("submit", (ev) => {
    ev.preventDefault();
    const data = new FormData();
    data.append("kind", "voice");
    const problem = src.fill(data);
    if (problem) { showError(problem); return; }
    data.append("voice", state.voice);
    data.append("voice_mode", state.mode);
    data.append("hook", state.hook);
    data.append("framing", state.framing);
    submitJob(form, data, src.hasUpload());
  });
  await recentList("voice");
}

/* results of the AI voice tool: the video, plus a card to change the line, voice or style */
async function renderVoiceResults(job) {
  renderFileResults(job);
  const opts = await getOptions();
  const o = job.options;
  document.getElementById("results-title").textContent = "Your video";
  const adjust = document.getElementById("adjust");
  adjust.hidden = false;
  document.getElementById("restyle").hidden = true;
  document.getElementById("repick").hidden = true;
  adjust.querySelector("#voice-form")?.remove();
  adjust.insertAdjacentHTML("beforeend", `<form class="adjust-card wide" id="voice-form">
      <div class="adjust-head"><span class="label">Change the voice</span>
        <span class="hint-s">Edit the line, pick another voice or style, then apply. Only the voice is redone.</span></div>
      <textarea rows="2" maxlength="220" id="voice-line" aria-label="Narrator line">${esc(job.hook || o.hook || "")}</textarea>
      <div id="voice-edit"></div>
      <button class="primary small" type="submit">Apply</button>
    </form>`);
  const vstate = { mode: o.voice_mode, voice: o.voice };
  voiceControls(document.getElementById("voice-edit"), opts, vstate, () => {}, { required: true });
  document.getElementById("voice-form").addEventListener("submit", async (ev) => {
    ev.preventDefault();
    const btn = ev.target.querySelector("button[type=submit]");
    btn.disabled = true;
    const data = new FormData();
    data.append("voice", vstate.voice);
    data.append("voice_mode", vstate.mode);
    data.append("hooks", JSON.stringify({ 1: document.getElementById("voice-line").value }));
    try {
      await api(`/api/jobs/${job.id}/voice`, { method: "POST", body: data });
      route();
    } catch (e) { alert(e.message); }
    btn.disabled = false;
  });
}

/* ---------------------------------------------------------------- subtitles */

const SUB_LINES = [
  ["00:00:01,200", "00:00:03,050", "I had never seen anything like it.", "Je n'avais jamais rien vu de pareil."],
  ["00:00:03,050", "00:00:05,400", "So we kept walking toward the light.", "Alors on a continué vers la lumière."],
  ["00:00:05,400", "00:00:07,900", "And that is when everything changed.", "Et c'est là que tout a changé."],
];

async function renderSubtitles(params) {
  setNav("subtitles");
  mount("tpl-subtitles");
  const opts = await getOptions();
  const form = document.getElementById("tool-form");
  const burn = document.getElementById("burn");
  const translate = document.getElementById("translate");
  const state = { preset: opts.default_preset, position: opts.default_position, mode: "", file: "srt" };
  let labels = {};
  let src = null;
  const demo = document.getElementById("sub-demo");
  demo.innerHTML = `<div class="sub-frame" id="sub-frame"></div>
    <div class="files">
      <div class="file-tabs" role="tablist" aria-label="Example file">
        ${["srt", "vtt", "txt"].map((f) => `<button type="button" role="tab" data-file="${f}">.${f}</button>`).join("")}
      </div>
      <pre class="file-body" id="file-body"></pre>
    </div>`;
  function fileText() {
    const lines = SUB_LINES.map(([a, b, en, fr]) => [a, b, translate.checked ? en : fr]);
    if (state.file === "txt") return esc(lines.map((l) => l[2]).join(" "));
    if (state.file === "vtt") return `WEBVTT\n\n` + lines.map(([a, b, t]) => `<span class="tc">${a.replace(",", ".")} --&gt; ${b.replace(",", ".")}</span>\n${esc(t)}`).join("\n\n");
    return lines.map(([a, b, t], i) => `<span class="n">${i + 1}</span>\n<span class="tc">${a} --&gt; ${b}</span>\n${esc(t)}`).join("\n\n");
  }
  function update() {
    document.getElementById("style-step").hidden = !burn.checked;
    document.querySelectorAll(".file-tabs [role=tab]").forEach((b) => b.setAttribute("aria-selected", String(b.dataset.file === state.file)));
    document.getElementById("file-body").innerHTML = fileText();
    document.getElementById("stage-note").textContent = translate.checked ? "Example: French video, translated" : "Example: French video";
    // swap in a fresh frame: that stops the previous demo loop (it ends once its element is detached)
    const old = document.getElementById("sub-frame");
    const frame = old.cloneNode(false);
    old.replaceWith(frame);
    if (burn.checked) playDemo(frame, () => state);
    else frame.innerHTML = `<div class="soft"><span>${esc(translate.checked ? SUB_LINES[0][2] : SUB_LINES[0][3])}</span></div>`;
    const from = src ? src.describe() : "";
    summary([from ? `<b>${esc(from)}</b>` : "No file yet", "SRT, VTT, TXT",
      translate.checked ? "translated to English" : "", burn.checked ? `burned in: ${esc(labels[state.preset] || "")}, ${state.position}` : ""]);
  }
  labels = styleControls(opts, state, () => { if (src) update(); });
  document.querySelectorAll(".file-tabs [role=tab]").forEach((b) => b.addEventListener("click", () => { state.file = b.dataset.file; update(); }));
  burn.addEventListener("change", update);
  translate.addEventListener("change", update);
  src = await sourcePicker(document.getElementById("source"), {
    media: "any", accept: "video/*,audio/*,.mkv,.ts,.mts,.opus,.flac", prefill: params, onChange: () => src && update() });
  update();

  form.addEventListener("submit", (ev) => {
    ev.preventDefault();
    const data = new FormData();
    data.append("kind", "subtitles");
    const problem = src.fill(data);
    if (problem) { showError(problem); return; }
    data.append("translate", translate.checked ? "true" : "");
    data.append("burn", burn.checked ? "true" : "");
    data.append("erase_subs", burn.checked && document.getElementById("erase-subs").checked ? "1" : "");
    data.append("preset", state.preset);
    data.append("position", state.position);
    submitJob(form, data, src.hasUpload());
  });
  await recentList("subtitles");
}

/* ---------------------------------------------------------------- converter */

const OP_HELP = {
  mp3: ["Extract audio", "Keeps only the sound as a high-quality MP3 (about 190 kb/s). Works on audio files too."],
  compress: ["Compress to a size", "Re-encodes in two passes so the file lands just under the size you pick. Lowers the resolution when the size is too small for full quality. Discord free: 10 MB. WhatsApp: 16 MB per video in chat, 100 MB as a document."],
  vertical: ["Make vertical", "Turns the whole video into 1080×1920. Blur keeps the full frame over a blurred copy of itself; crop cuts the sides off the center."],
  trim: ["Trim", "Cuts the part between the two times, frame accurate."],
  gif: ["GIF", "Up to 30 seconds, 15 fps, with a colour palette made from the clip itself."],
};
const WAVE = [5, 9, 14, 8, 18, 12, 6, 15, 20, 10, 7, 13, 17, 9, 5, 11, 16, 8, 12, 6];
const OP_ICON = {
  mp3: `<span class="wave">${WAVE.slice(0, 8).map((h) => `<i style="height:${h}px"></i>`).join("")}</span>`,
  compress: `<span class="big-f">250MB</span><span class="muted">→</span><span class="small-f">10MB</span>`,
  vertical: `<i class="w"></i><span class="muted">→</span><i class="t"></i>`,
  trim: `<i class="cut"></i>`,
  gif: `<span class="gif">GIF</span>`,
};

async function renderConvert(params) {
  setNav("convert");
  mount("tpl-convert");
  const opts = await getOptions();
  const form = document.getElementById("tool-form");
  const state = { op: params.op && OP_HELP[params.op] ? params.op : "mp3", mode: "blur" };
  let src = null;

  const syncOp = radioGroup(document.getElementById("ops"), opts.convert_ops, state.op,
    (id) => { state.op = id; apply(); }, (o) => `<span class="op-ico" aria-hidden="true">${OP_ICON[o.id] || ""}</span>${esc(o.label)}`, "op");
  const syncMode = radioGroup(document.getElementById("v-mode"), [{ id: "blur", label: "Blurred background" }, { id: "crop", label: "Cropping the sides" }],
    state.mode, (id) => { state.mode = id; syncMode(id); apply(); }, (m) => m.label);
  document.getElementById("size-presets").innerHTML = [8, 10, 16, 25, 50, 100].map((mb) => `<button type="button" class="btn-s" data-mb="${mb}">${mb} MB</button>`).join("");
  document.querySelectorAll("[data-mb]").forEach((b) => b.addEventListener("click", () => { form.elements.target_mb.value = b.dataset.mb; apply(); }));
  ["target_mb", "start", "end", "width"].forEach((n) => form.elements[n].addEventListener("input", apply));

  function demo() {
    const wide = `<span class="d-frame wide"></span><span class="arrow">→</span>`;
    if (state.op === "mp3") return `${wide}<span class="d-wave">${WAVE.map((h) => `<i style="height:${h * 3}px"></i>`).join("")}</span>`;
    if (state.op === "vertical") return `${wide}<span class="d-frame tall ${state.mode}">${state.mode === "blur" ? "<i></i>" : ""}</span>`;
    if (state.op === "compress") {
      const mb = Math.max(1, Number(form.elements.target_mb.value) || 10);
      const before = Math.max(250, mb * 4);
      const h = (v) => Math.max(8, Math.round((v / before) * 130));
      return `<span class="d-size"><span class="blk" style="height:${h(before)}px"></span>${before} MB<span>example</span></span>
        <span class="arrow">→</span><span class="d-size"><span class="blk k" style="height:${h(mb)}px"></span>${mb} MB<span>just under</span></span>`;
    }
    const s = parseTime(form.elements.start.value), e = parseTime(form.elements.end.value);
    const ok = !Number.isNaN(s) && !Number.isNaN(e) && e > s;
    const span = Math.max(60, (ok ? e : 10) * 1.3);
    const sel = ok ? `left:${(s / span) * 100}%;width:${((e - s) / span) * 100}%` : "left:0;width:15%";
    const extra = state.op === "gif" ? ` · GIF ${esc(form.elements.width.value)} px wide, loops` : "";
    return `<div class="d-trim"><div class="track"><span class="sel" style="${sel}"></span></div>
      <div class="cap-row"><span>${ok ? fmtTime(s) : "?"}</span><span>${ok ? fmtTime(e) : "?"}</span></div>
      <p class="d-note">${ok ? `${+(e - s).toFixed(1)} s kept${extra}` : "The start must be before the end"}</p></div>`;
  }

  function apply() {
    syncOp(state.op);
    form.querySelectorAll(".op-opts, [data-op]").forEach((el) => { el.hidden = !el.dataset.op.split(" ").includes(state.op); });
    const [title, text] = OP_HELP[state.op];
    document.getElementById("op-demo").innerHTML = demo();
    document.getElementById("op-help").innerHTML = `<b>${esc(title)}</b><p>${esc(text)}</p>`;
    form.querySelector("button[type=submit]").textContent = { mp3: "Extract MP3", compress: "Compress", vertical: "Make vertical", trim: "Trim", gif: "Make GIF" }[state.op];
    const from = src ? src.describe() : "";
    const detail = { compress: `to ${form.elements.target_mb.value} MB`, vertical: state.mode === "blur" ? "blurred background" : "cropped",
      trim: `${form.elements.start.value} to ${form.elements.end.value}`, gif: `${form.elements.start.value} to ${form.elements.end.value}, ${form.elements.width.value} px` }[state.op];
    summary([from ? `<b>${esc(from)}</b>` : "No file yet", `<b>${esc(title)}</b>${detail ? ` ${esc(detail)}` : ""}`]);
  }
  src = await sourcePicker(document.getElementById("source"), {
    media: "any", accept: "video/*,audio/*,.mkv,.ts,.mts,.opus,.flac", prefill: params, onChange: () => src && apply() });
  apply();

  form.addEventListener("submit", (ev) => {
    ev.preventDefault();
    const data = new FormData();
    data.append("kind", "convert");
    const problem = src.fill(data);
    if (problem) { showError(problem); return; }
    data.append("op", state.op);
    if (state.op === "compress") data.append("target_mb", form.elements.target_mb.value);
    if (state.op === "vertical") data.append("mode", state.mode);
    if (state.op === "trim" || state.op === "gif") {
      const start = parseTime(form.elements.start.value), end = parseTime(form.elements.end.value);
      if (Number.isNaN(start) || Number.isNaN(end)) { showError("Write times as m:ss (1:30) or seconds (90)."); return; }
      if (end <= start) { showError("The end time must be after the start time."); return; }
      if (state.op === "gif" && end - start > opts.gif_max_seconds) { showError(`GIFs are limited to ${opts.gif_max_seconds} seconds.`); return; }
      data.append("start", start);
      data.append("end", end);
      if (state.op === "gif") data.append("width", form.elements.width.value);
    }
    submitJob(form, data, src.hasUpload());
  });
  await recentList("convert");
}

/* ---------------------------------------------------------------- job page */

async function renderJob(id) {
  setNav("");
  mount("tpl-job");
  const opts = await getOptions();
  const labels = Object.fromEntries(opts.presets.map((p) => [p.id, p.label]));
  document.getElementById("job-log").href = `/files/${id}/log.txt`;
  let shownVersion = null;

  const retry = document.getElementById("job-retry");
  retry.addEventListener("click", async () => {
    retry.disabled = true;
    try { await api(`/api/jobs/${id}/retry`, { method: "POST" }); document.getElementById("job-error").hidden = true; tick(); }
    catch (e) { alert(e.message); }
    retry.disabled = false;
  });

  const repick = document.getElementById("repick");
  const pickState = { cats: [] };
  repick.addEventListener("submit", async (ev) => {
    ev.preventDefault();
    const btn = repick.querySelector("button[type=submit]");
    btn.disabled = true;
    const data = new FormData(repick);
    data.append("cats", pickState.cats.join(","));
    try {
      await api(`/api/jobs/${id}/repick`, { method: "POST", body: data });
      document.getElementById("results").hidden = true;
      shownVersion = null;
      tick();
    } catch (e) { alert(e.message); }
    btn.disabled = false;
  });

  const restyle = document.getElementById("restyle");
  restyle.addEventListener("submit", async (ev) => {
    ev.preventDefault();
    const btn = restyle.querySelector("button");
    btn.disabled = true;
    try {
      await api(`/api/jobs/${id}/restyle`, { method: "POST", body: new FormData(restyle) });
      document.getElementById("results").hidden = true;
      shownVersion = null;
      tick();
    } catch (e) { alert(e.message); }
    btn.disabled = false;
  });

  async function tick() {
    clearTimeout(pollTimer);
    if (!document.getElementById("stages")) return;
    let job;
    try { job = await api(`/api/jobs/${id}`); }
    catch (e) { document.getElementById("job-title").textContent = e.message; return; }

    const kind = job.kind || "clips";
    setNav(KIND_ROUTE[kind]);
    view.style.setProperty("--k", `var(--k-${kind})`);
    const crumb = document.getElementById("crumb");
    crumb.href = `#/${KIND_ROUTE[kind]}`;
    crumb.textContent = kind === "clips" ? "Clip maker" : kind === "download" ? "Downloader" : KIND_LABEL[kind] || "Library";
    document.getElementById("job-title").textContent = job.title || "Untitled";

    const stateText = { queued: job.queue_position ? `Waiting, ${job.queue_position} in line` : "Waiting", running: "Working", done: "Done", error: "Failed" }[job.state];
    const o = job.options, src = job.source;
    const chips = [[stateText, `state-${job.state}`]];
    if (kind === "clips") {
      const cm = opts.clip_modes[o.mode || "viral"];
      if (cm) chips.push([`${cm.label}${o.cats ? ` · ${o.cats.map((c) => ((opts.categories[c] || {}).label || "").toLowerCase()).filter(Boolean).join(", ")}` : ""}`]);
      chips.push([o.clips ? `${o.clips} clip${o.clips > 1 ? "s" : ""}` : "Auto count"], [o.preset === "none" ? "No captions" : `${labels[o.preset] || o.preset} captions, ${o.position}`]);
      if (o.framing === "blur") chips.push(["Whole picture, blurred bars"]);
      if (o.erase_subs) chips.push(["Old subtitles removed"]);
      if (job.picker) chips.push([job.picker.startsWith("heuristic") ? "Picked from sound, picture & replays" : `Picked by ${job.picker.replace(/^ollama:/, "")}`]);
      if (o.length && o.length !== "normal") chips.push([`${cap1(o.length)} clips`]);
      const v = o.voice && opts.voices.find((x) => x.id === o.voice);
      if (v) chips.push([`AI voice: ${v.label}, ${(MODE_INFO[o.voice_mode] || [""])[0].toLowerCase()}`]);
    }
    if (kind === "download") chips.push([o.mode === "audio" ? "MP3 audio" : `MP4, ${QUALITY_LABEL[o.quality] || o.quality}`]);
    if (kind === "live") chips.push([src.capture?.reason || "Live highlight"], ["Original aspect · no captions"], ["Review in CapCut"]);
    if (kind === "subtitles") {
      if (o.translate) chips.push(["Translated to English"]);
      if (o.burn) chips.push([`${labels[o.preset] || o.preset} captions, ${o.position}`]);
    }
    if (kind === "convert") chips.push([(opts.convert_ops.find((x) => x.id === o.op) || {}).label || o.op]);
    if (kind === "voice") {
      chips.push([`${voiceName(opts, o.voice)}, ${(MODE_INFO[o.voice_mode] || [""])[0].toLowerCase()}`], [(SHAPE_INFO[o.framing] || SHAPE_INFO.keep)[0]]);
      if (!o.hook) chips.push(["Line written from the video"]);
    }
    if (src.duration) chips.push([`Source ${fmtTime(src.duration)}${src.width ? `, ${src.width}×${src.height}` : ""}`]);
    if (job.media && job.media.site) chips.push([job.media.site]);
    if (job.language) chips.push([`Language: ${job.language}`]);
    document.getElementById("job-chips").innerHTML = chips.map(([t, c]) => `<span class="chip ${c || ""}">${esc(t)}</span>`).join("");

    const stages = Object.values(job.stages);
    const rail = document.getElementById("stages");
    rail.style.setProperty("--n", stages.length);
    rail.innerHTML = stages.map((s) => {
      const time = s.state === "running" ? elapsed(s.started) : s.state === "done" ? elapsed(s.started, s.ended) : "";
      const detail = s.state === "pending" ? "" : [s.detail, time].filter(Boolean).join(", ");
      return `<li class="${s.state}" style="--p:${Math.round(s.progress * 100)}%">
        <span class="dot"></span><strong>${esc(s.label)}</strong>
        <span class="detail" title="${esc(detail)}">${esc(detail || (s.state === "running" ? "starting" : ""))}</span>
      </li>`;
    }).join("");

    const errBox = document.getElementById("job-error");
    retry.hidden = job.state !== "error";
    if (job.state === "error") {
      const where = job.stage ? job.stages[job.stage].label : "Setup";
      errBox.innerHTML = `<strong>${esc(where)} failed</strong>${esc(job.error)}\n\nThe log has the full output. Retry resumes from this step.`;
      errBox.hidden = false;
    } else {
      errBox.hidden = true;
    }

    if (job.state === "done" && shownVersion !== job.finished) {
      shownVersion = job.finished;
      const restyleOk = kind === "clips" || (kind === "subtitles" && job.stages.burn);
      document.getElementById("adjust").hidden = !restyleOk;
      if (restyleOk) {
        document.getElementById("restyle-preset").innerHTML = opts.presets.filter((p) => kind === "clips" || p.id !== "none").map((p) => `<option value="${p.id}" ${p.id === o.preset ? "selected" : ""}>${esc(p.label)}</option>`).join("");
        document.getElementById("restyle-position").innerHTML = opts.positions.map((p) => `<option value="${p}" ${p === o.position ? "selected" : ""}>${cap1(p)}</option>`).join("");
        document.getElementById("restyle-erase").checked = !!o.erase_subs;
        const pick = () => ({ preset: restyle.elements.preset.value, position: restyle.elements.position.value, mode: "" });
        const redraw = playDemo(document.getElementById("restyle-demo"), pick);
        restyle.onchange = redraw;
      }
      repick.hidden = kind !== "clips";
      if (kind === "clips") {
        const mode = opts.clip_modes[o.mode] ? o.mode : "viral";
        const sel = (name, items, current) => {
          repick.querySelector(`[name=${name}]`).innerHTML = items.map(([v, t]) => `<option value="${v}" ${String(v) === String(current) ? "selected" : ""}>${esc(t)}</option>`).join("");
        };
        sel("clip_mode", Object.entries(opts.clip_modes).map(([k, m]) => [k, m.label]), mode);
        sel("clips", [[0, "Auto, best only"], ...Array.from({ length: 10 }, (_, i) => [i + 1, `${i + 1} clip${i ? "s" : ""}`])], o.clips ?? 3);
        sel("length", Object.entries(opts.lengths).map(([k, v]) => [k, `${cap1(k)}, ${v}`]), o.length || "normal");
        sel("framing", Object.entries(FRAMING_INFO).map(([k, [t, d]]) => [k, `${t}, ${d}`]), o.framing || "crop");
        pickState.cats = [...(o.cats || opts.clip_modes[mode].cats)];
        const syncCats = catChips(document.getElementById("repick-cats"), opts, pickState, () => {});
        repick.elements.clip_mode.onchange = () => {
          const m = opts.clip_modes[repick.elements.clip_mode.value];
          pickState.cats = [...m.cats];
          repick.querySelector("[name=framing]").value = m.framing;
          syncCats();
        };
      }
      const files = (job.outputs || []).length + (job.clips || []).length;
      const zip = document.getElementById("zip");
      zip.href = `/api/jobs/${job.id}/zip?v=${encodeURIComponent(job.finished)}`;
      zip.hidden = files < 2;
      ({ layout: renderFileResults, autoclip: renderFileResults, live: renderFileResults, clips: renderClipResults, download: renderFileResults, convert: renderFileResults, subtitles: renderSubtitleResults, voice: renderVoiceResults })[kind](job);
      document.getElementById("results").hidden = false;
      if (["clips","subtitles","layout","autoclip"].includes(kind) && o.preset !== "none") {
        const panel=document.createElement("details");panel.className="section";
        panel.innerHTML=`<summary>Adjust caption timing</summary><form id="caption-timing-form"><label class="field">Timing adjustment (milliseconds)<input name="offset_ms" type="number" min="-5000" max="5000" step="50" value="${Number(o.caption_offset_ms)||0}"></label><p class="hint-s">Negative = earlier. Positive = later. Applies to this job's captions.</p><label class="check"><input type="checkbox" name="rebuild" value="1">Re-transcribe audio to rebuild word timing</label><button class="primary small">Apply timing</button><p role="alert" class="error-box" hidden></p></form>`;
        document.getElementById("result-body").append(panel);
        panel.querySelector("form").onsubmit=async e=>{e.preventDefault();const button=panel.querySelector("button"),error=panel.querySelector("[role=alert]");button.disabled=true;error.hidden=true;
          try{await api(`/api/jobs/${id}/caption-timing`,{method:"POST",body:new FormData(e.currentTarget)});if(panel.isConnected)renderJob(id);}
          catch(err){error.textContent=err.message;error.hidden=false;button.disabled=false;}};
      }
    }
    if (job.state === "queued" || job.state === "running") pollTimer = setTimeout(tick, 1500);
  }
  tick();
}

function copyButtons(scope) {
  scope.querySelectorAll(".copy").forEach((b) => b.addEventListener("click", async () => {
    try {
      const text = b.dataset.copy ?? document.getElementById(b.dataset.copyFrom).textContent;
      await navigator.clipboard.writeText(text);
      flash(b, "Copied");
    } catch { alert("The browser blocked clipboard access. Select the text and copy it by hand."); }
  }));
}

function metric(label, value, max, measured) {
  const pct = Math.round((value / max) * 100);
  return `<div class="metric ${measured ? "measured" : ""}">
    <div class="m-row"><span>${label}</span><b>${Math.round(value)}${max === 25 ? "/25" : ""}</b></div>
    <div class="track"><i style="width:${pct}%"></i></div></div>`;
}

function mediaTag(job, o, attrs = "") {
  const url = fileUrl(job.id, o.file, false, job.finished);
  if (o.media === "text") return `<span class="muted">${esc(o.file)}</span>`;
  if (o.media === "audio") return `<audio src="${url}" controls preload="metadata" ${attrs}></audio>`;
  if (o.media === "image") return `<img class="out-img" src="${url}" alt="${esc(o.label)}">`;
  const vertical = o.height > o.width;
  return `<video class="${vertical ? "tall" : "wide"}" src="${url}#t=0.1" controls preload="metadata" playsinline ${attrs}></video>`;
}

function renderFileResults(job) {
  const outs = job.outputs || [];
  document.getElementById("results-title").textContent = outs.length > 1 ? `${outs.length} files` : "Your file";
  const body = document.getElementById("result-body");
  body.innerHTML = outs.map((o) => {
    const before = o.source_size ? `${fmtSize(o.source_size)} → ${fmtSize(o.size)}` : fmtSize(o.size);
    const facts = [o.duration ? fmtTime(o.duration) : "", o.width ? `${o.width}×${o.height}` : "", before].filter(Boolean).join(" · ");
    return `<article class="out ${o.height > o.width ? "out-tall" : ""}">
      <div class="out-media">${mediaTag(job, o)}</div>
      <div class="out-body">
        <h3>${esc(o.label)}</h3>
        <p class="muted">${esc(o.download_name)}<br><span class="mono">${esc(facts)}</span></p>
        <div class="downloads">
          <a class="main" href="${fileUrl(job.id, o.file, true, job.finished)}">Save ${esc(o.file.split(".").pop().toUpperCase())}</a>
        </div>
        ${o.media === "video" ? `<span class="label">Use it in</span><div class="downloads">${useInLinks(job.id, o.file)}</div>` : ""}
        ${o.media === "audio" ? `<span class="label">Use it in</span><div class="downloads"><a href="#/subtitles?from=${job.id}&file=${encodeURIComponent(o.file)}">Subtitles</a><a href="#/convert?from=${job.id}&file=${encodeURIComponent(o.file)}&op=trim">Trim</a></div>` : ""}
        ${job.media && job.media.url ? `<a class="src-link" href="${esc(job.media.url)}" target="_blank" rel="noopener noreferrer">Original post</a>` : ""}
      </div>
    </article>`;
  }).join("");
}

async function renderSubtitleResults(job) {
  const outs = job.outputs || [];
  document.getElementById("results-title").textContent = "Subtitles";
  const burned = outs.find((o) => o.media === "video");
  const vtt = outs.find((o) => o.file.endsWith(".vtt"));
  const txt = outs.find((o) => o.file.endsWith(".txt"));
  const src = job.source;
  let player = "";
  if (burned) player = mediaTag(job, burned);
  else if (src.stored_as && src.has_video !== false) {
    // play the source with the subtitles as a track, so they can be checked without burning
    player = `<video class="${src.height > src.width ? "tall" : "wide"}" src="${fileUrl(job.id, src.stored_as, false, job.finished)}" controls preload="metadata" playsinline crossorigin="anonymous">
      ${vtt ? `<track kind="subtitles" src="${fileUrl(job.id, vtt.file, false, job.finished)}" default label="Subtitles">` : ""}</video>`;
  }
  const body = document.getElementById("result-body");
  const tall = burned ? burned.height > burned.width : src.height > src.width;
  body.innerHTML = `<article class="out ${tall ? "out-tall" : ""}">
    ${player ? `<div class="out-media">${player}</div>` : ""}
    <div class="out-body">
      <span class="label">Files</span>
      <div class="downloads">${outs.map((o, i) => `<a class="${i === 0 ? "main" : ""}" href="${fileUrl(job.id, o.file, true, job.finished)}">${esc(o.label)}</a>`).join("")}</div>
      ${txt ? `<div class="post">
        <div class="post-head"><span class="label">Transcript</span><button class="copy" data-copy-from="transcript-text">Copy</button></div>
        <div class="transcript" id="transcript-text">Loading…</div>
      </div>` : ""}
    </div>
  </article>`;
  copyButtons(body);
  if (txt) {
    const res = await fetch(fileUrl(job.id, txt.file, false, job.finished));
    const box = document.getElementById("transcript-text");
    if (box) box.textContent = res.ok ? await res.text() : "Could not load the transcript.";
  }
}

async function renderClipResults(job) {
  const f = (name, dl) => fileUrl(job.id, name, dl, job.finished);
  const dur = job.source.duration || Math.max(...job.clips.map((c) => c.end));
  const opts = await getOptions();

  document.getElementById("timeline").hidden = false;
  document.getElementById("tl-end").textContent = fmtTime(dur);
  document.getElementById("tl-bar").innerHTML = job.clips.map((c) => `
    <a class="tl-seg" href="#clip-${c.index}" data-clip="${c.index}" title="${c.tag ? `${esc(c.tag.label)}: ` : ""}${esc(c.title)} (${fmtTime(c.start)}–${fmtTime(c.end)})"
       style="left:${(c.start / dur) * 100}%;width:${((c.end - c.start) / dur) * 100}%">${c.index}</a>`).join("");
  document.querySelectorAll(".tl-seg").forEach((a) => a.addEventListener("click", (ev) => {
    ev.preventDefault();
    document.getElementById(`clip-${a.dataset.clip}`).scrollIntoView({ behavior: "smooth" });
  }));
  document.getElementById("results-title").textContent = `${job.clips.length} clip${job.clips.length === 1 ? "" : "s"}`;

  const o = job.options;
  const adjust = document.getElementById("adjust");
  adjust.querySelector("#voice-form")?.remove();
  adjust.insertAdjacentHTML("beforeend", `<form class="adjust-card" id="voice-form">
      <div class="adjust-head"><span class="label">AI voice hook</span>
        <span class="hint-s">Edit the narrator line in each clip, then apply. Only the intro is redone.</span></div>
      <div id="voice-edit"></div>
      <button class="primary small" type="submit">${o.voice ? "Apply voice changes" : "Add voice hook"}</button>
    </form>`);
  adjust.querySelector("#thumbs-form")?.remove();
  const hasThumbs = job.clips.some((c) => c.files.thumb);
  adjust.insertAdjacentHTML("beforeend", `<form class="adjust-card" id="thumbs-form">
      <div class="adjust-head"><span class="label">Thumbnails</span>
        <span class="hint-s">A cover image per clip: the best frame, big words, an arrow.${hasThumbs ? " Remaking keeps your words." : ""}</span></div>
      <div class="ai-row">
        <label class="check"><input type="checkbox" name="ai" value="1" ${o.thumbs_ai ? "checked" : ""} ${opts.redraw.installed ? "" : "disabled"}>
          <span>AI redraw the picture <small>${opts.redraw.installed ? "about 15 s a clip on your graphics card" : "not installed on this PC"}</small></span></label>
        <select name="strength" aria-label="How much the AI changes the picture" ${opts.redraw.installed ? "" : "disabled"}>${strengthOptions(opts, o.thumbs_strength || "normal")}</select>
      </div>
      <button class="primary small" type="submit">${hasThumbs ? "Remake all thumbnails" : "Make thumbnails"}</button>
    </form>`);
  document.getElementById("thumbs-form").addEventListener("submit", async (ev) => {
    ev.preventDefault();
    const btn = ev.target.querySelector("button");
    btn.disabled = true;
    try { await api(`/api/jobs/${job.id}/thumbs`, { method: "POST", body: new FormData(ev.target) }); renderJob(job.id); }
    catch (e) { alert(e.message); btn.disabled = false; }
  });

  const body = document.getElementById("result-body");
  body.innerHTML = job.clips.map((c) => {
    const r = c.rubric || {}, sig = c.signals || {};
    const metrics = [
      ...(r.hook !== undefined ? [metric("Hook", r.hook, 25), metric("Standalone", r.standalone, 25), metric("Payoff", r.payoff, 25), metric("Emotion", r.emotion, 25)] : []),
      ...(sig.replay !== undefined ? [metric("Rewatched on YouTube", sig.replay, 100, true)] : []),
      ...(sig.energy !== undefined ? [metric("Sound energy", sig.energy, 100, true)] : []),
      ...(sig.jump !== undefined ? [metric("Sudden sounds", sig.jump, 100, true)] : []),
      ...(sig.motion !== undefined ? [metric("Motion on screen", sig.motion, 100, true)] : []),
      ...(sig.pace !== undefined ? [metric("Talking pace", sig.pace, 100, true)] : []),
    ].join("");
    const tag = c.tag ? `<span class="ctag ct-${c.tag.id}">${esc(c.tag.label)}</span>` : "";
    const post = c.post;
    const tags = post ? post.hashtags.map((t) => `#${t}`).join(" ") : "";
    const full = post ? `${post.title}\n\n${post.description}\n\n${tags}` : "";
    return `<article class="clip" id="clip-${c.index}" data-clip="${c.index}">
      <video src="${f(c.files.voiced || c.files.captioned)}#t=0.1" controls preload="metadata" playsinline></video>
      <div class="clip-body">
        <div class="clip-head">
          <span class="clip-num">${String(c.index).padStart(2, "0")}</span>
          <div>${tag}<h3>${esc(c.title)}</h3><div class="when">${fmtTime(c.start)} → ${fmtTime(c.end)} · ${c.duration.toFixed(1)} s</div></div>
          <div class="score" style="--s:${Math.round(c.score)}" title="${c.llm_score != null ? "Score: 70% AI judgement, 30% measured sound, picture and replays" : "Score from the sound, the picture, YouTube replays and keywords"}"><b>${Math.round(c.score)}</b><span>score</span></div>
        </div>
        <div class="downloads">
          ${c.files.voiced ? `<a class="main" href="${f(c.files.voiced, true)}">Save with AI hook</a>` : ""}
          <a class="${c.files.voiced ? "" : "main"}" href="${f(c.files.captioned, true)}">${c.files.voiced ? (o.preset === "none" ? "Without voice hook" : "Captioned only") : "Save MP4"}</a>
          <a href="${f(c.files.clean, true)}">No captions</a>
          <a href="${f(c.files.srt, true)}">SRT</a>
          ${c.files.txt ? `<a href="${f(c.files.txt, true)}">Post text</a>` : ""}
          ${c.files.thumb ? `<a href="${f(c.files.thumb, true)}">Thumbnail</a>` : ""}
        </div>
        ${c.files.thumb ? thumbFold(job, c, opts) : ""}
        ${post ? `<details class="fold" open>
          <summary>Post copy</summary>
          <div class="fold-body post">
            <div class="post-head"><span class="post-title">${esc(post.title)}</span>
              <div class="copy-row">
                <button class="copy" data-copy="${esc(post.title)}">Copy title</button>
                <button class="copy" data-copy="${esc(post.description + "\n\n" + tags)}">Copy description</button>
                <button class="copy" data-copy="${esc(full)}">Copy all</button>
              </div></div>
            <p class="post-desc">${esc(post.description)}</p>
            <div class="post-tags">${esc(tags)}</div>
          </div></details>` : ""}
        <details class="fold">
          <summary>Why this moment <small>${esc(c.reason)}</small></summary>
          <div class="fold-body">
            ${metrics ? `<div class="breakdown">${metrics}</div>` : ""}
            <p class="reason">${esc(c.reason)}</p>
          </div></details>
        <details class="fold">
          <summary>Narrator line <small>${esc(c.voice_hook || "none yet")}</small></summary>
          <label class="fold-body hook-edit">
            <textarea rows="2" maxlength="220" data-hook="${c.index}" data-was="${esc(c.voice_hook || "")}" aria-label="Narrator line for clip ${c.index}"
              placeholder="Written for you when the voice hook is made, or type your own">${esc(c.voice_hook || "")}</textarea>
            <span class="hint-s">Apply it from the AI voice hook card above.</span>
          </label></details>
      </div>
    </article>`;
  }).join("");
  copyButtons(body);
  body.querySelectorAll("form.thumb-edit").forEach((form) => wireThumb(job, form));
  const vstate = { mode: o.voice ? o.voice_mode : "", voice: o.voice || opts.voices[0].id };
  voiceControls(document.getElementById("voice-edit"), opts, vstate);
  document.getElementById("voice-form").addEventListener("submit", async (ev) => {
    ev.preventDefault();
    const btn = ev.target.querySelector("button[type=submit]");
    const data = new FormData();
    if (vstate.mode) { data.append("voice", vstate.voice); data.append("voice_mode", vstate.mode); }
    const hooks = {};
    body.querySelectorAll("[data-hook]").forEach((t) => { if (t.value.trim() && t.value.trim() !== t.dataset.was) hooks[t.dataset.hook] = t.value.trim(); });
    data.append("hooks", JSON.stringify(hooks));
    btn.disabled = true;
    try {
      await api(`/api/jobs/${job.id}/voice`, { method: "POST", body: data });
      renderJob(job.id);
    } catch (e) { alert(e.message); btn.disabled = false; }
  });
  document.querySelectorAll(".clip").forEach((row) => {
    const seg = document.querySelector(`.tl-seg[data-clip="${row.dataset.clip}"]`);
    row.addEventListener("mouseenter", () => seg && seg.classList.add("hot"));
    row.addEventListener("mouseleave", () => seg && seg.classList.remove("hot"));
  });
}

const STRENGTH_LABEL = { light: "Light touch", normal: "Normal", strong: "Strong (changes more)" };
const strengthOptions = (opts, current) => opts.redraw_strengths.map((k) => `<option value="${k}" ${k === current ? "selected" : ""}>${STRENGTH_LABEL[k] || k}</option>`).join("");

function thumbFold(job, c, opts) {
  const th = c.thumb || {};
  return `<details class="fold" open>
    <summary>Thumbnail <small>${esc(th.text || "")}</small></summary>
    <form class="fold-body thumb-edit" data-index="${c.index}">
      <a class="thumb-img" href="${fileUrl(job.id, c.files.thumb, true)}" title="Save the thumbnail">
        <img src="${fileUrl(job.id, c.files.thumb, false, `${job.finished}-${th.v || 0}`)}" alt="Thumbnail of clip ${c.index}" loading="lazy"></a>
      <div class="thumb-fields">
        <label><span class="label">Big words</span><input type="text" name="text" maxlength="30" value="${esc(th.text || "")}"></label>
        <label><span class="label">Small label</span><input type="text" name="tag" maxlength="20" value="${esc(th.tag || "")}" placeholder="optional"></label>
        <label class="check"><input type="checkbox" name="marks" value="1" ${th.marks === false ? "" : "checked"}> Arrow and circle</label>
        ${opts.redraw.installed ? `<div class="ai-row">
          <label class="check"><input type="checkbox" name="ai" value="1" ${th.ai ? "checked" : ""}> AI redraw</label>
          <select name="strength" aria-label="How much the AI changes the picture">${strengthOptions(opts, th.strength || "normal")}</select>
        </div>` : ""}
        <span class="hint-s thumb-note" aria-live="polite"></span>
        <div class="copy-row">
          <button class="ghost small" type="submit">Redraw</button>
          <button class="ghost small" type="button" data-next>Another frame</button>
        </div>
      </div>
    </form></details>`;
}

function wireThumb(job, form) {
  const send = async (frame) => {
    const data = new FormData(form);
    data.set("index", form.dataset.index);
    data.set("frame", frame);
    if (!data.has("marks")) data.set("marks", "0");
    if (!data.has("ai")) data.set("ai", "0");
    form.querySelectorAll("button").forEach((b) => (b.disabled = true));
    form.classList.add("busy");
    const note = form.querySelector(".thumb-note");
    if (data.get("ai") === "1") note.textContent = "AI is redrawing… (up to a minute the first time)";
    try {
      const st = await api(`/api/jobs/${job.id}/thumb`, { method: "POST", body: data });
      const c = st.clips[form.dataset.index - 1];
      form.querySelector("img").src = fileUrl(job.id, c.files.thumb, false, `${st.finished}-${c.thumb.v}`);
      form.elements.text.value = c.thumb.text;
      form.elements.tag.value = c.thumb.tag;
      form.closest("details").querySelector("summary small").textContent = c.thumb.text;
    } catch (e) { alert(e.message); }
    note.textContent = "";
    form.querySelectorAll("button").forEach((b) => (b.disabled = false));
    form.classList.remove("busy");
  };
  form.addEventListener("submit", (ev) => { ev.preventDefault(); send("same"); });
  form.querySelector("[data-next]").addEventListener("click", () => send("next"));
}

/* ---------------------------------------------------------------- library */

async function renderLibrary(params) {
  setNav("library");
  mount("tpl-library");
  const state = { kind: KIND_LABEL[params.kind] ? params.kind : "", q: "" };
  const kinds = [{ id: "", label: "All" }, ...Object.entries(KIND_LABEL).map(([id, label]) => ({ id, label: id === "download" ? "Downloads" : label }))];
  const sync = radioGroup(document.getElementById("lib-kinds"), kinds, state.kind, (id) => { state.kind = id; sync(id); draw(); }, (k) => k.label);
  const search = document.getElementById("lib-search");
  search.addEventListener("input", () => { state.q = search.value.trim().toLowerCase(); draw(); });

  const libList = document.getElementById("lib-list");
  let all = await api("/api/jobs?size=true");
  if (!libList.isConnected) return;
  const total = () => {
    const bytes = all.reduce((n, j) => n + (j.size || 0), 0);
    document.getElementById("lib-total").textContent = `${all.length} job${all.length === 1 ? "" : "s"} · ${fmtSize(bytes) || "0 KB"} on disk`;
  };
  function draw() {
    const box = document.getElementById("lib-list");
    const rows = all.filter((j) => (!state.kind || j.kind === state.kind) && (!state.q || (j.title || "").toLowerCase().includes(state.q)));
    box.innerHTML = rows.length ? rows.map((j) => jobRow(j)).join("")
      : `<p class="empty">${all.length ? "Nothing matches." : `Nothing here yet. <a href="#/">Start with a link</a>.`}</p>`;
    wireRows(box, async () => { all = await api("/api/jobs?size=true"); if (box.isConnected) { total(); draw(); } });
  }
  total();
  draw();
}

/* ---------------------------------------------------------------- system */

async function renderSystem() {
  setNav("system");
  mount("tpl-system");
  const grid = document.getElementById("sys-grid");
  let s;
  try { s = await api("/api/system"); } catch (e) { grid.innerHTML = `<div class="error-box"><strong>Could not read the system status</strong>${esc(e.message)}</div>`; return; }
  document.getElementById("cfg-path").textContent = s.config_path;
  const card = (title, ok, rows, extra = "") => `<div class="sys ${ok ? "ok" : "bad"}">
    <div class="sys-head"><span class="sys-dot"></span><strong>${title}</strong></div>
    <dl>${rows.filter(Boolean).map(([k, v]) => `<dt>${esc(k)}</dt><dd>${v}</dd>`).join("")}</dl>${extra}</div>`;
  const w = s.whisper, y = s.ytdlp, l = s.llm, v = s.voice;
  const norm = (x) => String(x || "").split(".").map(Number).join(".");  // 2026.08.19 == 2026.8.19
  const restartNote = y.installed && norm(y.installed) !== norm(y.running) ? `<p class="note">Version ${esc(y.installed)} is installed. Restart ClipForge to use it.</p>` : "";
  grid.innerHTML = [
    card("Downloader (yt-dlp)", true, [["Version", esc(y.running)], ["Browser logins", y.cookies_from_browser ? esc(y.cookies_from_browser) : "off <span class='muted'>(download.cookies_from_browser)</span>"]],
      `${restartNote}<button class="ghost" id="update-dl">Update downloader</button><p class="note muted">Sites like Instagram and TikTok change often. When downloads start failing, update first.</p>`),
    card("Transcription (Whisper)", !w.error, w.error ? [["Problem", esc(w.error)]] : [["Model", esc(w.model)], ["Runs on", w.device === "cuda" ? "NVIDIA GPU" : "CPU (slower)"], ["Precision", esc(w.compute_type)]]),
    card("Moment picking (LLM)", l.ready || l.provider === "heuristic", [
      ["Provider", esc(l.provider)], ["Model", esc(l.model)],
      ["Status", l.ready ? "Ready" : esc(l.problem || "Off, using the rule-based picker")]],
      l.ready ? `<p class="note muted">Slow picking on a laptop with Intel + NVIDIA graphics? Run <code>setx OLLAMA_VULKAN 0</code> and restart Ollama.</p>` : ""),
    v ? card("AI voice (Kokoro)", v.installed, [
      ["Engine", v.installed ? "kokoro-onnx, runs on CPU" : "Not installed. Run run.bat again to install it."],
      ["Model", v.model_ready ? `Downloaded (${v.model_mb} MB)` : `Downloads on first use (${v.download_mb} MB)`],
      ["Voices", String(v.voices)]]) : "",
    card("ffmpeg", !!s.ffmpeg, [["Version", s.ffmpeg ? esc(s.ffmpeg) : "Not found. Install it: winget install Gyan.FFmpeg"]]),
    card("Storage", true, [["Used", fmtSize(s.storage.bytes) || "0 KB"],
      ["Jobs", Object.entries(s.storage.jobs).map(([k, n]) => `${n} ${esc((KIND_LABEL[k] || k).toLowerCase())}`).join(", ") || "none"],
      ["Folder", `<code>${esc(s.storage.folder)}</code>`]], `<a class="ghost" href="#/library">Manage in Library</a>`),
    card("Workers", true, Object.entries(s.workers).map(([name, wk]) => [name === "heavy" ? "Clips, subtitles, convert" : "Downloads",
      `${wk.running ? `<a href="#/job/${wk.running}">busy</a>` : "idle"}${wk.waiting ? `, ${wk.waiting} waiting` : ""}`])),
  ].join("");

  grid.insertAdjacentHTML("beforebegin", `<section class="health" id="health" aria-live="polite"><div class="health-head"><div><span class="eyebrow">Health check</span><h2 id="health-title">Checking…</h2></div><button class="ghost" id="health-run" type="button">Check again</button></div><ul class="health-list" id="health-list"></ul></section>`);
  document.getElementById("health-run").addEventListener("click", runHealth);
  runHealth();

  const upd = document.getElementById("update-dl");
  upd.addEventListener("click", async () => {
    upd.disabled = true; upd.textContent = "Updating… (up to a minute)";
    try {
      const r = await api("/api/system/update-downloader", { method: "POST" });
      upd.textContent = r.changed ? `Updated to ${r.after}. Restart ClipForge to use it.` : `Already up to date (${r.after})`;
    } catch (e) { upd.textContent = "Update failed"; alert(e.message); upd.disabled = false; }
  });
}

const HEALTH_TITLE = { ok: "Everything works", warn: "Working, with notes", fail: "Something needs fixing" };
async function runHealth() {
  const box = document.getElementById("health"), list = document.getElementById("health-list"), btn = document.getElementById("health-run");
  if (!box) return;
  btn.disabled = true; btn.textContent = "Checking…";
  try {
    const r = await api("/api/system/check");
    if (!box.isConnected) return;
    box.dataset.status = r.status;
    document.getElementById("health-title").textContent = HEALTH_TITLE[r.status];
    list.innerHTML = r.checks.map((c) => `<li data-status="${esc(c.status)}"><span class="health-dot" aria-label="${esc(c.status)}"></span><b>${esc(c.label)}</b><span>${esc(c.detail)}${c.status !== "ok" && c.fix ? `<small>${esc(c.fix)}</small>` : ""}</span></li>`).join("");
    healthDot(r.status);
  } catch (e) { list.innerHTML = `<li data-status="fail"><span class="health-dot"></span><b>Check failed</b><span>${esc(e.message)}</span></li>`; }
  finally { btn.disabled = false; btn.textContent = "Check again"; }
}
function healthDot(status) {
  const link = document.querySelector('.nav a[data-nav="system"]');
  if (link) link.dataset.health = status;
}
api("/api/system/check").then((r) => healthDot(r.status)).catch(() => {});

/* ---------------------------------------------------------------- router */

async function renderLive(params={}) {
  setNav("live"); clearTimeout(pollTimer);
  view.innerHTML = `<section class="live-page"><header class="page-head"><p class="eyebrow">Live capture / Twitch + YouTube</p><h1>Catch it before chat erupts</h1><p class="lede">Capture the moment. Turn a replay or saved clip into captioned Shorts.</p></header>
    <div class="live-timeline" aria-label="Default capture timing"><div><span>20 seconds before</span><strong>The setup</strong></div><div><span>Chat reaction spike</span><strong>The moment</strong></div><div><span>12 seconds after chat settles</span><strong>The ending</strong></div></div>
    <details class="live-setup" id="live-connection-setup" open><summary>Connect the Chrome companion <span>One-time setup</span></summary><ol>
      <li><strong>Load the extension.</strong> Open <code>chrome://extensions</code>, enable Developer mode, choose <b>Load unpacked</b>, then select <code id="live-path">Loading…</code>. <a href="/api/live/extension.zip">Download ZIP</a> if you prefer to extract it elsewhere.</li>
      <li><strong>Connect once.</strong> Click <b>Connect app</b> in the extension. For manual setup: <button class="ghost small" id="live-pair">Show pairing key</button><div id="live-key-box" hidden><label class="field"><span class="label">Paste into “Use a pairing key instead” in the extension</span><input id="live-key" type="password" readonly autocomplete="off"></label><button class="ghost small" id="live-copy">Copy key</button><span class="hint-s" id="live-port"></span></div></li>
      <li><strong>Open a live stream and its chat.</strong> Pin ClipForge Live in Chrome, keep the player fully visible and playing, then click <b>Start capture</b> in the extension. Allow 20 seconds to build the buffer.</li></ol>
      <p class="hint-s">Keep the stream tab visible while recording. Capture includes anything shown over the player, including ads. Settings apply to the next capture. The extension’s popup shows capture status; this page shows received clips.</p><p id="live-setup-error" role="alert"></p></details>
    <p id="live-verified" class="hint-s" role="status">Waiting for an extension connection check.</p>
    <div id="live-tools"></div>
    <div class="section-head"><h2>Captured moments</h2><span class="muted" id="live-count">Checking…</span><button class="ghost small" id="live-refresh">Refresh</button></div><div id="live-inbox" aria-live="polite"></div>
    <p class="hint-s">Chat spikes are candidate highlights, not a guarantee of a good clip. Adjust sensitivity or use “Save moment” in the extension. Optional sound spikes can help when chat is quiet.</p></section>`;
  const box = document.getElementById("live-inbox"), setupError = document.getElementById("live-setup-error");
  try {
    const setup = await api("/api/live/setup");
    if (!box.isConnected) return;
    document.getElementById("live-path").textContent = setup.extension_path;
    document.getElementById("live-port").textContent = ` Port ${setup.port}`;
  } catch (e) { setupError.textContent = e.message; }
  document.getElementById("live-pair").onclick = async (event) => {
    const button=event.currentTarget;button.disabled=true;setupError.textContent="Requesting the connection key…";
    try {
      const data = await api("/api/live/pair", {method: "POST"});
      if (!box.isConnected) return;
      document.getElementById("live-key").value = data.token;
      document.getElementById("live-key-box").hidden = false;
      document.getElementById("live-key").select();
      setupError.textContent="Key ready. Paste it in the extension and press Verify key.";
    } catch (e) { setupError.textContent = e.message; }
    finally {button.disabled=false;}
  };
  document.getElementById("live-copy").onclick = async (event) => {
    try { await navigator.clipboard.writeText(document.getElementById("live-key").value); flash(event.target, "Copied"); }
    catch { setupError.textContent = "Select the key and press Ctrl+C to copy it."; }
  };
  const toolsHost=document.getElementById("live-tools");
  box.after(toolsHost);
  await mountLiveTools(toolsHost,params);
  if(params.tools==="1"&&toolsHost.isConnected)toolsHost.scrollIntoView({behavior:"smooth",block:"start"});
  let refreshing = false, previous = "", connectionSeen=false;
  async function refresh() {
    if (refreshing || !box.isConnected) return;
    refreshing = true; clearTimeout(pollTimer);
    try {
      const [data,setup] = await Promise.all([api("/api/jobs?kind=live"),api("/api/live/setup"),toolsHost.refreshShorts?.()]), items = Array.isArray(data) ? data : data.jobs;
      if (!box.isConnected) return;
      document.getElementById("live-verified").textContent=setup.verified_at?`Extension connection verified at ${new Date(setup.verified_at*1000).toLocaleTimeString()}.`:`App ready on port ${setup.port}. In the extension, choose Connect app or Verify key. Extension version: ${setup.extension_version||"reload after updating"}.`;
      if(setup.verified_at&&!connectionSeen){document.getElementById("live-connection-setup").open=false;connectionSeen=true;}
      document.getElementById("live-count").textContent = `${items.length} received · auto-refreshes`;
      const version = JSON.stringify(items.map((j) => [j.id, j.state, j.finished, j.error]));
      if (version !== previous) {
        previous = version;
        const selected = box.querySelector(".live-grid")?._sel;
        box.innerHTML = items.length ? `<div class="live-grid">${items.map((job) => {
          const file = job.outputs?.find((f) => f.media === "video");
          return `<article class="live-card" data-id="${esc(job.id)}"><label class="pick" title="${LIVE_BUSY.has(job.state) ? "Still exporting" : "Select"}"><input type="checkbox" aria-label="Select clip"${LIVE_BUSY.has(job.state) ? " disabled" : ""}></label>${file && job.state === "done" ? `<video controls preload="metadata" src="${fileUrl(job.id, file.file, false, job.finished)}"></video>` : `<div class="live-wait"><span>${job.state === "error" ? "Export needs attention" : "Preparing your MP4…"}</span></div>`}<div class="live-card-body"><p class="hint-s">${esc(fmtDate(job.created))} · ${esc(job.state)}</p><h3>${esc(job.title)}</h3>${job.error ? `<p class="error-box">${esc(job.error)}</p>` : ""}<div class="live-actions">${file && job.state === "done" ? `<a class="primary small" href="${fileUrl(job.id, file.file, true, job.finished)}">Download MP4</a>` : ""}<a class="ghost small" href="#/job/${job.id}">${job.state === "error" ? "Review & retry" : "Review / edit"}</a><button type="button" class="btn-s del live-del" data-id="${esc(job.id)}"${LIVE_BUSY.has(job.state) ? ' disabled title="Still exporting"' : ""}>Delete</button></div><p class="form-error live-del-error" role="alert" hidden></p></div></article>`;
        }).join("")}</div>` : `<div class="workflow-empty"><p class="eyebrow">Ready when the stream is</p><h2>Your next highlight lands here</h2><p>Start capture on a stream, then let chat trigger a clip or press “Save moment.” The original aspect ratio and audio are kept, with no added captions.</p></div>`;
        const grid = box.querySelector(".live-grid");
        if (grid) {
          grid._sel = selected;
          for (const card of grid.querySelectorAll(".live-card")) {
            const job = items.find(j => j.id === card.dataset.id);
            const file = job?.state === "done" && job.outputs?.find(f => f.media === "video");
            if (file) card.querySelector(".live-actions").insertAdjacentHTML("afterbegin", `<a class="primary small live-gaming" href="#/gaming?from=${job.id}&file=${encodeURIComponent(file.file)}&preset=bold">Make captioned Short</a><a class="ghost small" href="#/live?from=${job.id}&file=${encodeURIComponent(file.file)}&clips=1">Find highlights</a>`);
          }
          bulkBar(grid, { item: ".live-card", noun: "clip", onDeleted: () => { previous = ""; refresh(); } });
        }
      }
    } catch (e) { if (box.isConnected) { box.textContent = e.message; previous = ""; } }
    finally { refreshing = false; if (box.isConnected) pollTimer = setTimeout(refresh, 4000); }
  }
  document.getElementById("live-refresh").onclick = refresh;
  box.addEventListener("click", async (event) => {
    const gaming=event.target.closest(".live-gaming");
    if(gaming){gaming.href=gaming.href.replace(/preset=[^&]*/,`preset=${encodeURIComponent(toolsHost.captionPreset?.()||"bold")}`);return;}
    const btn = event.target.closest(".live-del");
    if (!btn || btn.disabled) return;
    const card = btn.closest(".live-card"), err = card.querySelector(".live-del-error");
    if (!confirm("Delete this clip from ClipForge? The file is removed from this PC.")) return;
    btn.disabled = true; err.hidden = true;
    try {
      await api(`/api/jobs/${encodeURIComponent(btn.dataset.id)}`, { method: "DELETE" });
      card.remove(); previous = ""; box.querySelector(".live-grid")?._bulkSync?.();
      const left = box.querySelectorAll(".live-card").length;
      document.getElementById("live-count").textContent = `${left} received · auto-refreshes`;
      if (!left) refresh();
    } catch (e) { err.textContent = e.message; err.hidden = false; btn.disabled = false; }
  });
  await refresh();
}
const LIVE_BUSY = new Set(["active", "running", "receiving", "queued"]);

const ROUTES = { "": renderHome, publish: renderPublishing, gaming: renderGaming, monitor: renderMonitor, live: renderLive, clips: renderClips, download: renderDownload, subtitles: renderSubtitles,
  convert: renderConvert, voice: renderVoice, library: renderLibrary, history: renderLibrary, system: renderSystem };

let routeSeq = 0;
function route() {
  const hash = location.hash.replace(/^#\/?/, "");
  const [path, query = ""] = hash.split("?");
  const params = Object.fromEntries(new URLSearchParams(query));
  const m = path.match(/^job\/([a-f0-9]{12})$/);
  view.style.removeProperty("--k");
  const seq = ++routeSeq;
  const run = m ? renderJob(m[1]) : (ROUTES[path] || renderHome)(params);
  run.catch((e) => { if (seq !== routeSeq) return; console.error(e); view.innerHTML = `<div class="error-box"><strong>Something broke</strong>${esc(e.message)}</div>`; });
}
window.addEventListener("hashchange", route);
view.tabIndex = -1;
route();
pollActivity();
