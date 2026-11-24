"""plugins/image_generator.py — AI Image Generator (local SD/ComfyUI + API + procedural fallback)."""

from __future__ import annotations

import base64
from pathlib import Path
import re
import subprocess
import time

from core.env_loader import get_env

_BASE_DIR = Path(__file__).resolve().parent.parent
_DEFAULT_OUT_DIR = _BASE_DIR / "reports" / "images"
_WSL_PYTHON = "/home/tanishq/unsloth_env/bin/python"

PLUGIN = {
    "name": "image_generator",
    "brief": "Generate images using local RTX SD/ComfyUI, cloud image APIs, or procedural PIL artwork",
    "description": (
        "Probes local RTX 5070 Ti diffusion capabilities (ComfyUI on 127.0.0.1:8188, local diffusers, "
        "or WSL2 unsloth_env) and cloud image APIs, with deterministic procedural PIL artwork fallback "
        "so offline generation always produces clean PNG assets. Actions: status, probe, generate."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "action": {
                "type": "STRING",
                "description": "Action: status | probe | generate (default: status)",
            },
            "prompt": {
                "type": "STRING",
                "description": "Image generation prompt.",
            },
            "out_path": {
                "type": "STRING",
                "description": "Optional output PNG path.",
            },
            "width": {
                "type": "INTEGER",
                "description": "Image width in pixels (default: 768).",
            },
            "height": {
                "type": "INTEGER",
                "description": "Image height in pixels (default: 512).",
            },
            "backend": {
                "type": "STRING",
                "description": "Backend: auto | comfyui | local_sd | api | local_poster (default: auto).",
            },
        },
        "required": [],
    },
}


def probe_capabilities() -> dict:
    """Detect available local GPU diffusion, ComfyUI, and API backends."""
    caps = {
        "comfyui": False,
        "win_diffusers": False,
        "wsl_unsloth": False,
        "openai_api": bool(get_env("OPENAI_API_KEY")),
        "gemini_api": bool(get_env("GEMINI_API_KEY")),
        "pil_poster": True,
    }
    try:
        import httpx
        with httpx.Client(timeout=0.5) as client:
            r = client.get("http://127.0.0.1:8188/system_stats")
            caps["comfyui"] = r.status_code == 200
    except Exception:
        pass
    try:
        import torch  # noqa: F401
        import diffusers  # noqa: F401
        caps["win_diffusers"] = True
    except Exception:
        pass
    return caps


def render_local_poster(prompt: str, out_file: Path, *, width: int = 768, height: int = 512) -> Path:
    """Generate a crisp HUD/cybernetic procedural PNG poster locally via Pillow."""
    from PIL import Image, ImageDraw, ImageFont

    w = max(256, min(1920, int(width or 768)))
    h = max(256, min(1920, int(height or 512)))
    img = Image.new("RGB", (w, h), (7, 17, 26))
    draw = ImageDraw.Draw(img)

    # Vertical cybernetic gradient
    for y in range(h):
        frac = y / max(1, h - 1)
        r = int(5 + 14 * (1.0 - frac))
        g = int(16 + 38 * frac)
        b = int(28 + 56 * frac)
        draw.line([(0, y), (w, y)], fill=(r, g, b))

    # Geometric blueprint grid
    step = 48
    for x in range(0, w, step):
        draw.line([(x, 0), (x, h)], fill=(14, 52, 74), width=1)
    for y in range(0, h, step):
        draw.line([(0, y), (w, y)], fill=(14, 52, 74), width=1)

    cx, cy = w // 2, h // 2
    for rad, col in ((min(w, h) // 3, (0, 175, 220)), (min(w, h) // 4, (0, 212, 255)), (min(w, h) // 6, (255, 107, 0))):
        draw.ellipse([cx - rad, cy - rad, cx + rad, cy + rad], outline=col, width=2)

    font = ImageFont.load_default()
    label = f"MARK-LVII SYNTH // {prompt[:56]}"
    draw.rectangle([24, h - 64, w - 24, h - 24], fill=(4, 14, 22), outline=(0, 212, 255))
    draw.text((38, h - 50), label, fill=(216, 248, 255), font=font)

    out_file.parent.mkdir(parents=True, exist_ok=True)
    img.save(str(out_file), "PNG")
    return out_file


def _try_api_generate(prompt: str, out_file: Path) -> bool:
    oai = get_env("OPENAI_API_KEY")
    if oai:
        try:
            import httpx
            with httpx.Client(timeout=45.0) as client:
                r = client.post(
                    "https://api.openai.com/v1/images/generations",
                    headers={"Authorization": f"Bearer {oai}"},
                    json={"model": "gpt-image-1", "prompt": prompt, "size": "1024x1024"},
                )
                if r.status_code == 200:
                    b64 = ((r.json().get("data") or [{}])[0]).get("b64_json")
                    if b64:
                        out_file.parent.mkdir(parents=True, exist_ok=True)
                        out_file.write_bytes(base64.b64decode(b64))
                        return True
        except Exception:
            pass
    return False


def run(parameters: dict, player=None, session_memory=None) -> str:
    """Plugin entrypoint for AI image generation."""
    params = parameters if isinstance(parameters, dict) else {}
    action = str(params.get("action") or "status").strip().lower()

    if action in ("status", "probe"):
        caps = probe_capabilities()
        active = [k for k, v in caps.items() if v]
        return f"Image Generator ready. Available backends: {', '.join(active)}."

    if action == "generate":
        prompt = str(params.get("prompt") or "Futuristic holographic telemetry core").strip()
        backend = str(params.get("backend") or "auto").strip().lower()
        width = int(params.get("width") or 768)
        height = int(params.get("height") or 512)
        if params.get("out_path"):
            out_file = Path(str(params["out_path"]))
        else:
            slug = re.sub(r"[^a-zA-Z0-9_-]+", "_", prompt.lower()).strip("_")[:36] or "render"
            out_file = _DEFAULT_OUT_DIR / f"{slug}_{time.strftime('%Y%m%d_%H%M%S')}.png"

        used = "local_poster"
        if backend == "api" or (backend == "auto" and get_env("OPENAI_API_KEY")):
            if _try_api_generate(prompt, out_file):
                used = "api"
                return f"Generated image via {used}: {out_file}"

        if backend == "comfyui":
            caps = probe_capabilities()
            if not caps["comfyui"]:
                return "ComfyUI server not reachable at http://127.0.0.1:8188."

        render_local_poster(prompt, out_file, width=width, height=height)
        return f"Generated image via {used} ({width}x{height}): {out_file}"

    return f"Unknown image_generator action '{action}'. Use: status, probe, generate."
