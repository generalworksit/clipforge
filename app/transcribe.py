"""Stage 2: faster-whisper transcription with word timestamps."""
from __future__ import annotations

import gc
import json
import os
import sys
from pathlib import Path
from typing import Callable


def _add_cuda_dll_dirs() -> None:
    """On Windows, pip's nvidia-cublas/cudnn wheels put their DLLs in site-packages/nvidia/*/bin,
    which is not on the DLL search path. Add them before ctranslate2 loads."""
    if sys.platform != "win32":
        return
    for base in sys.path:
        nvidia = Path(base) / "nvidia"
        if nvidia.is_dir():
            for bin_dir in nvidia.glob("*/bin"):
                os.add_dll_directory(str(bin_dir))
                os.environ["PATH"] = str(bin_dir) + os.pathsep + os.environ.get("PATH", "")


_add_cuda_dll_dirs()
os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")

import ctranslate2  # noqa: E402
from faster_whisper import WhisperModel  # noqa: E402

from .audio import load_wav  # noqa: E402
from .config import CONFIG  # noqa: E402


def pick_settings() -> tuple[str, str, str]:
    """Return (model, device, compute_type) honouring config.yaml, auto-detecting the GPU."""
    w = CONFIG["whisper"]
    device = w["device"]
    if device == "auto":
        device = "cuda" if ctranslate2.get_cuda_device_count() > 0 else "cpu"
    model = w["model"] if w["model"] != "auto" else ("large-v3" if device == "cuda" else "small")
    compute = w["compute_type"] if w["compute_type"] != "auto" else ("float16" if device == "cuda" else "int8")
    return model, device, compute




def _run(audio: Path, model_name: str, device: str, compute: str, duration: float,
         progress: Callable[[float, str], None], task: str = "transcribe") -> dict:
    progress(0.0, f"loading {model_name} on {device} (first use downloads the model)")
    model = WhisperModel(model_name, device=device, compute_type=compute)
    try:
        samples = load_wav(audio)
        language, language_probability, _ = model.detect_language(samples, vad_filter=True, language_detection_segments=3)
        segments, info = model.transcribe(
            samples, word_timestamps=True, vad_filter=True, task=task, language=language,
            beam_size=int(CONFIG["whisper"]["beam_size"]),
            # Prose supplied as hotwords can be emitted instead of the speech (especially
            # on short clips). Decode the audio without invented vocabulary, and do not
            # carry repetitions from a previous window into the next one.
            condition_on_previous_text=False,
        )
        out_segments = []
        for seg in segments:  # generator: decoding happens while we iterate
            words = [
                {"text": w.word.strip(), "start": round(w.start, 3), "end": round(w.end, 3),
                 "prob": round(w.probability, 3)}
                for w in (seg.words or []) if w.word.strip()
            ]
            out_segments.append({"id": len(out_segments), "start": round(seg.start, 3),
                                 "end": round(seg.end, 3), "text": seg.text.strip(), "words": words})
            if duration:
                progress(min(seg.end / duration, 1.0), f"{model_name} on {device} · {info.language}")
        return {"language": info.language, "language_probability": round(language_probability, 3),
                "duration": duration, "model": model_name, "device": device, "segments": out_segments}
    finally:
        del model
        gc.collect()  # releases VRAM so the LLM step has room


def transcribe(audio: Path, out_json: Path, duration: float, log: Callable[[str], None],
               progress: Callable[[float, str], None], task: str = "transcribe") -> dict:
    """task="translate" makes Whisper write English text whatever language is spoken."""
    model_name, device, compute = pick_settings()
    log(f"whisper: model={model_name} device={device} compute_type={compute} task={task}")
    try:
        result = _run(audio, model_name, device, compute, duration, progress, task)
    except (RuntimeError, OSError) as exc:  # CUDA DLL / OOM problems: fall back to CPU so the job still finishes
        if device != "cuda":
            raise
        log(f"whisper on cuda failed ({exc!r}); retrying on cpu with small/int8")
        model_name = "small" if CONFIG["whisper"]["model"] == "auto" else model_name
        result = _run(audio, model_name, "cpu", "int8", duration, progress, task)
    words = sum(len(s["words"]) for s in result["segments"])
    log(f"whisper: language={result['language']} ({result['language_probability']}), "
        f"{len(result['segments'])} segments, {words} words")
    if not words:
        raise RuntimeError("No speech was detected in this video.")
    out_json.write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
    return result
