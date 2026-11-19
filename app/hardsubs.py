"""Find and hide subtitles that are burned into the picture, so new captions can replace them.

Soft subtitle tracks are never copied into clips, so only burned-in text matters here. Subtitles
are told apart from other text on screen by where they sit and how they behave: wide lines of
text, horizontally centred, that appear and change from one moment to the next. Fixed text (a
logo, the buttons of a website, a channel name) stays the same and is left alone; a chat box sits
at the side and is left alone too.

The band found is hidden under a strong blur of itself (not a black bar), which keeps the colours
of the scene so the new captions on top look natural.
"""
from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Callable

import numpy as np

Log = Callable[[str], None]

SAMPLE_FPS = 2
DET_W = 480


def _frames(src: Path, start: float, end: float, dw: int, dh: int):
    cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-ss", f"{start:.3f}", "-i", str(src),
           "-t", f"{max(end - start, 0.5):.3f}", "-an", "-sn", "-map", "0:v:0",
           "-vf", f"fps={SAMPLE_FPS},scale={dw}:{dh}", "-f", "rawvideo", "-pix_fmt", "gray", "-"]
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    size = dw * dh
    try:
        while True:
            buf = proc.stdout.read(size)
            if len(buf) < size:
                break
            yield np.frombuffer(buf, np.uint8).reshape(dh, dw)
    finally:
        proc.stdout.close()
        proc.wait()


def _text_lines(gray: np.ndarray) -> list[tuple[int, int, int, int]]:
    """Boxes (x, y, w, h) of wide, centred lines of subtitle-like text. Subtitles are bright letters
    (white or yellow) with a dark outline or shadow right next to them, which almost nothing else in
    a scene looks like; the letters are then joined horizontally into lines."""
    import cv2
    h, w = gray.shape
    bright = gray > 185
    dark = cv2.dilate((gray < 75).astype(np.uint8), np.ones((3, 3), np.uint8), iterations=2) > 0
    text = (bright & dark).astype(np.uint8) * 255
    joined = cv2.morphologyEx(text, cv2.MORPH_CLOSE, cv2.getStructuringElement(cv2.MORPH_RECT, (max(9, w // 40), 3)))
    n, _, stats, _ = cv2.connectedComponentsWithStats(joined, 8)
    out = []
    for x, y, bw, bh, area in stats[1:]:
        if not (0.02 * h <= bh <= 0.14 * h) or bw < max(2.2 * bh, 0.08 * w):
            continue
        if abs((x + bw / 2) - w / 2) > 0.2 * w:  # subtitles are centred; chat boxes and UI sit at the sides
            continue
        fill = float(np.count_nonzero(text[y:y + bh, x:x + bw])) / (bw * bh)
        if fill < 0.08:
            continue
        # a line of text is many separate narrow letters; a white stripe on a jacket is one long shape
        k, _, letters, _ = cv2.connectedComponentsWithStats(text[y:y + bh, x:x + bw], 8)
        glyphs = [s for s in letters[1:] if s[3] >= 0.3 * bh and s[2] <= 1.5 * bh]
        if len(glyphs) < max(4, bw / bh * 0.6):
            continue
        out.append((int(x), int(y), int(bw), int(bh)))
    return out


def find_band(src: Path, width: int, height: int, start: float, end: float, log: Log) -> tuple[int, int, int, int] | None:
    """The box (x, y, w, h, in source pixels) where burned-in subtitles appear, or None."""
    try:
        import cv2  # noqa: F401
    except ImportError:
        log("subtitles: OpenCV is not installed, cannot look for burned-in subtitles")
        return None
    dw = DET_W
    dh = int(round(height * dw / width / 2)) * 2
    frames, boxes = [], []
    for gray in _frames(src, start, end, dw, dh):
        frames.append(gray)
        boxes.append(_text_lines(gray))
    n = len(frames)
    if n < 3:
        return None
    rows = np.zeros(dh)
    for found in boxes:
        hit = np.zeros(dh, bool)
        for x, y, bw, bh in found:
            hit[y:y + bh] = True
        rows += hit
    share = rows / n
    # contiguous rows with text in at least 20 % of the frames, small gaps (two-line subtitles) bridged
    on = share >= 0.2
    gap = int(0.03 * dh)
    groups, i = [], 0
    while i < dh:
        if not on[i]:
            i += 1
            continue
        j = i
        while j < dh and (on[j] or (j + gap < dh and on[j:j + gap].any())):
            j += 1
        groups.append((i, j))
        i = j
    best = None
    for y0, y1 in groups:
        inside = [(x, y, bw, bh) for found in boxes for (x, y, bw, bh) in found if y < y1 and y + bh > y0]
        present = sum(1 for found in boxes if any(y < y1 and y + bh > y0 for (x, y, bw, bh) in found))
        # subtitles change: compare the band between frames that both show text
        band_frames = [k for k, found in enumerate(boxes) if any(y < y1 and y + bh > y0 for (x, y, bw, bh) in found)]
        diffs = [float(np.mean(np.abs(frames[a][y0:y1].astype(np.int16) - frames[b][y0:y1].astype(np.int16))))
                 for a, b in zip(band_frames, band_frames[1:])]
        change = float(np.median(diffs)) if diffs else 0.0
        if change < 4.0:
            log(f"subtitles: text at rows {y0}-{y1} of {dh} never changes (a logo or interface), left alone")
            continue
        if present > 0.97 * n and change < 12.0:
            log(f"subtitles: text at rows {y0}-{y1} of {dh} is always there (an overlay), left alone")
            continue
        score = present * (y1 - y0)
        if best is None or score > best[0]:
            xs0 = np.percentile([x for x, _, _, _ in inside], 5)
            xs1 = np.percentile([x + bw for x, _, bw, _ in inside], 95)
            best = (score, y0, y1, xs0, xs1, present / n, change)
    if best is None:
        log(f"subtitles: no burned-in subtitles found in {n} frames")
        return None
    _, y0, y1, x0, x1, share_on, change = best
    pad = (y1 - y0) * 0.35 + 0.01 * dh
    s = width / dw
    bx0, bx1 = max(0.0, (x0 - pad) * s), min(width, (x1 + pad) * s)
    by0, by1 = max(0.0, (y0 - pad) * s), min(height, (y1 + pad) * s)
    box = (int(bx0) // 2 * 2, int(by0) // 2 * 2, int(bx1 - bx0) // 2 * 2, int(by1 - by0) // 2 * 2)
    log(f"subtitles: burned-in text found at {box} (on screen {share_on:.0%} of the time), hiding it")
    return box


def erase_chain(box: tuple[int, int, int, int]) -> str:
    """Filter-graph fragment (input -> output, no labels at the ends) that hides the box under a
    heavy blur of itself."""
    # shrink the band to a few pixels and back: the letters vanish, the scene's colours stay. (A big
    # gblur on a small region zeroes the chroma planes and turns the patch green.)
    x, y, w, h = box
    small_w, small_h = max(4, w // 48), 3
    return (f"split[hs_main][hs_band];[hs_band]crop={w}:{h}:{x}:{y},format=yuv444p,"
            f"scale={small_w}:{small_h}:flags=area,scale={w}:{h}:flags=bicubic,"
            f"gblur=sigma={max(4, h // 5)},format=yuv420p[hs_blur];"
            f"[hs_main][hs_blur]overlay={x}:{y}")
