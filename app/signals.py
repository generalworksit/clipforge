"""Measured signals for moment picking: what the transcript alone cannot tell.

- audio: loudness, and sudden jumps in loudness (a scream, a crash, a jump scare)
- video: motion and scene cuts from a fast low-resolution decode (fights, chases, montages)
- viewers: YouTube's "most replayed" curve, the parts people actually rewatch

Everything is cached in the job folder, so retries and re-picks are instant.
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Callable

import numpy as np

from .audio import Loudness

Log = Callable[[str], None]
Progress = Callable[[float, str], None]

FPS = 4            # frames per second sampled for motion
MIN_CUT = 0.2      # scene score a frame needs (at 96x54) to be a cut, if it also stands out locally
SPIKE_LOOKBACK = (8.0, 1.0)  # loudness is compared with the window [t-8 s, t-1 s]


def _flags() -> int:
    return getattr(subprocess, "CREATE_NO_WINDOW", 0)


def scene_scores(src: Path, cache: Path, duration: float, log: Log, progress: Progress) -> np.ndarray:
    """ffmpeg scene-change score (0-1) for FPS frames per second. The decoder skips its loop
    filter and runs in fast mode, which is ~80x real time on a laptop and accurate enough here."""
    if cache.is_file():
        try:
            return np.asarray(json.loads(cache.read_text(encoding="utf-8")), dtype=np.float32)
        except (OSError, ValueError):
            pass
    cmd = ["ffmpeg", "-hide_banner", "-nostats", "-skip_loop_filter", "all", "-flags2", "fast",
           "-i", str(src), "-an", "-sn", "-map", "0:v:0",
           "-vf", f"fps={FPS},scale=96:54,select='gte(scene,0)',metadata=print:file=-", "-f", "null", "-"]
    log("$ " + subprocess.list2cmdline(cmd))
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True,
                            encoding="utf-8", errors="replace", creationflags=_flags())
    scores: list[float] = []
    t = 0.0
    for line in proc.stdout:
        if line.startswith("frame:"):
            for part in line.split():
                if part.startswith("pts_time:"):
                    try:
                        t = float(part[9:])
                    except ValueError:
                        pass
            if duration > 0 and len(scores) % (FPS * 30) == 0:
                progress(min(t / duration, 1.0), f"watching the video {t / duration:.0%}")
        elif "scene_score=" in line:
            try:
                scores.append(float(line.split("=", 1)[1]))
            except ValueError:
                scores.append(0.0)
    proc.wait()
    if proc.returncode != 0 or not scores:
        log(f"scene detection failed (ffmpeg exit {proc.returncode}), motion signals off")
        return np.zeros(0, dtype=np.float32)
    cache.write_text(json.dumps([round(s, 4) for s in scores]), encoding="utf-8")
    return np.asarray(scores, dtype=np.float32)


def youtube_heatmap(job_dir: Path, source: dict, log: Log) -> list[dict]:
    """The "most replayed" curve: [{start_time, end_time, value 0-1}]. Saved by the download
    stage; fetched once for older jobs. Empty for uploads and sites without it."""
    path = job_dir / "heatmap.json"
    if path.is_file():
        try:
            return json.loads(path.read_text(encoding="utf-8")) or []
        except (OSError, ValueError):
            return []
    heat: list[dict] = []
    url = source.get("url") if source.get("type") == "url" else None
    if url and any(k in url for k in ("youtube.com", "youtu.be")):
        try:
            from . import download
            heat = download.heatmap(url, log)
        except Exception as exc:  # noqa: BLE001 - a missing bonus signal must never fail the job
            log(f"most-replayed data unavailable ({exc})")
    path.write_text(json.dumps(heat), encoding="utf-8")
    return heat


class Signals:
    """Per-second signal curves for one video. Every window query returns 0-100."""

    def __init__(self, loudness: Loudness | None, scenes: np.ndarray | None = None,
                 heatmap: list[dict] | None = None, duration: float = 0.0):
        self.loudness = loudness
        self.duration = duration
        # loudness jumps, per 0.5 s step
        self.spike = None
        if loudness is not None:
            db, step = loudness.db, loudness.STEP
            back, near = int(SPIKE_LOOKBACK[0] / step), int(SPIKE_LOOKBACK[1] / step)
            csum = np.concatenate([[0.0], np.cumsum(np.maximum(db, -60.0))])
            idx = np.arange(len(db))
            lo, hi = np.maximum(idx - back, 0), np.maximum(idx - near, 1)
            before = (csum[hi] - csum[lo]) / np.maximum(hi - lo, 1)
            self.spike = np.where(idx > near, db - before, 0.0)
        # motion and cuts
        self.scenes = scenes if scenes is not None and scenes.size else None
        self.cuts: list[float] = []
        if self.scenes is not None:
            # a cut is a frame much more different than the ~2 s around it (a fast pan or a crash is
            # different from frame to frame for a while, a cut only once)
            sc = self.scenes
            kernel = np.ones(2 * FPS * 2 + 1)
            kernel[FPS * 2] = 0
            local = np.convolve(sc, kernel, mode="same") / (kernel.sum())
            is_cut = (sc >= 0.45) | ((sc >= MIN_CUT) & (sc >= 3.0 * local))
            last = -9.0
            for k in np.nonzero(is_cut)[0]:
                t = k / FPS
                if t - last > 0.5:
                    self.cuts.append(round(t, 2))
                    last = t
            self._cut_arr = np.asarray(self.cuts)
            calm = sc[~is_cut]
            self.motion_median = float(np.median(calm)) if calm.size else 0.0
            self._is_cut = is_cut
        # viewers
        self.heat = None
        if heatmap:
            # everyone watches the first seconds, so the start of the curve says nothing: skip it
            skip = max(20.0, (duration or float(heatmap[-1].get("end_time", 0))) * 0.03)
            heatmap = [h for h in heatmap if float(h.get("start_time", 0)) >= skip]
            vals = np.asarray([h.get("value", 0.0) for h in heatmap], dtype=np.float32)
            if vals.size and float(vals.max()) > 0:
                self.heat = [(float(h["start_time"]), float(h["end_time"]), float(v)) for h, v in zip(heatmap, vals)]
                self.heat_median = float(np.median(vals))
                self.heat_max = float(vals.max())

    @property
    def available(self) -> dict:
        return {"audio": self.loudness is not None, "video": self.scenes is not None, "replay": self.heat is not None}

    def energy(self, start: float, end: float) -> float:
        return self.loudness.score(start, end) if self.loudness is not None else 50.0

    def jump(self, start: float, end: float) -> float:
        """Biggest sudden rise in loudness inside the window: ~10 dB is noticeable, 25 dB is a scream."""
        if self.spike is None:
            return 0.0
        step = self.loudness.STEP
        span = self.spike[int(start / step):max(int(end / step), int(start / step) + 1)]
        return float(np.clip((float(span.max(initial=0.0)) - 8.0) * 6.0, 0, 100))

    def motion(self, start: float, end: float) -> float:
        """How much the picture moves compared with this video's calm shots (cuts excluded)."""
        if self.scenes is None:
            return 0.0
        lo, hi = int(start * FPS), max(int(end * FPS), int(start * FPS) + 1)
        span = self.scenes[lo:hi][~self._is_cut[lo:hi]]
        if span.size == 0:
            return 0.0
        ratio = float(np.mean(span)) / max(self.motion_median, 1e-4)
        return float(np.clip((ratio - 1.0) * 40.0 + 30.0, 0, 100))

    def cut_rate(self, start: float, end: float) -> float:
        """Cuts per 10 s, as 0-100 (6 cuts in 10 s, a fast action edit, is 100)."""
        if self.scenes is None or end <= start:
            return 0.0
        n = int(np.count_nonzero((self._cut_arr >= start) & (self._cut_arr < end)))
        return float(np.clip(n / (end - start) * 10.0 / 6.0 * 100.0, 0, 100))

    def replay(self, start: float, end: float) -> float | None:
        """How rewatched the window is: 0 = the least, 100 = the most replayed part of the video."""
        if self.heat is None:
            return None
        total = weight = peak = 0.0
        for a, b, v in self.heat:
            overlap = min(b, end) - max(a, start)
            if overlap > min(2.0, (end - start) / 4):
                total += v * overlap
                weight += overlap
                peak = max(peak, v)
        if weight == 0:
            return None
        v = (total / weight + peak) / 2  # a short peak inside a clip counts, not only the average
        lo = min(self.heat_median, self.heat_max * 0.5)
        return float(np.clip((v - lo) / max(self.heat_max - lo, 1e-6) * 100.0, 0, 100))

    def cuts_between(self, start: float, end: float) -> list[float]:
        return [c for c in self.cuts if start <= c <= end]
