"""
Local wake-word detection for JARVIS ("Hey Jarvis").

Design goals:
  • ZERO cost when the feature is off — openwakeword is imported ONLY inside
    start()/install helpers, never at module load. If the user never enables
    wake word, none of this touches the app.
  • ZERO latency on the audio path — the microphone callback only ever does a
    cheap, non-blocking queue push (feed()); the actual model inference runs in
    this module's own background thread, so the real-time audio thread and the
    Gemini stream are never slowed.
  • Fully local & offline — audio fed here never leaves the machine; there is no
    network call except the one-time model download the user triggers from the UI.

openwakeword ships small ONNX models (a few MB each) and runs comfortably on a
CPU. The pretrained wake phrase used here is "Hey Jarvis".
"""
from __future__ import annotations

import functools
import io
import os
import queue
import subprocess
import sys
import threading
import time
import wave
from pathlib import Path
from typing import Callable

# Pretrained openwakeword model that listens for "Hey Jarvis".
WAKE_MODEL = "hey_jarvis"
# Score in [0,1]; above this counts as a detection. Tunable per environment.
DEFAULT_THRESHOLD = 0.5
# Mic frames arrive at 16 kHz int16; this is just the detector's input rate.
SAMPLE_RATE = 16000
# openWakeWord processes audio natively in 80 ms (1280-sample) increments.
_OWW_FRAME_SAMPLES = 1280
_MIN_MODEL_BYTES = 1024


def _onnx_present(models_dir: Path, prefix: str) -> bool:
    """True when at least one non-empty ONNX model matching `prefix` exists."""
    return any(
        p.is_file() and p.stat().st_size > _MIN_MODEL_BYTES
        for p in models_dir.glob(f"{prefix}*.onnx")
    )


def is_installed() -> bool:
    """True if the openwakeword package is importable (no model check)."""
    try:
        import importlib.util
        return importlib.util.find_spec("openwakeword") is not None
    except Exception:
        return False


def is_ready() -> bool:
    """True if openwakeword is installed AND its ONNX model files are present on disk.

    This is a cheap, DETERMINISTIC file-existence check. It deliberately does NOT
    construct a Model to probe readiness — doing that is slow and, worse, can clash
    with the detector's own Model when it's already running, which intermittently
    returned False and made the UI flicker to 'not downloaded'. Never raises.
    """
    if not is_installed():
        return False
    try:
        import openwakeword
        models_dir = Path(openwakeword.__file__).resolve().parent / "resources" / "models"
        if not models_dir.is_dir():
            return False
        # WakeWordDetector always runs inference_framework="onnx", so .onnx files
        # (not .tflite alone) must exist and be non-trivial.
        return bool(
            _onnx_present(models_dir, WAKE_MODEL)
            and _onnx_present(models_dir, "melspectrogram")
            and _onnx_present(models_dir, "embedding_model")
        )
    except Exception:
        return False


def _download_onnx_file(url: str, dest: Path) -> None:
    """Stream-download one ONNX model file atomically via a temporary file."""
    import requests
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_name(dest.name + ".tmp")
    try:
        with requests.get(url, stream=True, timeout=30) as r:
            r.raise_for_status()
            with open(tmp, "wb") as fh:
                for chunk in r.iter_content(chunk_size=16384):
                    if chunk:
                        fh.write(chunk)
        if not tmp.is_file() or tmp.stat().st_size <= _MIN_MODEL_BYTES:
            raise RuntimeError(f"downloaded file {dest.name} is empty or truncated")
        os.replace(tmp, dest)
    except Exception:
        try:
            tmp.unlink(missing_ok=True)
        except Exception:
            pass
        raise


def _ensure_required_onnx_models() -> None:
    """Download any missing ONNX files directly from openwakeword's official URLs."""
    import openwakeword
    models_dir = Path(openwakeword.__file__).resolve().parent / "resources" / "models"
    models_dir.mkdir(parents=True, exist_ok=True)

    required: list[tuple[str, str]] = []
    for feat in openwakeword.FEATURE_MODELS.values():
        url = str(feat["download_url"]).replace(".tflite", ".onnx")
        prefix = url.rsplit("/", 1)[-1].split(".onnx", 1)[0]
        required.append((prefix, url))
    if WAKE_MODEL in openwakeword.MODELS:
        url = str(openwakeword.MODELS[WAKE_MODEL]["download_url"]).replace(".tflite", ".onnx")
        required.append((WAKE_MODEL, url))

    for prefix, url in required:
        if not _onnx_present(models_dir, prefix):
            filename = url.rsplit("/", 1)[-1]
            _download_onnx_file(url, models_dir / filename)


def install_and_download(logger: Callable[[str], None] = print,
                         notify: Callable[[str], None] | None = None) -> tuple[bool, str]:
    """
    One-click setup for the UI button: pip-install openwakeword if missing, then
    download the wake model. Returns (ok, message). Never raises — every failure
    is reported through the returned message and the logger.
    """
    _tell = notify or (lambda _msg: None)
    try:
        if not is_installed():
            logger("Wake word: installing openwakeword (one-time)…")
            _tell("Wake word: installing openwakeword (one-time)…")
            r = subprocess.run(
                [sys.executable, "-m", "pip", "install", "openwakeword"],
                capture_output=True, text=True, encoding="utf-8", errors="replace",
            )
            if r.returncode != 0:
                tail = (r.stderr or r.stdout or "").strip().splitlines()[-1:] or [""]
                return False, f"pip install failed: {tail[0][:160]}"
        # Download the pretrained melspectrogram/embedding + wake models.
        logger("Wake word: downloading models…")
        _tell("Wake word: downloading models…")
        try:
            import openwakeword.utils as _u
            orig_stderr = sys.stderr
            orig_stdout = sys.stdout
            try:
                # Under pythonw.exe sys.stderr/stdout can be None, which makes tqdm crash.
                if sys.stderr is None:
                    sys.stderr = io.StringIO()
                if sys.stdout is None:
                    sys.stdout = io.StringIO()
                try:
                    _u.download_models([WAKE_MODEL])
                except TypeError:
                    _u.download_models()   # older signature downloads the default set
            finally:
                sys.stderr = orig_stderr
                sys.stdout = orig_stdout
            if not is_ready():
                _ensure_required_onnx_models()
        except Exception as e:
            return False, f"model download failed: {e}"

        if not is_ready():
            return False, "installed, but the ONNX wake model files could not be verified."
        logger("Wake word: ready.")
        return True, "Wake word installed and ready."
    except Exception as e:
        return False, f"setup error: {e}"


@functools.lru_cache(maxsize=1)
def build_wake_chime_wav() -> bytes:
    """Synthesize a crisp 220 ms two-tone rising chime (24 kHz mono 16-bit WAV)."""
    import numpy as np

    sr = 24000
    note_samples = int(sr * 0.110)  # 110 ms per note -> 220 ms total
    t = np.arange(note_samples, dtype=np.float32) / sr
    # Smooth half-sine attack/release envelope so each tone is click-free.
    env = np.sin(np.pi * np.arange(note_samples, dtype=np.float32) / max(1, note_samples - 1))
    amp = 0.48 * 32767.0
    n1 = amp * env * (0.80 * np.sin(2.0 * np.pi * 587.33 * t) + 0.20 * np.sin(2.0 * np.pi * 1174.66 * t))
    n2 = amp * env * (0.80 * np.sin(2.0 * np.pi * 880.0 * t) + 0.20 * np.sin(2.0 * np.pi * 1760.0 * t))
    pcm = np.concatenate((n1, n2)).astype(np.int16)

    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sr)
        wf.writeframes(pcm.tobytes())
    return buf.getvalue()


def play_wake_chime() -> bool:
    """Play the subtle wake chime asynchronously without touching the live mic gate."""
    if sys.platform != "win32":
        return False
    try:
        import winsound
        wav_bytes = build_wake_chime_wav()
        winsound.PlaySound(
            wav_bytes,
            winsound.SND_MEMORY | winsound.SND_ASYNC | winsound.SND_NODEFAULT,
        )
        return True
    except Exception:
        return False


class WakeWordDetector:
    """
    Runs the wake model in a dedicated thread. The mic thread calls feed() with
    raw int16 frames; detections invoke on_detect() (called from this thread —
    the callback must marshal to whatever loop/UI it needs).
    """

    def __init__(self, on_detect: Callable[[], None],
                 threshold: float = DEFAULT_THRESHOLD,
                 cooldown_s: float = 1.5,
                 logger: Callable[[str], None] = print,
                 notify: Callable[[str], None] | None = None):
        self._on_detect = on_detect
        self._threshold = threshold
        self._cooldown_s = max(0.0, float(cooldown_s))
        self._logger    = logger
        # See PluginRegistry: `logger` is the console and gets everything,
        # `notify` is the activity log and gets only what the user must act on.
        self._notify    = notify or (lambda _msg: None)
        self._queue: queue.Queue = queue.Queue(maxsize=50)
        self._thread: threading.Thread | None = None
        self._running = False
        self._model = None
        self._ready = False
        self._reset_pending = False
        self._cooldown_until = 0.0
        self._buf = None

    def start(self) -> bool:
        """Load the model and spawn the inference thread. Returns True on success.
        Safe to call again — a no-op if already running. Never raises."""
        if self._running:
            return True
        try:
            from openwakeword.model import Model
            self._model = Model(wakeword_models=[WAKE_MODEL], inference_framework="onnx")
        except Exception as e:
            self._logger(f"Wake word: could not load model — {e}")
            self._notify("Wake word unavailable — use the WAKE NOW button.")
            self._model = None
            return False
        self._running = True
        self._ready = True
        self._thread = threading.Thread(target=self._loop, daemon=True, name="WakeWordThread")
        self._thread.start()
        self._logger("Wake word: listening for 'Hey Jarvis'.")
        return True

    def stop(self) -> None:
        self._running = False
        # unblock the thread if it's waiting on the queue
        try:
            self._queue.put_nowait(None)
        except Exception:
            pass
        t, self._thread = self._thread, None
        if t is not None and t.is_alive() and threading.current_thread() is not t:
            t.join(timeout=1.0)
        self._model = None
        self._ready = False

    @property
    def ready(self) -> bool:
        return self._ready

    def reset(self) -> None:
        """Forget what was heard so far — call it when the assistant goes to sleep.
        Done on the detector's own thread, which is the only one that touches
        the model."""
        self._reset_pending = True

    def _reset_model(self) -> None:
        # The model keeps its recent audio features and scores. Left alone, one
        # "Hey Jarvis" fired a dozen times in a row (measured: 12), and audio
        # from before the assistant woke was still in there when it went back
        # to sleep — so it could wake again on words said minutes earlier.
        self._buf = None
        try:
            if self._model is not None:
                self._model.reset()
        except Exception:
            pass

    def feed(self, frame_int16) -> None:
        """Called from the mic callback (real-time thread). Must stay cheap and
        never block — the frame is copied and dropped if the queue is backed up."""
        if not self._running:
            return
        try:
            # frame_int16 is a numpy int16 array (possibly 2-D mono) — flatten to 1-D
            data = frame_int16[:, 0].copy() if getattr(frame_int16, "ndim", 1) > 1 else frame_int16.copy()
            self._queue.put_nowait(data)
        except queue.Full:
            pass
        except Exception:
            pass

    def _loop(self) -> None:
        import numpy as np
        self._buf = np.empty(0, dtype=np.int16)
        while self._running:
            try:
                frame = self._queue.get()
                if frame is None or not self._running:
                    break
                if self._reset_pending:
                    self._reset_pending = False
                    self._reset_model()
                chunk = np.asarray(frame, dtype=np.int16).ravel()
                if chunk.size == 0:
                    continue
                if self._buf is None or self._buf.size == 0:
                    self._buf = chunk
                else:
                    self._buf = np.concatenate((self._buf, chunk))

                while self._buf is not None and self._buf.size >= _OWW_FRAME_SAMPLES and self._running:
                    slice_1280 = self._buf[:_OWW_FRAME_SAMPLES]
                    self._buf = self._buf[_OWW_FRAME_SAMPLES:]
                    scores = self._model.predict(slice_1280)
                    score = 0.0
                    if isinstance(scores, dict):
                        # match the jarvis model regardless of exact key suffix
                        for k, v in scores.items():
                            if "jarvis" in k.lower():
                                score = max(score, float(v))
                        if score == 0.0 and scores:
                            score = max(float(v) for v in scores.values())
                    if score >= self._threshold:
                        now = time.monotonic()
                        if now < self._cooldown_until:
                            continue
                        self._cooldown_until = now + self._cooldown_s
                        # drain any backlog and clear the model, so one utterance is one wake
                        self._drain()
                        self._reset_model()
                        try:
                            self._on_detect()
                        except Exception as e:
                            self._logger(f"Wake word: on_detect error — {e}")
                        break
            except Exception as e:
                self._logger(f"Wake word: inference error — {e}")

    def _drain(self) -> None:
        try:
            while True:
                self._queue.get_nowait()
        except Exception:
            pass
