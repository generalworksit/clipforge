"""A clickable cover image (thumbnail) for every clip, in the style that works on YouTube Shorts,
TikTok and Reels: the best frame of the clip with punchy colours, 2 to 4 huge words in a heavy
font with a thick black outline (one line white, one yellow, slightly tilted), an optional small
label in a black box, and a red arrow + circle pointing at the thing to look at.

What makes a cover get clicked (what tools like vidIQ score): a sharp, bright, colourful frame;
a big face or one clear subject; very few words that add curiosity instead of repeating the title;
strong contrast so it still reads at the size of a thumbnail in a grid.
"""
from __future__ import annotations

import io
import math
import re
import subprocess
from pathlib import Path
from typing import Callable

import numpy as np

from .config import ROOT

Log = Callable[[str], None]

W, H = 1080, 1920
FONTS = ROOT / "assets" / "fonts"
FONT = FONTS / "Anton-Regular.ttf"
YELLOW = (255, 225, 26)
WHITE = (255, 255, 255)
RED = (235, 30, 35)
SCAN_W, SCAN_H = 360, 640   # frames are scored at this size
CANDIDATES = 24             # frames looked at per clip
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


# ---------------------------------------------------------------- picking the frame

def _scan(clip: Path, duration: float):
    """CANDIDATES frames spread over the clip (skipping the first and last 6 %), as (time, BGR)."""
    start, span = duration * 0.06, duration * 0.88
    fps = CANDIDATES / max(span, 0.5)
    cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-ss", f"{start:.3f}", "-i", str(clip),
           "-t", f"{span:.3f}", "-an", "-sn", "-map", "0:v:0",
           "-vf", f"fps={fps:.5f},scale={SCAN_W}:{SCAN_H}", "-f", "rawvideo", "-pix_fmt", "bgr24", "-"]
    out = subprocess.run(cmd, capture_output=True, creationflags=NO_WINDOW).stdout
    size = SCAN_W * SCAN_H * 3
    return [(start + (i + 0.5) / fps, np.frombuffer(out[i * size:(i + 1) * size], np.uint8).reshape(SCAN_H, SCAN_W, 3))
            for i in range(len(out) // size)]


def _detector(log: Log):
    try:
        import cv2
        from . import faces
        return cv2.FaceDetectorYN.create(str(faces.ensure_model(log)), "", (SCAN_W, SCAN_H), 0.75, 0.3, 20)
    except Exception as exc:  # noqa: BLE001  no face detector: frames are scored without faces
        log(f"thumbnail: no face detection ({exc})")
        return None


def _faces(det, frame) -> list[tuple[float, float, float, float]]:
    """Faces as (x, y, w, h) in 0..1 of the frame, biggest first."""
    if det is None:
        return []
    _, found = det.detect(frame)
    if found is None:
        return []
    boxes = [(round(float(f[0]) / SCAN_W, 4), round(float(f[1]) / SCAN_H, 4), round(float(f[2]) / SCAN_W, 4),
              round(float(f[3]) / SCAN_H, 4)) for f in found if f[3] >= 0.04 * SCAN_H]
    return sorted(boxes, key=lambda b: -b[2] * b[3])


def _rank(values: list[float]) -> np.ndarray:
    a = np.asarray(values, float)
    return np.argsort(np.argsort(a)) / max(len(a) - 1, 1)


def pick_frames(clip: Path, duration: float, log: Log) -> list[dict]:
    """The best frames of a clip, best first, at least 1.5 s apart. Each: {time, score, faces}."""
    import cv2
    frames = _scan(clip, duration)
    if not frames:
        return [{"time": duration / 2, "score": 0.0, "faces": []}]
    det = _detector(log)
    rows = []
    for t, img in frames:
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        b, g, r = (img[..., i].astype(float) for i in range(3))
        rg, yb = r - g, 0.5 * (r + g) - b
        colour = math.hypot(rg.std(), yb.std()) + 0.3 * math.hypot(rg.mean(), yb.mean())
        light = gray.mean() / 255
        fs = _faces(det, img)
        face = 0.0
        if fs:
            x, y, w, h = fs[0]
            face = min(1.0, h / 0.22) * (1.0 if 0.12 < y + h / 2 < 0.75 else 0.6)
        rows.append({"time": round(t, 2), "faces": fs, "sharp": cv2.Laplacian(gray, cv2.CV_64F).var(),
                     "colour": colour, "light": 1 - min(1.0, abs(light - 0.5) / 0.4), "face": face})
    sharp, colour = _rank([r["sharp"] for r in rows]), _rank([r["colour"] for r in rows])
    for i, r in enumerate(rows):
        r["score"] = round(float(0.3 * sharp[i] + 0.2 * colour[i] + 0.15 * r["light"] + 0.35 * r["face"]), 3)
    rows.sort(key=lambda r: -r["score"])
    best: list[dict] = []
    for r in rows:
        if all(abs(r["time"] - b["time"]) >= 1.5 for b in best):
            best.append({k: r[k] for k in ("time", "score", "faces")})
        if len(best) == 6:
            break
    return best


def grab(clip: Path, t: float):
    from PIL import Image
    out = subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-ss", f"{t:.3f}", "-i", str(clip),
                          "-frames:v", "1", "-f", "image2pipe", "-vcodec", "png", "-"],
                         capture_output=True, creationflags=NO_WINDOW).stdout
    if not out:
        raise RuntimeError(f"could not read a frame at {t:.1f} s of {clip.name}")
    img = Image.open(io.BytesIO(out)).convert("RGB")
    return img if img.size == (W, H) else _cover(img)


def _cover(img):
    """Fill 1080x1920 (crop the sides or the top/bottom)."""
    from PIL import Image
    s = max(W / img.width, H / img.height)
    img = img.resize((round(img.width * s), round(img.height * s)), Image.LANCZOS)
    x, y = (img.width - W) // 2, (img.height - H) // 2
    return img.crop((x, y, x + W, y + H))


# ---------------------------------------------------------------- where to point

def _target(img, faces: list, text_box: tuple[float, float]) -> dict | None:
    """The thing to circle: the most eye-catching spot (spectral-residual saliency plus colour pop)
    away from the text and the faces. None when nothing stands out."""
    import cv2
    small = np.asarray(img.resize((90, 160)).convert("RGB")).astype(np.float32)
    gray = small.mean(axis=2)
    f = np.fft.fft2(gray)
    log_amp = np.log(np.abs(f) + 1e-6)
    resid = log_amp - cv2.blur(log_amp, (3, 3))
    sal = np.abs(np.fft.ifft2(np.exp(resid + 1j * np.angle(f)))) ** 2
    sal = cv2.GaussianBlur(sal.astype(np.float32), (0, 0), 3)
    lab = cv2.cvtColor(small / 255, cv2.COLOR_RGB2LAB)
    pop = np.linalg.norm(lab - lab.reshape(-1, 3).mean(0), axis=2)
    sal = sal / (sal.max() + 1e-9) + 0.6 * cv2.GaussianBlur(pop, (0, 0), 3) / (pop.max() + 1e-9)
    mask = np.ones_like(sal)
    mask[:, :8] = mask[:, -8:] = 0
    mask[:12] = mask[-22:] = 0                              # edges; the bottom holds the app's buttons
    y0, y1 = (int(v * 160) for v in text_box)
    mask[max(0, y0 - 6):min(160, y1 + 6)] = 0
    for x, y, w, h in faces:                               # the face is already the subject
        mask[max(0, int((y - h * 0.4) * 160)):int((y + h * 1.4) * 160),
             max(0, int((x - w * 0.3) * 90)):int((x + w * 1.3) * 90)] = 0
    s = sal * mask
    if s.max() <= 0:
        return None
    iy, ix = np.unravel_index(int(s.argmax()), s.shape)
    strength = float(s.max() / (sal[mask > 0].mean() + 1e-9))
    area = float((s > s.max() * 0.55).sum()) / s.size
    r = min(0.14, max(0.08, math.sqrt(area / math.pi) * 1.1))
    return {"x": float((ix + 0.5) / 90), "y": float((iy + 0.5) / 160), "r": r, "strength": round(strength, 2)}


# ---------------------------------------------------------------- drawing

def _grade(img):
    from PIL import Image, ImageEnhance, ImageFilter
    lum = np.asarray(img.convert("L")).mean() / 255
    if lum < 0.38:
        img = ImageEnhance.Brightness(img).enhance(min(1.45, 0.45 / max(lum, 0.1)))
    img = ImageEnhance.Color(img).enhance(1.4)
    img = ImageEnhance.Contrast(img).enhance(1.15)
    img = img.filter(ImageFilter.UnsharpMask(radius=3, percent=90, threshold=2))
    yy, xx = np.mgrid[0:H, 0:W]
    d = np.hypot((xx - W / 2) / (W / 2), (yy - H / 2) / (H / 2))
    shade = np.clip((d - 0.75) / 0.7, 0, 1) * 0.45               # soft dark corners
    arr = np.asarray(img).astype(np.float32) * (1 - shade[..., None])
    return Image.fromarray(arr.clip(0, 255).astype(np.uint8))


def _font(size: int):
    from PIL import ImageFont
    return ImageFont.truetype(str(FONT), size)


def _lines(text: str) -> list[str]:
    """One or two lines, balanced."""
    words = text.split()
    if len(words) <= 1 or len(text) <= 9:
        return [text]
    best = min(range(1, len(words)), key=lambda i: abs(len(" ".join(words[:i])) - len(" ".join(words[i:]))))
    return [" ".join(words[:best]), " ".join(words[best:])]


def _text_layer(text: str, tag: str, max_h: int):
    """The headline (+ tag box) on a transparent layer, tilted like a hand-made thumbnail."""
    from PIL import Image, ImageDraw, ImageFilter
    lines = _lines(text)
    size = 330
    while size > 90:
        f = _font(size)
        widths = [f.getbbox(line)[2] for line in lines]
        if max(widths) <= W * 0.86 and size * 1.02 * len(lines) <= max_h:
            break
        size -= 10
    f = _font(size)
    stroke = max(6, size // 13)
    line_h = int(size * 1.04)
    tag_f = _font(max(62, int(size / 2.4))) if tag else None
    tag_h = int(tag_f.size * 1.5) if tag else 0
    lw, lh = W + 200, line_h * len(lines) + tag_h + size // 2 + 60
    layer = Image.new("RGBA", (lw, lh), (0, 0, 0, 0))
    shadow = Image.new("RGBA", (lw, lh), (0, 0, 0, 0))
    d, ds = ImageDraw.Draw(layer), ImageDraw.Draw(shadow)
    colours = [WHITE, YELLOW] if len(lines) == 2 else [YELLOW]
    y = 30
    right = 0
    for line, colour in zip(lines, colours):
        w = f.getbbox(line)[2]
        x = (lw - w) // 2
        right = max(right, x + w)
        off = max(8, size // 18)
        ds.text((x + off, y + off * 1.3), line, font=f, fill=(0, 0, 0, 230), stroke_width=stroke, stroke_fill=(0, 0, 0, 230))
        d.text((x, y), line, font=f, fill=colour, stroke_width=stroke, stroke_fill=(0, 0, 0))
        y += line_h
    if tag:
        pad = tag_f.size // 3
        tw = tag_f.getbbox(tag)[2]
        box = Image.new("RGBA", (tw + pad * 4, int(tag_f.size * 1.35)), (0, 0, 0, 0))
        bd = ImageDraw.Draw(box)
        bd.rounded_rectangle((0, 0, box.width - 1, box.height - 1), radius=pad, fill=(12, 12, 12, 245),
                             outline=WHITE, width=max(3, pad // 3))
        bd.text((pad * 2, int(tag_f.size * 0.12)), tag, font=tag_f, fill=WHITE)
        box = box.transform(box.size, Image.AFFINE, (1, 0.18, -box.height * 0.09, 0, 1, 0), Image.BICUBIC)  # italic slant
        layer.alpha_composite(box, (max(0, min(lw - box.width, right - box.width + 20)), y + int(size * 0.2)))
    shadow = shadow.filter(ImageFilter.GaussianBlur(max(4, size // 30)))
    shadow.alpha_composite(layer)
    bbox = shadow.getbbox() or (0, 0, lw, lh)
    return shadow.crop(bbox).rotate(4, resample=Image.BICUBIC, expand=True)


def _arrow(draw, start, end, scale: float) -> None:
    """A curved red arrow with a white outline from start to end (pixels)."""
    (x0, y0), (x1, y1) = start, end
    dx, dy = x1 - x0, y1 - y0
    length = math.hypot(dx, dy)
    if length < 40 * scale:
        return
    nx, ny = -dy / length, dx / length
    bend = 0.18 * length * (1 if dx >= 0 else -1)
    cx, cy = (x0 + x1) / 2 + nx * bend, (y0 + y1) / 2 + ny * bend
    head = 95 * scale
    pts = []
    for i in range(41):
        t = i / 40
        pts.append(((1 - t) ** 2 * x0 + 2 * (1 - t) * t * cx + t * t * x1,
                    (1 - t) ** 2 * y0 + 2 * (1 - t) * t * cy + t * t * y1))
    # stop the shaft under the head
    keep = [p for p in pts if math.hypot(p[0] - x1, p[1] - y1) > head * 0.7]
    tx, ty = x1 - pts[-4][0], y1 - pts[-4][1]
    tl = math.hypot(tx, ty) or 1
    ux, uy = tx / tl, ty / tl
    base = (x1 - ux * head, y1 - uy * head)
    tri = [(x1, y1), (base[0] - uy * head * 0.62, base[1] + ux * head * 0.62),
           (base[0] + uy * head * 0.62, base[1] - ux * head * 0.62)]
    o = 9 * scale
    tri_out = [(x1 + ux * o * 1.8, y1 + uy * o * 1.8),
               (tri[1][0] - ux * o - uy * o, tri[1][1] - uy * o + ux * o),
               (tri[2][0] - ux * o + uy * o, tri[2][1] - uy * o - ux * o)]
    w = int(30 * scale)
    draw.line(keep, fill=WHITE, width=w + int(2 * o), joint="curve")
    draw.polygon(tri_out, fill=WHITE)
    draw.line(keep, fill=RED, width=w, joint="curve")
    draw.polygon(tri, fill=RED)


def render(frame, text: str, tag: str, faces: list, out: Path, marks: bool = True, grade: bool = True) -> dict:
    """Draw the thumbnail on a 1080x1920 frame and save it as JPEG. Returns what was drawn.
    grade=False for an AI-redrawn frame, which already has the punchy look."""
    from PIL import Image, ImageDraw
    img = (_grade(frame) if grade else frame).convert("RGBA")
    layer = _text_layer(text, tag, int(H * 0.27))
    lx = (W - layer.width) // 2

    def covered(y: int) -> float:  # face area the text would hide (faces are padded a little: hair, chin)
        z0, z1 = y / H, (y + layer.height) / H
        x0, x1 = lx / W, (lx + layer.width) / W
        return sum(max(0.0, min(fy + fh * 1.15, z1) - max(fy - fh * 0.15, z0)) * max(0.0, min(fx + fw, x1) - max(fx, x0))
                   for fx, fy, fw, fh in faces)

    top, low = int(H * 0.035), int(H * 0.56)
    at_bottom = covered(top) > covered(low) + 1e-4                # the face is up there: text goes lower
    ly = low if at_bottom else top
    # a dark fade behind the text keeps it readable on bright frames
    fade = np.zeros((H, W), np.float32)
    y0, y1 = ly, ly + layer.height
    ramp = np.clip(1 - np.abs(np.arange(H) - (y0 + y1) / 2) / ((y1 - y0) / 2 + 160), 0, 1)
    fade[:] = ramp[:, None] * 120
    img.alpha_composite(Image.fromarray(np.dstack([np.zeros((H, W, 3), np.uint8), fade.astype(np.uint8)]), "RGBA"))
    img.alpha_composite(layer, (lx, ly))
    drawn = {"text_at": "bottom" if at_bottom else "top", "mark": None}
    if marks:
        target = _target(frame, faces, (ly / H, (ly + layer.height) / H))
        pointer = None
        if target and target["strength"] >= 3.0:
            pointer = (target["x"] * W, target["y"] * H, target["r"] * W, True)
        elif faces:                                         # point at the face instead, no circle
            x, y, w, h = faces[0]
            pointer = ((x + w / 2) * W, (y + h / 2) * H, max(w * W, h * H) * 0.62, False)
        if pointer:
            s = 2                                            # draw at 2x, then shrink: smooth edges
            over = Image.new("RGBA", (W * s, H * s), (0, 0, 0, 0))
            d = ImageDraw.Draw(over)
            px, py, pr, circle = pointer
            if circle:
                for width, colour, grow in ((34, (0, 0, 0, 90), 6), (28, WHITE, 0), (18, RED, 0)):
                    rr = (pr + grow) * s
                    d.ellipse((px * s - rr, py * s - rr, px * s + rr, py * s + rr), outline=colour, width=width * s)
            # from beside the text towards the target, ending at the circle edge
            sx = W * (0.3 if px > W / 2 else 0.7)
            sy = (ly + layer.height + 30) if not at_bottom else (ly - 30)
            ang = math.atan2(py - sy, px - sx)
            gap = pr * (1.15 if circle else 0.95)
            ex, ey = px - math.cos(ang) * gap, py - math.sin(ang) * gap
            if math.hypot(ex - sx, ey - sy) > 140:
                _arrow(d, (sx * s, sy * s), (ex * s, ey * s), s)
                img.alpha_composite(over.resize((W, H), Image.LANCZOS))
                drawn["mark"] = "circle" if circle else "face"
    img.convert("RGB").save(out, "JPEG", quality=92, optimize=True)
    return drawn


# ---------------------------------------------------------------- the words

EMOJI_RE = re.compile("[\U0001F000-\U0001FAFF☀-➿️‍#]")

CATEGORY_TEXT = {  # (en, fr) when there is no LLM
    "funny": ("NO WAY?!", "PAS POSSIBLE?!"), "scary": ("DON'T LOOK...", "NE REGARDE PAS..."),
    "action": ("HE DID WHAT?!", "IL A FAIT QUOI?!"), "emotional": ("THIS HURTS...", "ÇA FAIT MAL..."),
    "shocking": ("WAIT FOR IT...", "ATTENDS LA FIN..."), "romantic": ("THE CHEMISTRY...", "CETTE ALCHIMIE..."),
    "hype": ("INSANE!", "C'EST FOU!"), "drama": ("IT GOT HEATED", "ÇA DÉGÉNÈRE"),
    "insight": ("NOBODY TELLS YOU", "PERSONNE NE LE DIT"), "story": ("THEN THIS HAPPENED...", "ET LÀ..."),
}


def clean_text(text: str, max_len: int) -> str:
    text = re.sub(r"[\"“”«»]", "", EMOJI_RE.sub("", text or ""))
    text = re.sub(r"\s+", " ", text).strip(" '").rstrip(",;:").upper()
    if text.endswith(".") and not text.endswith("..."):
        text = text.rstrip(".")
    if len(text) > max_len:
        text = text[:max_len].rsplit(" ", 1)[0].rstrip(",;:") + "..."
    return text


def rule_text(clip: dict, language: str) -> tuple[str, str]:
    """Headline and tag without an LLM: the moment type's punch line, and the clip's main word."""
    from .posts import _keywords
    cat = CATEGORY_TEXT.get(clip.get("category") or (clip.get("tag") or {}).get("id") or "", ("WAIT FOR IT...", "ATTENDS LA FIN..."))
    words = _keywords(clip.get("title") or "", 1)  # the title names the subject; speech is full of filler
    return cat[1] if language == "fr" else cat[0], clean_text(words[0], 16) if words else ""


def _plain(text: str) -> str:
    return re.sub(r"[^\w ]", "", EMOJI_RE.sub("", text or "")).strip().lower()


VAGUE_TAGS = {"", "funny", "shock", "shocking", "hype", "insane", "crazy", "wow", "omg", "viral", "drama",
              "emotional", "scary", "action", "story", "insight", "romantic", "epic", "lol", "fyp",
              "drôle", "choc", "fou", "dingue"}


THUMB_SCHEMA = {
    "type": "array",
    "items": {"type": "object", "properties": {"id": {"type": "integer"}, "text": {"type": "string"},
                                                 "tag": {"type": "string"}, "scene": {"type": "string"}},
              "required": ["id", "text", "tag", "scene"]},
}

THUMB_SYSTEM = """You write the big words on the cover image (thumbnail) of short vertical videos (YouTube Shorts,
TikTok, Reels), in {language}. People see it for half a second in a grid, next to the video title.
For every clip:
- text: 2 to 4 words, at most 24 characters, capital letters. It must ADD curiosity, never repeat the title:
  a bold claim, a number or a price that is really in the clip, a short question, or an unfinished thought
  ending with "...". End with "?!", "!" or "..." when it fits. Use the clip's real subject (a name, a thing,
  a place) when there is one. Never invent facts. No emojis, no hashtags.
- tag: an optional small label of 1 to 3 words (at most 16 characters, capitals) naming a thing, a place,
  a name or a situation you can see in the clip, never a feeling or the kind of moment. Use an empty string
  when nothing fits.
- scene: in English, 12 to 30 words describing the picture for an image AI: who or what is in the clip,
  where, what they are doing, the emotion (shocked face, speed, danger...). Only what the clip's words and
  title make likely, nothing invented. No text, no names of real people.
Every clip gets different words.

Answer with strict JSON only: [{{"id": integer, "text": string, "tag": string, "scene": string}}]"""


def write_texts(clips: list[dict], language: str, provider, log: Log, video_title: str = "") -> str:
    """Set clip["thumb_text"] and clip["thumb_tag"] on every clip. Returns the method used."""
    import requests
    from .moments import InvalidOutput, _ask
    method = "rules"
    if provider is not None:
        listing = [{"id": i, "title": (c.get("post") or {}).get("title") or c.get("title"),
                    "kind_of_moment": c.get("category") or "", "text": (c.get("text") or "")[:600]}
                   for i, c in enumerate(clips)]

        def validate(items: list) -> tuple[list, list]:
            good, problems, seen = [], [], set()
            for it in items:
                try:
                    cid, raw = int(it["id"]), clean_text(str(it["text"]), 99)
                    text, tag = clean_text(raw, 26), clean_text(str(it.get("tag") or ""), 18)
                except (KeyError, TypeError, ValueError):
                    problems.append(f"bad item {str(it)[:80]}")
                    continue
                if len(raw.split()) > 4 or len(raw) > 26:
                    problems.append(f"id {cid}: {raw!r} is too long, use 2 to 4 short words")
                    continue
                if cid in seen or not 0 <= cid < len(clips) or not 1 <= len(text.split()) <= 5:
                    problems.append(f"id {cid}: missing, repeated, or not 2 to 4 words")
                    continue
                if text in (g["text"] for g in good):
                    problems.append(f"id {cid}: same words as another clip")
                    continue
                if _plain(text) == _plain(listing[cid]["title"] or ""):
                    problems.append(f"id {cid}: the text repeats the title, add something new")
                    continue
                if _plain(tag) in VAGUE_TAGS or _plain(tag) in _plain(text):
                    tag = ""  # "FUNNY", "SHOCK": says nothing a viewer can see
                seen.add(cid)
                good.append({"id": cid, "text": text, "tag": tag,
                             "scene": re.sub(r"\s+", " ", str(it.get("scene") or ""))[:300]})
            return good, problems

        try:
            context = f"The clips come from a video titled: {video_title}\n" if video_title else ""
            for it in _ask(provider, THUMB_SYSTEM.format(language=language),
                           context + "Clips:\n" + __import__("json").dumps(listing, ensure_ascii=False, indent=1)
                           + f"\n\nWrite the cover words for all {len(clips)} clips as a JSON array.",
                           THUMB_SCHEMA, validate, log, "thumbnails"):
                c = clips[it["id"]]
                if not c.get("thumb_text"):  # words already there may be the user's own edit: keep them
                    c.update(thumb_text=it["text"], thumb_tag=it["tag"])
                c["thumb_scene"] = it["scene"]
            method = provider.name
        except (requests.RequestException, InvalidOutput, RuntimeError, KeyError) as exc:
            log(f"LLM thumbnail words failed ({exc!r}); using rules")
    for c in clips:
        if not c.get("thumb_text"):
            c["thumb_text"], c["thumb_tag"] = rule_text(c, language)
            if method != "rules" and "+ rules" not in method:
                method += " + rules"
    return method


def ai_base(out: Path, t: float, strength: str) -> Path:
    """Where the AI-redrawn version of the frame at t is kept (so redrawing words is instant)."""
    return out.with_name(f"{out.stem}_ai_{round(t * 10)}_{strength}.png")


def prepare_ai(todo: list[tuple[Path, Path, float, str]], strength: str, log: Log,
               progress: Callable[[float, str], None] | None = None) -> None:
    """AI-redraw the frames that are not redrawn yet. todo: [(clip, thumbnail out path, time, scene)].
    Raises redraw.RedrawError when it cannot run."""
    from . import redraw
    missing = [(c, o, t, sc) for c, o, t, sc in todo if not ai_base(o, t, strength).is_file()]
    if not missing:
        return
    items = []
    for i, (clip, out, t, scene) in enumerate(missing):
        src = out.with_name(f"{out.stem}_frame.png")
        grab(clip, t).save(src)
        items.append({"src": str(src), "out": str(ai_base(out, t, strength)), "strength": strength, "scene": scene, "seed": 7 + i})
    try:
        redraw.redraw_many(items, log, progress)
    finally:
        for it in items:
            Path(it["src"]).unlink(missing_ok=True)


SCENE_HINT = {  # without an LLM: the moment type sets the mood of the redraw
    "funny": "people laughing hard, funny reaction", "scary": "dark tense scary moment, fear",
    "action": "intense action, speed, motion blur, dust", "emotional": "emotional faces, tears, warm light",
    "shocking": "shocked faces, wide eyes, open mouth", "romantic": "romantic couple, warm golden light",
    "hype": "hyped crowd, energy, celebration", "drama": "heated argument, tension, angry faces",
    "insight": "person explaining something important, focused", "story": "person telling a story, expressive face",
}


def scene_for(clip: dict, video_title: str = "") -> str:
    if clip.get("thumb_scene"):
        return clip["thumb_scene"]
    cat = clip.get("category") or (clip.get("tag") or {}).get("id") or ""
    return ", ".join(x for x in (SCENE_HINT.get(cat, ""), clip.get("title") or "", video_title) if x)


def clear_ai(out: Path) -> None:
    for old in out.parent.glob(f"{out.stem}_ai_*.png"):
        old.unlink(missing_ok=True)


def make(clip_path: Path, duration: float, out: Path, text: str, tag: str, log: Log,
         frame: int = 0, frames: list[dict] | None = None, marks: bool = True,
         ai: bool = False, strength: str = "normal") -> dict:
    """Pick the frames (once), render frame number `frame` of them. With ai=True the redrawn frame
    made by prepare_ai() is used when there is one. Returns the thumb record."""
    from PIL import Image
    frames = frames or pick_frames(clip_path, duration, log)
    pick = frames[frame % len(frames)]
    base = ai_base(out, pick["time"], strength)
    used_ai = ai and base.is_file()
    img = Image.open(base).convert("RGB") if used_ai else grab(clip_path, pick["time"])
    drawn = render(img, text, tag, pick["faces"], out, marks=marks)
    log(f"{out.name}: frame {pick['time']:.1f} s (score {pick['score']}){', AI redraw' if used_ai else ''}, {drawn}")
    return {"file": out.name, "text": text, "tag": tag, "frame": frame % len(frames), "frames": frames,
            "time": pick["time"], "marks": marks, "ai": used_ai, "strength": strength, **drawn}
