"""yt-dlp wrapper: link previews, the downloader tool, and fetching sources for the other tools."""
from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Callable

import yt_dlp

from .config import CONFIG

QUALITIES = ["best", "2160", "1440", "1080", "720", "480", "360"]
MAX_ITEMS = 20  # playlists and multi-video posts (Instagram carousels) are capped
SKIP_SUFFIXES = {".part", ".ytdl", ".json", ".jpg", ".jpeg", ".png", ".webp", ".vtt", ".srt"}


class DownloadError(RuntimeError):
    pass


class _YtdlpLogger:
    def __init__(self, log: Callable[[str], None]):
        self.log = log

    def debug(self, msg: str) -> None:
        if not msg.startswith("[download]"):  # skip per-chunk progress spam
            self.log("yt-dlp: " + msg)

    def info(self, msg: str) -> None:
        self.debug(msg)

    def warning(self, msg: str) -> None:
        self.log("yt-dlp WARNING: " + msg)

    def error(self, msg: str) -> None:
        self.log("yt-dlp ERROR: " + msg)


def _base_opts(log: Callable[[str], None]) -> dict:
    opts = {
        "quiet": True,
        "noprogress": True,
        "logger": _YtdlpLogger(log),
        "ffmpeg_location": shutil.which("ffmpeg"),
        # YouTube needs a JS runtime for its signature challenges; use whichever is installed.
        "js_runtimes": {"deno": {}, "node": {}},
        "socket_timeout": 30,
    }
    browser = (CONFIG["download"].get("cookies_from_browser") or "").strip()
    if browser:  # lets yt-dlp use the browser's logins (Instagram, age-restricted YouTube...)
        opts["cookiesfrombrowser"] = (browser,)
    return opts


def _friendly(exc: Exception) -> DownloadError:
    msg = str(exc).removeprefix("ERROR: ")
    low = msg.lower()
    hint = ""
    if any(k in low for k in ("ip address is blocked", "in your country", "geo-restrict", "geo restrict")):
        hint = "\n\nThe uploader or the site blocks this video in your region. Nothing on this side can fix that."
    elif any(k in low for k in ("login", "log in", "sign in", "cookies", "private", "rate-limit", "not available")):
        hint = ("\n\nThis site wants a logged-in browser. Set download.cookies_from_browser in config.yaml "
                "(firefox works best on Windows; Chrome must be closed), or update the downloader on the "
                "System page.")
    elif "unsupported url" in low:
        hint = "\n\nThis site is not supported, or the link is not a video page."
    elif any(k in low for k in ("challenge", "signature", "403", "forbidden", "report this issue", "impersonat")):
        hint = "\n\nThe site changed something. Update the downloader on the System page and try again."
    return DownloadError(f"Could not download: {msg}{hint}")


def probe_url(url: str) -> dict:
    """Title, thumbnail and available qualities of a link, without downloading it."""
    opts = _base_opts(lambda _m: None) | {"skip_download": True, "noplaylist": True,
                                         "playlistend": MAX_ITEMS, "extract_flat": "in_playlist"}
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=False)
    except yt_dlp.utils.DownloadError as exc:
        raise _friendly(exc) from exc
    entries = [e for e in (info.get("entries") or []) if e] if info.get("_type") == "playlist" else []
    first = entries[0] if entries else info
    heights = sorted({int(f["height"]) for f in (first.get("formats") or [])
                      if f.get("height") and f.get("vcodec") not in (None, "none")}, reverse=True)
    thumb = first.get("thumbnail") or info.get("thumbnail")
    if not thumb and first.get("thumbnails"):
        thumb = first["thumbnails"][-1].get("url")
    return {
        "title": info.get("title") or first.get("title") or url,
        "uploader": info.get("uploader") or info.get("channel") or first.get("uploader"),
        "duration": first.get("duration"),
        "thumbnail": thumb,
        "site": info.get("extractor_key") or info.get("extractor"),
        "url": info.get("webpage_url") or url,
        "heights": heights,
        "items": len(entries) if entries else 1,
    }


def _collect(info: dict, job_dir: Path, prefix: str) -> list[Path]:
    found: list[Path] = []
    for entry in (info.get("entries") or [info]):
        for item in (entry or {}).get("requested_downloads") or []:
            path = Path(item.get("filepath") or "")
            if path.is_file() and path not in found:
                found.append(path)
    if not found:
        found = sorted(p for p in job_dir.glob(prefix + "*")
                       if p.is_file() and p.suffix.lower() not in SKIP_SUFFIXES)
    return found


def fetch(url: str, job_dir: Path, log: Callable[[str], None], progress: Callable[[float, str], None],
          mode: str = "video", quality: str = "best", compatible: bool = True,
          prefix: str = "media", allow_playlist: bool = True) -> tuple[list[Path], dict]:
    """Download a link into job_dir. mode is "video" (mp4) or "audio" (mp3).
    Returns (files, info). Several files come back for playlists and carousels."""
    state = {"item": 1}

    def hook(d: dict) -> None:
        if d.get("status") == "downloading":
            total = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
            done = d.get("downloaded_bytes") or 0
            index = (d.get("info_dict") or {}).get("playlist_index") or 1
            state["item"] = index
            count = (d.get("info_dict") or {}).get("n_entries")
            label = f"item {index} of {count}, " if count and count > 1 else ""
            speed = d.get("speed")
            rate = f", {speed / 1e6:.1f} MB/s" if speed else ""
            if total:
                progress(min(done / total, 1.0), f"{label}{done / 1e6:.0f} / {total / 1e6:.0f} MB{rate}")
        elif d.get("status") == "finished":
            progress(1.0, "merging")

    opts = _base_opts(log) | {
        "outtmpl": str(job_dir / f"{prefix}%(playlist_index&_{{}}|)s.%(ext)s"),
        "noplaylist": not allow_playlist,
        "playlistend": MAX_ITEMS,
        "progress_hooks": [hook],
        "overwrites": True,
    }
    if mode == "audio":
        opts["format"] = "bestaudio/best"
        opts["postprocessors"] = [{"key": "FFmpegExtractAudio", "preferredcodec": "mp3", "preferredquality": "192"}]
    else:
        h = "" if quality == "best" else f"[height<={int(quality)}]"
        opts["format"] = f"bv*{h}+ba/b{h}/bv*+ba/b"
        opts["merge_output_format"] = "mp4"
        if compatible:  # H.264 + AAC plays everywhere (phones, editors, Windows); VP9/AV1 often does not
            opts["format_sort"] = ["vcodec:h264", "res", "acodec:m4a"]
    log(f"download {url} mode={mode} quality={quality} compatible={compatible}")
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=True)
    except yt_dlp.utils.DownloadError as exc:
        raise _friendly(exc) from exc
    files = _collect(info, job_dir, prefix)
    if not files:
        raise DownloadError("The downloader finished but no file was written.")
    return files, info


def download_url(url: str, job_dir: Path, log: Callable[[str], None],
                 progress: Callable[[float, str], None]) -> tuple[Path, str]:
    """Source video for the clip, subtitle and convert tools: one mp4, up to download.max_height."""
    files, info = fetch(url, job_dir, log, progress, mode="video",
                        quality=str(CONFIG["download"]["max_height"]), compatible=True,
                        prefix="source", allow_playlist=False)
    if info.get("_type") == "playlist" and info.get("entries"):
        info = next(e for e in info["entries"] if e)
    # YouTube's "most replayed" curve: the best evidence of which parts viewers loved
    (job_dir / "heatmap.json").write_text(json.dumps(_heat(info)), encoding="utf-8")
    return files[0], info.get("title") or url


def _heat(info: dict) -> list[dict]:
    return [{k: h[k] for k in ("start_time", "end_time", "value")} for h in (info.get("heatmap") or [])
            if all(k in h for k in ("start_time", "end_time", "value"))]


def heatmap(url: str, log: Callable[[str], None]) -> list[dict]:
    """The most-replayed curve of a video without downloading it (for jobs made before it was saved)."""
    opts = _base_opts(log) | {"skip_download": True, "noplaylist": True}
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=False)
    except yt_dlp.utils.DownloadError as exc:
        raise _friendly(exc) from exc
    return _heat(info)
