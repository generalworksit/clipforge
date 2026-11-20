"""Quick health check: is everything ClipForge needs present and working?

Used by the launchers before the app starts (python -m app.health) and by the System page
(GET /api/system/check). Every check is fast (under a couple of seconds in total) and never
downloads anything. Each result is ok, warn (works, with a limit) or fail (a tool will break).
"""
from __future__ import annotations

import datetime as dt
import importlib
import importlib.metadata
import os
import shutil
import socket
import subprocess
import sys
import urllib.request

from .config import CONFIG, JOBS_DIR, ROOT

HIDDEN = getattr(subprocess, "CREATE_NO_WINDOW", 0)
WINDOWS = sys.platform == "win32"
LAUNCHER = "run.bat" if WINDOWS else "./run.sh"


def _result(id_: str, label: str, status: str, detail: str, fix: str = "") -> dict:
    return {"id": id_, "label": label, "status": status, "detail": detail, "fix": fix}


def _ffmpeg() -> dict:
    missing = [t for t in ("ffmpeg", "ffprobe") if not shutil.which(t)]
    if missing:
        fix = "winget install Gyan.FFmpeg" if WINDOWS else "Install FFmpeg with your package manager"
        return _result("ffmpeg", "FFmpeg", "fail", f"{' and '.join(missing)} not found", f"{fix}, or run {LAUNCHER} again")
    try:
        out = subprocess.run(["ffmpeg", "-hide_banner", "-version"], capture_output=True, text=True, timeout=10, creationflags=HIDDEN)
        version = (out.stdout.splitlines() or [""])[0].replace("ffmpeg version ", "").split(" Copyright")[0]
        if out.returncode:
            raise RuntimeError(out.stderr.strip()[-200:])
    except Exception as exc:  # noqa: BLE001 - reported to the user
        return _result("ffmpeg", "FFmpeg", "fail", f"FFmpeg is installed but does not run: {exc}", "Reinstall FFmpeg")
    return _result("ffmpeg", "FFmpeg", "ok", version or "found")


def _python() -> dict:
    v = sys.version_info
    text = f"{v.major}.{v.minor}.{v.micro}"
    if (3, 11) <= (v.major, v.minor) <= (3, 13):
        return _result("python", "Python", "ok", text)
    return _result("python", "Python", "warn", f"{text}, tested with 3.11 to 3.13", f"Delete the .venv folder and run {LAUNCHER} again")


PACKAGES = {"fastapi": "web app", "faster_whisper": "transcription", "yt_dlp": "downloads", "cv2": "face tracking",
            "PIL": "thumbnails", "kokoro_onnx": "AI voice", "soundfile": "AI voice", "multipart": "uploads"}


def _packages() -> dict:
    broken = []
    for module, use in PACKAGES.items():
        try:
            importlib.import_module(module)
        except Exception:  # noqa: BLE001 - any import failure means the tool is broken
            broken.append(f"{module} ({use})")
    if broken:
        return _result("packages", "Python packages", "fail", "Missing or broken: " + ", ".join(broken), f"Run {LAUNCHER} again, it reinstalls them")
    return _result("packages", "Python packages", "ok", f"{len(PACKAGES)} core packages load")


def _storage() -> dict:
    try:
        JOBS_DIR.mkdir(parents=True, exist_ok=True)
        probe = JOBS_DIR / ".write-test"
        probe.write_bytes(b"ok")
        probe.unlink()
    except OSError as exc:
        return _result("storage", "Storage", "fail", f"Cannot write to {JOBS_DIR}: {exc.strerror or exc}", "Move ClipForge to a folder you own, like Desktop or Documents")
    free = shutil.disk_usage(JOBS_DIR).free / 1e9
    if free < 1:
        return _result("storage", "Storage", "fail", f"{free:.1f} GB free", "Free some space or delete old jobs in Library")
    if free < 5:
        return _result("storage", "Storage", "warn", f"{free:.1f} GB free, long videos need more", "Delete old jobs in Library")
    return _result("storage", "Storage", "ok", f"{free:.0f} GB free")


def _gpu() -> dict:
    try:
        import ctranslate2
        cuda = ctranslate2.get_cuda_device_count()
    except Exception:  # noqa: BLE001
        cuda = 0
    if cuda:
        return _result("gpu", "Graphics card", "ok", "NVIDIA GPU in use for transcription")
    if shutil.which("nvidia-smi"):
        return _result("gpu", "Graphics card", "warn", "NVIDIA GPU found but CUDA libraries are missing, transcription uses the CPU",
                       f"Delete the .venv folder and run {LAUNCHER} again")
    return _result("gpu", "Graphics card", "ok", "No NVIDIA GPU, transcription runs on the CPU (slower)")


def _ytdlp() -> dict:
    try:
        version = importlib.metadata.version("yt-dlp")
        made = dt.date(*map(int, version.split(".")[:3]))
    except Exception:  # noqa: BLE001
        return _result("ytdlp", "Downloader", "warn", "Version unknown", "Update it on the System page")
    age = (dt.date.today() - made).days
    if age > 60:
        return _result("ytdlp", "Downloader", "warn", f"yt-dlp {version} is {age} days old, sites may have changed", "Press Update downloader on the System page")
    return _result("ytdlp", "Downloader", "ok", f"yt-dlp {version}")


def _ollama() -> dict:
    moments = CONFIG["moments"]
    if moments.get("provider") != "ollama":
        return _result("ollama", "Local AI", "ok", "Off, moments are picked from sound, picture and transcript")
    url = moments["ollama"]["url"].rstrip("/") + "/api/tags"
    try:
        with urllib.request.urlopen(url, timeout=1.5) as r:  # noqa: S310 - local URL from config
            ok = r.status == 200
    except Exception:  # noqa: BLE001
        ok = False
    if ok:
        return _result("ollama", "Local AI", "ok", f"Ollama is running ({moments['ollama']['model']})")
    if shutil.which("ollama"):
        return _result("ollama", "Local AI", "warn", "Ollama is installed but not running, clips are picked without it", "Start Ollama, or restart ClipForge")
    return _result("ollama", "Local AI", "warn", "Not installed (optional), clips are picked without it",
                   "winget install Ollama.Ollama" if WINDOWS else "curl -fsSL https://ollama.com/install.sh | sh")


def _node() -> dict:
    if shutil.which("node"):
        return _result("node", "Node.js", "ok", "Found, Auto monitor can run")
    return _result("node", "Node.js", "warn", "Not found, only Auto monitor needs it", f"Run {LAUNCHER} again to install it")


def _port() -> dict:
    port = int(CONFIG["server"]["port"])
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.3)
        busy = s.connect_ex(("127.0.0.1", port)) == 0
    if busy:
        return _result("port", f"Port {port}", "warn", "Already in use, ClipForge may already be open", "Close the other ClipForge window, or set server.port in config.yaml")
    return _result("port", f"Port {port}", "ok", "Free")


CHECKS = [_ffmpeg, _python, _packages, _storage, _gpu, _ytdlp, _ollama, _node]


def run(include_port: bool = False) -> dict:
    results = []
    for check in CHECKS + ([_port] if include_port else []):
        try:
            results.append(check())
        except Exception as exc:  # noqa: BLE001 - a broken check must not hide the others
            name = check.__name__.strip("_")
            results.append(_result(name, name.title(), "warn", f"Check could not run: {exc}"))
    worst = "fail" if any(r["status"] == "fail" for r in results) else "warn" if any(r["status"] == "warn" for r in results) else "ok"
    return {"status": worst, "checks": results, "checked": dt.datetime.now().isoformat(timespec="seconds")}


def main() -> int:
    """Console report for the launchers. Exit 1 only when a required part fails."""
    os.environ.setdefault("OPENCV_LOG_LEVEL", "ERROR")
    report = run(include_port=True)
    mark = {"ok": "  ok  ", "warn": " note ", "fail": " FAIL "}
    print("\nSystem check")
    for r in report["checks"]:
        print(f" [{mark[r['status']]}] {r['label']:<16} {r['detail']}")
        if r["status"] != "ok" and r["fix"]:
            print(f"{'':27}-> {r['fix']}")
    if report["status"] == "fail":
        print("\nSomething ClipForge needs is missing. Fix the FAIL lines above, then start it again.")
        return 1
    print("All good." if report["status"] == "ok" else "Ready. Notes above are optional.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
