"""
Screen & webcam capture for JARVIS vision.

Provides the two capture entry points main.py uses — `_capture_screen()` and
`_capture_camera()` — plus their helpers (compression, camera auto-detection,
config access). main.py grabs a frame here on demand, then injects it into the
main Gemini Live session; there is no separate vision session here.
"""
from __future__ import annotations

import io
import json
import sys
from pathlib import Path

import numpy as np

from core.lazyimport import LazyModule, installed

# OpenCV is 224 ms and is only ever touched by the webcam path — see
# core/lazyimport.py. Screen capture, which is what this action does on almost
# every call, never reaches it. numpy above stays eager: main.py already
# imports it for audio, so it is in memory before this file is read.
_CV2 = installed("cv2")

cv2 = LazyModule("cv2")

try:
    import mss
    import mss.tools
    _MSS = True
except ImportError:
    _MSS = False

try:
    import PIL.Image
    _PIL = True
except ImportError:
    _PIL = False


def _base_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent.parent


_BASE        = _base_dir()
_CONFIG_PATH = _BASE / "config" / "api_keys.json"


def _load_config() -> dict:
    try:
        return json.loads(_CONFIG_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_config_key(key: str, value) -> None:
    try:
        cfg = _load_config()
        cfg[key] = value
        _CONFIG_PATH.write_text(json.dumps(cfg, indent=4), encoding="utf-8")
    except Exception as e:
        print(f"[Vision] ⚠️  Could not save config key '{key}': {e}")


def _get_os() -> str:
    return _load_config().get("os_system", "windows").lower()


_IMG_MAX_W = 1280
_IMG_MAX_H = 720
_JPEG_Q    = 82


def _compress(img_bytes: bytes, source_format: str = "PNG") -> tuple[bytes, str]:
    if not _PIL:
        return img_bytes, f"image/{source_format.lower()}"

    try:
        img = PIL.Image.open(io.BytesIO(img_bytes)).convert("RGB")
        img.thumbnail((_IMG_MAX_W, _IMG_MAX_H), PIL.Image.BILINEAR)
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=_JPEG_Q, optimize=False)
        return buf.getvalue(), "image/jpeg"
    except Exception as e:
        print(f"[Vision] ⚠️  Image compress failed: {e}")
        return img_bytes, f"image/{source_format.lower()}"


def _is_wsl() -> bool:
    try:
        return "microsoft" in Path("/proc/sys/kernel/osrelease").read_text(encoding="utf-8", errors="ignore").lower()
    except Exception:
        return False


def _capture_windows_host_screen() -> bytes | None:
    """Capture the real Windows desktop screen from inside WSL2 via PowerShell."""
    import base64
    import subprocess
    ps_script = (
        "Add-Type -AssemblyName System.Windows.Forms,System.Drawing; "
        "$b = [System.Windows.Forms.Screen]::PrimaryScreen.Bounds; "
        "$bmp = New-Object System.Drawing.Bitmap($b.Width, $b.Height); "
        "$g = [System.Drawing.Graphics]::FromImage($bmp); "
        "$g.CopyFromScreen($b.Location, [System.Drawing.Point]::Empty, $b.Size); "
        "$ms = New-Object System.IO.MemoryStream; "
        "$bmp.Save($ms, [System.Drawing.Imaging.ImageFormat]::Png); "
        "$g.Dispose(); $bmp.Dispose(); "
        "[Convert]::ToBase64String($ms.ToArray())"
    )
    try:
        r = subprocess.run(
            ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", ps_script],
            capture_output=True, text=True, timeout=8,
        )
        if r.returncode == 0 and r.stdout.strip():
            return base64.b64decode(r.stdout.strip())
    except Exception as e:
        print(f"[Vision] ⚠️  WSL host screen capture failed: {e}")
    return None


# What the last screen capture covered, so a caller that hands the JPEG to a
# Vision model can map the model's pixel/normalised coordinates back onto the
# real (physical-pixel) monitor. The JPEG is downscaled (2560x1600 -> 1152x720
# on the live panel), so image pixels are NOT screen pixels.
LAST_CAPTURE_META: dict = {}

# A frame with almost no variance is a black/blank grab (secure desktop, a
# capture from a session without a desktop, a GPU-protected surface).
_BLANK_STD = 2.0


def _frame_stats(rgb_bytes: bytes, size) -> tuple[float, float]:
    try:
        arr = np.frombuffer(rgb_bytes, dtype=np.uint8)
        sample = arr[:: max(1, len(arr) // 200_000)]
        return float(sample.mean()), float(sample.std())
    except Exception:
        return 0.0, 0.0


def _grab_mss() -> tuple[bytes, tuple[int, int], dict]:
    sct_cls = getattr(mss, "MSS", None) or mss.mss
    with sct_cls() as sct:
        monitors = sct.monitors          # [0] = all combined, [1..n] = real screens
        target   = monitors[1] if len(monitors) > 1 else monitors[0]
        shot     = sct.grab(target)
        png      = mss.tools.to_png(shot.rgb, shot.size)
        mean, std = _frame_stats(shot.rgb, shot.size)
    mon = {"left": int(target["left"]), "top": int(target["top"]),
           "width": int(target["width"]), "height": int(target["height"])}
    return png, (mean, std), mon


def _grab_pil() -> tuple[bytes, tuple[float, float], dict] | None:
    try:
        from PIL import ImageGrab
        img = ImageGrab.grab(all_screens=False).convert("RGB")
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        arr = np.asarray(img)[::8, ::8]
        mon = {"left": 0, "top": 0, "width": img.width, "height": img.height}
        return buf.getvalue(), (float(arr.mean()), float(arr.std())), mon
    except Exception:
        return None


def _capture_screen() -> tuple[bytes, str]:
    if _is_wsl():
        win_png = _capture_windows_host_screen()
        if win_png:
            return _compress(win_png, "PNG")

    if not _MSS:
        raise RuntimeError("mss is not installed. Run: pip install mss")

    try:
        from core.dpi import ensure_dpi_awareness
        ensure_dpi_awareness()
    except Exception:
        pass

    png, (mean, std), mon = _grab_mss()
    backend = "mss"
    if std < _BLANK_STD:
        # One quick retry (a grab racing a full-screen transition), then GDI.
        import time as _time
        _time.sleep(0.15)
        png, (mean, std), mon = _grab_mss()
        if std < _BLANK_STD and sys.platform == "win32":
            alt = _grab_pil()
            if alt is not None and alt[1][1] >= std:
                png, (mean, std), mon = alt
                backend = "pil_imagegrab"

    data, mime = _compress(png, "PNG")
    img_w, img_h = mon["width"], mon["height"]
    if _PIL and mime == "image/jpeg":
        try:
            img_w, img_h = PIL.Image.open(io.BytesIO(data)).size
        except Exception:
            pass
    LAST_CAPTURE_META.clear()
    LAST_CAPTURE_META.update({
        "monitor": mon, "image_w": int(img_w), "image_h": int(img_h),
        "mean": round(mean, 2), "std": round(std, 2),
        "blank": bool(std < _BLANK_STD), "backend": backend,
    })
    return data, mime


def capture_screen_meta() -> tuple[bytes, str, dict]:
    """`_capture_screen()` plus the geometry/blank metadata of that same frame."""
    data, mime = _capture_screen()
    return data, mime, dict(LAST_CAPTURE_META)


def _cv2_backend() -> int:
    """Return the best OpenCV camera backend for the current OS."""
    if not _CV2:
        return 0
    os_name = _get_os()
    if os_name == "windows":
        return cv2.CAP_DSHOW
    if os_name == "mac":
        return cv2.CAP_AVFOUNDATION
    return cv2.CAP_ANY


def _probe_camera(index: int, backend: int, warmup: int = 5) -> bool:

    if not _CV2:
        return False
    cap = cv2.VideoCapture(index, backend)
    if not cap.isOpened():
        cap.release()
        return False
    for _ in range(warmup):
        cap.read()
    ret, frame = cap.read()
    cap.release()
    if not ret or frame is None:
        return False
    return bool(np.mean(frame) > 8)


def _detect_camera_index() -> int:

    backend = _cv2_backend()
    print("[Vision] 🔍 Auto-detecting camera...")
    for idx in range(6):
        if _probe_camera(idx, backend):
            print(f"[Vision] ✅ Camera found at index {idx}")
            _save_config_key("camera_index", idx)
            return idx
        print(f"[Vision] ⚠️  Camera index {idx}: no usable frame")

    print("[Vision] ⚠️  No camera found — defaulting to index 0")
    _save_config_key("camera_index", 0)
    return 0


def _get_camera_index() -> int:
    cfg = _load_config()
    if "camera_index" in cfg:
        return int(cfg["camera_index"])
    return _detect_camera_index()


def _capture_camera() -> tuple[bytes, str]:
    if not _CV2:
        raise RuntimeError("OpenCV (cv2) is not installed. Run: pip install opencv-python")

    index   = _get_camera_index()
    backend = _cv2_backend()
    cap     = cv2.VideoCapture(index, backend)

    if not cap.isOpened():
        raise RuntimeError(f"Camera index {index} could not be opened.")

    for _ in range(10):
        cap.read()

    ret, frame = cap.read()
    cap.release()

    if not ret or frame is None:
        raise RuntimeError("Camera returned no frame.")

    if _PIL:
        rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        img = PIL.Image.fromarray(rgb)
        img.thumbnail((_IMG_MAX_W, _IMG_MAX_H), PIL.Image.BILINEAR)
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=_JPEG_Q)
        return buf.getvalue(), "image/jpeg"

    _, buf = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, _JPEG_Q])
    return buf.tobytes(), "image/jpeg"
