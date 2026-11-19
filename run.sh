#!/usr/bin/env bash
# Start ClipForge on macOS / Linux (on Windows use run.bat).
set -euo pipefail
cd "$(dirname "$0")"

for bin in ffmpeg ffprobe; do
  if ! command -v "$bin" >/dev/null; then
    echo "$bin is required. Install it with: sudo apt install ffmpeg   (macOS: brew install ffmpeg)"
    exit 1
  fi
done

PY=python3
command -v "$PY" >/dev/null || PY=python
if [ ! -x .venv/bin/python ]; then
  echo "Creating virtual environment in .venv ..."
  "$PY" -m venv .venv
fi

if ! .venv/bin/python -c "import fastapi, faster_whisper, yt_dlp, yaml, multipart, curl_cffi, kokoro_onnx, soundfile" 2>/dev/null; then
  echo "Installing Python packages, this takes a few minutes the first time ..."
  .venv/bin/python -m pip install --upgrade pip
  .venv/bin/python -m pip install -r requirements.txt
  if command -v nvidia-smi >/dev/null; then
    echo "NVIDIA GPU found, installing CUDA libraries for faster-whisper ..."
    .venv/bin/python -m pip install -r requirements-cuda.txt
  fi
fi

# pip's CUDA wheels ship their .so files inside site-packages/nvidia/*/lib
if [ -d .venv/lib ]; then
  for d in .venv/lib/python3*/site-packages/nvidia/*/lib; do
    [ -d "$d" ] && export LD_LIBRARY_PATH="$PWD/$d:${LD_LIBRARY_PATH:-}"
  done
fi

exec .venv/bin/python -m app "$@"
