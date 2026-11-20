"""User-directed gaming compositions. Original ClipForge implementation; no third-party code.

All regions use fractions of the displayed source frame. The same render plan is
returned to the editor and translated into FFmpeg, so previews and exports agree.
"""
from __future__ import annotations

import json
import math
from pathlib import Path

DEFAULT = {"mode": "stack", "camera": [0, .6, .3, .4], "game": [0, 0, 1, 1],
           "share": .38, "order": "camera", "camera_fit": "contain", "game_fit": "contain",
           "mirror": False, "resolution": 1080, "captions_at": "seam"}


def number(value, label: str, lo: float, hi: float) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not lo <= value <= hi:
        raise ValueError(f"{label} must be between {lo:g} and {hi:g}.")
    return float(value)


def validate(value: dict) -> dict:
    if not isinstance(value, dict) or set(value) - set(DEFAULT):
        raise ValueError("Invalid gaming layout settings.")
    out = {**DEFAULT, **value}
    for name in ("camera", "game"):
        rect = out[name]
        if not isinstance(rect, list) or len(rect) != 4:
            raise ValueError(f"{name.title()} needs x, y, width and height.")
        x, y, w, h = [number(v, name, 0 if i < 2 else .01, 1) for i, v in enumerate(rect)]
        if x + w > 1.000001 or y + h > 1.000001:
            raise ValueError(f"{name.title()} must stay inside the source frame.")
        out[name] = [x, y, min(w, 1-x), min(h, 1-y)]
    for name, choices in {"mode": ("stack", "pip", "game"), "order": ("camera", "game"),
                          "camera_fit": ("contain", "cover"), "game_fit": ("contain", "cover"),
                          "captions_at": ("seam", "bottom", "top")}.items():
        if out[name] not in choices:
            raise ValueError(f"Unknown {name.replace('_', ' ')}.")
    out["share"] = number(out["share"], "Camera size", .2, .55)
    if isinstance(out["resolution"], bool) or out["resolution"] not in (720, 1080):
        raise ValueError("Choose 720p or 1080p.")
    if not isinstance(out["mirror"], bool):
        raise ValueError("Mirror must be on or off.")
    return out


def even(v: float) -> int:
    return max(2, int(v) // 2 * 2)


def source_box(rect: list, width: int, height: int) -> list[int]:
    width, height = even(width), even(height)
    x = min(width-2, int(rect[0]*width)//2*2)
    y = min(height-2, int(rect[1]*height)//2*2)
    return [x, y, min(width-x, even(rect[2]*width)), min(height-y, even(rect[3]*height))]


def plan(settings: dict, source_width: int, source_height: int) -> dict:
    s = validate(settings)
    if not 2 <= source_width <= 16384 or not 2 <= source_height <= 16384:
        raise ValueError("Invalid source dimensions.")
    w, h = s["resolution"], s["resolution"]*16//9
    camera_h = even(h*s["share"])
    if s["mode"] == "stack":
        camera_y = 0 if s["order"] == "camera" else h-camera_h
        game_y = camera_h if s["order"] == "camera" else 0
        destinations = [("camera", [0, camera_y, w, camera_h]), ("game", [0, game_y, w, h-camera_h])]
        seam = camera_h if s["order"] == "camera" else h-camera_h
    elif s["mode"] == "pip":
        cw = even(w*s["share"])
        camera = source_box(s["camera"], source_width, source_height)
        ch = even(min(h*.3, cw*camera[3]/camera[2]))
        destinations = [("game", [0, 0, w, h]), ("camera", [even(w*.05), even(h*.08), cw, ch])]
        seam = h*.78
    else:
        destinations = [("game", [0, 0, w, h])]
        seam = h*.78
    layers = [{"role": role, "source": source_box(s[role], source_width, source_height),
               "destination": dst, "fit": s[role+"_fit"], "mirror": role == "camera" and s["mirror"]}
              for role, dst in destinations]
    caption_y = seam if s["captions_at"] == "seam" else h*(.14 if s["captions_at"] == "top" else .80)
    return {"width": w, "height": h, "layers": layers, "caption_y": round(caption_y), "settings": s}


def graph(p: dict) -> str:
    """Build only from a validated numeric plan; no user filter expressions."""
    layers = p["layers"]
    parts = [f"[0:v:0]setsar=1,split={len(layers)+1}" + "".join(f"[s{i}]" for i in range(len(layers)+1))]
    parts.append(f"[s0]scale={p['width']}:{p['height']},drawbox=color=black:t=fill[base0]")
    for i, layer in enumerate(layers, 1):
        x, y, w, h = layer["source"]
        dx, dy, dw, dh = layer["destination"]
        fit = (f"scale={dw}:{dh}:force_original_aspect_ratio=decrease:force_divisible_by=2:flags=lanczos,"
               f"pad={dw}:{dh}:(ow-iw)/2:(oh-ih)/2:color=black" if layer["fit"] == "contain" else
               f"scale={dw}:{dh}:force_original_aspect_ratio=increase:force_divisible_by=2:flags=lanczos,crop={dw}:{dh}")
        flip = ",hflip" if layer["mirror"] else ""
        parts.append(f"[s{i}]crop={w}:{h}:{x}:{y}{flip},{fit},setsar=1[layer{i}]")
        parts.append(f"[base{i-1}][layer{i}]overlay={dx}:{dy}:shortest=1[base{i}]")
    parts.append(f"[base{len(layers)}]fps=30,format=yuv420p[v]")
    return ";".join(parts)


def render(job) -> None:
    from . import jobs, video, captions, transcribe, moments
    s, o = job.status, job.status["options"]
    src, info = jobs._source(job, wav=False, need_audio=False)
    start, end = o["start"], o["end"] or min(info.duration, o["start"]+60)
    if start >= info.duration or end > info.duration+.1 or end <= start or end-start > 180:
        raise ValueError("Choose a range inside the video, up to 180 seconds.")
    end = min(end, info.duration)
    duration = end-start
    p = plan(o["layout"], info.width, info.height)
    clean = job.dir / "gaming.mp4"
    if not job.done("render") or not clean.is_file():
        with job.stage("render"):
            video.run_ffmpeg(["-y", "-ss", str(start), "-i", str(src), "-t", str(duration),
                "-filter_complex", graph(p), "-map", "[v]", "-map", "0:a:0?", *video._video_codec_args(),
                *video._aac_args(), "-movflags", "+faststart", str(clean)], job.log,
                duration=duration, progress=job.progress)
            (job.dir / "layout.json").write_text(json.dumps({**p, "start": start, "end": end}, indent=2), encoding="utf-8")
            job.output(clean, "Gaming layout · clean", "video", " (gaming)", duration=duration, width=p["width"], height=p["height"])
            job.output(job.dir / "layout.json", "Layout settings", "text")
    transcript_path = job.dir / "transcript.json"
    if o["preset"] != "none":
        if not info.has_audio:
            raise ValueError("This source has no audio. The clean video is saved; export with No captions.")
        if job.done("transcribe") and transcript_path.is_file():
            transcript = json.loads(transcript_path.read_text(encoding="utf-8"))
        else:
            with job.stage("transcribe"):
                video.extract_audio(clean, job.dir / "audio.wav", job.log)
                transcript = transcribe.transcribe(job.dir / "audio.wav", transcript_path, duration, job.log, job.progress,
                                                  task="translate" if o["translate"] else "transcribe")
                s["language"] = transcript["language"]
        with job.stage("caption"):
            words = moments.all_words(transcript)
            jobs.install_fonts(job.dir)
            ass = captions.build_ass(words, duration, o["preset"], "middle", size=(p["width"], p["height"]), anchor_y=p["caption_y"])
            (job.dir / "captions.ass").write_text(ass, encoding="utf-8")
            video.burn_captions(job.dir, clean.name, "captions.ass", "captioned.mp4", job.log, duration=duration, progress=job.progress)
            job.output(job.dir / "captioned.mp4", "Gaming layout · captions", "video", " (captioned)",
                       duration=duration, width=p["width"], height=p["height"])
            for name, text in (("subtitles.srt", captions.build_srt(words, duration)), ("subtitles.vtt", captions.build_vtt(words, duration))):
                path = job.dir / name
                path.write_text(text, encoding="utf-8")
                job.output(path, name, "text")
    else:
        for stage in ("transcribe", "caption"):
            with job.stage(stage):
                s["stages"][stage]["detail"] = "Skipped · No captions"
    jobs._finish(job)


# ---------------------------------------------------------------- automatic regions (Claude, 2026-11-20)

AUTO_MAX_FRAMES = 8


def _edge_line(strength, lo: int, hi: int, span: tuple[int, int], axis: int, prefer_far: bool) -> int | None:
    """Strongest straight edge between lo and hi. axis=0 scans columns, axis=1 rows.
    Returns None when no line clearly stands out (no overlay border)."""
    import numpy as np
    lo, hi = max(1, lo), min(strength.shape[1 - axis] - 2, hi)
    if hi - lo < 3:
        return None
    a, b = span
    band = strength[a:b, lo:hi + 1] if axis == 0 else strength[lo:hi + 1, a:b].T
    if band.size == 0:
        return None
    # share of the span where the edge is strong: an overlay border runs the whole length
    cut = np.percentile(strength, 90)
    score = (band > cut).mean(axis=0)
    best = int(np.argmax(score))
    if score[best] < 0.55:
        return None
    return lo + best


def auto_regions(images: list[bytes]) -> dict:
    """Find the webcam in a gaming recording from a few frames.

    The webcam is the face that stays in one place across the samples. Its box starts as the
    usual facecam framing around that face and is then fitted to the overlay's straight border
    when one is visible. Everything else stays gameplay."""
    import cv2
    import numpy as np
    from .faces import ensure_model

    frames = []
    for data in images[:AUTO_MAX_FRAMES]:
        img = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR)
        if img is not None and img.shape[0] >= 90 and img.shape[1] >= 160:
            frames.append(img)
    if not frames:
        raise ValueError("Could not read frames from this video.")
    h0, w0 = frames[0].shape[:2]
    frames = [f if f.shape[:2] == (h0, w0) else cv2.resize(f, (w0, h0)) for f in frames]
    scale = 640 / w0
    dw, dh = 640, max(90, int(round(h0 * scale / 2)) * 2)
    small = [cv2.resize(f, (dw, dh)) for f in frames]
    det = cv2.FaceDetectorYN.create(str(ensure_model(lambda _m: None)), "", (dw, dh), 0.7, 0.3, 20)

    faces = []
    for i, img in enumerate(small):
        _, found = det.detect(img)
        for f in [] if found is None else found:
            x, y, w, h = (float(v) for v in f[:4])
            if h >= 0.035 * dh:
                faces.append((i, x, y, w, h, float(f[-1])))
    # group by position: the webcam face is in roughly the same place in most frames.
    # Of the steady groups, the biggest face is the streamer (small ones are posters or game characters).
    steady = max(2, round(len(small) * .4))
    best_group: list = []
    best_size = 0.0
    for _, x, y, w, h, _s in faces:
        cx, cy = x + w / 2, y + h / 2
        group = [f for f in faces if abs(f[1] + f[3] / 2 - cx) < max(w, f[3]) * .6 and abs(f[2] + f[4] / 2 - cy) < max(h, f[4]) * .6]
        if len({f[0] for f in group}) < steady:
            continue
        size = float(np.median([f[4] for f in group]))
        if size > best_size:
            best_group, best_size = group, size
    seen_in = len({f[0] for f in best_group})
    if not best_group:
        return {"found": False, "confidence": 0, "settings": {"mode": "game", "game": [0, 0, 1, 1]},
                "message": "No steady webcam found, so this is set to gameplay only. Draw the webcam yourself if there is one."}

    fx, fy, fw, fh = (float(np.median([f[k] for f in best_group])) for k in (1, 2, 3, 4))
    if fh > .42 * dh:
        return {"found": True, "confidence": round(seen_in / len(small), 2),
                "settings": {"mode": "stack", "camera": [0, 0, 1, 1], "game": [0, 0, 1, 1]},
                "message": "This looks like a face-only video, not a game with a webcam. Clip maker may suit it better."}

    # usual facecam framing: about 3 face widths wide, 4:3, face a little above the middle
    bw = min(dw, fw * 3.2)
    bh = min(dh, bw * .75)
    bx = fx + fw / 2 - bw / 2
    by = fy + fh * .45 - bh * .42
    left, top, right, bottom = bx, by, bx + bw, by + bh

    # fit to the overlay border: straight edges that stay put while the picture moves
    gray = [cv2.cvtColor(s, cv2.COLOR_BGR2GRAY).astype(np.float32) for s in small]
    gx = np.mean([np.abs(cv2.Sobel(g, cv2.CV_32F, 1, 0, ksize=3)) for g in gray], axis=0)
    gy = np.mean([np.abs(cv2.Sobel(g, cv2.CV_32F, 0, 1, ksize=3)) for g in gray], axis=0)
    rows = (int(max(0, fy - fh * .3)), int(min(dh, fy + fh * 1.3)))
    cols = (int(max(0, fx - fw * .3)), int(min(dw, fx + fw * 1.3)))
    snapped = 0
    for side, (lo, hi, axis, span) in {
        "left": (int(fx - fw * 2.6), int(fx - fw * .35), 0, rows),
        "right": (int(fx + fw * 1.35), int(fx + fw * 3.6), 0, rows),
        "top": (int(fy - fh * 2.2), int(fy - fh * .25), 1, cols),
        "bottom": (int(fy + fh * 1.25), int(fy + fh * 3.2), 1, cols),
    }.items():
        found = _edge_line(gx if axis == 0 else gy, lo, hi, span, axis, side in ("right", "bottom"))
        if found is not None:
            snapped += 1
            if side == "left": left = found
            elif side == "right": right = found
            elif side == "top": top = found
            else: bottom = found
    # webcams usually sit in a corner: snap to frame edges that are very close
    pad_x, pad_y = dw * .04, dh * .04
    left = 0 if left < pad_x else left
    top = 0 if top < pad_y else top
    right = dw if dw - right < pad_x else right
    bottom = dh if dh - bottom < pad_y else bottom
    left, top = max(0, left), max(0, top)
    right, bottom = min(dw, right), min(dh, bottom)
    cam = [round(left / dw, 4), round(top / dh, 4), round((right - left) / dw, 4), round((bottom - top) / dh, 4)]
    cam[2], cam[3] = max(.05, min(cam[2], 1 - cam[0])), max(.05, min(cam[3], 1 - cam[1]))
    confidence = round(min(1, seen_in / len(small) * (.7 + .075 * snapped)), 2)
    note = "Webcam found" + (" and fitted to its border." if snapped >= 2 else ". Adjust the box if it cuts the webcam.")
    return {"found": True, "confidence": confidence,
            "settings": {"mode": "stack", "order": "camera", "camera": cam, "game": [0, 0, 1, 1],
                         "camera_fit": "cover", "game_fit": "cover"},
            "message": note}
