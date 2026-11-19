"""Local Chrome capture bridge and clean, original-aspect MP4 exports."""
from __future__ import annotations

import asyncio
import hmac
import json
import math
import secrets
import shutil
import tempfile
import threading
import uuid
from pathlib import Path
from urllib.parse import urlparse

from fastapi import HTTPException, UploadFile

from . import jobs, video
from .config import ROOT

TOKEN_PATH = ROOT / ".live-token"
MAX_BYTES = 100 * 1024 * 1024
_token_lock = threading.Lock()
_capture_lock = asyncio.Lock()


def pairing_key() -> str:
    with _token_lock:
        if not TOKEN_PATH.exists():
            TOKEN_PATH.write_text(secrets.token_urlsafe(32), encoding="utf-8")
        return TOKEN_PATH.read_text(encoding="utf-8").strip()


def authorized(header: str) -> bool:
    if not TOKEN_PATH.exists():
        return False
    return hmac.compare_digest(header.encode("utf-8"), ("Bearer " + TOKEN_PATH.read_text(encoding="utf-8").strip()).encode("utf-8"))


def validate(raw: str, count: int) -> dict:
    try:
        if len(raw) > 16000:
            raise ValueError("Metadata is too large.")
        data = json.loads(raw)
        capture_id = str(uuid.UUID(data["id"]))
        url = urlparse(data["source_url"])
        if url.scheme != "https" or url.hostname not in {"twitch.tv", "www.twitch.tv", "youtube.com", "www.youtube.com"} or url.username or url.port:
            raise ValueError("Use a Twitch or YouTube stream.")

        def number(value):
            n = float(value)
            if not math.isfinite(n) or n < 0 or n > 7 * 86400:
                raise ValueError("Invalid capture timing.")
            return n

        start, end, trigger = (number(data[k]) for k in ("start", "end", "trigger"))
        if not 0.1 <= end - start <= 121 or not start <= trigger <= end:
            raise ValueError("Clip must be between 0.1 and 121 seconds and contain the trigger.")
        parts = data["segments"]
        if not isinstance(parts, list) or not 1 <= len(parts) == count <= 40:
            raise ValueError("Segment count does not match the upload.")
        segments = []
        for part in parts:
            a, b = number(part["start"]), number(part["end"])
            if not 0 < b - a <= 15 or (segments and not -0.1 <= a - segments[-1]["end"] <= 1):
                raise ValueError("Capture segments are out of order or interrupted.")
            segments.append({"start": a, "end": b})
        if start < segments[0]["start"] or end > segments[-1]["end"] or segments[-1]["end"] - segments[0]["start"] > 140:
            raise ValueError("Clip exceeds the supplied buffer.")
        detection = None
        if data.get("detection") is not None:
            evidence = data["detection"]
            allowed = {"chat_surge", "reaction_shift", "crowd_surge", "sound_spike"}
            if not isinstance(evidence, dict) or evidence.get("version") != 2 or not isinstance(evidence.get("signals"), list) or not 1 <= len(evidence["signals"]) <= 4 or any(s not in allowed for s in evidence["signals"]):
                raise ValueError("Invalid detection signals.")
            detection = {"version": 2, "signals": evidence["signals"]}
            for key, limit in {"score": 100, "unique": 5000, "reactionShare": 1, "rateLift": 1000}.items():
                value = number(evidence[key])
                if value > limit:
                    raise ValueError("Invalid detection score.")
                detection[key] = value
        return {"id": capture_id, "title": str(data.get("title") or "Live highlight")[:180],
                "source_url": data["source_url"][:2048], "reason": str(data.get("reason") or "Live highlight")[:80],
                "start": start, "end": end, "trigger": trigger, "segments": segments,
                "captured_at": str(data.get("captured_at", ""))[:40], "detection": detection}
    except (KeyError, TypeError, ValueError, AttributeError) as exc:
        raise HTTPException(400, str(exc) or "Invalid capture metadata.") from None


def existing(capture_id: str) -> dict | None:
    for summary in jobs.list_jobs(kind="live"):
        st = jobs.read_status(summary["id"])
        if st and st["source"].get("capture_id") == capture_id:
            if st["source"].get("capture_complete"):
                return st
            # A previous process could have closed between creating the job and moving its files.
            st["source"].pop("capture_id", None)
            st.update(state="error", error="Upload was interrupted. A fresh upload will replace this capture.")
            jobs._write_status(jobs.job_dir(st["id"]), st)
    return None


async def receive(raw: str, files: list[UploadFile]) -> dict:
    meta = validate(raw, len(files))
    # Serialized only while accepting small local captures. A retry reuses the same job.
    async with _capture_lock:
        previous = existing(meta["id"])
        if previous:
            return {"id": previous["id"], "duplicate": True}
        jobs.JOBS_DIR.mkdir(exist_ok=True, parents=True)
        with tempfile.TemporaryDirectory(prefix=".live-upload-", dir=jobs.JOBS_DIR) as tmp:
            folder = Path(tmp)
            total = 0
            for i, upload in enumerate(files):
                target = folder / f"segment-{i:03d}.webm"
                with target.open("wb") as out:
                    first = True
                    while chunk := await upload.read(1024 * 1024):
                        total += len(chunk)
                        if total > MAX_BYTES:
                            raise HTTPException(413, "Capture exceeds 100 MB.")
                        if first and not chunk.startswith(b"\x1a\x45\xdf\xa3"):
                            raise HTTPException(400, "Each segment must be a complete WebM recording.")
                        first = False
                        out.write(chunk)
                    if first:
                        raise HTTPException(400, "An empty segment was uploaded.")
            st = jobs.create("live", {"type": "live", "capture_id": meta["id"], "url": meta["source_url"],
                                      "capture": meta}, {"preset": "none", "framing": "keep"}, meta["title"])
            directory = jobs.job_dir(st["id"])
            try:
                for part in folder.iterdir():
                    shutil.move(str(part), str(directory / part.name))
                st["source"]["capture_complete"] = True
                jobs._write_status(directory, st)
            except OSError as exc:
                st.update(state="error", error=f"Capture could not be stored: {exc}")
                # Do not acknowledge an incomplete upload; the same UUID can be retried.
                st["source"].pop("capture_id", None)
                jobs._write_status(directory, st)
                raise HTTPException(500, "Could not store clip. Retry the upload.") from exc
            jobs.submit(st["id"])
            return {"id": st["id"], "duplicate": False}


def render(job: jobs.Job) -> None:
    meta = job.status["source"]["capture"]
    duration = meta["end"] - meta["start"]
    with job.stage("render"):
        lines = ["ffconcat version 1.0"]
        for i, part in enumerate(meta["segments"]):
            # Paths are generated locally, never taken from extension metadata.
            lines.extend([f"file 'segment-{i:03d}.webm'", f"duration {part['end'] - part['start']:.6f}"])
        (job.dir / "capture.ffconcat").write_text("\n".join(lines) + "\n", encoding="utf-8")
        out = job.dir / "highlight.mp4"
        video.run_ffmpeg(["-y", "-protocol_whitelist", "file,pipe", "-f", "concat", "-safe", "1", "-i", "capture.ffconcat",
                          "-ss", f"{meta['start'] - meta['segments'][0]['start']:.6f}", "-t", f"{duration:.6f}",
                          "-map", "0:v:0", "-map", "0:a:0?", "-vf", "fps=30,scale=trunc(iw/2)*2:trunc(ih/2)*2,setsar=1",
                          "-af", "aresample=async=1:first_pts=0", "-c:v", "libx264", "-preset", "veryfast", "-crf", "19",
                          "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", out.name],
                         job.log, cwd=job.dir, duration=duration, progress=job.progress)
        info = video.probe(out)
        if not info.has_video or info.duration < 0.1:
            raise RuntimeError("Capture contains no playable video.")
        job.status["source"].update(duration=info.duration, width=info.width, height=info.height)
        job.output(out, "Clean MP4 · CapCut ready", "video", duration=info.duration, width=info.width, height=info.height,
                   reason=meta["reason"], detection=meta.get("detection"))
    jobs._finish(job)
