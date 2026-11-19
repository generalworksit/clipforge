"""A bounded, disk-backed recording buffer of completed FFmpeg segments."""
import csv
import re
import shutil
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

HIDDEN = getattr(subprocess, "CREATE_NO_WINDOW", 0)
SEGMENT = re.compile(r"^segment-\d{7}\.ts$")


@dataclass(frozen=True)
class Segment:
    path: Path
    start: float
    end: float


class RollingBuffer:
    def __init__(self, directory, seconds=150, max_bytes=3 * 1024**3, min_free=8 * 1024**3):
        self.directory = Path(directory).resolve()
        self.directory.mkdir(parents=True, exist_ok=True)
        self.seconds, self.max_bytes, self.min_free = seconds, max_bytes, min_free
        self.process = None
        self.started = time.monotonic()
        self.segments = []

    def start(self, source, realtime=False):
        self.check_space()
        if any(self.directory.iterdir()):
            raise RuntimeError("A recording buffer must start in a new, empty session folder.")
        # No stderr log: signed media URLs can contain credentials. A stalled / failed
        # input gets a clear generic message; no shell or provider login bypass is used.
        args = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin", "-y"]
        if str(source).startswith(("http://", "https://")):
            args += ["-rw_timeout", "15000000", "-protocol_whitelist", "http,https,tcp,tls,crypto"]
        if realtime:
            args += ["-re"]
        args += ["-i", str(source), "-map", "0:v:0", "-map", "0:a:0?", "-c", "copy",
                 "-f", "segment", "-segment_time", "2", "-segment_list", "segments.csv",
                 "-segment_list_type", "csv", "-segment_list_size", "512", "-reset_timestamps", "1",
                 "segment-%07d.ts"]
        self.process = subprocess.Popen(args, cwd=self.directory, stdin=subprocess.DEVNULL,
                                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=HIDDEN)
        self.started = time.monotonic()

    def check_space(self):
        if shutil.disk_usage(self.directory).free < self.min_free:
            raise RuntimeError("Recording paused: less than 8 GiB free. Free some storage and try again.")
        size = sum(p.stat().st_size for p in self.directory.iterdir() if p.is_file())
        if size > self.max_bytes:
            raise RuntimeError("Recording buffer reached its 3 GB disk limit.")

    def refresh(self):
        path = self.directory / "segments.csv"
        found = []
        if path.is_file():
            # The CSV lists only closed segments. Incomplete trailing writes are ignored.
            with path.open(encoding="utf-8", newline="") as f:
                for row in csv.reader(f):
                    try:
                        name, begin, end = row
                        if not SEGMENT.fullmatch(name):
                            continue
                        begin, end = float(begin), float(end)
                        p = self.directory / name
                        if p.is_file() and end > begin:
                            found.append(Segment(p, begin, end))
                    except ValueError:
                        continue
        self.segments = sorted(found, key=lambda s: s.start)
        if self.segments:
            cutoff = self.segments[-1].end - self.seconds
            for s in self.segments:
                if s.end < cutoff:
                    s.path.unlink(missing_ok=True)
            self.segments = [s for s in self.segments if s.end >= cutoff]
        self.check_space()
        return self.segments

    def snapshot(self, start, end, destination):
        self.refresh()
        chosen = [s for s in self.segments if s.end > start and s.start < end]
        if not chosen or end - start < 1:
            raise RuntimeError("Not enough buffered video yet.")
        for a, b in zip(chosen, chosen[1:]):
            if b.start - a.end > 0.5:
                raise RuntimeError("The recording has a gap. This moment needs another capture.")
        begin = max(start, chosen[0].start)
        finish = min(end, chosen[-1].end)
        if finish - begin < 1:
            raise RuntimeError("The moment is no longer in the recording buffer.")
        destination = Path(destination)
        destination.mkdir(parents=True, exist_ok=True)
        names = []
        for i, segment in enumerate(chosen):
            name = f"capture-{i:04d}.ts"
            # Durable independent copies: buffer retention cannot remove queued media.
            shutil.copyfile(segment.path, destination / name)
            names.append(name)
        return {"segments": names, "offset": begin - chosen[0].start,
                "duration": finish - begin, "media_start": begin, "media_end": finish,
                "pre_roll_truncated": begin > start + 0.1}

    def close(self):
        if self.process and self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=8)
            except subprocess.TimeoutExpired:
                self.process.kill(); self.process.wait(timeout=5)

    def discard(self):
        self.close()
        # Delete only our own known files in this resolved session directory.
        for p in self.directory.iterdir():
            if p.is_file() and (SEGMENT.fullmatch(p.name) or p.name == "segments.csv"):
                p.unlink(missing_ok=True)
        try:
            self.directory.rmdir()
        except OSError:
            pass


def audio_levels(segment):
    """Per-second RMS; basic loudness evidence, not a laughter / ad classifier."""
    import numpy as np
    p = subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-i", str(segment.path),
                        "-vn", "-t", "30", "-ac", "1", "-ar", "8000", "-f", "f32le", "pipe:1"],
                       capture_output=True, timeout=20, creationflags=HIDDEN)
    if p.returncode:
        return []
    data = np.frombuffer(p.stdout, dtype="<f4")
    return [(segment.start + i / 8000, float(np.sqrt(np.mean(data[i:i+8000] ** 2))))
            for i in range(0, len(data), 8000) if len(data[i:i+8000])]
