"""Face-aware vertical framing.

"face": a 9:16 window that stays on whoever is talking. Faces are found with YuNet (a 230 KB
        OpenCV model), tracked across frames, and the speaker is the face whose mouth moves most
        (head movement subtracted). The camera holds still shots and cuts when the speaker changes
        or walks out of frame, like a human editor, instead of panning jerkily.
"split": two people stacked (top: the left one, bottom: the right one), the Omegle / podcast
        layout. Falls back to "face" when there are not two people most of the time.

Both return an ffmpeg filter for the clip, or None when no usable face is found (the caller then
uses the centre crop).
"""
from __future__ import annotations

import hashlib
import os
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

import numpy as np
import requests

from .config import CONFIG, ROOT

Log = Callable[[str], None]
os.environ.setdefault("OPENCV_LOG_LEVEL", "ERROR")  # hide OpenCV's backend notices in the console

MODEL_URL = ("https://media.githubusercontent.com/media/opencv/opencv_zoo/main/models/"
             "face_detection_yunet/face_detection_yunet_2023mar.onnx")
MODEL_SHA256 = "8f2383e4dd3cfbb4553ea8718107fc0423210dc964f9f4280604804ed2552fa4"
MODEL_PATH = ROOT / "models" / "face" / "yunet.onnx"

SAMPLE_FPS = 8        # frames analysed per second
DET_W = 640           # analysis width (height follows the aspect ratio)
MIN_FACE = 0.07       # faces smaller than this share of the frame height are ignored (crowds, posters)
MIN_SHOT = 1.2        # seconds: a speaker change shorter than this is ignored
TRACK_GAP = 2.0       # seconds a face may vanish (turning away, a hand) and keep its track


class FaceError(RuntimeError):
    pass


def ensure_model(log: Log) -> Path:
    if MODEL_PATH.is_file():
        return MODEL_PATH
    log("downloading the face detector (230 KB)")
    MODEL_PATH.parent.mkdir(parents=True, exist_ok=True)
    data = requests.get(MODEL_URL, timeout=60).content
    if hashlib.sha256(data).hexdigest() != MODEL_SHA256:
        raise FaceError("the face detector download is corrupt")
    tmp = MODEL_PATH.with_suffix(".part")
    tmp.write_bytes(data)
    tmp.replace(MODEL_PATH)
    return MODEL_PATH


def available() -> bool:
    try:
        import cv2  # noqa: F401
    except ImportError:
        return False
    return True


# ---------------------------------------------------------------- analysis

@dataclass
class Track:
    id: int
    samples: dict[int, tuple] = field(default_factory=dict)   # sample index -> (cx, cy, w, h) in source px
    activity: dict[int, float] = field(default_factory=dict)  # sample index -> mouth movement
    last_i: int = -99
    last_box: tuple = (0, 0, 0, 0)
    mouth: np.ndarray | None = None
    face: np.ndarray | None = None


def _frames(src: Path, start: float, end: float, dw: int, dh: int):
    """Frames of [start, end] at SAMPLE_FPS, scaled to dw x dh, as BGR arrays."""
    cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-ss", f"{start:.3f}", "-i", str(src),
           "-t", f"{end - start:.3f}", "-an", "-sn", "-map", "0:v:0",
           "-vf", f"fps={SAMPLE_FPS},scale={dw}:{dh}", "-f", "rawvideo", "-pix_fmt", "bgr24", "-"]
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    size = dw * dh * 3
    try:
        while True:
            buf = proc.stdout.read(size)
            if len(buf) < size:
                break
            yield np.frombuffer(buf, np.uint8).reshape(dh, dw, 3)
    finally:
        proc.stdout.close()
        proc.wait()


def _patch(gray: np.ndarray, x0: float, y0: float, x1: float, y1: float, size: tuple[int, int]):
    import cv2
    h, w = gray.shape
    x0, x1 = int(max(0, x0)), int(min(w, x1))
    y0, y1 = int(max(0, y0)), int(min(h, y1))
    if x1 - x0 < 4 or y1 - y0 < 4:
        return None
    return cv2.resize(gray[y0:y1, x0:x1], size, interpolation=cv2.INTER_AREA).astype(np.float32)


def analyse(src: Path, width: int, height: int, start: float, end: float, log: Log) -> tuple[list[Track], int]:
    """Detect and track faces in [start, end]. Returns (tracks, number of samples)."""
    import cv2
    dw = DET_W
    dh = int(round(height * dw / width / 2)) * 2
    scale = width / dw
    det = cv2.FaceDetectorYN.create(str(ensure_model(log)), "", (dw, dh), 0.75, 0.3, 50)
    tracks: list[Track] = []
    n = 0
    for i, frame in enumerate(_frames(src, start, end, dw, dh)):
        n = i + 1
        _, found = det.detect(frame)
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        faces = [] if found is None else [f for f in found if f[3] >= MIN_FACE * dh]
        used: set[int] = set()
        for f in sorted(faces, key=lambda f: -f[2] * f[3]):
            x, y, w, h = (float(v) for v in f[:4])
            cx, cy = x + w / 2, y + h / 2
            # the nearest live track whose last face was close to this one
            best, dist = None, 1e9
            for t in tracks:
                if t.id in used or i - t.last_i > TRACK_GAP * SAMPLE_FPS:
                    continue
                tx, ty, tw, th = t.last_box
                d = ((cx - tx) ** 2 + (cy - ty) ** 2) ** 0.5
                if d < max(tw, w) * 0.8 and d < dist:
                    best, dist = t, d
            if best is None:
                best = Track(id=len(tracks))
                tracks.append(best)
            used.add(best.id)
            # mouth: between the mouth corners (landmarks 3 and 4), minus how much the whole face moved
            mx, my = (f[10] + f[12]) / 2, (f[11] + f[13]) / 2
            mouth = _patch(gray, mx - w * 0.32, my - h * 0.14, mx + w * 0.32, my + h * 0.2, (32, 16))
            face = _patch(gray, x, y, x + w, y + h, (24, 24))
            act = 0.0
            if best.last_i == i - 1 and mouth is not None and best.mouth is not None and face is not None \
                    and best.face is not None:
                act = max(0.0, float(np.mean(np.abs(mouth - best.mouth))) - 0.6 * float(np.mean(np.abs(face - best.face))))
            best.mouth, best.face = mouth, face
            best.last_i, best.last_box = i, (cx, cy, w, h)
            best.samples[i] = (cx * scale, cy * scale, w * scale, h * scale)
            best.activity[i] = act
    tracks = [t for t in tracks if len(t.samples) >= max(3, SAMPLE_FPS // 2)]
    log(f"faces: {n} frames analysed, {len(tracks)} face track(s): "
        + ", ".join(f"#{t.id} in {len(t.samples) / max(n, 1):.0%} of frames" for t in tracks))
    return tracks, n


def _smooth(values: np.ndarray, window: int) -> np.ndarray:
    k = np.ones(window) / window
    return np.convolve(values, k, mode="same")


def speaker_timeline(tracks: list[Track], n: int) -> list[int | None]:
    """Track index on screen for every sample: the one talking (mouth movement over ~1.5 s),
    held through pauses, with speaker changes shorter than MIN_SHOT ignored."""
    if not tracks:
        return [None] * n
    present = np.zeros((len(tracks), n), bool)
    act = np.zeros((len(tracks), n))
    for k, t in enumerate(tracks):
        for i, a in t.activity.items():
            present[k, i] = True
            act[k, i] = a
    win = max(3, int(SAMPLE_FPS * 1.5))
    talk = np.array([_smooth(a, win) for a in act])
    size = np.array([np.mean([s[3] for s in t.samples.values()]) for t in tracks])
    out: list[int | None] = []
    cur = None
    for i in range(n):
        here = [k for k in range(len(tracks)) if present[k, max(0, i - 2):i + 3].any()]
        if not here:
            out.append(cur)
            continue
        loudest = max(here, key=lambda k: talk[k, i])
        if cur not in here:
            cur = loudest if talk[loudest, i] > 0.5 else max(here, key=lambda k: size[k])
        elif loudest != cur and talk[loudest, i] > max(1.2 * talk[cur, i], 0.8):
            cur = loudest
        out.append(cur)
    # drop speaker turns shorter than MIN_SHOT: they become part of the shot around them
    min_len = int(MIN_SHOT * SAMPLE_FPS)
    runs, i = [], 0
    while i < n:
        j = i
        while j < n and out[j] == out[i]:
            j += 1
        runs.append([out[i], i, j])
        i = j
    changed = True
    while changed and len(runs) > 1:
        changed = False
        for r in range(len(runs)):
            who, a, b = runs[r]
            if b - a < min_len:
                nb = runs[r - 1] if r > 0 else runs[r + 1]
                nb[1], nb[2] = min(nb[1], a), max(nb[2], b)
                runs.pop(r)
                changed = True
                break
        merged = []
        for run in runs:
            if merged and merged[-1][0] == run[0]:
                merged[-1][2] = run[2]
            else:
                merged.append(run)
        runs = merged
    final: list[int | None] = [None] * n
    for who, a, b in runs:
        for i in range(a, b):
            final[i] = who
    return final


def _even(v: float) -> int:
    return int(round(v / 2)) * 2


def _esc(expr: str) -> str:
    return expr.replace(",", "\\,")


def follow_filter(tracks: list[Track], n: int, width: int, height: int) -> tuple[str, int] | None:
    """Crop filter whose x jumps between still shots on the speaker. Returns (filter, shots)."""
    who = speaker_timeline(tracks, n)
    if all(w is None for w in who):
        return None
    out_w, out_h = CONFIG["video"]["width"], CONFIG["video"]["height"]
    cw = min(width, _even(height * out_w / out_h))
    centre: list[float | None] = []
    last = None
    for i, k in enumerate(who):
        s = tracks[k].samples.get(i) if k is not None else None
        if s is None and k is not None:  # face briefly lost: nearest known position of that track
            near = min(tracks[k].samples, key=lambda j: abs(j - i))
            s = tracks[k].samples[near]
        last = s[0] if s else last
        centre.append(last)
    first = next((c for c in centre if c is not None), width / 2)
    centre = [c if c is not None else first for c in centre]
    # still shots: a new one when the speaker changes or the face leaves the middle of the window
    # (moving around inside the window is fine; only a face that stays near the edge for 0.5 s
    # gets a new shot, so a lively speaker does not cause a cut every second)
    shots: list[list] = []  # [start sample, x centre, track]
    outside = 0
    for i, (c, k) in enumerate(zip(centre, who)):
        if not shots or k != shots[-1][2]:
            shots.append([i, c, k])
            outside = 0
            continue
        outside = outside + 1 if abs(c - shots[-1][1]) > cw * 0.3 else 0
        if outside >= SAMPLE_FPS // 2 and i - shots[-1][0] >= MIN_SHOT * SAMPLE_FPS:
            start = i - outside + 1
            shots.append([start, float(np.median(centre[start:i + 1])), k])
            outside = 0
    # each shot sits on the median face position of its whole duration
    bounds = [s[0] for s in shots] + [n]
    xs = []
    for a, b in zip(bounds, bounds[1:]):
        x = float(np.median(centre[a:b])) - cw / 2
        xs.append(_even(min(max(x, 0), width - cw)))
    expr = str(xs[-1])
    for s in range(len(xs) - 2, -1, -1):
        expr = f"if(lt(t,{bounds[s + 1] / SAMPLE_FPS:.3f}),{xs[s]},{expr})"
    return f"crop={cw}:{height}:{_esc(expr)}:0,scale={out_w}:{out_h}:flags=lanczos", len(xs)


def split_filter(tracks: list[Track], n: int, width: int, height: int) -> str | None:
    """Two people stacked, left one on top. None unless two faces are on screen most of the time."""
    # one person whose face was lost for a moment comes back as a new track in the same place:
    # join tracks that sit at the same spot and are never on screen at the same time
    med = lambda t: float(np.median([s[0] for s in t.samples.values()]))  # noqa: E731
    slots: list[Track] = []
    for t in sorted(tracks, key=lambda t: -len(t.samples)):
        home = next((s for s in slots if abs(med(s) - med(t)) < 0.12 * width
                     and len(set(s.samples) & set(t.samples)) < 0.1 * len(t.samples)), None)
        if home is None:
            slots.append(Track(id=t.id, samples=dict(t.samples), activity=dict(t.activity)))
        else:
            home.samples.update(t.samples)
            home.activity.update(t.activity)
    regular = sorted((t for t in slots if len(t.samples) >= 0.5 * n), key=lambda t: -len(t.samples))[:2]
    if len(regular) < 2:
        return None
    both = len(set(regular[0].samples) & set(regular[1].samples))
    cx = [float(np.median([s[0] for s in t.samples.values()])) for t in regular]
    if both < 0.6 * n or abs(cx[0] - cx[1]) < 0.15 * width:
        return None
    out_w, out_h = CONFIG["video"]["width"], CONFIG["video"]["height"]
    pw, ph = out_w, _even(out_h / 2)
    parts = []
    for t in sorted(regular, key=lambda t: np.median([s[0] for s in t.samples.values()])):
        fx = float(np.median([s[0] for s in t.samples.values()]))
        fy = float(np.median([s[1] for s in t.samples.values()]))
        fh = float(np.median([s[3] for s in t.samples.values()]))
        ch = min(height, max(height * 0.38, fh * 2.4))
        cw = ch * pw / ph
        if cw > width:
            cw, ch = width, width * ph / pw
        x = min(max(fx - cw / 2, 0), width - cw)
        y = min(max(fy - ch * 0.42, 0), height - ch)  # face a little above the middle of its panel
        parts.append(f"crop={_even(cw)}:{_even(ch)}:{_even(x)}:{_even(y)},scale={pw}:{ph}:flags=lanczos")
    return f"split[fa][fb];[fa]{parts[0]}[top];[fb]{parts[1]}[bot];[top][bot]vstack"


def vertical_filter(src: Path, width: int, height: int, start: float, end: float, mode: str, log: Log) -> str | None:
    """The geometry part of the filter graph for "face" or "split" framing, or None (use the centre)."""
    if not available():
        log("faces: OpenCV is not installed, using the centre crop")
        return None
    if width <= height:
        return None  # already vertical
    try:
        tracks, n = analyse(src, width, height, start, end, log)
    except (FaceError, requests.RequestException, OSError) as exc:
        log(f"faces: unavailable ({exc}), using the centre crop")
        return None
    if mode == "split":
        graph = split_filter(tracks, n, width, height)
        if graph:
            log("faces: two people, stacked")
            return graph
        log("faces: not two people most of the time, following the speaker instead")
    plan = follow_filter(tracks, n, width, height)
    if plan is None:
        log("faces: no face found, using the centre crop")
        return None
    log(f"faces: following the speaker in {plan[1]} shot(s)")
    return plan[0]
