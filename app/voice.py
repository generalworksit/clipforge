"""AI voice intro: a narrator (Kokoro TTS, runs locally) reads a one-line hook before the clip.

Two modes:
- "over":  the voice plays over the clip's first seconds, the original sound ducked underneath,
           with the hook shown as a text bubble.
- "intro": the clip's clear first frame stays paused with the hook in big letters
           while the voice reads it, then the complete clip starts from the beginning.

The model (about 350 MB) downloads into models/kokoro on first use.
"""
from __future__ import annotations

import re
import threading
from pathlib import Path
from typing import Callable

import requests

from .config import CONFIG, ROOT
from .video import _aac_args, _video_codec_args, run_ffmpeg

Log = Callable[[str], None]
Progress = Callable[[float, str], None]

MODEL_DIR = ROOT / "models" / "kokoro"
RELEASE = "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/"
MODEL_FILES = {"kokoro-v1.0.onnx": 325_532_387, "voices-v1.0.bin": 28_214_398}

# Kokoro v1.0 voices, best first within each language. lang: Whisper language code.
VOICES = [
    {"id": "am_michael", "label": "Michael", "desc": "calm narrator", "lang": "en", "accent": "US", "gender": "m"},
    {"id": "am_fenrir", "label": "Fenrir", "desc": "deep, dramatic", "lang": "en", "accent": "US", "gender": "m"},
    {"id": "am_adam", "label": "Adam", "desc": "deep TikTok storyteller", "lang": "en", "accent": "US", "gender": "m"},
    {"id": "am_puck", "label": "Puck", "desc": "upbeat, fast", "lang": "en", "accent": "US", "gender": "m"},
    {"id": "am_echo", "label": "Echo", "desc": "young, casual", "lang": "en", "accent": "US", "gender": "m"},
    {"id": "af_heart", "label": "Heart", "desc": "warm, natural", "lang": "en", "accent": "US", "gender": "f"},
    {"id": "af_bella", "label": "Bella", "desc": "energetic", "lang": "en", "accent": "US", "gender": "f"},
    {"id": "af_nicole", "label": "Nicole", "desc": "soft, ASMR", "lang": "en", "accent": "US", "gender": "f"},
    {"id": "af_sky", "label": "Sky", "desc": "bright, young", "lang": "en", "accent": "US", "gender": "f"},
    {"id": "bm_george", "label": "George", "desc": "British documentary", "lang": "en", "accent": "UK", "gender": "m"},
    {"id": "bm_fable", "label": "Fable", "desc": "British storyteller", "lang": "en", "accent": "UK", "gender": "m"},
    {"id": "bf_emma", "label": "Emma", "desc": "British, clear", "lang": "en", "accent": "UK", "gender": "f"},
    {"id": "ff_siwis", "label": "Siwis", "desc": "voix française", "lang": "fr", "accent": "FR", "gender": "f"},
    {"id": "em_alex", "label": "Alex", "desc": "voz española", "lang": "es", "accent": "ES", "gender": "m"},
    {"id": "ef_dora", "label": "Dora", "desc": "voz española", "lang": "es", "accent": "ES", "gender": "f"},
    {"id": "im_nicola", "label": "Nicola", "desc": "voce italiana", "lang": "it", "accent": "IT", "gender": "m"},
    {"id": "if_sara", "label": "Sara", "desc": "voce italiana", "lang": "it", "accent": "IT", "gender": "f"},
    {"id": "pm_alex", "label": "Alex", "desc": "voz brasileira", "lang": "pt", "accent": "BR", "gender": "m"},
    {"id": "pf_dora", "label": "Dora", "desc": "voz brasileira", "lang": "pt", "accent": "BR", "gender": "f"},
]
VOICE_BY_ID = {v["id"]: v for v in VOICES}
KOKORO_LANG = {"a": "en-us", "b": "en-gb", "f": "fr-fr", "e": "es", "i": "it", "p": "pt-br"}
MODES = {"over": "Over the first seconds", "intro": "Separate intro"}
SAMPLES = {
    "en": "This guy climbed a mountain with frozen hands, and what he said at the top hit different.",
    "fr": "Ce mec a grimpé une montagne avec les mains gelées, et ce qu'il dit au sommet va te surprendre.",
    "es": "Este chico subió una montaña con las manos congeladas, y lo que dijo arriba te va a sorprender.",
    "it": "Questo ragazzo ha scalato una montagna con le mani congelate, e quello che dice in cima ti sorprenderà.",
    "pt": "Esse cara subiu uma montanha com as mãos congeladas, e o que ele disse no topo vai te surpreender.",
}

_lock = threading.Lock()
_engine = None


class VoiceError(RuntimeError):
    pass


def model_ready() -> bool:
    return all((MODEL_DIR / name).is_file() and (MODEL_DIR / name).stat().st_size == size
               for name, size in MODEL_FILES.items())


def ensure_model(log: Log, progress: Progress | None = None) -> None:
    """Download the Kokoro model files once (resumes nothing: a partial file is re-fetched)."""
    if model_ready():
        return
    MODEL_DIR.mkdir(parents=True, exist_ok=True)
    total = sum(MODEL_FILES.values())
    done = sum(MODEL_FILES[n] for n in MODEL_FILES if (MODEL_DIR / n).is_file()
               and (MODEL_DIR / n).stat().st_size == MODEL_FILES[n])
    for name, size in MODEL_FILES.items():
        path = MODEL_DIR / name
        if path.is_file() and path.stat().st_size == size:
            continue
        log(f"downloading {RELEASE}{name} ({size / 1e6:.0f} MB)")
        tmp = path.with_suffix(path.suffix + ".part")
        try:
            with requests.get(RELEASE + name, stream=True, timeout=30) as r:
                r.raise_for_status()
                with open(tmp, "wb") as f:
                    for chunk in r.iter_content(1 << 20):
                        f.write(chunk)
                        done += len(chunk)
                        if progress:
                            progress(done / total * 0.9, f"downloading the voice model, {done / 1e6:.0f}/{total / 1e6:.0f} MB")
        except requests.RequestException as exc:
            tmp.unlink(missing_ok=True)
            raise VoiceError(f"Could not download the voice model ({exc}). Check the internet connection "
                             "and click Retry.") from exc
        if tmp.stat().st_size != size:
            tmp.unlink(missing_ok=True)
            raise VoiceError(f"The voice model download was incomplete ({name}). Click Retry.")
        tmp.replace(path)


def _kokoro():
    global _engine
    if _engine is None:
        from kokoro_onnx import Kokoro
        _engine = Kokoro(str(MODEL_DIR / "kokoro-v1.0.onnx"), str(MODEL_DIR / "voices-v1.0.bin"))
    return _engine


def choose_voice(voice_id: str, language: str) -> tuple[dict | None, str]:
    """The voice to use for a video in `language`. A voice must speak the video's language (the hook
    is written in it), so a mismatch falls back to the best voice of that language."""
    voice = VOICE_BY_ID.get(voice_id)
    if voice and voice["lang"] == language:
        return voice, ""
    same = [v for v in VOICES if v["lang"] == language]
    if not same:
        return None, f"no AI voice speaks this video's language ({language})"
    pick = next((v for v in same if voice and v["gender"] == voice["gender"]), same[0])
    return pick, f"{voice['label'] if voice else voice_id} does not speak '{language}', used {pick['label']}"


def synthesize(text: str, voice: dict, out_wav: Path, speed: float = 1.08) -> float:
    """Write the spoken text to a WAV file and return its length in seconds."""
    import soundfile as sf
    with _lock:  # one ONNX session, shared by the worker and the preview endpoint
        samples, rate = _kokoro().create(text, voice=voice["id"], speed=speed,
                                         lang=KOKORO_LANG[voice["id"][0]])
    sf.write(str(out_wav), samples, rate)
    return len(samples) / rate


def preview(voice_id: str, cache_dir: Path) -> Path:
    """A short sample of one voice, cached, for the voice picker."""
    voice = VOICE_BY_ID.get(voice_id)
    if voice is None:
        raise VoiceError("Unknown voice.")
    if not model_ready():
        raise VoiceError("The voice model is not downloaded yet.")
    cache_dir.mkdir(parents=True, exist_ok=True)
    path = cache_dir / f"{voice_id}.wav"
    if not path.is_file():
        synthesize(SAMPLES[voice["lang"]], voice, path)
    return path


# ---------------------------------------------------------------- hook text

EMOJI = re.compile(r"[\U0001F000-\U0001FAFF☀-➿️‍]+")


def clean_hook(text: str) -> str:
    text = EMOJI.sub("", text or "").replace("#", "")
    return re.sub(r"\s+", " ", text).strip(" -–—:")[:220]


TEASERS = {
    "en": ["Wait until you hear how this one ends.", "Nobody expected what happened next.",
           "Watch this until the very end.", "This is the moment everyone is talking about."],
    "fr": ["Attends de voir comment ça se termine.", "Personne ne s'attendait à ce qui arrive ensuite.",
           "Regarde bien jusqu'à la fin.", "C'est le moment dont tout le monde parle."],
    "es": ["Espera a ver cómo termina esto.", "Nadie esperaba lo que pasó después.",
           "Mira esto hasta el final.", "Este es el momento del que todos hablan."],
    "it": ["Aspetta di vedere come finisce.", "Nessuno si aspettava quello che è successo dopo.",
           "Guarda fino alla fine.", "Questo è il momento di cui parlano tutti."],
    "pt": ["Espera só pra ver como isso termina.", "Ninguém esperava o que aconteceu depois.",
           "Assiste até o final.", "Esse é o momento que todo mundo está comentando."],
}


def default_hook(index: int, language: str) -> str:
    """Without an LLM there is no clip-specific hook, so use a generic teaser (editable on the results page)."""
    lines = TEASERS.get(language, TEASERS["en"])
    return lines[(index - 1) % len(lines)]


# ---------------------------------------------------------------- rendering

def _ass_time(t: float) -> str:
    cs = int(round(max(t, 0.0) * 100))
    return f"{cs // 360000}:{cs // 6000 % 60:02d}:{cs // 100 % 60:02d}.{cs % 100:02d}"


def hook_ass(text: str, duration: float, mode: str, caption_position: str,
             size: tuple[int, int] | None = None) -> str:
    """Intro: big centred white words. Over: a white TikTok-style text bubble kept away from the
    burned captions (top, or lower third when the captions sit at the top). Sizes are designed for
    1080x1920 and scaled for other videos (a horizontal one keeps the same text height)."""
    w, h = size or (CONFIG["video"]["width"], CONFIG["video"]["height"])
    k, kv = min(w, h) / 1080, h / 1920  # text scale, vertical margin scale
    px = lambda v: max(1, round(v * k))  # noqa: E731
    font = CONFIG["captions"]["font_name"]
    text = text.replace("\\", "/").replace("{", "(").replace("}", ")")
    if mode == "intro":
        style = (f"Style: Hook,{font},{px(118)},&H00FFFFFF,&H00FFFFFF,&H00000000,&H90000000,0,0,0,0,100,100,1,0,1,"
                 f"{px(8)},{px(5)},5,{px(110)},{px(110)},0,1")
        body = f"{{\\fad(120,0)\\fscx80\\fscy80\\t(0,180,\\fscx100\\fscy100)}}{text.upper()}"
    else:
        top = caption_position != "top"
        align, margin = (8, round(330 * kv)) if top else (2, round(430 * kv))
        style = (f"Style: Hook,{font},{px(86)},&H00101010,&H00101010,&H00FFFFFF,&H00FFFFFF,0,0,0,0,100,100,0,0,3,"
                 f"{px(20)},0,{align},{px(140)},{px(140)},{margin},1")
        body = f"{{\\fad(120,180)}}{text}"
    return "\n".join([
        "[Script Info]", "ScriptType: v4.00+", f"PlayResX: {w}", f"PlayResY: {h}", "WrapStyle: 0",
        "ScaledBorderAndShadow: yes", "YCbCr Matrix: TV.709", "",
        "[V4+ Styles]",
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, "
        "Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, "
        "Alignment, MarginL, MarginR, MarginV, Encoding",
        style, "",
        "[Events]", "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
        f"Dialogue: 0,{_ass_time(0)},{_ass_time(duration)},Hook,,0,0,0,,{body}",
    ]) + "\n"


VOICE_FILTER = "aresample=48000,aformat=channel_layouts=stereo,loudnorm=I=-16:TP=-1.5:LRA=11"


def render(job_dir: Path, clip_name: str, captioned: str, clean: str, hook: str, voice: dict,
           mode: str, caption_position: str, log: Log,
           size: tuple[int, int] | None = None) -> tuple[str, float]:
    """Make clip_NN_voice.mp4 from the captioned clip. Returns (file name, voice length).
    size: the video's width and height when it is not the clip format (the AI voice tool)."""
    size = size or (CONFIG["video"]["width"], CONFIG["video"]["height"])
    wav, ass = f"{clip_name}_voice.wav", f"{clip_name}_hook.ass"
    spoken = synthesize(hook, voice, job_dir / wav)
    log(f"{clip_name}: voice {voice['id']} {spoken:.2f}s: {hook}")
    out = f"{clip_name}_voice.mp4"
    if mode == "intro":
        length = spoken + 0.45
        (job_dir / ass).write_text(hook_ass(hook, length, mode, caption_position, size), encoding="utf-8")
        frame, intro = f"{clip_name}_frame.png", f"{clip_name}_intro.mp4"
        run_ffmpeg(["-y", "-i", clean, "-frames:v", "1", frame], log, cwd=job_dir)
        fps = CONFIG["video"]["fps"]
        run_ffmpeg([
            "-y", "-loop", "1", "-framerate", str(fps), "-t", f"{length:.3f}", "-i", frame, "-i", wav,
            "-filter_complex",
            f"[0:v]scale={size[0]}:{size[1]},subtitles={ass}:fontsdir=fonts,format=yuv420p,setsar=1[v];"
            f"[1:a]{VOICE_FILTER},apad[a]",
            "-map", "[v]", "-map", "[a]", "-t", f"{length:.3f}", "-r", str(fps),
            *_video_codec_args(), *_aac_args(), intro,
        ], log, cwd=job_dir)
        run_ffmpeg([
            "-y", "-i", intro, "-i", captioned, "-filter_complex",
            "[0:v]setpts=PTS-STARTPTS[v0];[0:a]asetpts=PTS-STARTPTS[a0];"
            "[1:v]setpts=PTS-STARTPTS[v1];[1:a]asetpts=PTS-STARTPTS[a1];"
            "[v0][a0][v1][a1]concat=n=2:v=1:a=1[v][a]",
            "-map", "[v]", "-map", "[a]", *_video_codec_args(), *_aac_args(), "-movflags", "+faststart", out,
        ], log, cwd=job_dir)
        for name in (frame, intro):
            (job_dir / name).unlink(missing_ok=True)
    else:
        length = spoken + 0.3
        (job_dir / ass).write_text(hook_ass(hook, length, mode, caption_position, size), encoding="utf-8")
        # original sound at 20 % under the voice, back to full over 0.4 s after it
        duck = f"volume='0.2+0.8*clip((t-{spoken:.3f})/0.4,0,1)':eval=frame"
        run_ffmpeg([
            "-y", "-i", captioned, "-i", wav, "-filter_complex",
            f"[0:v]subtitles={ass}:fontsdir=fonts,format=yuv420p[v];"
            f"[0:a]{duck}[orig];[1:a]{VOICE_FILTER}[vo];"
            f"[orig][vo]amix=inputs=2:duration=first:normalize=0[a]",
            "-map", "[v]", "-map", "[a]", *_video_codec_args(), *_aac_args(), "-movflags", "+faststart", out,
        ], log, cwd=job_dir)
    return out, spoken


def system_info() -> dict:
    size = sum((MODEL_DIR / n).stat().st_size for n in MODEL_FILES if (MODEL_DIR / n).is_file())
    try:
        import kokoro_onnx  # noqa: F401
        installed = True
    except ImportError:
        installed = False
    return {"installed": installed, "model_ready": model_ready(), "model_mb": round(size / 1e6),
            "download_mb": round(sum(MODEL_FILES.values()) / 1e6), "voices": len(VOICES)}
