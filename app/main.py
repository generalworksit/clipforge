"""FastAPI app: JSON API for every tool, file serving, and the single-page UI."""
from __future__ import annotations

import importlib.metadata
import json
import re
import shutil
import subprocess
import sys
import zipfile
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import urlparse

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from . import convert, jobs, live, moments, redraw, voice
from .captions import POSITIONS, PRESETS
from .effects import EFFECTS
from .config import CONFIG, CONFIG_PATH, JOBS_DIR, ROOT, STATIC_DIR
from .download import QUALITIES
from .monitoring.api import router as monitor_router
from .monitoring.service import supervisor as monitor_supervisor

FILE_RE = re.compile(r"^[A-Za-z0-9_.-]+$")
URL_RE = re.compile(r"^https?://\S+$")
VIDEO_EXTS = {".mp4", ".mov", ".mkv", ".webm", ".avi", ".m4v", ".flv", ".ts", ".mts", ".wmv", ".3gp"}
AUDIO_EXTS = {".mp3", ".m4a", ".wav", ".aac", ".ogg", ".opus", ".flac"}
MAX_URLS = 20
PORT = int(CONFIG["server"]["port"])
LOCAL_HOSTS = {f"127.0.0.1:{PORT}", f"localhost:{PORT}"}


@asynccontextmanager
async def lifespan(app: FastAPI):
    jobs.start_worker()
    monitor_supervisor.start()
    try:
        yield
    finally:
        monitor_supervisor.close()


app = FastAPI(title="ClipForge", lifespan=lifespan)
app.include_router(monitor_router)
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
app.mount("/assets", StaticFiles(directory=ROOT / "assets"), name="assets")


@app.middleware("http")
async def local_only(request: Request, call_next):
    """The server listens on 127.0.0.1, but a web page in the browser can still send requests to
    it. Reject foreign Host headers (DNS rebinding) and cross-site writes (another site posting a
    job or deleting one)."""
    if request.headers.get("host") not in LOCAL_HOSTS:
        return JSONResponse({"detail": "Open ClipForge at http://127.0.0.1:%d" % PORT}, status_code=403)
    origin = request.headers.get("origin")
    bridge = request.url.path in {"/api/live/capture", "/api/live/health"}
    extension = bool(origin and re.fullmatch(r"chrome-extension://[a-p]{32}", origin))
    cors = {"Access-Control-Allow-Origin": origin, "Vary": "Origin",
            "Access-Control-Allow-Headers": "Authorization, Content-Type",
            "Access-Control-Allow-Methods": "GET, POST, OPTIONS"} if bridge and extension else {}
    if bridge:
        if request.method == "OPTIONS":
            return JSONResponse({}, headers=cors)
        if not live.authorized(request.headers.get("authorization", "")):
            return JSONResponse({"detail": "Pair the extension using the key on Live clips."}, status_code=401, headers=cors)
        if request.url.path == "/api/live/capture" and request.method == "POST":
            try:
                size = int(request.headers.get("content-length", "-1"))
            except ValueError:
                size = -1
            if size < 0 or size > live.MAX_BYTES + 1024 * 1024:
                return JSONResponse({"detail": "Upload requires a Content-Length below 101 MB."}, status_code=413, headers=cors)
        if extension:
            response = await call_next(request)
            response.headers.update(cors)
            return response
    if request.method not in ("GET", "HEAD", "OPTIONS") and origin and urlparse(origin).netloc not in LOCAL_HOSTS:
        return JSONResponse({"detail": "Cross-site request blocked."}, status_code=403)
    return await call_next(request)


@app.get("/api/live/setup")
def live_setup():
    return {"extension_path": str(ROOT / "extension"), "port": PORT, "paired": live.TOKEN_PATH.exists()}


@app.post("/api/live/pair")
def live_pair():
    return JSONResponse({"token": live.pairing_key(), "port": PORT}, headers={"Cache-Control": "no-store"})


@app.get("/api/live/health")
def live_health():
    return {"ok": True, "version": 1}


@app.post("/api/live/capture")
async def live_capture(metadata: str = Form(...), files: list[UploadFile] = File(...)):
    return await live.receive(metadata, files)


@app.get("/api/live/extension.zip")
def live_extension():
    from io import BytesIO
    from fastapi.responses import Response
    out = BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as archive:
        for file in sorted((ROOT / "extension").iterdir()):
            if file.suffix in {".js", ".mjs", ".json", ".html", ".css", ".md"}:
                archive.write(file, file.name)
    return Response(out.getvalue(), media_type="application/zip",
                    headers={"Content-Disposition": 'attachment; filename="clipforge-live.zip"'})


@app.get("/", include_in_schema=False)
def index() -> HTMLResponse:
    """index.html with versioned asset URLs, so an update never runs a cached old app.js/style.css."""
    html = (STATIC_DIR / "index.html").read_text(encoding="utf-8")
    for name in ("app.js", "monitor.js", "style.css"):
        version = int((STATIC_DIR / name).stat().st_mtime)
        html = html.replace(f"/static/{name}\"", f"/static/{name}?v={version}\"")
    return HTMLResponse(html, headers={"Cache-Control": "no-cache"})


@app.get("/api/options")
def options() -> dict:
    c = CONFIG["captions"]
    return {
        "presets": [{"id": k, "label": v["label"]} for k, v in PRESETS.items()],
        "positions": list(POSITIONS),
        "default_preset": c["default_preset"], "default_position": c["default_position"],
        "max_clips": 10, "max_urls": MAX_URLS, "qualities": QUALITIES,
        "effects": EFFECTS,
        "convert_ops": [{"id": k, "label": v["label"], "needs_video": v["needs_video"]} for k, v in convert.OPS.items()],
        "gif_max_seconds": convert.GIF_MAX_SECONDS,
        "kinds": {k: v["label"] for k, v in jobs.KINDS.items()},
        "voices": voice.VOICES, "voice_modes": voice.MODES, "voice_ready": voice.model_ready(),
        "voice_download_mb": round(sum(voice.MODEL_FILES.values()) / 1e6),
        "lengths": {"short": "15–30 s", "normal": "{min_seconds}–{max_seconds} s".format(**CONFIG["moments"]),
                    "long": "45–90 s"},
        "clip_modes": {k: {"label": v["label"], "hint": v["hint"], "cats": v["cats"], "framing": v["framing"]}
                       for k, v in moments.MODES.items()},
        "categories": {k: {"label": v["label"], "emoji": v["emoji"]} for k, v in moments.CATEGORIES.items()},
        "framings": moments.FRAMINGS,
        "llm_problem": moments.get_provider()[1],
        "redraw": redraw.status(), "redraw_strengths": list(redraw.STRENGTH),
    }


# ------------------------------------------------------------------ creating jobs

def _status_or_404(job_id: str) -> dict:
    try:
        status = jobs.read_status(job_id)
    except ValueError:
        status = None
    if status is None:
        raise HTTPException(404, "Job not found.")
    return status


def _job_files(status: dict) -> set[str]:
    names = {o["file"] for o in status.get("outputs") or []}
    for clip in status.get("clips") or []:
        names.update(v for k, v in clip["files"].items() if k in ("captioned", "clean", "voiced"))
    return names


async def _attach_source(kind: str, url: str, file: UploadFile | None, from_job: str, from_file: str,
                         exts: set[str]) -> tuple[dict, str | None, object]:
    """Validate exactly one source. Returns (source, title, writer) where writer(job_dir) stores
    the uploaded or linked file in the new job's folder."""
    url = url.strip()
    has_file = file is not None and bool(file.filename)
    if sum([bool(url), has_file, bool(from_job)]) != 1:
        raise HTTPException(400, "Give one source: a link, a file, or a file from the library.")
    if url:
        if not URL_RE.match(url):
            raise HTTPException(400, "The link must start with http:// or https://")
        return {"type": "url", "url": url}, None, None

    if has_file:
        ext = Path(file.filename).suffix.lower()
        if ext not in exts:
            raise HTTPException(400, f"Unsupported file type '{ext}'. Use one of: {', '.join(sorted(exts))}")
        source = {"type": "file", "filename": Path(file.filename).name, "stored_as": "source" + ext}

        async def write(directory: Path) -> None:
            with open(directory / source["stored_as"], "wb") as out:
                while chunk := await file.read(4 * 1024 * 1024):
                    out.write(chunk)
            if (directory / source["stored_as"]).stat().st_size == 0:
                raise HTTPException(400, "The uploaded file is empty.")
        return source, Path(file.filename).stem, write

    origin = _status_or_404(from_job)
    if origin["state"] != "done" or from_file not in _job_files(origin) or not FILE_RE.match(from_file):
        raise HTTPException(400, "That library file is not available.")
    ext = Path(from_file).suffix.lower()
    if ext not in exts:
        raise HTTPException(400, f"This tool cannot use a {ext} file.")
    source = {"type": "file", "filename": from_file, "stored_as": "source" + ext, "from_job": from_job}

    async def link(directory: Path) -> None:
        jobs.link_or_copy(jobs.job_dir(from_job) / from_file, directory / source["stored_as"])
    return source, origin.get("title"), link


def _voice_options(voice_id: str, voice_mode: str) -> dict:
    if not voice_id:
        return {"voice": ""}
    if voice_id not in voice.VOICE_BY_ID or voice_mode not in voice.MODES:
        raise HTTPException(400, "Unknown AI voice or intro style.")
    return {"voice": voice_id, "voice_mode": voice_mode}


def _pick_options(clips: int, length: str, clip_mode: str, cats: str, framing: str) -> dict:
    """Validated moment-picking options. clips = 0 means auto (only the strong moments)."""
    if not 0 <= clips <= 10:
        raise HTTPException(400, "Number of clips must be between 1 and 10, or auto.")
    if length not in ("short", "normal", "long"):
        raise HTTPException(400, f"Unknown clip length '{length}'.")
    if clip_mode not in moments.MODES:
        raise HTTPException(400, f"Unknown video type '{clip_mode}'.")
    chosen = [c for c in dict.fromkeys(x.strip() for x in cats.split(",")) if c]
    if any(c not in moments.CATEGORIES for c in chosen):
        raise HTTPException(400, "Unknown moment type.")
    framing = framing or moments.MODES[clip_mode]["framing"]
    if framing not in moments.FRAMINGS:
        raise HTTPException(400, f"Unknown framing '{framing}'.")
    return {"clips": clips, "length": length, "mode": clip_mode,
            "cats": chosen or moments.MODES[clip_mode]["cats"], "framing": framing}


def _flag(value: str) -> bool:
    return value.lower() in ("1", "true", "on", "yes")


def _redraw_options(ai: bool, strength: str) -> dict:
    if strength not in redraw.STRENGTH:
        raise HTTPException(400, "AI redraw strength must be light, normal or strong.")
    if ai and not redraw.installed():
        raise HTTPException(400, "AI redraw is not installed on this PC.")
    return {"thumbs_ai": ai, "thumbs_strength": strength}


@app.post("/api/jobs", status_code=201)
async def create_job(
    kind: str = Form("clips"),
    url: str = Form(""),
    file: UploadFile | None = File(None),
    from_job: str = Form(""),
    from_file: str = Form(""),
    clips: int = Form(3),
    preset: str = Form(CONFIG["captions"]["default_preset"]),
    position: str = Form(CONFIG["captions"]["default_position"]),
    translate: str = Form(""),
    burn: str = Form(""),
    op: str = Form("mp3"),
    target_mb: float = Form(25),
    mode: str = Form("blur"),
    start: float = Form(0),
    end: float = Form(0),
    width: int = Form(480),
    length: str = Form("normal"),
    voice_id: str = Form("", alias="voice"),
    voice_mode: str = Form("over"),
    clip_mode: str = Form("viral"),
    cats: str = Form(""),
    framing: str = Form(""),
    hook: str = Form(""),
    erase_subs: str = Form(""),
    thumbs: str = Form(""),
    thumbs_ai: str = Form(""),
    thumbs_strength: str = Form("normal"),
    effect: str = Form("none"),
    effect_intensity: float = Form(0.5),
) -> dict:
    if kind not in ("clips", "subtitles", "convert", "voice"):
        raise HTTPException(400, f"Unknown tool '{kind}'. Downloads go to /api/downloads.")
    if preset not in PRESETS:
        raise HTTPException(400, f"Unknown caption preset '{preset}'.")
    if preset == "none" and kind != "clips":
        raise HTTPException(400, "No captions is available in Clip maker. For subtitles, turn off Burn captions instead.")
    if position not in POSITIONS:
        raise HTTPException(400, f"Unknown caption position '{position}'.")
    if kind == "clips":
        opts, exts = {"preset": preset, "position": position}, VIDEO_EXTS
        opts.update(_effect_options(effect, effect_intensity))
        opts.update(_pick_options(clips, length, clip_mode, cats, framing), erase_subs=_flag(erase_subs),
                    thumbs=_flag(thumbs), **_redraw_options(_flag(thumbs) and _flag(thumbs_ai), thumbs_strength))
        opts.update(_voice_options(voice_id, voice_mode))
    elif kind == "voice":
        opts, exts = _voice_options(voice_id, voice_mode), VIDEO_EXTS
        if not opts["voice"]:
            raise HTTPException(400, "Pick a voice.")
        if framing not in ("keep", "face", "split", "crop", "blur"):
            raise HTTPException(400, "Framing must be keep, face, split, crop or blur.")
        opts.update(hook=voice.clean_hook(hook), framing=framing)
    elif kind == "subtitles":
        opts, exts = {"translate": _flag(translate), "burn": _flag(burn), "preset": preset,
                      "position": position, "erase_subs": _flag(burn) and _flag(erase_subs)}, VIDEO_EXTS | AUDIO_EXTS
    else:
        opts = {"op": op}
        if op == "compress":
            opts["target_mb"] = target_mb
        elif op == "vertical":
            opts["mode"] = mode
        elif op in ("trim", "gif"):
            opts.update(start=start, end=end)
            if op == "gif":
                opts["width"] = width
        try:
            convert.validate(op, opts)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from None
        exts = VIDEO_EXTS if convert.OPS[op]["needs_video"] else VIDEO_EXTS | AUDIO_EXTS

    source, title, store = await _attach_source(kind, url, file, from_job, from_file, exts)
    status = jobs.create(kind, source, opts, title=title)
    if store is not None:
        try:
            await store(jobs.job_dir(status["id"]))
        except BaseException:
            jobs.delete(status["id"])
            raise
    jobs.submit(status["id"])
    return status


def _effect_options(effect: str, intensity: float) -> dict:
    if effect not in EFFECTS or not 0 <= intensity <= 1:
        raise HTTPException(400, "Choose a known light effect and an intensity between 0 and 1.")
    return {"effect": effect, "effect_intensity": intensity}


@app.get("/api/discover")
def discover(q: str, sort: str = "relevance") -> dict:
    from .discovery import search
    if not 2 <= len(q.strip()) <= 160 or sort not in ("relevance", "views"):
        raise HTTPException(400, "Enter a topic of 2–160 characters and a valid sort order.")
    try:
        return search(q.strip(), sort)
    except RuntimeError as exc:
        raise HTTPException(502, str(exc)) from None


@app.post("/api/jobs/{job_id}/effects")
def change_effects(job_id: str, effect: str = Form("none"), effect_intensity: float = Form(0.5)) -> dict:
    status = _idle_job(job_id)
    if status.get("kind", "clips") != "clips" or status["state"] != "done":
        raise HTTPException(409, "Light effects can be changed on finished clip jobs.")
    return jobs.requeue(job_id, ["caption"], _effect_options(effect, effect_intensity))


@app.post("/api/downloads", status_code=201)
def create_downloads(urls: str = Form(...), mode: str = Form("video"), quality: str = Form("best"),
                     compatible: str = Form("true")) -> dict:
    """One download job per link (one link per line)."""
    links = list(dict.fromkeys(u.strip() for u in re.split(r"[\s,]+", urls) if u.strip()))
    if not links:
        raise HTTPException(400, "Paste at least one link.")
    if len(links) > MAX_URLS:
        raise HTTPException(400, f"At most {MAX_URLS} links at a time.")
    bad = [u for u in links if not URL_RE.match(u)]
    if bad:
        raise HTTPException(400, f"Not a link: {bad[0][:80]}")
    if mode not in ("video", "audio") or quality not in QUALITIES:
        raise HTTPException(400, "Unknown format or quality.")
    created = []
    for link in links:
        status = jobs.create("download", {"type": "url", "url": link},
                             {"mode": mode, "quality": quality, "compatible": _flag(compatible)})
        jobs.submit(status["id"])
        created.append(status)
    return {"jobs": created}


@app.post("/api/probe")
def probe(url: str = Form(...)) -> dict:
    """Preview a link (title, thumbnail, qualities) before downloading."""
    from .download import DownloadError, probe_url
    url = url.strip()
    if not URL_RE.match(url):
        raise HTTPException(400, "The link must start with http:// or https://")
    try:
        return probe_url(url)
    except DownloadError as exc:
        raise HTTPException(422, str(exc)) from None


# ------------------------------------------------------------------ reading jobs

@app.get("/api/jobs")
def list_jobs(kind: str | None = None, size: bool = False) -> list[dict]:
    return jobs.list_jobs(kind, with_size=size)


@app.get("/api/activity")
def activity() -> dict:
    """What each worker is doing right now (sidebar indicator, polled)."""
    out = {}
    for name, w in jobs.workers().items():
        run = None
        st = jobs.read_status(w["running"]) if w["running"] else None
        if st and st.get("stage"):
            stage = st["stages"][st["stage"]]
            run = {"id": st["id"], "title": st.get("title"), "kind": st.get("kind", "clips"),
                   "stage": stage["label"], "progress": stage["progress"]}
        out[name] = {"running": run, "waiting": w["waiting"]}
    return out


@app.get("/api/jobs/{job_id}")
def get_job(job_id: str) -> dict:
    status = _status_or_404(job_id)
    status.setdefault("kind", "clips")
    status["queue_position"] = jobs.queue_position(job_id)
    return status


@app.delete("/api/jobs/{job_id}")
def delete_job(job_id: str) -> dict:
    status = _status_or_404(job_id)
    if status["state"] in {"receiving", "queued", "running"} or jobs.is_running(job_id):
        raise HTTPException(409, "This job is still being saved or processed. Wait for it to finish before deleting it.")
    jobs.delete(job_id)
    return {"deleted": job_id}


@app.get("/files/{job_id}/{name}")
def get_file(job_id: str, name: str, download: bool = False) -> FileResponse:
    status = _status_or_404(job_id)
    if not FILE_RE.match(name) or name.startswith("."):
        raise HTTPException(404, "File not found.")
    directory = jobs.job_dir(job_id).resolve()
    path = (directory / name).resolve()
    if path.parent != directory or not path.is_file():
        raise HTTPException(404, "File not found.")
    nice = next((o.get("download_name") for o in status.get("outputs") or [] if o["file"] == name), None)
    return FileResponse(path, filename=(nice or name) if download else None,
                        content_disposition_type="attachment" if download else "inline")


def _idle_job(job_id: str) -> dict:
    status = _status_or_404(job_id)
    if status["state"] in ("queued", "running") or jobs.is_running(job_id):
        raise HTTPException(409, "This job is already queued or running.")
    return status


@app.post("/api/jobs/{job_id}/retry")
def retry_job(job_id: str) -> dict:
    """Resume a failed job from the stage that failed."""
    status = _idle_job(job_id)
    if status["state"] != "error":
        raise HTTPException(409, "Only failed jobs can be retried.")
    failed = status.get("stage") or next((k for k, v in status["stages"].items() if v["state"] != "done"), "download")
    return jobs.requeue(job_id, [failed])


@app.post("/api/jobs/{job_id}/restyle")
def restyle_job(job_id: str, preset: str = Form(...), position: str = Form(...), erase_subs: str = Form("")) -> dict:
    """Burn the captions again with another style or position, reusing everything else. Turning
    the removal of burned-in subtitles on or off re-cuts the clips first."""
    status = _idle_job(job_id)
    if status["state"] != "done":
        raise HTTPException(409, "Only finished jobs can be restyled.")
    if preset not in PRESETS or position not in POSITIONS:
        raise HTTPException(400, "Unknown caption style or position.")
    kind = status.get("kind", "clips")
    if preset == "none" and kind != "clips":
        raise HTTPException(400, "No captions is available for clip jobs only.")
    erase = _flag(erase_subs)
    if kind == "clips":
        redo = ["cut", "caption"] if erase != bool(status["options"].get("erase_subs")) else ["caption"]
        return jobs.requeue(job_id, redo, {"preset": preset, "position": position, "erase_subs": erase})
    if kind == "subtitles" and "burn" in status["stages"]:
        return jobs.requeue(job_id, ["burn"], {"preset": preset, "position": position, "erase_subs": erase})
    raise HTTPException(409, "This job has no burned-in captions.")


@app.post("/api/jobs/{job_id}/repick")
def repick_job(job_id: str, clips: int = Form(3), length: str = Form("normal"), clip_mode: str = Form("viral"),
               cats: str = Form(""), framing: str = Form("")) -> dict:
    """Pick the moments again with another video type, moment types, count or length. The
    transcript and the measured signals are reused, so only picking and cutting run again."""
    status = _idle_job(job_id)
    if status.get("kind", "clips") != "clips" or status["state"] not in ("done", "error"):
        raise HTTPException(409, "Only finished clip jobs can be re-picked.")
    if not (status.get("stages", {}).get("transcribe", {}).get("state") == "done"):
        raise HTTPException(409, "This job has no transcript yet. Use Retry instead.")
    return jobs.requeue(job_id, ["pick"], _pick_options(clips, length, clip_mode, cats, framing))


@app.post("/api/jobs/{job_id}/voice")
def revoice_job(job_id: str, voice_id: str = Form("", alias="voice"), voice_mode: str = Form("over"),
                hooks: str = Form("{}")) -> dict:
    """Add, change or remove the AI voice intro of a finished clip job. hooks: {"<clip index>": "text"}
    replaces the narrator line of those clips."""
    status = _idle_job(job_id)
    if status.get("kind", "clips") == "voice" and status["state"] in ("done", "error"):
        # the AI voice tool: one video, hooks {"1": "new line"}; an empty line keeps the current one
        opts = _voice_options(voice_id, voice_mode)
        if not opts["voice"]:
            raise HTTPException(400, "Pick a voice.")
        try:
            text = voice.clean_hook(str(json.loads(hooks).get("1", "")))
        except (ValueError, TypeError, AttributeError):
            raise HTTPException(400, "Bad hook list.") from None
        opts["hook"] = text or status.get("hook") or status["options"].get("hook", "")
        return jobs.requeue(job_id, ["voice"], opts)
    if status.get("kind", "clips") != "clips" or status["state"] != "done":
        raise HTTPException(409, "Only finished clip jobs can get a voice intro.")
    opts = _voice_options(voice_id, voice_mode)
    if not opts["voice"]:
        return jobs.remove_voice(job_id)
    try:
        edits = {int(k): voice.clean_hook(str(v)) for k, v in json.loads(hooks).items()}
    except (ValueError, TypeError, AttributeError):
        raise HTTPException(400, "Bad hook list.") from None
    if edits:
        path = jobs.job_dir(job_id) / "moments.json"
        picked = json.loads(path.read_text(encoding="utf-8"))
        for index, text in edits.items():
            if 1 <= index <= len(picked) and text:
                picked[index - 1]["voice_hook"] = text
        path.write_text(json.dumps(picked, ensure_ascii=False, indent=1), encoding="utf-8")
    return jobs.requeue(job_id, ["voice"], opts)


@app.post("/api/jobs/{job_id}/thumbs")
def make_thumbs(job_id: str, ai: str = Form(""), strength: str = Form("normal")) -> dict:
    """Make (or remake, e.g. with the AI redraw) the thumbnails of every clip of a finished job."""
    status = _idle_job(job_id)
    if status.get("kind", "clips") != "clips" or status["state"] != "done":
        raise HTTPException(409, "Only finished clip jobs can get thumbnails.")
    return jobs.requeue(job_id, ["thumbs"], {"thumbs": True, **_redraw_options(_flag(ai), strength)})


@app.post("/api/jobs/{job_id}/thumb")
def edit_thumb(job_id: str, index: int = Form(...), text: str = Form(""), tag: str = Form(""),
               frame: str = Form("same"), marks: str = Form("1"), ai: str = Form("0"),
               strength: str = Form("normal")) -> dict:
    """Redraw one clip's thumbnail with other words, another frame (frame=next) or without the
    arrow. Takes a few seconds, so it runs right away instead of going through the queue."""
    from . import thumbs
    status = _idle_job(job_id)
    if status.get("kind", "clips") != "clips" or status["state"] != "done":
        raise HTTPException(409, "Only finished clip jobs have thumbnails.")
    job = jobs.Job(job_id)
    clips = job.status.get("clips") or []
    if not 1 <= index <= len(clips):
        raise HTTPException(400, "Unknown clip.")
    c = clips[index - 1]
    old = c.get("thumb") or {}
    words = thumbs.clean_text(text, 30) or old.get("text") or c.get("thumb_text") or "WAIT FOR IT..."
    label = thumbs.clean_text(tag, 20)
    frames = old.get("frames")
    pick = (old.get("frame", 0) + 1) if frame == "next" and frames else old.get("frame", 0)
    use_ai = _redraw_options(_flag(ai), strength)["thumbs_ai"]
    out = job.dir / f"{c['name']}_thumb.jpg"
    if use_ai:
        if not frames:
            frames = thumbs.pick_frames(job.dir / c["files"]["clean"], c["duration"], job.log)
        if not jobs.gpu_lock.acquire(blocking=False):
            raise HTTPException(409, "The graphics card is busy with another job or redraw. Try again when it is done.")
        try:
            thumbs.prepare_ai([(job.dir / c["files"]["clean"], out, frames[pick % len(frames)]["time"],
                                thumbs.scene_for(c, job.status.get("title") or ""))], strength, job.log)
        except redraw.RedrawError as exc:
            raise HTTPException(503, str(exc)) from None
        finally:
            jobs.gpu_lock.release()
    rec = thumbs.make(job.dir / c["files"]["clean"], c["duration"], out, words, label,
                      job.log, frame=pick, frames=frames, marks=_flag(marks), ai=use_ai, strength=strength)
    rec["v"] = old.get("v", 0) + 1
    c.update(thumb=rec, thumb_text=words, thumb_tag=label)
    c["files"]["thumb"] = rec["file"]
    path = job.dir / "moments.json"
    picked = json.loads(path.read_text(encoding="utf-8"))
    if index <= len(picked):
        picked[index - 1].update(thumb_text=words, thumb_tag=label)
        path.write_text(json.dumps(picked, ensure_ascii=False, indent=1), encoding="utf-8")
    (job.dir / "bundle.zip").unlink(missing_ok=True)
    job.save()
    return job.status


@app.get("/api/voices/{voice_id}/preview")
def voice_preview(voice_id: str) -> FileResponse:
    """A few seconds of one voice, for the picker. Downloads the model the first time."""
    if voice_id not in voice.VOICE_BY_ID:
        raise HTTPException(404, "Unknown voice.")
    try:
        voice.ensure_model(lambda msg: None)
        path = voice.preview(voice_id, ROOT / "models" / "previews")
    except voice.VoiceError as exc:
        raise HTTPException(503, str(exc)) from None
    return FileResponse(path, media_type="audio/wav", headers={"Cache-Control": "max-age=86400"})


@app.get("/api/jobs/{job_id}/zip")
def zip_job(job_id: str) -> FileResponse:
    """Every result file of a finished job in one zip."""
    status = _status_or_404(job_id)
    if status["state"] != "done":
        raise HTTPException(409, "The job is not finished yet.")
    directory = jobs.job_dir(job_id)
    bundle = directory / "bundle.zip"
    names: list[tuple[str, str]] = []  # (file in folder, name in zip)
    for clip in status.get("clips") or []:
        names += [(n, n) for n in clip["files"].values()]
    names += [(o["file"], o.get("download_name") or o["file"]) for o in status.get("outputs") or []]
    if not bundle.is_file() or bundle.stat().st_mtime < (directory / "status.json").stat().st_mtime:
        tmp = directory / "bundle.zip.tmp"
        with zipfile.ZipFile(tmp, "w", zipfile.ZIP_STORED) as zf:  # media is already compressed
            for name, arcname in names:
                if (directory / name).is_file():
                    zf.write(directory / name, arcname)
        tmp.replace(bundle)
    return FileResponse(bundle, filename=f"{jobs.safe_name(status.get('title'), 'clipforge')} - ClipForge.zip")


# ------------------------------------------------------------------ system

_ffmpeg_version: str | None = None
_whisper: dict | None = None


def _ffmpeg() -> str | None:
    global _ffmpeg_version
    if _ffmpeg_version is None and shutil.which("ffmpeg"):
        proc = subprocess.run(["ffmpeg", "-version"], capture_output=True, text=True,
                              creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        _ffmpeg_version = (proc.stdout.splitlines() or [""])[0].replace("ffmpeg version ", "").split(" Copyright")[0]
    return _ffmpeg_version


def _whisper_settings() -> dict:
    global _whisper
    if _whisper is None:
        try:
            from .transcribe import pick_settings
            model, device, compute = pick_settings()
            _whisper = {"model": model, "device": device, "compute_type": compute}
        except Exception as exc:  # noqa: BLE001 - shown to the user as is
            _whisper = {"error": str(exc)}
    return _whisper


def _version(dist: str) -> str | None:
    try:
        return importlib.metadata.version(dist)
    except importlib.metadata.PackageNotFoundError:
        return None


@app.get("/api/system")
def system() -> dict:
    import yt_dlp

    from .moments import get_provider
    provider, problem = get_provider()
    m = CONFIG["moments"]
    counts: dict[str, int] = {}
    total = 0
    for j in jobs.list_jobs(with_size=True):
        counts[j["kind"]] = counts.get(j["kind"], 0) + 1
        total += j["size"]
    return {
        "ffmpeg": _ffmpeg(),
        "ytdlp": {"running": yt_dlp.version.__version__, "installed": _version("yt-dlp"),
                  "cookies_from_browser": CONFIG["download"].get("cookies_from_browser") or None},
        "whisper": _whisper_settings(),
        "llm": {"provider": m["provider"], "model": m["ollama"]["model"], "url": m["ollama"]["url"],
                "ready": provider is not None, "problem": problem},
        "storage": {"bytes": total, "jobs": counts, "folder": str(JOBS_DIR)},
        "workers": jobs.workers(),
        "voice": voice.system_info(),
        "config_path": str(CONFIG_PATH),
        "python": sys.version.split()[0],
    }


@app.post("/api/system/update-downloader")
def update_downloader() -> dict:
    """pip install -U yt-dlp: sites like Instagram and TikTok change often and break old versions."""
    before = _version("yt-dlp")
    proc = subprocess.run([sys.executable, "-m", "pip", "install", "-U", "yt-dlp", "yt-dlp-ejs"],
                          capture_output=True, text=True, timeout=600,
                          creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    after = _version("yt-dlp")
    if proc.returncode != 0:
        raise HTTPException(500, "Update failed:\n" + "\n".join((proc.stderr or proc.stdout).strip().splitlines()[-6:]))
    return {"before": before, "after": after, "changed": before != after}
