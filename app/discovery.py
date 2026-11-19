"""Public YouTube search using the existing downloader; no private account credentials."""
from __future__ import annotations

import re
import threading
import time

import yt_dlp

_cache: dict[tuple[str, str], tuple[float, dict]] = {}
_lock = threading.Lock()


def search(query: str, sort: str) -> dict:
    key = (query.casefold(), sort)
    with _lock:
        cached = _cache.get(key)
        if cached and time.monotonic() - cached[0] < 300:
            return cached[1]
    opts = {"quiet": True, "no_warnings": True, "skip_download": True, "extract_flat": True,
            "socket_timeout": 15, "retries": 1, "extractor_retries": 1, "ignoreerrors": False}
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            data = ydl.extract_info(f"ytsearch12:{query}", download=False)
    except Exception as exc:
        raise RuntimeError("YouTube search is unavailable. Try again or paste a video link in Clip maker.") from exc
    videos = []
    for item in (data or {}).get("entries") or []:
        if not item or not re.fullmatch(r"[A-Za-z0-9_-]{11}", item.get("id") or ""):
            continue
        if item.get("live_status") == "is_live":
            continue
        videos.append({"id": item["id"], "title": item.get("title") or "Untitled video",
                       "url": f"https://www.youtube.com/watch?v={item['id']}",
                       "thumbnail": f"https://i.ytimg.com/vi/{item['id']}/hqdefault.jpg",
                       "channel": item.get("channel") or item.get("uploader") or "Unknown channel",
                       "views": item.get("view_count"), "duration": item.get("duration")})
    if sort == "views":
        videos.sort(key=lambda v: v["views"] if v["views"] is not None else -1, reverse=True)
    result = {"videos": videos, "source": "YouTube public search",
              "note": "View sort compares these search results only. Views do not predict clip performance."}
    with _lock:
        if len(_cache) >= 40:
            _cache.pop(next(iter(_cache)))
        _cache[key] = (time.monotonic(), result)
    return result
