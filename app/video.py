"""ffmpeg/ffprobe helpers: probing, cutting + reframing to 9:16, burning captions."""
from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from .config import CONFIG

Log = Callable[[str], None]
Progress = Callable[[float, str], None]


class FfmpegError(RuntimeError):
    pass


def require_binaries() -> None:
    missing = [b for b in ("ffmpeg", "ffprobe") if shutil.which(b) is None]
    if missing:
        raise FfmpegError(
            f"{' and '.join(missing)} not found on PATH. Install it (Windows: winget install Gyan.FFmpeg) "
            "and restart the app."
        )


def run_ffmpeg(args: list[str], log: Log, cwd: Path | None = None,
               duration: float = 0.0, progress: Progress | None = None, label: str = "") -> None:
    """Run ffmpeg, log the command and its full stderr, raise with the stderr tail on failure.
    With duration and progress, report how far the encode is (for long, whole-video encodes)."""
    report = progress is not None and duration > 0
    cmd = ["ffmpeg", "-hide_banner", "-nostats", *(["-progress", "pipe:1"] if report else []), *args]
    log("$ " + subprocess.list2cmdline(cmd) + (f"   (cwd: {cwd})" if cwd else ""))
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    if report:
        # stderr goes to a file so a full pipe can never stall ffmpeg while we read progress
        with tempfile.TemporaryFile("w+", encoding="utf-8", errors="replace") as err:
            proc = subprocess.Popen(cmd, cwd=cwd, stdout=subprocess.PIPE, stderr=err, text=True,
                                    encoding="utf-8", errors="replace", creationflags=flags)
            for line in proc.stdout:
                if line.startswith("out_time_us="):
                    try:
                        done = int(line.split("=", 1)[1]) / 1e6
                    except ValueError:
                        continue
                    progress(min(done / duration, 1.0), f"{label}{done / duration:.0%}")
            proc.wait()
            proc.stdout.close()
            err.seek(0)
            stderr = err.read()
        returncode = proc.returncode
    else:
        proc = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, encoding="utf-8",
                              errors="replace", creationflags=flags)
        stderr, returncode = proc.stderr, proc.returncode
    if stderr.strip():
        log(stderr.rstrip())
    if returncode != 0:
        tail = "\n".join(stderr.strip().splitlines()[-6:])
        raise FfmpegError(f"ffmpeg exited with code {returncode}:\n{tail}")


@dataclass
class MediaInfo:
    duration: float
    width: int          # display width (rotation applied, as ffmpeg autorotate does)
    height: int
    has_audio: bool
    has_video: bool


def probe(path: Path) -> MediaInfo:
    cmd = ["ffprobe", "-v", "error", "-print_format", "json", "-show_format", "-show_streams", str(path)]
    proc = subprocess.run(
        cmd, capture_output=True, text=True, encoding="utf-8", errors="replace",
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    if proc.returncode != 0:
        raise FfmpegError(f"ffprobe could not read {path.name}: {proc.stderr.strip()[-400:]}")
    data = json.loads(proc.stdout)
    streams = data.get("streams", [])
    video = next((s for s in streams if s.get("codec_type") == "video" and not s.get("disposition", {}).get("attached_pic")), None)
    audio = next((s for s in streams if s.get("codec_type") == "audio"), None)
    width = height = 0
    if video:
        width, height = int(video.get("width", 0)), int(video.get("height", 0))
        rotation = 0
        for side in video.get("side_data_list", []) or []:
            if "rotation" in side:
                rotation = int(float(side["rotation"]))
        if "rotate" in (video.get("tags") or {}):
            rotation = int(float(video["tags"]["rotate"]))
        if abs(rotation) % 180 == 90:
            width, height = height, width
    duration = float(data.get("format", {}).get("duration") or (video or audio or {}).get("duration") or 0)
    return MediaInfo(duration=duration, width=width, height=height, has_audio=audio is not None, has_video=video is not None)


def extract_audio(src: Path, dst: Path, log: Log) -> None:
    run_ffmpeg(["-y", "-i", str(src), "-vn", "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", str(dst)], log)


# ---------------------------------------------------------------- reframing
# A reframer decides which part of the source frame becomes the vertical clip.
# Swap in a face-tracking reframer later by adding it to REFRAMERS and setting
# video.reframe in config.yaml. Signature: (info, start, end) -> CropBox | None,
# where None means "keep the whole frame" (scaled to fit, padded if needed).

@dataclass
class CropBox:
    x: int
    y: int
    w: int
    h: int


def center_crop(info: MediaInfo, start: float, end: float) -> CropBox | None:
    out_w, out_h = CONFIG["video"]["width"], CONFIG["video"]["height"]
    if info.width <= info.height:
        return None  # vertical or square source: leave the framing untouched
    crop_w = min(info.width, int(round(info.height * out_w / out_h / 2)) * 2)
    return CropBox(x=(info.width - crop_w) // 2, y=0, w=crop_w, h=info.height)


REFRAMERS: dict[str, Callable[[MediaInfo, float, float], CropBox | None]] = {
    "center": center_crop,
}


def reframe_filter(box: CropBox | None) -> str:
    v = CONFIG["video"]
    w, h, fps = v["width"], v["height"], v["fps"]
    if box is None:
        geom = (f"scale={w}:{h}:force_original_aspect_ratio=decrease:flags=lanczos,"
                f"pad={w}:{h}:(ow-iw)/2:(oh-ih)/2:color=black")
    else:
        geom = f"crop={box.w}:{box.h}:{box.x}:{box.y},scale={w}:{h}:flags=lanczos"
    return f"{geom},setsar=1,fps={fps},format=yuv420p"


def _video_codec_args() -> list[str]:
    v = CONFIG["video"]
    enc = v["encoder"]
    if "nvenc" in enc:
        return ["-c:v", enc, "-preset", "p5", "-rc", "vbr", "-cq", str(v["crf"]), "-b:v", "0"]
    return ["-c:v", enc, "-preset", v["preset"], "-crf", str(v["crf"])]


def _aac_args() -> list[str]:
    return ["-c:a", "aac", "-b:a", CONFIG["video"]["audio_bitrate"], "-ar", "48000", "-ac", "2"]


def blur_fit_graph(inp: str = "[0:v:0]") -> str:
    """Whole frame centred over a blurred, zoomed copy of itself (how film clips are usually posted)."""
    v = CONFIG["video"]
    w, h = v["width"], v["height"]
    return (f"{inp}split[a][b];[a]scale={w}:{h}:force_original_aspect_ratio=increase,crop={w}:{h},"
            f"boxblur=24:2[bg];[b]scale={w}:{h}:force_original_aspect_ratio=decrease:flags=lanczos[fg];"
            f"[bg][fg]overlay=(W-w)/2:(H-h)/2,setsar=1,fps={v['fps']},format=yuv420p[v]")


def cut_clip(src: Path, info: MediaInfo, start: float, end: float, dst: Path, log: Log,
             framing: str = "crop", erase_subs: bool = False) -> dict | None:
    """Accurate cut (input seek + re-encode), reframed to the configured vertical size.
    framing "blur" keeps the whole picture over a blurred background instead of cropping.
    erase_subs hides subtitles burned into the source first. Returns the hidden box, if any."""
    v = CONFIG["video"]
    hidden = None
    inp = "[0:v:0]"
    pre = ""
    if erase_subs:
        from . import hardsubs
        hidden = hardsubs.find_band(src, info.width, info.height, start, end, log)
        if hidden:
            pre = f"[0:v:0]{hardsubs.erase_chain(hidden)}[hs_out];"
            inp = "[hs_out]"
    graph = None
    if framing in ("face", "split") and info.width > info.height:
        from . import faces
        graph = faces.vertical_filter(src, info.width, info.height, start, end, framing, log)
    if graph:
        log(f"reframe {start:.2f}-{end:.2f}: {framing}")
        video = ["-filter_complex", f"{pre}{inp}{graph},setsar=1,fps={v['fps']},format=yuv420p[v]", "-map", "[v]"]
    elif framing == "blur" and info.width > info.height:
        log(f"reframe {start:.2f}-{end:.2f}: whole frame over blurred background")
        video = ["-filter_complex", pre + blur_fit_graph(inp), "-map", "[v]"]
    else:
        reframer = REFRAMERS[CONFIG["video"]["reframe"]]
        box = reframer(info, start, end)
        log(f"reframe {start:.2f}-{end:.2f}: {'full frame (fit)' if box is None else box}")
        video = ["-filter_complex", f"{pre}{inp}{reframe_filter(box)}[v]", "-map", "[v]"]
    run_ffmpeg([
        "-y", "-ss", f"{start:.3f}", "-i", str(src), "-t", f"{end - start:.3f}",
        *video, "-map", "0:a:0",
        *_video_codec_args(),
        *_aac_args(), "-movflags", "+faststart", str(dst),
    ], log)
    return {"box": list(hidden)} if hidden else None


def burn_captions(job_dir: Path, clean_name: str, ass_name: str, out_name: str, log: Log,
                  duration: float = 0.0, progress: Progress | None = None, copy_audio: bool = True,
                  pre_filter: str = "") -> None:
    """Burn an .ass file into a video. Runs inside the job folder with relative names so the
    filtergraph never has to escape Windows drive letters; fonts are read from job_dir/fonts.
    pre_filter runs on the picture first (hiding burned-in subtitles)."""
    chain = f"subtitles={ass_name}:fontsdir=fonts,format=yuv420p"
    run_ffmpeg([
        "-y", "-i", clean_name,
        "-map", "[v]" if pre_filter else "0:v:0", "-map", "0:a?",
        *(["-filter_complex", f"[0:v:0]{pre_filter},{chain}[v]"] if pre_filter else ["-vf", chain]),
        *_video_codec_args(),
        *(["-c:a", "copy"] if copy_audio else _aac_args()), "-movflags", "+faststart", out_name,
    ], log, cwd=job_dir, duration=duration, progress=progress)
