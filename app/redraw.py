"""AI redraw of thumbnail frames, fully local: Stable Diffusion XL (RealVisXL V5 Lightning, a
photoreal fine-tune that needs only ~6 steps) in image-to-image mode on the NVIDIA GPU. The frame
keeps its layout, the model makes it look like a staged, hyper-detailed thumbnail photo.

It runs in a separate Python process (python -m app.redraw batch.json): PyTorch's CUDA libraries
never meet Whisper's in the worker, and all GPU memory is given back when the batch ends.
Optional: needs `torch` (CUDA build) + `diffusers` + `transformers` + `accelerate` in the venv.
"""
from __future__ import annotations

import importlib.util
import json
import math
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Callable

ROOT = Path(__file__).resolve().parent.parent
MODEL_REPO = "SG161222/RealVisXL_V5.0_Lightning"
MODEL_DIR = ROOT / "models" / "sdxl" / "realvisxl5-lightning"
VAE_REPO = "madebyollin/sdxl-vae-fp16-fix"   # the stock SDXL VAE turns black (NaN) in float16
VAE_DIR = ROOT / "models" / "sdxl" / "vae-fp16-fix"
MODEL_FILES = ["model_index.json", "scheduler/*", "tokenizer/*", "tokenizer_2/*", "*/config.json",
               "text_encoder/model.fp16.safetensors", "text_encoder_2/model.fp16.safetensors",
               "unet/diffusion_pytorch_model.fp16.safetensors", "vae/diffusion_pytorch_model.fp16.safetensors"]
DOWNLOAD_GB = 7.3
SIZE = (768, 1344)       # an SDXL training shape with the 9:16 ratio
STRENGTH = {"light": 0.45, "normal": 0.6, "strong": 0.75}  # strong can change faces

PROMPT = ("dramatic viral youtube thumbnail photo, hyper detailed, ultra sharp focus, vivid saturated colors, "
          "high contrast, bright punchy lighting, cinematic, intense, professional photography")
NEGATIVE = ("text, letters, watermark, logo, caption, blurry, lowres, jpeg artifacts, cartoon, painting, "
            "drawing, deformed, bad anatomy, extra fingers, extra limbs, disfigured face, dull colors")

Log = Callable[[str], None]
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


class RedrawError(RuntimeError):
    pass


def installed() -> bool:
    return all(importlib.util.find_spec(m) is not None for m in ("torch", "diffusers", "transformers", "accelerate"))


def model_ready() -> bool:
    return (MODEL_DIR / "unet" / "diffusion_pytorch_model.fp16.safetensors").is_file() and \
        (VAE_DIR / "diffusion_pytorch_model.safetensors").is_file()


def status() -> dict:
    return {"installed": installed(), "model_ready": model_ready(), "download_gb": DOWNLOAD_GB}


def ensure_model(log: Log) -> None:
    if model_ready():
        return
    from huggingface_hub import snapshot_download
    log(f"downloading the AI redraw model ({DOWNLOAD_GB} GB, once)")
    snapshot_download(MODEL_REPO, local_dir=str(MODEL_DIR), allow_patterns=MODEL_FILES)
    snapshot_download(VAE_REPO, local_dir=str(VAE_DIR), allow_patterns=["config.json", "diffusion_pytorch_model.safetensors"])
    if not model_ready():
        raise RedrawError("the AI redraw model download is incomplete")


def redraw_many(items: list[dict], log: Log, progress: Callable[[float, str], None] | None = None,
                timeout: float = 1800) -> None:
    """items: [{"src": png path, "out": png path, "strength": "normal", "scene": str, "seed": int}]. Runs the batch
    in a child process; raises RedrawError when it fails (no CUDA GPU, out of memory...)."""
    if not installed():
        raise RedrawError("AI redraw is not installed (PyTorch + diffusers)")
    ensure_model(log)
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8") as f:
        json.dump(items, f)
        batch = f.name
    cmd = [sys.executable, "-m", "app.redraw", batch]
    proc = subprocess.Popen(cmd, cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                            encoding="utf-8", errors="replace", creationflags=NO_WINDOW)
    started, tail = time.monotonic(), []
    try:
        for line in proc.stdout:
            line = line.rstrip()
            if not line:
                continue
            tail = (tail + [line])[-15:]
            if line.startswith("DONE "):
                n = int(line.split()[1])
                if progress:
                    progress(n / len(items), f"AI redraw {n} of {len(items)}")
            if line.startswith(("DONE", "LOADED", "ERROR")):
                log(f"redraw: {line}")
            if time.monotonic() - started > timeout:
                proc.kill()
                raise RedrawError("AI redraw took too long")
        proc.wait()
    finally:
        Path(batch).unlink(missing_ok=True)
    if proc.returncode != 0:
        log("redraw output:\n" + "\n".join(tail))
        err = next((t[6:] for t in reversed(tail) if t.startswith("ERROR ")), tail[-1] if tail else "unknown error")
        raise RedrawError(f"AI redraw failed: {err}")


# ---------------------------------------------------------------- child process

def _run(batch_path: str) -> int:
    items = json.loads(Path(batch_path).read_text(encoding="utf-8"))
    import torch
    if not torch.cuda.is_available():
        print("ERROR no NVIDIA GPU available to PyTorch", flush=True)
        return 2
    from diffusers import AutoencoderKL, DPMSolverMultistepScheduler, StableDiffusionXLImg2ImgPipeline
    from PIL import Image
    t0 = time.monotonic()
    vae = AutoencoderKL.from_pretrained(VAE_DIR, torch_dtype=torch.float16)
    pipe = StableDiffusionXLImg2ImgPipeline.from_pretrained(MODEL_DIR, vae=vae, torch_dtype=torch.float16,
                                                            variant="fp16", use_safetensors=True)
    # what the Lightning model card asks for: DPM++ SDE Karras, few steps, low guidance
    pipe.scheduler = DPMSolverMultistepScheduler.from_config(pipe.scheduler.config, algorithm_type="sde-dpmsolver++",
                                                             use_karras_sigmas=True)
    pipe.enable_model_cpu_offload()   # 8 GB cards: only the part that is working sits on the GPU
    pipe.set_progress_bar_config(disable=True)
    print(f"LOADED in {time.monotonic() - t0:.0f} s", flush=True)
    for n, it in enumerate(items, 1):
        t1 = time.monotonic()
        src = Image.open(it["src"]).convert("RGB")
        w, h = src.size
        strength = STRENGTH.get(it.get("strength", "normal"), STRENGTH["normal"])
        scene = str(it.get("scene") or "").strip(" ,.")
        out = pipe(prompt=f"{scene}, {PROMPT}" if scene else PROMPT, negative_prompt=NEGATIVE, image=src.resize(SIZE, Image.LANCZOS),
                   strength=strength, num_inference_steps=math.ceil(6 / strength), guidance_scale=2.0,
                   generator=torch.Generator("cpu").manual_seed(int(it.get("seed", 7)))).images[0]
        out.resize((w, h), Image.LANCZOS).save(it["out"])
        print(f"DONE {n} {time.monotonic() - t1:.1f}s", flush=True)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(_run(sys.argv[1]))
    except Exception as exc:  # noqa: BLE001  reported to the parent on one line
        import traceback
        traceback.print_exc()
        msg = str(exc).splitlines()[0][:200] if str(exc) else exc.__class__.__name__
        if "out of memory" in msg.lower():
            msg = "the graphics card ran out of memory (close games or other GPU apps and try again)"
        print(f"ERROR {msg}", flush=True)
        sys.exit(1)
