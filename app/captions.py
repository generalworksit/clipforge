"""Stage 5 helpers: word-timed .ass captions (karaoke-style highlight) and .srt files."""
from __future__ import annotations

from pathlib import Path

from .config import CONFIG

# ASS colours are &HAABBGGRR (alpha first, 00 = opaque, then blue, green, red).
WHITE = "&H00FFFFFF"
BLACK = "&H00000000"
YELLOW = "&H0000E1FF"   # #FFE100
CYAN = "&H00FFFF00"     # #00FFFF
GREEN = "&H006EFF3D"    # #3DFF6E
RED = "&H004040FF"      # #FF4040
PURPLE = "&H00ED3A7C"   # #7C3AED
PINK = "&H00B45AFF"     # #FF5AB4
ORANGE = "&H00008CFF"   # #FF8C00

_BASE = {
    "primary": WHITE, "outline_colour": BLACK, "back": "&H80000000", "outline": 8, "shadow": 3, "spacing": 1,
    "highlight": None,   # colour of the word being spoken (None = no per-word highlight)
    "cycle": None,       # list of highlight colours rotating per caption group
    "hl_scale": 100,     # % size of the spoken word
    "hl_box": None,      # colour of a thick block behind the spoken word
    "glow": False,       # blurred glow behind the spoken word, in the highlight colour
    "font": None,        # family name of a font in assets/fonts (None = captions.font_name)
    "karaoke": False,    # words fill with the highlight colour as they are spoken
    "reveal": False,     # words appear one by one as they are spoken
    "bounce": False,     # the spoken word pops in from a bigger size
    "tilt": 0,           # rotation in degrees
    "extra": "", "upper": True, "max_words": 4, "max_chars": 18, "min_words": 2,
}

PRESETS = {
    "none": {**_BASE, "label": "No captions", "size": 150},
    "bold": {**_BASE, "label": "Bold", "size": 150, "outline": 9, "shadow": 4, "highlight": YELLOW},
    "clean": {**_BASE, "label": "Clean", "size": 100, "outline_colour": "&H90000000", "back": "&HA0000000",
              "outline": 1.5, "shadow": 4, "spacing": 2, "extra": r"\blur1.5", "upper": False,
              "max_words": 5, "max_chars": 26},
    "neon": {**_BASE, "label": "Neon", "size": 140, "outline_colour": "&H00402010", "back": "&H00000000",
             "outline": 3, "shadow": 0, "highlight": CYAN, "glow": True},
    "pop": {**_BASE, "label": "Pop", "size": 150, "outline": 9, "shadow": 0, "highlight": GREEN,
            "hl_scale": 122, "max_words": 3, "max_chars": 16},
    "box": {**_BASE, "label": "Box", "size": 138, "outline": 4, "shadow": 0, "highlight": WHITE,
            "hl_box": PURPLE, "max_words": 3, "max_chars": 16},
    "solo": {**_BASE, "label": "One word", "size": 200, "outline": 11, "shadow": 5, "max_words": 1,
             "min_words": 1, "max_chars": 40},
    "hype": {**_BASE, "label": "Hype", "size": 150, "outline": 10, "shadow": 5, "highlight": YELLOW,
             "cycle": [YELLOW, GREEN, RED], "hl_scale": 112, "max_words": 3, "max_chars": 16},
    "karaoke": {**_BASE, "label": "Karaoke", "size": 140, "outline": 8, "shadow": 3, "highlight": YELLOW,
                "karaoke": True, "max_words": 4, "max_chars": 20},
    "podcast": {**_BASE, "label": "Podcast", "font": "Poppins ExtraBold", "size": 112, "outline": 7, "shadow": 4,
                "highlight": GREEN, "cycle": [GREEN, YELLOW], "hl_scale": 108, "max_words": 3, "max_chars": 18},
    "marker": {**_BASE, "label": "Marker", "font": "Poppins ExtraBold", "size": 108, "outline": 6, "shadow": 0,
               "highlight": BLACK, "hl_box": YELLOW, "max_words": 3, "max_chars": 18},
    "comic": {**_BASE, "label": "Comic", "font": "Bangers", "size": 165, "outline": 9, "shadow": 6, "spacing": 3,
              "primary": YELLOW, "highlight": WHITE, "hl_scale": 115, "tilt": 3, "max_words": 3, "max_chars": 16},
    "bounce": {**_BASE, "label": "Bounce", "size": 150, "outline": 9, "shadow": 4, "highlight": PINK,
               "bounce": True, "max_words": 3, "max_chars": 16},
    "typewriter": {**_BASE, "label": "Typewriter", "size": 140, "outline": 8, "shadow": 3, "highlight": ORANGE,
                   "reveal": True, "max_words": 5, "max_chars": 22},
    "minimal": {**_BASE, "label": "Minimal", "font": "Poppins SemiBold", "size": 92, "outline": 0, "shadow": 3,
                "back": "&H70000000", "upper": False, "extra": r"\blur0.8", "max_words": 6, "max_chars": 30},
    "traffic": {**_BASE, "label": "Red / green", "size": 148, "outline": 9, "shadow": 3,
                "highlight": GREEN, "cycle": [GREEN, RED, WHITE], "bounce": True, "max_words": 3, "max_chars": 17},
    "ice": {**_BASE, "label": "Ice glow", "size": 138, "outline": 3, "shadow": 0,
            "highlight": CYAN, "glow": True, "reveal": True, "max_words": 3, "max_chars": 17},
    "headline": {**_BASE, "label": "Headline", "font": "Poppins ExtraBold", "size": 115,
                 "outline": 7, "shadow": 2, "highlight": RED, "max_words": 4, "max_chars": 23},
    "lime": {**_BASE, "label": "Lime marker", "size": 130, "outline": 4, "shadow": 0,
             "highlight": BLACK, "hl_box": GREEN, "max_words": 3, "max_chars": 17},
    "impact": {**_BASE, "label": "Impact", "size": 185, "outline": 10, "shadow": 4,
               "highlight": RED, "bounce": True, "max_words": 1, "min_words": 1, "max_chars": 30},
    "editorial": {**_BASE, "label": "Editorial", "font": "Poppins SemiBold", "size": 100,
                  "outline": 3, "shadow": 2, "upper": False, "reveal": True, "max_words": 5, "max_chars": 27},
}

# Safe areas for 1080x1920: TikTok/Reels/Shorts cover roughly the bottom 20 %, the top 12 %
# and a column of buttons on the right, so text stays inside these margins.
POSITIONS = {
    "bottom": {"alignment": 2, "margin_v": 420},   # lower third, above the caption/username block
    "middle": {"alignment": 5, "margin_v": 0},
    "top": {"alignment": 8, "margin_v": 300},
}
MARGIN_LR = 130


def clip_words(words: list[dict], start: float, end: float) -> list[dict]:
    """Words inside [start, end], shifted to the clip's own timeline."""
    dur = end - start
    out = []
    for w in words:
        mid = (w["start"] + w["end"]) / 2
        if start <= mid <= end:
            out.append({"text": w["text"], "start": max(0.0, w["start"] - start), "end": min(dur, w["end"] - start)})
    return out


def group_words(words: list[dict], max_words: int = 4, max_chars: int = 18, min_words: int = 2) -> list[list[dict]]:
    """2-4 words per caption, breaking early after punctuation or a pause."""
    groups: list[list[dict]] = []
    cur: list[dict] = []
    for i, w in enumerate(words):
        cur.append(w)
        nxt = words[i + 1] if i + 1 < len(words) else None
        length = sum(len(x["text"]) + 1 for x in cur)
        punct = w["text"][-1:] in ".,!?;:…"
        pause = nxt is not None and nxt["start"] - w["end"] > 0.5
        too_long = nxt is not None and length + len(nxt["text"]) > max_chars
        if (nxt is None or len(cur) >= max_words
                or (len(cur) >= min_words and (punct or pause or too_long))):
            groups.append(cur)
            cur = []
    return groups


def _ts(t: float) -> str:
    cs = int(round(max(t, 0.0) * 100))
    h, cs = divmod(cs, 360000)
    m, cs = divmod(cs, 6000)
    s, cs = divmod(cs, 100)
    return f"{h}:{m:02d}:{s:02d}.{cs:02d}"


def _clean(text: str, upper: bool) -> str:
    text = text.replace("\\", "/").replace("{", "(").replace("}", ")").replace("\n", " ")
    return text.upper() if upper else text


def _spoken(word: str, p: dict, colour: str, k: float = 1.0) -> str:
    """Override tags for the word being spoken, closed with a reset to the line style."""
    tags = f"\\1c{colour}&"
    if p["bounce"]:  # starts at 140 % and settles to normal size in 0.15 s
        tags += "\\fscx140\\fscy140\\t(0,150,\\fscx100\\fscy100)"
    elif p["hl_scale"] != 100:
        tags += f"\\fscx{p['hl_scale']}\\fscy{p['hl_scale']}"
    if p["hl_box"]:
        tags += f"\\3c{p['hl_box']}&\\bord{round(22 * k, 1)}\\shad0"
    return f"{{{tags}}}{word}{{\\r}}"


def build_ass(words: list[dict], clip_duration: float, preset: str, position: str,
              size: tuple[int, int] | None = None) -> str:
    """Presets are tuned for a 1080x1920 canvas. For another size (a whole landscape video in the
    Subtitles tool) everything is scaled to the frame height and the margins are recomputed."""
    p = PRESETS[preset]
    pos = POSITIONS[position]
    font = p["font"] or CONFIG["captions"]["font_name"]
    w, h = size or (CONFIG["video"]["width"], CONFIG["video"]["height"])
    k = h / 1920
    if w > h:  # landscape: no app overlays to avoid, so sit close to the edge like TV subtitles
        k = h / 1450  # a 1080p frame gets 0.75x the vertical sizes (Bold: about 110 px)
        margin_lr, margin_v = round(w * 0.1), (round(h * 0.07) if position != "middle" else 0)
    else:
        margin_lr, margin_v = round(MARGIN_LR * w / 1080), round(pos["margin_v"] * k)
    sc = lambda v: round(v * k, 1)  # noqa: E731

    def style(name: str, primary: str, outline_colour: str, outline: float, shadow: float,
              secondary: str | None = None) -> str:
        return (f"Style: {name},{font},{sc(p['size'])},{primary},{secondary or primary},{outline_colour},"
                f"{p['back']},0,0,0,0,100,100,{sc(p['spacing'])},{p['tilt']},1,{sc(outline)},{sc(shadow)},{pos['alignment']},"
                f"{margin_lr},{margin_lr},{margin_v},1")

    lines = [
        "[Script Info]", "ScriptType: v4.00+", f"PlayResX: {w}", f"PlayResY: {h}",
        "WrapStyle: 0", "ScaledBorderAndShadow: yes", "YCbCr Matrix: TV.709", "",
        "[V4+ Styles]",
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, "
        "Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, "
        "Alignment, MarginL, MarginR, MarginV, Encoding",
        # karaoke: \kf fills each word from SecondaryColour (not sung yet) to PrimaryColour (sung)
        style("Main", p["highlight"] if p["karaoke"] else p["primary"], p["outline_colour"], p["outline"],
              p["shadow"], p["primary"] if p["karaoke"] else None),
    ]
    if p["glow"]:
        lines.append(style("Glow", p["primary"], p["highlight"], 12, 0))
    lines += ["", "[Events]", "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text"]

    if preset == "none":
        return "\n".join(lines) + "\n"

    groups = group_words(words, p["max_words"], p["max_chars"], p["min_words"])
    for gi, group in enumerate(groups):
        g_start = group[0]["start"]
        g_end = group[-1]["end"] + 0.6
        if gi + 1 < len(groups):
            g_end = min(g_end, groups[gi + 1][0]["start"])
        g_end = min(max(g_end, group[-1]["end"]), clip_duration)
        texts = [_clean(x["text"], p["upper"]) for x in group]
        colour = p["cycle"][gi % len(p["cycle"])] if p["cycle"] else p["highlight"]
        lead = f"{{{p['extra']}}}" if p["extra"] else ""

        if p["karaoke"]:
            parts, t = [], g_start
            for wi, (word, text) in enumerate(zip(group, texts)):
                nxt = group[wi + 1]["start"] if wi + 1 < len(group) else word["end"]
                parts.append(f"{{\\kf{max(1, round((nxt - t) * 100))}}}{text}")
                t = nxt
            lines.append(f"Dialogue: 0,{_ts(g_start)},{_ts(g_end)},Main,,0,0,0,,{lead}{' '.join(parts)}")
            continue
        if p["reveal"]:  # words not spoken yet keep their place but are invisible, so the line never shifts
            colour = colour or p["primary"]
        if not colour:
            lines.append(f"Dialogue: 0,{_ts(g_start)},{_ts(g_end)},Main,,0,0,0,,{lead}{' '.join(texts)}")
            continue
        for wi, word in enumerate(group):
            t0 = g_start if wi == 0 else word["start"]
            t1 = group[wi + 1]["start"] if wi + 1 < len(group) else g_end
            if t1 - t0 < 0.01:
                continue
            main = " ".join(_spoken(t, p, colour, k) if j == wi
                            else (f"{{\\alpha&HFF&}}{t}{{\\r}}" if p["reveal"] and j > wi else t)
                            for j, t in enumerate(texts))
            lines.append(f"Dialogue: 1,{_ts(t0)},{_ts(t1)},Main,,0,0,0,,{lead}{main}")
            if p["glow"]:
                # Invisible copy of the line (same layout) whose only visible part is a blurred
                # outline in the highlight colour behind the spoken word.
                glow = " ".join(f"{{\\3a&H30&}}{t}{{\\3a&HFF&}}" if j == wi else t for j, t in enumerate(texts))
                lines.append(f"Dialogue: 0,{_ts(t0)},{_ts(t1)},Glow,,0,0,0,,"
                             f"{{\\1a&HFF&\\3a&HFF&\\blur{round(10 * k, 1)}}}{glow}")
    return "\n".join(lines) + "\n"


def _srt_ts(t: float) -> str:
    ms = int(round(max(t, 0.0) * 1000))
    h, ms = divmod(ms, 3600000)
    m, ms = divmod(ms, 60000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def build_srt(words: list[dict], clip_duration: float) -> str:
    out = []
    groups = group_words(words, max_words=8, max_chars=42, min_words=3)
    for i, group in enumerate(groups):
        end = min(group[-1]["end"] + 0.3, clip_duration)
        if i + 1 < len(groups):
            end = min(end, groups[i + 1][0]["start"])
        out.append(f"{i + 1}\n{_srt_ts(group[0]['start'])} --> {_srt_ts(max(end, group[-1]['end']))}\n"
                   + " ".join(w["text"] for w in group) + "\n")
    return "\n".join(out)


def write_caption_files(words: list[dict], start: float, end: float, preset: str, position: str,
                        ass_path: Path, srt_path: Path) -> int:
    local = clip_words(words, start, end)
    ass_path.write_text(build_ass(local, end - start, preset, position), encoding="utf-8")
    srt_path.write_text(build_srt(local, end - start), encoding="utf-8")
    return len(local)


def build_vtt(words: list[dict], duration: float) -> str:
    """WebVTT twin of the SRT, used by browsers (<track>) and some editors."""
    srt = build_srt(words, duration)
    body = []
    for block in srt.strip().split("\n\n"):
        lines = block.split("\n")
        if len(lines) >= 3:
            body.append(lines[1].replace(",", ".") + "\n" + "\n".join(lines[2:]))
    return "WEBVTT\n\n" + "\n\n".join(body) + "\n"


def build_txt(segments: list[dict]) -> str:
    """Readable transcript: segments joined into paragraphs, a new paragraph after a long pause."""
    paras: list[list[str]] = [[]]
    last_end = None
    for seg in segments:
        if last_end is not None and seg["start"] - last_end > 2.0 and paras[-1]:
            paras.append([])
        paras[-1].append(seg["text"].strip())
        last_end = seg["end"]
    return "\n\n".join(" ".join(p) for p in paras if p) + "\n"
