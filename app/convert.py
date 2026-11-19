"""The Convert tool: audio extraction, compression to a target size, vertical 9:16, trim, GIF."""
from __future__ import annotations

import os
from pathlib import Path

from .config import CONFIG
from .video import (FfmpegError, Log, MediaInfo, Progress, _aac_args, _video_codec_args, center_crop,
                    reframe_filter, run_ffmpeg)

OPS = {
    "mp3": {"label": "Extract audio (MP3)", "needs_video": False},
    "compress": {"label": "Compress to a size", "needs_video": True},
    "vertical": {"label": "Make vertical (9:16)", "needs_video": True},
    "trim": {"label": "Trim", "needs_video": False},
    "gif": {"label": "GIF", "needs_video": True},
}
GIF_MAX_SECONDS = 30


def validate(op: str, params: dict, info: MediaInfo | None = None) -> None:
    """Raise ValueError with a message the user can act on."""
    if op not in OPS:
        raise ValueError(f"Unknown conversion '{op}'.")
    if op == "compress":
        mb = float(params.get("target_mb") or 0)
        if not 1 <= mb <= 4000:
            raise ValueError("Target size must be between 1 and 4000 MB.")
    if op == "vertical" and params.get("mode") not in ("crop", "blur"):
        raise ValueError("Vertical mode must be crop or blur.")
    if op in ("trim", "gif"):
        start, end = float(params.get("start") or 0), float(params.get("end") or 0)
        if start < 0 or end <= start:
            raise ValueError("The end time must be after the start time.")
        if op == "gif" and end - start > GIF_MAX_SECONDS:
            raise ValueError(f"GIFs are limited to {GIF_MAX_SECONDS} seconds.")
        if info and start >= info.duration:
            raise ValueError("The start time is after the end of the video.")
    if op == "gif" and int(params.get("width") or 0) not in (320, 480, 640, 720):
        raise ValueError("GIF width must be 320, 480, 640 or 720.")


def _compress(src: Path, info: MediaInfo, target_mb: float, dst: Path, job_dir: Path,
              log: Log, progress: Progress) -> str:
    """Two-pass x264 at the bitrate that lands just under target_mb, scaling down when the
    bitrate is too low for the source resolution."""
    if info.duration <= 0:
        raise FfmpegError("The video length is unknown, so the bitrate cannot be computed.")
    total_kbps = target_mb * 8 * 1024 / info.duration * 0.96  # 4 % for the mp4 container
    audio_kbps = 128 if total_kbps > 1200 else 64 if total_kbps > 300 else 32
    video_kbps = int(total_kbps - (audio_kbps if info.has_audio else 0))
    if video_kbps < 80:
        need = (80 + audio_kbps) * info.duration / 8 / 1024 / 0.96
        raise FfmpegError(f"{target_mb:g} MB is too small for a {info.duration / 60:.0f} min video. "
                          f"Try at least {need:.0f} MB, or trim it first.")
    height = info.height
    for limit, kbps in ((1080, 2500), (720, 1200), (480, 500), (360, 0)):
        height = min(info.height, limit)
        if video_kbps >= kbps:
            break
    scale = ["-vf", f"scale=-2:{height}"] if height < info.height else []
    log(f"compress: target {target_mb} MB, video {video_kbps} kb/s, audio {audio_kbps} kb/s, height {height}")
    common = ["-c:v", "libx264", "-preset", "medium", "-b:v", f"{video_kbps}k",
              "-maxrate", f"{int(video_kbps * 1.5)}k", "-bufsize", f"{video_kbps * 2}k",
              "-pix_fmt", "yuv420p", "-passlogfile", "pass"]
    half = lambda frac, detail: progress(frac / 2, "pass 1, " + detail)  # noqa: E731
    run_ffmpeg(["-y", "-i", str(src), "-map", "0:v:0", *scale, *common, "-pass", "1", "-an",
                "-f", "null", os.devnull], log, cwd=job_dir, duration=info.duration, progress=half)
    rest = lambda frac, detail: progress(0.5 + frac / 2, "pass 2, " + detail)  # noqa: E731
    audio = ["-map", "0:a:0?", "-c:a", "aac", "-b:a", f"{audio_kbps}k"] if info.has_audio else []
    run_ffmpeg(["-y", "-i", str(src), "-map", "0:v:0", *scale, *common, "-pass", "2", *audio,
                "-movflags", "+faststart", str(dst)], log, cwd=job_dir, duration=info.duration, progress=rest)
    for leftover in job_dir.glob("pass*.log*"):
        leftover.unlink(missing_ok=True)
    return f"{dst.stat().st_size / 1024 / 1024:.1f} MB at {height}p"


def run(op: str, params: dict, src: Path, info: MediaInfo, job_dir: Path, log: Log,
        progress: Progress) -> tuple[str, str]:
    """Run one conversion. Returns (output file name, short detail for the UI)."""
    if OPS[op]["needs_video"] and not info.has_video:
        raise FfmpegError("This file has no video, so it can only be converted to MP3 or trimmed.")
    dur = info.duration
    if op == "mp3":
        if not info.has_audio:
            raise FfmpegError("This video has no audio track.")
        out = job_dir / "audio.mp3"
        run_ffmpeg(["-y", "-i", str(src), "-vn", "-c:a", "libmp3lame", "-q:a", "2", str(out)], log,
                   duration=dur, progress=progress)
        return out.name, "MP3, VBR ~190 kb/s"

    if op == "compress":
        out = job_dir / "compressed.mp4"
        return out.name, _compress(src, info, float(params["target_mb"]), out, job_dir, log, progress)

    if op == "vertical":
        v = CONFIG["video"]
        w, h = v["width"], v["height"]
        if params["mode"] == "crop":
            graph = f"[0:v:0]{reframe_filter(center_crop(info, 0, dur))}[v]"
        else:  # whole frame centred over a blurred, zoomed copy of itself
            graph = (f"[0:v:0]split[a][b];[a]scale={w}:{h}:force_original_aspect_ratio=increase,crop={w}:{h},"
                     f"boxblur=24:2[bg];[b]scale={w}:{h}:force_original_aspect_ratio=decrease[fg];"
                     f"[bg][fg]overlay=(W-w)/2:(H-h)/2,setsar=1,fps={v['fps']},format=yuv420p[v]")
        out = job_dir / "vertical.mp4"
        run_ffmpeg(["-y", "-i", str(src), "-filter_complex", graph, "-map", "[v]", "-map", "0:a:0?",
                    *_video_codec_args(), *_aac_args(), "-movflags", "+faststart", str(out)],
                   log, duration=dur, progress=progress)
        return out.name, f"{w}×{h}, {'blurred background' if params['mode'] == 'blur' else 'center crop'}"

    start, end = float(params["start"]), min(float(params["end"]), dur or float(params["end"]))
    length = end - start
    if op == "trim":
        if info.has_video:
            out = job_dir / "trimmed.mp4"
            run_ffmpeg(["-y", "-ss", f"{start:.3f}", "-i", str(src), "-t", f"{length:.3f}",
                        "-map", "0:v:0", "-map", "0:a:0?", "-vf", "format=yuv420p", *_video_codec_args(),
                        *_aac_args(), "-movflags", "+faststart", str(out)],
                       log, duration=length, progress=progress)
        else:
            out = job_dir / "trimmed.mp3"
            run_ffmpeg(["-y", "-ss", f"{start:.3f}", "-i", str(src), "-t", f"{length:.3f}", "-vn",
                        "-c:a", "libmp3lame", "-q:a", "2", str(out)], log, duration=length, progress=progress)
        return out.name, f"{length:.1f} s"

    # gif: palette generated from the clip itself so colours stay clean
    width, fps = int(params["width"]), 15
    out = job_dir / "clip.gif"
    run_ffmpeg(["-y", "-ss", f"{start:.3f}", "-t", f"{length:.3f}", "-i", str(src),
                "-filter_complex", f"fps={fps},scale={width}:-2:flags=lanczos,split[s0][s1];"
                                   f"[s0]palettegen=stats_mode=diff[p];[s1][p]paletteuse=dither=bayer:bayer_scale=4",
                "-loop", "0", str(out)], log, duration=length, progress=progress)
    return out.name, f"{width}px, {fps} fps, {out.stat().st_size / 1024 / 1024:.1f} MB"
