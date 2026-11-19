"""Audio helpers shared by transcription and moment scoring."""
from __future__ import annotations

import wave
from pathlib import Path

import numpy as np

SAMPLE_RATE = 16000


def load_wav(path: Path) -> np.ndarray:
    """Read the 16 kHz mono s16 WAV made by the download stage as float32 samples in [-1, 1].
    Passing samples to faster-whisper skips its PyAV decoder, whose API breaks between releases."""
    with wave.open(str(path), "rb") as f:
        if f.getframerate() != SAMPLE_RATE or f.getnchannels() != 1 or f.getsampwidth() != 2:
            raise RuntimeError(f"{path.name} must be 16 kHz mono 16-bit PCM")
        raw = f.readframes(f.getnframes())
    return np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0


class Loudness:
    """Loudness in dB per 0.5 s window, used to spot excited, loud or laughing stretches."""

    STEP = 0.5

    def __init__(self, path: Path):
        samples = load_wav(path)
        hop = int(SAMPLE_RATE * self.STEP)
        n = max(1, len(samples) // hop)
        frames = samples[: n * hop].reshape(n, hop) if len(samples) >= hop else samples.reshape(1, -1)
        rms = np.sqrt(np.mean(frames ** 2, axis=1)) + 1e-9
        self.db = 20 * np.log10(rms)
        voiced = self.db[self.db > -50]
        self.median = float(np.median(voiced)) if voiced.size else -30.0

    def score(self, start: float, end: float) -> float:
        """0-100: how much louder and more dynamic this span is than the video's typical speech."""
        lo, hi = int(start / self.STEP), max(int(end / self.STEP), int(start / self.STEP) + 1)
        span = self.db[lo:hi]
        span = span[span > -50]
        if span.size == 0:
            return 0.0
        lift = float(np.mean(span)) - self.median        # dB above typical level
        peaks = float(np.mean(span > self.median + 6))   # share of clearly loud moments
        return round(float(np.clip(50 + lift * 8 + peaks * 60, 0, 100)), 1)
