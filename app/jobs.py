"""Job folders, status.json, and the background workers that run each tool.

Every tool (clips, download, subtitles, convert) is a job: a folder in jobs/ with a status.json.
Heavy jobs (GPU, long encodes) run one at a time on the main worker; downloads only use the
network, so they have their own worker and never wait behind a long clip job.
"""
from __future__ import annotations

import json
import os
import queue
import re
import shutil
import sys
import threading
import time
import traceback
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from .config import JOBS_DIR, ROOT, font_path

FONTS_DIR = ROOT / "assets" / "fonts"

CLIP_STAGES = [
    ("download", "Getting the video"),
    ("transcribe", "Transcribing"),
    ("pick", "Picking moments"),
    ("posts", "Writing titles"),
    ("cut", "Cutting & reframing"),
    ("caption", "Captioning"),
    ("voice", "AI voice intro"),
    ("thumbs", "Making thumbnails"),
]
KINDS = {
    "autoclip": {"label": "Auto clips", "queue": "heavy", "stages": [("extract", "Saving the moment"), ("transcribe", "Generating subtitles"), ("export", "Formatting the clip")]},
    "live": {"label": "Live clips", "queue": "light", "stages": [("render", "Preparing clean MP4")]},
    "clips": {"label": "Clips", "queue": "heavy", "stages": CLIP_STAGES},
    "download": {"label": "Download", "queue": "light", "stages": [("download", "Downloading")]},
    "subtitles": {"label": "Subtitles", "queue": "heavy", "stages": [
        ("download", "Getting the video"), ("transcribe", "Transcribing"),
        ("export", "Writing subtitles"), ("burn", "Burning captions")]},
    "convert": {"label": "Convert", "queue": "heavy", "stages": [
        ("download", "Getting the video"), ("convert", "Converting")]},
    "voice": {"label": "AI voice", "queue": "heavy", "stages": [
        ("download", "Getting the video"), ("transcribe", "Listening"), ("hook", "Writing the narrator line"),
        ("voice", "Adding the AI voice")]},
}
ID_RE = re.compile(r"^[a-f0-9]{12}$")

_queues: dict[str, "queue.Queue[str]"] = {"heavy": queue.Queue(), "light": queue.Queue()}
_write_lock = threading.Lock()
_running: dict[str, str] = {}  # queue name -> job id
gpu_lock = threading.Lock()  # held by the heavy worker during a job, and by an AI redraw from the results page


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def job_dir(job_id: str) -> Path:
    if not ID_RE.match(job_id):
        raise ValueError("invalid job id")
    return JOBS_DIR / job_id


def stage_list(kind: str, options: dict) -> list[tuple[str, str]]:
    stages = KINDS[kind]["stages"]
    if kind == "subtitles" and not options.get("burn"):
        stages = [s for s in stages if s[0] != "burn"]
    if kind == "clips" and not options.get("voice"):
        stages = [s for s in stages if s[0] != "voice"]
    if kind == "clips" and not options.get("thumbs"):
        stages = [s for s in stages if s[0] != "thumbs"]
    if kind == "voice" and options.get("hook"):  # the line is typed in: nothing to listen to or write
        stages = [s for s in stages if s[0] not in ("transcribe", "hook")]
    return stages


def install_fonts(directory: Path) -> None:
    """Every caption font next to the job's .ass files (ffmpeg's subtitles filter reads fonts/)."""
    fonts = directory / "fonts"
    fonts.mkdir(exist_ok=True)
    for font in {font_path(), *FONTS_DIR.glob("*.ttf"), *FONTS_DIR.glob("*.otf")}:
        if not (fonts / font.name).is_file():
            shutil.copy2(font, fonts / font.name)


def safe_name(title: str, fallback: str = "video") -> str:
    """A file name Windows, macOS and Linux all accept."""
    name = re.sub(r'[\\/:*?"<>|\x00-\x1f]+', " ", title or "")
    name = re.sub(r"\s+", " ", name).strip(" .")[:90]
    return name or fallback


def read_status(job_id: str) -> dict | None:
    path = job_dir(job_id) / "status.json"
    for _ in range(20):  # the worker may be swapping the file in right now
        if not path.exists():
            return None
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            time.sleep(0.03)
    return None


def _write_status(directory: Path, status: dict) -> None:
    tmp = directory / "status.json.tmp"
    with _write_lock:
        tmp.write_text(json.dumps(status, ensure_ascii=False, indent=1), encoding="utf-8")
        for attempt in range(40):
            try:
                os.replace(tmp, directory / "status.json")
                return
            except PermissionError:  # Windows: a reader has the file open for a moment
                time.sleep(0.025)
        raise PermissionError(f"could not update {directory / 'status.json'}")


def create(kind: str, source: dict, options: dict, title: str | None = None, *, state: str = "queued") -> dict:
    job_id = uuid.uuid4().hex[:12]
    directory = JOBS_DIR / job_id
    directory.mkdir(parents=True)
    status = {
        "id": job_id, "kind": kind, "created": now(), "state": state,
        "title": title or source.get("url") or source.get("filename"),
        "source": source, "options": options, "stage": None, "error": None, "picker": None,
        "stages": {key: {"label": label, "state": "pending", "progress": 0.0, "detail": "",
                         "started": None, "ended": None} for key, label in stage_list(kind, options)},
        "clips": [], "outputs": [], "media": {},
    }
    _write_status(directory, status)
    return status


def submit(job_id: str) -> None:
    st = read_status(job_id) or {}
    _queues[KINDS[st.get("kind", "clips")]["queue"]].put(job_id)


def _dir_size(directory: Path) -> int:
    total = 0
    for entry in os.scandir(directory):
        if entry.is_file(follow_symlinks=False):
            total += entry.stat().st_size
        elif entry.is_dir(follow_symlinks=False):
            total += _dir_size(Path(entry.path))
    return total


def list_jobs(kind: str | None = None, with_size: bool = False) -> list[dict]:
    out = []
    if JOBS_DIR.exists():
        for d in JOBS_DIR.iterdir():
            if not (d.is_dir() and ID_RE.match(d.name)):
                continue
            st = read_status(d.name)
            if not st or (kind and st.get("kind", "clips") != kind):
                continue
            item = {k: st.get(k) for k in ("id", "created", "state", "title", "stage", "error", "options",
                                          "finished")}
            item.update(kind=st.get("kind", "clips"), clip_count=len(st.get("clips") or []),
                        outputs=st.get("outputs") or [], thumbnail=(st.get("media") or {}).get("thumbnail"),
                        duration=(st.get("source") or {}).get("duration") or (st.get("media") or {}).get("duration"))
            if st.get("stage") and st["state"] == "running":
                item["progress"] = st["stages"][st["stage"]]["progress"]
                item["detail"] = st["stages"][st["stage"]]["detail"]
            if with_size:
                item["size"] = _dir_size(d)
            out.append(item)
    return sorted(out, key=lambda j: j["created"], reverse=True)


def queue_position(job_id: str) -> int | None:
    for q in _queues.values():
        pending = list(q.queue)
        if job_id in pending:
            return pending.index(job_id) + 1
    return None


def is_running(job_id: str) -> bool:
    return job_id in _running.values()


def workers() -> dict:
    return {name: {"running": _running.get(name), "waiting": q.qsize()} for name, q in _queues.items()}


def delete(job_id: str) -> None:
    directory = job_dir(job_id)
    shutil.rmtree(directory)  # a queued job is skipped by the worker once its folder is gone


def link_or_copy(src: Path, dst: Path) -> None:
    """Hard link when possible (instant, no extra disk space), copy otherwise."""
    try:
        os.link(src, dst)
    except OSError:
        shutil.copy2(src, dst)


# ----------------------------------------------------------------- running

class Job:
    def __init__(self, job_id: str):
        self.id = job_id
        self.dir = job_dir(job_id)
        self.status = read_status(job_id)
        self.status.setdefault("kind", "clips")
        self.status.setdefault("outputs", [])
        self.status.setdefault("media", {})
        self.stages = stage_list(self.status["kind"], self.status["options"])
        self._last_save = 0.0
        for key, label in self.stages:  # jobs created by older versions lack newer stages
            self.status["stages"].setdefault(key, {"label": label, "state": "pending", "progress": 0.0,
                                                   "detail": "", "started": None, "ended": None})
        self.status["stages"] = {key: self.status["stages"][key] for key, _ in self.stages}

    def done(self, key: str) -> bool:
        return self.status["stages"][key]["state"] == "done"

    def save(self, force: bool = True) -> None:
        if force or time.monotonic() - self._last_save > 0.5:
            _write_status(self.dir, self.status)
            self._last_save = time.monotonic()

    def log(self, msg: str) -> None:
        stamp = datetime.now().strftime("%H:%M:%S")
        with open(self.dir / "log.txt", "a", encoding="utf-8") as f:
            f.write(f"[{stamp}] {msg}\n")

    def progress(self, frac: float, detail: str = "") -> None:
        st = self.status["stages"][self.status["stage"]]
        st["progress"] = round(max(0.0, min(frac, 1.0)), 3)
        if detail:
            st["detail"] = detail
        self.save(force=False)

    def output(self, path: Path, label: str, media: str, suffix: str = "", **extra) -> dict:
        """Register a finished file; download_name is what the browser saves it as."""
        out = {"file": path.name, "label": label, "media": media, "size": path.stat().st_size,
               "download_name": f"{safe_name(self.status.get('title'))}{suffix}{path.suffix}"} | extra
        self.status["outputs"] = [o for o in self.status["outputs"] if o["file"] != path.name] + [out]
        return out

    @contextmanager
    def stage(self, key: str):
        st = self.status["stages"][key]
        self.status["stage"] = key
        st.update(state="running", started=now(), progress=0.0)
        self.save()
        self.log(f"=== stage {key} ===")
        try:
            yield
        except Exception:
            st.update(state="error", ended=now())
            raise
        st.update(state="done", ended=now(), progress=1.0)
        self.save()


def _source(job: Job, wav: bool, need_video: bool = True, need_audio: bool = True):
    """The "download" stage shared by clips, subtitles and convert: fetch a link (an uploaded or
    linked file is already in the folder), check its streams and optionally extract the WAV."""
    from . import download, video
    s = job.status
    src = job.dir / s["source"].get("stored_as", "")
    if job.done("download") and src.is_file() and (not wav or (job.dir / "audio.wav").is_file()):
        return src, video.probe(src)
    with job.stage("download"):
        if s["source"]["type"] == "url":
            src, title = download.download_url(s["source"]["url"], job.dir, job.log, job.progress)
            s["title"] = title
        info = video.probe(src)
        if need_video and not info.has_video:
            raise RuntimeError("This file has no video stream.")
        if need_audio and not info.has_audio:
            raise RuntimeError("This video has no audio track, so it cannot be transcribed.")
        s["source"].update(stored_as=src.name, duration=round(info.duration, 2), width=info.width,
                           height=info.height, has_video=info.has_video, has_audio=info.has_audio)
        job.log(f"source {src.name}: {info}")
        if wav:
            job.progress(0.95, "extracting audio")
            video.extract_audio(src, job.dir / "audio.wav", job.log)
        s["stages"]["download"]["detail"] = (f"{info.width}×{info.height}, " if info.has_video else "") + \
            f"{info.duration / 60:.1f} min"
    return src, info


def _finish(job: Job) -> None:
    s = job.status
    s.update(state="done", stage=None, finished=now())
    job.save()
    job.log("job done")


def run_download(job: Job) -> None:
    from . import download, video
    s, o = job.status, job.status["options"]
    with job.stage("download"):
        files, info = download.fetch(s["source"]["url"], job.dir, job.log, job.progress, mode=o["mode"],
                                     quality=o["quality"], compatible=o.get("compatible", True))
        first = next((e for e in (info.get("entries") or []) if e), info)
        s["title"] = info.get("title") or first.get("title") or s["title"]
        s["media"] = {
            "uploader": info.get("uploader") or info.get("channel") or first.get("uploader"),
            "site": info.get("extractor_key") or info.get("extractor"),
            "url": info.get("webpage_url") or s["source"]["url"],
            "thumbnail": first.get("thumbnail") or info.get("thumbnail"),
            "duration": first.get("duration"),
        }
        s["outputs"] = []
        for i, path in enumerate(files, 1):
            mi = video.probe(path)
            kind = "video" if mi.has_video else "audio"
            label = ("MP4 video" if kind == "video" else "MP3 audio") + (f", part {i}" if len(files) > 1 else "")
            job.output(path, label, kind, f" ({i})" if len(files) > 1 else "", duration=round(mi.duration, 2),
                       width=mi.width, height=mi.height)
        total = sum(o["size"] for o in s["outputs"])
        s["stages"]["download"]["detail"] = f"{len(files)} file{'s' if len(files) > 1 else ''}, {total / 1e6:.0f} MB"
    _finish(job)


def run_subtitles(job: Job) -> None:
    from . import captions, moments, transcribe, video
    s, o = job.status, job.status["options"]
    src, info = _source(job, wav=True, need_video=False)

    transcript_path = job.dir / "transcript.json"
    if job.done("transcribe") and transcript_path.is_file():
        transcript = json.loads(transcript_path.read_text(encoding="utf-8"))
    else:
        with job.stage("transcribe"):
            task = "translate" if o.get("translate") else "transcribe"
            transcript = transcribe.transcribe(job.dir / "audio.wav", transcript_path, info.duration,
                                               job.log, job.progress, task=task)
            s["language"] = transcript["language"]
            s["stages"]["transcribe"]["detail"] = (f"{transcript['model']} on {transcript['device']}, "
                                                   f"{transcript['language']}" +
                                                   (" translated to English" if task == "translate" else ""))

    words = moments.all_words(transcript)
    with job.stage("export"):
        s["outputs"] = [x for x in s["outputs"] if x["file"] == "captioned.mp4"]
        files = [("subtitles.srt", captions.build_srt(words, info.duration), "Subtitles (SRT)"),
                 ("subtitles.vtt", captions.build_vtt(words, info.duration), "Subtitles (VTT)"),
                 ("transcript.txt", captions.build_txt(transcript["segments"]), "Transcript (TXT)")]
        for name, text, label in files:
            (job.dir / name).write_text(text, encoding="utf-8")
            job.output(job.dir / name, label, "text")
        s["stages"]["export"]["detail"] = f"{len(words)} words"

    if "burn" in s["stages"]:
        with job.stage("burn"):
            if not info.has_video:
                s["stages"]["burn"]["detail"] = "skipped, the file has no video"
            else:
                install_fonts(job.dir)
                ass = captions.build_ass(words, info.duration, o["preset"], o["position"],
                                         size=(info.width, info.height))
                (job.dir / "captions.ass").write_text(ass, encoding="utf-8")
                pre = ""
                if o.get("erase_subs"):
                    from . import hardsubs
                    job.progress(0.01, "looking for burned-in subtitles")
                    box = hardsubs.find_band(src, info.width, info.height, 0.0, info.duration, job.log)
                    pre = hardsubs.erase_chain(box) if box else ""
                video.burn_captions(job.dir, src.name, "captions.ass", "captioned.mp4", job.log,
                                    duration=info.duration, progress=job.progress, copy_audio=False,
                                    pre_filter=pre)
                out = job.output(job.dir / "captioned.mp4", "Video with captions", "video", " (captions)")
                s["outputs"].insert(0, s["outputs"].pop(s["outputs"].index(out)))
                s["stages"]["burn"]["detail"] = f"{captions.PRESETS[o['preset']]['label']} style"
    _finish(job)


def run_convert(job: Job) -> None:
    from . import convert
    s, o = job.status, job.status["options"]
    src, info = _source(job, wav=False, need_video=convert.OPS[o["op"]]["needs_video"], need_audio=False)
    with job.stage("convert"):
        convert.validate(o["op"], o, info)
        name, detail = convert.run(o["op"], o, src, info, job.dir, job.log, job.progress)
        path = job.dir / name
        media = {".mp3": "audio", ".gif": "image"}.get(path.suffix, "video")
        suffix = {"mp3": "", "compress": " (compressed)", "vertical": " (vertical)", "trim": " (trimmed)",
                  "gif": ""}[o["op"]]
        s["outputs"] = []
        job.output(path, convert.OPS[o["op"]]["label"], media, suffix, source_size=src.stat().st_size)
        s["stages"]["convert"]["detail"] = detail
    _finish(job)


def run_clips(job: Job) -> None:
    """Run every stage that is not already done, so retrying a failed job or changing the
    caption style resumes where it makes sense instead of starting over."""
    # Imported here so the web server starts fast and the CUDA libraries load in the worker only.
    from . import audio, captions, moments, posts, transcribe, video

    s = job.status
    opts = s["options"]
    src, info = _source(job, wav=True)

    transcript_path = job.dir / "transcript.json"
    if job.done("transcribe") and transcript_path.is_file():
        transcript = json.loads(transcript_path.read_text(encoding="utf-8"))
    else:
        with job.stage("transcribe"):
            transcript = transcribe.transcribe(job.dir / "audio.wav", transcript_path,
                                               info.duration, job.log, job.progress)
            s["language"] = transcript["language"]
            s["stages"]["transcribe"]["detail"] = (f"{transcript['model']} on {transcript['device']}, "
                                                   f"language {transcript['language']}")

    moments_path = job.dir / "moments.json"
    picked = json.loads(moments_path.read_text(encoding="utf-8")) if moments_path.is_file() else None
    need_pick = not (job.done("pick") and picked)
    need_posts = need_pick or not (job.done("posts") and all("post" in m for m in picked))
    provider = problem = None
    if need_pick or need_posts:
        provider, problem = moments.get_provider()
    try:
        if need_pick:
            with job.stage("pick"):
                sig = _signals(job, src, info)
                picked, picker = moments.pick_moments(transcript, int(opts["clips"]), job.log, job.progress,
                                                      sig=sig, provider=provider, provider_problem=problem,
                                                      length=opts.get("length", "normal"),
                                                      mode=opts.get("mode", "viral"), cats=opts.get("cats"))
                if not picked:
                    raise RuntimeError("No usable moment was found in this video.")
                s["picker"] = picker
                s["stages"]["pick"]["detail"] = f"{len(picked)} moment(s), {picker}"
                moments_path.write_text(json.dumps(picked, ensure_ascii=False, indent=1), encoding="utf-8")
                for key in ("posts", "cut", "caption", "thumbs"):  # new moments invalidate everything after
                    if key in s["stages"]:
                        s["stages"][key].update(state="pending", progress=0.0, detail="")

        if need_posts:
            with job.stage("posts"):
                method = posts.write_posts(picked, transcript["language"], provider, job.log, job.progress,
                                           video_title=s.get("title") or "")
                if opts.get("thumbs") and provider is not None:  # the model is loaded now: write the cover words too
                    from . import thumbs
                    job.progress(0.8, "words for the thumbnails")
                    thumbs.write_texts(picked, transcript["language"], provider, job.log, s.get("title") or "")
                s["stages"]["posts"]["detail"] = f"{len(picked)} post(s), {method}"
                moments_path.write_text(json.dumps(picked, ensure_ascii=False, indent=1), encoding="utf-8")
    finally:
        if provider is not None:
            provider.release()
    for i, m in enumerate(picked, 1):
        (job.dir / f"clip_{i:02d}.txt").write_text(posts.post_text(m["post"]), encoding="utf-8")

    clips = s.get("clips") or []
    if not (job.done("cut") and len(clips) == len(picked)
            and all((job.dir / c["files"]["clean"]).is_file() for c in clips)):
        clips = []
        with job.stage("cut"):
            for i, m in enumerate(picked, 1):
                job.progress((i - 1) / len(picked), f"clip {i} of {len(picked)}")
                name = f"clip_{i:02d}"
                hidden = video.cut_clip(src, info, m["start"], m["end"], job.dir / f"{name}_clean.mp4", job.log,
                                        framing=opts.get("framing", "crop"), erase_subs=bool(opts.get("erase_subs")))
                clips.append({"index": i, "name": name, "files": {"clean": f"{name}_clean.mp4"},
                              "erased_subs": bool(hidden)})
            for key in ("caption", "thumbs"):
                if key in s["stages"]:
                    s["stages"][key].update(state="pending", progress=0.0, detail="")
    for c, m in zip(clips, picked):  # refresh the moment data shown on the results page
        c.update({k: m.get(k) for k in ("title", "hook", "score", "reason", "source", "start", "end", "duration",
                                        "text", "rubric", "signals", "llm_score", "post", "voice_hook", "tag",
                                        "thumb_text", "thumb_tag", "thumb_scene")})
        c["files"]["txt"] = f"{c['name']}.txt"
    s["clips"] = clips

    captioned = job.done("caption") and all(
        c["files"].get("captioned") and (job.dir / c["files"]["captioned"]).is_file() for c in clips)
    if not captioned:  # done already when only the voice intro is being redone
        with job.stage("caption"):
            install_fonts(job.dir)
            words = moments.all_words(transcript)
            s["stages"]["caption"]["detail"] = f"{captions.PRESETS[opts['preset']]['label']} style, {opts['position']}"
            for i, c in enumerate(clips, 1):
                job.progress((i - 1) / len(clips), f"clip {i} of {len(clips)}")
                ass, srt = f"{c['name']}.ass", f"{c['name']}.srt"
                count = captions.write_caption_files(words, c["start"], c["end"], opts["preset"], opts["position"],
                                                     job.dir / ass, job.dir / srt)
                job.log(f"{c['name']}: {count} caption words")
                from .effects import light_filter
                light = light_filter(opts.get("effect", "none"), opts.get("effect_intensity", 0.5),
                                     captions.clip_words(words, c["start"], c["end"]), c["duration"])
                video.burn_captions(job.dir, c["files"]["clean"], ass, f"{c['name']}.mp4", job.log,
                                    pre_filter=light)
                c["files"].update(captioned=f"{c['name']}.mp4", srt=srt)
            s["stages"]["caption"]["detail"] = f"{captions.PRESETS[opts['preset']]['label']} style, {opts['position']}"
            (job.dir / "bundle.zip").unlink(missing_ok=True)  # stale bundle

    for c in clips:  # files of a voice intro that was turned off since
        if not opts.get("voice") and c["files"].pop("voiced", None):
            (job.dir / f"{c['name']}_voice.mp4").unlink(missing_ok=True)
    voiced = "voice" in s["stages"] and job.done("voice") and all(
        c["files"].get("voiced") and (job.dir / c["files"]["voiced"]).is_file() for c in clips)
    if opts.get("voice") and not voiced:  # done already when only the thumbnails are being made
        _voice_stage(job, clips, picked, transcript["language"])
        moments_path.write_text(json.dumps(picked, ensure_ascii=False, indent=1), encoding="utf-8")
    if "thumbs" in s["stages"]:
        _thumbs_stage(job, clips, picked, transcript["language"])
        moments_path.write_text(json.dumps(picked, ensure_ascii=False, indent=1), encoding="utf-8")
    _finish(job)


def _thumbs_stage(job: Job, clips: list[dict], picked: list[dict], language: str) -> None:
    """A cover image per clip. The words come from the posts stage (LLM) or are written here."""
    from . import moments, thumbs
    s = job.status
    if job.done("thumbs") and all(c["files"].get("thumb") and (job.dir / c["files"]["thumb"]).is_file() for c in clips):
        return
    with job.stage("thumbs"):
        ai = bool(s["options"].get("thumbs_ai"))
        method = "AI" if all(m.get("thumb_text") and (m.get("thumb_scene") or not ai) for m in picked) else ""
        if not method:
            provider, problem = moments.get_provider()
            try:
                job.progress(0.02, "writing the words")
                method = thumbs.write_texts(picked, language, provider, job.log, s.get("title") or "")
            finally:
                if provider is not None:
                    provider.release()
        frames = []
        for i, c in enumerate(clips, 1):
            job.progress(0.05 + 0.15 * (i - 1) / len(clips), f"choosing frames, clip {i} of {len(clips)}")
            thumbs.clear_ai(job.dir / f"{c['name']}_thumb.jpg")  # the clips may have been re-cut
            frames.append(thumbs.pick_frames(job.dir / c["files"]["clean"], c["duration"], job.log))
        note = ""
        strength = s["options"].get("thumbs_strength", "normal")
        if ai:
            from . import redraw
            try:
                thumbs.prepare_ai([(job.dir / c["files"]["clean"], job.dir / f"{c['name']}_thumb.jpg", f[0]["time"],
                                    thumbs.scene_for(m, s.get("title") or "")) for c, m, f in zip(clips, picked, frames)],
                                  strength, job.log,
                                  lambda f, d: job.progress(0.2 + 0.7 * f, d))
            except redraw.RedrawError as exc:
                job.log(f"AI redraw skipped: {exc}")
                note = f", AI redraw failed: {exc}"
        for i, (c, m, f) in enumerate(zip(clips, picked, frames), 1):
            job.progress(0.9 + 0.1 * (i - 1) / len(clips), f"drawing, clip {i} of {len(clips)}")
            old = c.get("thumb") or {}
            rec = thumbs.make(job.dir / c["files"]["clean"], c["duration"], job.dir / f"{c['name']}_thumb.jpg",
                              m["thumb_text"], m.get("thumb_tag") or "", job.log, frames=f,
                              marks=old.get("marks", True), ai=ai, strength=strength)
            rec["v"] = old.get("v", 0) + 1
            c.update(thumb=rec, thumb_text=rec["text"], thumb_tag=rec["tag"], thumb_scene=m.get("thumb_scene"))
            c["files"]["thumb"] = rec["file"]
        s["stages"]["thumbs"]["detail"] = (f"{len(clips)} thumbnail(s), words: {'rules' if method == 'rules' else 'AI'}"
                                           + (", AI redraw" if ai and not note else note))
        (job.dir / "bundle.zip").unlink(missing_ok=True)


def _signals(job: Job, src: Path, info):
    """Loudness, motion/scene cuts and YouTube's most-replayed curve for the pick stage. Each part
    is optional: a failure only removes that signal."""
    from . import audio, signals
    loudness = scenes = None
    try:
        loudness = audio.Loudness(job.dir / "audio.wav")
    except Exception as exc:  # noqa: BLE001
        job.log(f"loudness unavailable ({exc})")
    if info.has_video:
        try:
            scenes = signals.scene_scores(src, job.dir / "scenes.json", info.duration, job.log,
                                          lambda f, d: job.progress(f * 0.5, d))
        except Exception as exc:  # noqa: BLE001
            job.log(f"scene detection unavailable ({exc})")
    heat = signals.youtube_heatmap(job.dir, job.status["source"], job.log)
    sig = signals.Signals(loudness, scenes, heat, info.duration)
    job.log(f"signals: {sig.available}, {len(sig.cuts)} scene cuts")
    return sig


def _voice_stage(job: Job, clips: list[dict], picked: list[dict], language: str) -> None:
    from . import voice
    s, opts = job.status, job.status["options"]
    with job.stage("voice"):
        install_fonts(job.dir)
        voice.ensure_model(job.log, job.progress)
        chosen, note = voice.choose_voice(opts["voice"], language)
        if note:
            job.log(f"voice: {note}")
        if chosen is None:
            s["stages"]["voice"]["detail"] = f"skipped, {note}"
            return
        mode = opts.get("voice_mode", "over")
        for i, (c, m) in enumerate(zip(clips, picked), 1):
            job.progress(0.9 + 0.1 * (i - 1) / len(clips), f"clip {i} of {len(clips)}")
            hook = voice.clean_hook(m.get("voice_hook") or "") or voice.default_hook(i, language)
            m["voice_hook"] = c["voice_hook"] = hook
            name, spoken = voice.render(job.dir, c["name"], c["files"]["captioned"], c["files"]["clean"], hook,
                                        chosen, mode, opts["position"], job.log)
            c["files"]["voiced"] = name
        s["stages"]["voice"]["detail"] = f"{chosen['label']}, {voice.MODES[mode].lower()}" + (f" ({note})" if note else "")
        (job.dir / "bundle.zip").unlink(missing_ok=True)


HOOK_LISTEN_SECONDS = 180  # the narrator line is written from the start of the video


def _prepare_for_voice(job: Job, src: Path, info, framing: str) -> tuple[str, tuple[int, int]]:
    """The video the voice goes on, re-encoded to a known format (30 fps, AAC 48 kHz) so the intro
    joins it cleanly: as it is (keep), or made vertical (crop / blur). Cached per framing."""
    from . import video
    from .config import CONFIG
    v = CONFIG["video"]
    vertical = framing in ("crop", "blur", "face", "split") and info.width > info.height  # a wide video made vertical
    if framing in ("crop", "blur", "face", "split"):
        size = (v["width"], v["height"])
    else:
        f = min(1.0, 1920 / max(info.width, info.height))
        size = (int(info.width * f) // 2 * 2, int(info.height * f) // 2 * 2)
    name = f"prepared_{framing}.mp4"
    if (job.dir / name).is_file():
        return name, size
    faced = None
    if framing in ("face", "split") and info.width > info.height:
        from . import faces
        faced = faces.vertical_filter(src, info.width, info.height, 0.0, info.duration, framing, job.log)
    if faced:
        graph = ["-filter_complex", f"[0:v:0]{faced},setsar=1,fps={v['fps']},format=yuv420p[v]", "-map", "[v]"]
    elif framing == "blur" and vertical:
        graph = ["-filter_complex", video.blur_fit_graph(), "-map", "[v]"]
    elif framing != "keep":  # centre crop, or an already vertical video: fill 1080x1920
        graph = ["-map", "0:v:0", "-vf", video.reframe_filter(video.center_crop(info, 0, info.duration))]
    else:
        graph = ["-map", "0:v:0", "-vf", f"scale={size[0]}:{size[1]}:flags=lanczos,setsar=1,fps={v['fps']},format=yuv420p"]
    video.run_ffmpeg(["-y", "-i", str(src), *graph, "-map", "0:a:0", *video._video_codec_args(),
                      *video._aac_args(), "-movflags", "+faststart", name],
                     job.log, cwd=job.dir, duration=info.duration,
                     progress=lambda f, d: job.progress(0.1 + 0.6 * f, "preparing the video " + d), label="")
    return name, size


def run_voice(job: Job) -> None:
    """The AI voice tool: a narrator hook on any video. The line is typed in, or written from what
    is said in the first minutes (Whisper + the LLM, or a generic teaser without an LLM)."""
    from . import moments, posts, transcribe, video, voice
    s, o = job.status, job.status["options"]
    src, info = _source(job, wav="transcribe" in s["stages"])
    hook = voice.clean_hook(o.get("hook") or s.get("hook") or "")
    language = s.get("language")

    if "transcribe" in s["stages"]:
        tpath = job.dir / "transcript.json"
        if job.done("transcribe") and tpath.is_file():
            transcript = json.loads(tpath.read_text(encoding="utf-8"))
        else:
            with job.stage("transcribe"):
                secs = min(info.duration, HOOK_LISTEN_SECONDS)
                video.run_ffmpeg(["-y", "-i", "audio.wav", "-t", f"{secs:.2f}", "-c", "copy", "hook_audio.wav"],
                                 job.log, cwd=job.dir)
                transcript = transcribe.transcribe(job.dir / "hook_audio.wav", tpath, secs, job.log, job.progress)
                s["language"] = transcript["language"]
                s["stages"]["transcribe"]["detail"] = f"{transcript['model']}, language {transcript['language']}"
        language = transcript["language"]
        if not (job.done("hook") and hook):
            with job.stage("hook"):
                provider, problem = moments.get_provider()
                try:
                    said = " ".join(w["text"] for w in moments.all_words(transcript))
                    line, method = posts.write_hook(said, s.get("title") or "", language, provider, job.log)
                finally:
                    if provider is not None:
                        provider.release()
                hook = voice.clean_hook(line) or voice.default_hook(1, language)
                s["hook"] = hook
                s["stages"]["hook"]["detail"] = method if line else f"generic teaser ({problem or 'nothing said'})"

    with job.stage("voice"):
        voice.ensure_model(job.log, lambda f, d: job.progress(0.1 * f, d))
        chosen, note = voice.VOICE_BY_ID[o["voice"]], ""
        if language:
            chosen, note = voice.choose_voice(o["voice"], language)
            if chosen is None:  # a typed line can be in any language: keep the picked voice
                chosen, note = voice.VOICE_BY_ID[o["voice"]], ""
        if note:
            job.log(f"voice: {note}")
        prepared, size = _prepare_for_voice(job, src, info, o.get("framing", "keep"))
        install_fonts(job.dir)
        job.progress(0.75, "speaking")
        name, spoken = voice.render(job.dir, "video", prepared, prepared, hook, chosen, o["voice_mode"],
                                    "bottom", job.log, size)
        s["hook"] = hook
        s["outputs"] = []
        mi = video.probe(job.dir / name)
        job.output(job.dir / name, "Video with AI voice", "video", " (AI voice)", width=size[0], height=size[1],
                   duration=round(mi.duration, 2))
        detail = f"{chosen['label']}, {voice.MODES[o['voice_mode']].lower()}, {spoken:.1f} s"
        s["stages"]["voice"]["detail"] = detail + (f" ({note})" if note else "")
        (job.dir / "bundle.zip").unlink(missing_ok=True)
    _finish(job)


def run_live(job: Job) -> None:
    from .live import render
    render(job)


def run_autoclip(job: Job) -> None:
    from .monitoring.processing import render
    render(job)


RUNNERS = {"clips": run_clips, "download": run_download, "subtitles": run_subtitles, "convert": run_convert,
           "voice": run_voice, "live": run_live, "autoclip": run_autoclip}


def run_job(job: Job) -> None:
    from . import video
    s = job.status
    s.update(state="running", started=now(), error=None)
    job.save()
    job.log(f"job {job.id} kind={s['kind']} options={s['options']} source={s['source']}")
    video.require_binaries()
    RUNNERS[s["kind"]](job)


def requeue(job_id: str, reset: list[str], options: dict | None = None) -> dict:
    """Mark stages as pending (plus anything after the earliest one) and queue the job again."""
    if options:  # first, so a stage the new options add (voice) exists below
        st = read_status(job_id)
        st["options"].update(options)
        _write_status(job_dir(job_id), st)
    job = Job(job_id)
    st = job.status
    keys = [k for k, _ in job.stages]
    first = min(keys.index(k) for k in reset if k in keys) if any(k in keys for k in reset) else len(keys) - 1
    for k in keys[first:]:
        if k in reset or st["stages"][k]["state"] != "done" or k in ("caption", "voice"):
            st["stages"][k].update(state="pending", progress=0.0, detail="", started=None, ended=None)
    st.update(state="queued", error=None, stage=None)
    job.save()
    submit(job_id)
    return st


def remove_voice(job_id: str) -> dict:
    """Turn the voice intro off on a finished clip job: nothing to render, only files to drop."""
    job = Job(job_id)
    job.status["options"]["voice"] = ""
    for c in job.status.get("clips") or []:
        c.pop("voice_hook", None)
        if c["files"].pop("voiced", None):
            for suffix in ("_voice.mp4", "_voice.wav", "_hook.ass"):
                (job.dir / f"{c['name']}{suffix}").unlink(missing_ok=True)
    job.status["stages"].pop("voice", None)
    (job.dir / "bundle.zip").unlink(missing_ok=True)
    job.save()
    return job.status


def _worker(name: str) -> None:
    q = _queues[name]
    while True:
        job_id = q.get()
        try:
            if not job_dir(job_id).exists():
                continue  # deleted while queued
            job = Job(job_id)
            _running[name] = job_id
            try:
                if name == "heavy":
                    with gpu_lock:
                        run_job(job)
                else:
                    run_job(job)
            except Exception as exc:
                job.log("FAILED:\n" + traceback.format_exc())
                job.status.update(state="error", error=str(exc) or exc.__class__.__name__, finished=now())
                if job.dir.exists():
                    job.save()
        except Exception:
            traceback.print_exc()
        finally:
            _running.pop(name, None)
            q.task_done()


class AlreadyRunning(RuntimeError):
    pass


_lock_file = None


def acquire_lock() -> None:
    """Only one ClipForge process may own the jobs folder: a second one would mark the first
    one's running job as interrupted and run queued jobs twice. The OS releases the lock
    automatically when the process exits, even after a crash."""
    global _lock_file
    if _lock_file is not None:
        return
    JOBS_DIR.mkdir(exist_ok=True)
    f = open(JOBS_DIR / ".worker.lock", "a+")
    try:
        if sys.platform == "win32":
            import msvcrt
            f.seek(0)
            msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        f.close()
        raise AlreadyRunning("ClipForge is already running (another window or terminal has it open). "
                             "Use that one, or close it first.") from None
    _lock_file = f


def start_worker() -> None:
    """Recover jobs left over from a previous run, then start the worker threads."""
    acquire_lock()
    for job in sorted(list_jobs(), key=lambda j: j["created"]):
        if job["kind"] == "autoclip" and job["state"] in ("running", "receiving"):
            st = read_status(job["id"])
            complete = bool(st["source"].get("segments"))
            st.update(state="queued" if complete else "error", error=None if complete else "Capture interrupted before its video was saved.")
            _write_status(job_dir(job["id"]), st)
            if complete:
                submit(job["id"])
            continue
        if job["state"] == "running":
            st = read_status(job["id"])
            st.update(state="error", error="The app was closed while this job was running. "
                                           "Click Retry to continue from the step that was interrupted.")
            if st.get("stage"):
                st["stages"][st["stage"]]["state"] = "error"
            _write_status(job_dir(job["id"]), st)
        elif job["state"] == "queued":
            submit(job["id"])
    for name in _queues:
        threading.Thread(target=_worker, args=(name,), name=f"clipforge-{name}", daemon=True).start()
