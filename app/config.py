"""Loads config.yaml on top of built-in defaults."""
from __future__ import annotations

import copy
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
JOBS_DIR = ROOT / "jobs"
STATIC_DIR = ROOT / "static"
CONFIG_PATH = ROOT / "config.yaml"

DEFAULTS: dict = {
    "server": {"port": 8000, "open_browser": True},
    "download": {"max_height": 1080, "cookies_from_browser": ""},
    "whisper": {"model": "auto", "device": "auto", "compute_type": "auto", "beam_size": 5},
    "moments": {
        "provider": "ollama",
        "min_seconds": 20,
        "max_seconds": 60,
        "ollama": {
            "url": "http://localhost:11434",
            "model": "qwen3:8b",
            "num_ctx": 8192,
            "temperature": 0.3,
            "timeout": 600,
            "chunk_minutes": 10,
            "chunk_overlap_minutes": 1,
            "unload_after": True,
        },
    },
    "video": {
        "width": 1080,
        "height": 1920,
        "fps": 30,
        "encoder": "libx264",
        "crf": 20,
        "preset": "veryfast",
        "audio_bitrate": "160k",
        "reframe": "center",
    },
    "captions": {
        "font_file": "assets/fonts/Anton-Regular.ttf",
        "font_name": "Anton",
        "default_preset": "bold",
        "default_position": "bottom",
    },
}


def _merge(base: dict, override: dict) -> dict:
    out = copy.deepcopy(base)
    for key, value in (override or {}).items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _merge(out[key], value)
        else:
            out[key] = value
    return out


def load() -> dict:
    data = {}
    if CONFIG_PATH.exists():
        data = yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8")) or {}
    return _merge(DEFAULTS, data)


CONFIG = load()


def font_path() -> Path:
    path = Path(CONFIG["captions"]["font_file"])
    return path if path.is_absolute() else ROOT / path
