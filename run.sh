#!/usr/bin/env bash
# Start ClipForge on macOS / Linux (on Windows use run.bat).
# First run installs what is missing (FFmpeg, Python, Node.js, Python packages).
set -euo pipefail
cd "$(dirname "$0")"
SETUP_ONLY=0; [ "${1:-}" = "--setup-only" ] && SETUP_ONLY=1 && shift

install_pkg() {
  # install_pkg <brew name> <apt name> <dnf name> <pacman name>
  if command -v brew >/dev/null; then brew install "$1"
  elif command -v apt-get >/dev/null; then sudo apt-get update -qq && sudo apt-get install -y $2
  elif command -v dnf >/dev/null; then sudo dnf install -y $3
  elif command -v pacman >/dev/null; then sudo pacman -S --noconfirm $4
  else return 1
  fi
}

if ! command -v ffmpeg >/dev/null || ! command -v ffprobe >/dev/null; then
  echo "[setup] FFmpeg is missing, installing it ..."
  install_pkg ffmpeg ffmpeg ffmpeg ffmpeg || { echo "Install FFmpeg yourself, then run ./run.sh again."; exit 1; }
fi

find_python() {
  for c in python3.12 python3.11 python3.13 python3 python; do
    if command -v "$c" >/dev/null && "$c" -c "import sys; sys.exit(0 if (3, 11) <= sys.version_info[:2] <= (3, 13) else 1)" 2>/dev/null; then
      echo "$c"; return 0
    fi
  done
  return 1
}

if [ ! -x .venv/bin/python ]; then
  PY=$(find_python) || {
    echo "[setup] Python 3.11+ is missing, installing it ..."
    install_pkg python@3.12 "python3 python3-venv python3-pip" python3 python || true
    PY=$(find_python) || PY=""
  }
  if [ -n "$PY" ] && "$PY" -m venv .venv 2>/dev/null; then
    echo "[setup] Created the virtual environment in .venv with $PY"
  else
    # Newer distros ship only Python 3.14+, or venv is missing: get a private Python 3.12 with uv
    # (installs into your home folder, nothing system-wide).
    rm -rf .venv
    echo "[setup] Getting Python 3.12 with uv (https://docs.astral.sh/uv) ..."
    export PATH="$HOME/.local/bin:$PATH"
    command -v uv >/dev/null || curl -LsSf https://astral.sh/uv/install.sh | sh
    uv venv --seed --python 3.12 .venv || { echo "Install Python 3.11, 3.12 or 3.13, then run ./run.sh again."; exit 1; }
  fi
fi

if ! .venv/bin/python -c "import fastapi, faster_whisper, yt_dlp, yaml, multipart, curl_cffi, kokoro_onnx, soundfile, cv2, PIL" 2>/dev/null; then
  echo "[setup] Installing Python packages, this takes a few minutes the first time ..."
  .venv/bin/python -m pip install --upgrade pip
  .venv/bin/python -m pip install -r requirements.txt
  if command -v nvidia-smi >/dev/null; then
    echo "[setup] NVIDIA GPU found, installing CUDA libraries for faster transcription ..."
    .venv/bin/python -m pip install -r requirements-cuda.txt
  fi
fi

if ! .venv/bin/python -c "import grpc, streamlink, google.protobuf" 2>/dev/null; then
  echo "[setup] Installing Auto monitor packages ..."
  .venv/bin/python -m pip install -r requirements-live.txt
fi

if ! command -v node >/dev/null; then
  echo "[setup] Node.js is missing, installing it for Auto monitor ..."
  install_pkg node nodejs nodejs nodejs || echo "[setup] Node.js not installed. Every tool works except Auto monitor."
fi

# pip's CUDA wheels ship their .so files inside site-packages/nvidia/*/lib
if [ -d .venv/lib ]; then
  for d in .venv/lib/python3*/site-packages/nvidia/*/lib; do
    [ -d "$d" ] && export LD_LIBRARY_PATH="$PWD/$d:${LD_LIBRARY_PATH:-}"
  done
fi

if [ "$SETUP_ONLY" = 1 ]; then echo "Setup complete. Start ClipForge with ./run.sh"; exit 0; fi

echo "ClipForge is starting at http://127.0.0.1:8000  (Ctrl+C to stop)"
exec .venv/bin/python -m app "$@"
