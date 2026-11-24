"""
Local double-clap wake detector for JARVIS.

Design goals:
  * ZERO new dependencies — pure NumPy + stdlib.
  * ZERO latency on the audio path — the microphone callback only does a cheap,
    non-blocking queue push (feed()); DSP analysis runs in its own daemon thread.
  * Bluetooth HFP & laptop-mic friendly — HFP/CVSD headsets (e.g. Headset (tanishq))
    roll off above ~3.4 kHz and apply AGC, so spectral flatness and mid/high vs low
    energy checks focus on 700-3500 Hz rather than relying on >4 kHz energy.
  * High specificity — combines adaptive noise floor, sharp attack/onset ratio,
    fast decay (<90 ms), broadband spectral flatness + low-band rejection, inter-clap
    quiet valley, timing window (180-900 ms), amplitude pairing, and burst suppression
    so speech, desk thuds, chimes, and typing bursts do not trigger false wakes.
"""
from __future__ import annotations

from collections import deque
import queue
import threading
from typing import Callable

import numpy as np

SAMPLE_RATE = 16000
FRAME_SAMPLES = 160      # 10 ms sub-frames at 16 kHz
FFT_SIZE = 512           # 32 ms spectral analysis window

_COMMON_PRESET = {
    "decay_window_s": 0.16,
    "decay_ratio": 0.50,
    "min_mid_ratio": 0.15,
    "max_top3_ratio": 0.60,
    "min_gap_s": 0.135,
    "max_gap_s": 1.15,
    "valley_ratio": 0.50,
    "pair_amp_min": 0.20,
    "pair_amp_max": 5.0,
    "burst_window_s": 1.5,
    "burst_max_impulses": 4,
    "burst_suppress_s": 1.5,
    "cooldown_s": 2.0,
}

SENSITIVITY_PRESETS: dict[str, dict[str, float]] = {
    "low": {
        **_COMMON_PRESET,
        "min_rms": 450.0,
        "min_peak": 1400.0,
        "snr_ratio": 4.8,
        "onset_ratio": 3.0,
        "min_high_ratio": 0.52,
        "min_flatness": 0.08,
        "min_crest": 1.80,
    },
    "medium": {
        **_COMMON_PRESET,
        "min_rms": 170.0,
        "min_peak": 580.0,
        "snr_ratio": 3.8,
        "onset_ratio": 2.4,
        "min_high_ratio": 0.45,
        "min_flatness": 0.055,
        "min_crest": 1.60,
    },
    "high": {
        **_COMMON_PRESET,
        "min_rms": 110.0,
        "min_peak": 380.0,
        "snr_ratio": 3.2,
        "onset_ratio": 2.0,
        "min_high_ratio": 0.40,
        "min_flatness": 0.040,
        "min_crest": 1.45,
    },
}


def normalize_sensitivity(level: str | None) -> str:
    """Clamp sensitivity name to 'low' | 'medium' | 'high'."""
    val = str(level or "medium").strip().lower()
    return val if val in SENSITIVITY_PRESETS else "medium"


class ClapAnalyzer:
    """Pure synchronous double-clap state machine timed by sample clock."""

    def __init__(self, sensitivity: str = "medium", sample_rate: int = SAMPLE_RATE):
        self._sr = max(8000, int(sample_rate))
        self._frame_samples = max(80, int(round(self._sr * 0.010)))
        self._fft_size = FFT_SIZE
        self._ring_cap = 4096    # 256 ms at 16 kHz so centered FFT windows fit cleanly

        self._sensitivity = normalize_sensitivity(sensitivity)
        self._cfg = dict(SENSITIVITY_PRESETS[self._sensitivity])

        # Precompute FFT window and speech/clap band masks once.
        self._hann = np.hanning(self._fft_size).astype(np.float32)
        freqs = np.fft.rfftfreq(self._fft_size, 1.0 / self._sr)
        self._mask_low = (freqs >= 60.0) & (freqs <= 320.0)
        self._mask_high = (freqs >= 450.0) & (freqs <= 7000.0)
        self._mask_mid = (freqs >= 450.0) & (freqs <= 3500.0)
        self._mask_core = (freqs >= 450.0) & (freqs <= 2000.0)
        self._mask_total = (freqs >= 60.0) & (freqs <= 7500.0)

        self._pending_samples = np.empty(0, dtype=np.float32)
        self._ring = np.zeros(self._ring_cap, dtype=np.float32)
        self._samples_processed = 0
        self._frames_seen = 0
        self._noise_floor = 60.0
        self._prev_rms: deque[float] = deque(maxlen=8)

        self._active: dict | None = None
        self._first_clap: tuple[float, float] | None = None
        self._valley_seen = False
        self._recent_impulses: deque[float] = deque(maxlen=16)
        self._suppress_until = 0.0
        self._cooldown_until = 0.0
        self.last_reject_reason: str = ""
        self.last_event: dict | None = None

    @property
    def sensitivity(self) -> str:
        return self._sensitivity

    def set_sensitivity(self, level: str) -> None:
        self._sensitivity = normalize_sensitivity(level)
        self._cfg = dict(SENSITIVITY_PRESETS[self._sensitivity])

    def reset(self, *, keep_noise_floor: bool = True, clear_cooldown: bool = True) -> None:
        """Clear transient audio buffers and pairing state (used on sleep/wake)."""
        floor = self._noise_floor
        self._pending_samples = np.empty(0, dtype=np.float32)
        self._ring.fill(0.0)
        self._prev_rms.clear()
        self._active = None
        self._first_clap = None
        self._valley_seen = False
        self._recent_impulses.clear()
        self._suppress_until = 0.0
        if clear_cooldown:
            self._cooldown_until = 0.0
        if keep_noise_floor:
            self._noise_floor = max(20.0, min(4000.0, floor))
        else:
            self._noise_floor = 60.0
            self._frames_seen = 0

    def process(self, frame_int16) -> bool:
        """Feed a mono int16 chunk; returns True if a double-clap occurred."""
        arr = np.asarray(frame_int16, dtype=np.float32)
        if arr.ndim > 1:
            arr = arr[:, 0]
        else:
            arr = arr.ravel()
        if arr.size == 0:
            return False

        if self._pending_samples.size:
            data = np.concatenate((self._pending_samples, arr))
        else:
            data = arr

        detected = False
        pos = 0
        step = self._frame_samples
        total = data.size
        while pos + step <= total:
            sub = data[pos : pos + step]
            pos += step
            if self._step_subframe(sub):
                detected = True

        self._pending_samples = data[pos:].copy() if pos < total else np.empty(0, dtype=np.float32)
        return detected

    def _capture_centered_window(self, peak_sample: int) -> np.ndarray:
        """Slice an FFT_SIZE window centered on `peak_sample` (where hann == 1.0)."""
        half = self._fft_size // 2
        ring_idx = self._ring_cap - (self._samples_processed - peak_sample)
        start = max(0, min(self._ring_cap - self._fft_size, ring_idx - half))
        return self._ring[start : start + self._fft_size].copy()

    def _passes_spectral_check(self, window: np.ndarray) -> bool:
        """Reject thuds, speech vowels, and chimes using band ratios + flatness."""
        if window.size != self._fft_size:
            self.last_reject_reason = "bad_window_size"
            return False
        centered = window - float(np.mean(window))
        rms = float(np.sqrt(np.mean(centered * centered)))
        if rms <= 1e-6:
            self.last_reject_reason = "zero_rms"
            return False
        crest = float(np.max(np.abs(centered))) / rms

        spec = np.fft.rfft(centered * self._hann)
        power = (spec.real * spec.real) + (spec.imag * spec.imag)

        e_low = float(np.sum(power[self._mask_low]))
        e_high = float(np.sum(power[self._mask_high]))
        e_mid = float(np.sum(power[self._mask_mid]))
        e_total = float(np.sum(power[self._mask_total]))
        if e_total <= 1e-9 or (e_low + e_high) <= 1e-9:
            self.last_reject_reason = "zero_energy"
            return False

        high_ratio = e_high / (e_low + e_high)
        mid_ratio = e_mid / e_total

        # Spectral flatness over core clap band (450-2000 Hz) + tonal peak concentration.
        mid_power = power[self._mask_mid]
        core_power = power[self._mask_core]
        if mid_power.size == 0 or core_power.size == 0:
            self.last_reject_reason = "empty_mid_band"
            return False
        mean_p = float(np.mean(core_power)) + 1e-12
        geo_p = float(np.exp(np.mean(np.log(core_power + 1e-12))))
        flatness = geo_p / mean_p
        top3_ratio = float(np.sum(np.sort(mid_power)[-3:]) / (float(np.sum(mid_power)) + 1e-12))

        self.last_event = {
            "rms": round(rms, 1),
            "crest": round(crest, 2),
            "high_ratio": round(high_ratio, 3),
            "mid_ratio": round(mid_ratio, 3),
            "flatness": round(flatness, 3),
            "top3": round(top3_ratio, 3),
        }

        if crest < self._cfg["min_crest"]:
            self.last_reject_reason = f"low_crest:{crest:.2f}"
            return False
        if high_ratio < self._cfg["min_high_ratio"]:
            self.last_reject_reason = f"low_high_ratio:{high_ratio:.3f}"
            return False
        if mid_ratio < self._cfg["min_mid_ratio"]:
            self.last_reject_reason = f"low_mid_ratio:{mid_ratio:.3f}"
            return False
        if top3_ratio > self._cfg["max_top3_ratio"]:
            self.last_reject_reason = f"tonal_top3:{top3_ratio:.3f}"
            return False
        if flatness < self._cfg["min_flatness"]:
            self.last_reject_reason = f"low_flatness:{flatness:.3f}"
            return False

        self.last_reject_reason = ""
        return True

    def _update_noise_floor(self, rms: float) -> None:
        if self._frames_seen <= 15:
            alpha = 0.25 if self._frames_seen > 1 else 1.0
        else:
            alpha = 0.03
        updated = (1.0 - alpha) * self._noise_floor + alpha * rms
        self._noise_floor = max(20.0, min(4000.0, updated))

    def _step_subframe(self, sub: np.ndarray) -> bool:
        self._samples_processed += sub.size
        self._frames_seen += 1
        t = self._samples_processed / self._sr

        # Append subframe to rolling ring buffer.
        shift = sub.size
        self._ring[:-shift] = self._ring[shift:]
        self._ring[-shift:] = sub

        rms = float(np.sqrt(np.mean(sub * sub)))
        sub_abs = float(np.max(np.abs(sub)))
        cfg = self._cfg

        # Expire stale first clap once outside max_gap_s.
        if self._first_clap is not None and (t - self._first_clap[0]) > cfg["max_gap_s"]:
            self._first_clap = None
            self._valley_seen = False

        # Check inter-clap silent valley after first clap has decayed.
        if self._first_clap is not None and not self._valley_seen and self._active is None:
            if rms <= cfg["valley_ratio"] * self._first_clap[1]:
                self._valley_seen = True

        # Track an active impulse awaiting fast decay + centered spectral window.
        if self._active is not None:
            act = self._active
            if not act["decayed"]:
                if (t - act["start_t"]) <= 0.040:
                    if sub_abs > act["peak_abs"]:
                        act["peak_abs"] = sub_abs
                        act["peak_sample"] = (
                            (self._samples_processed - sub.size) + int(np.argmax(np.abs(sub)))
                        )
                    if rms > act["peak_rms"]:
                        act["peak_rms"] = rms
                        act["peak_t"] = t

                if rms <= cfg["decay_ratio"] * act["peak_rms"]:
                    act["decayed"] = True
                    act["decay_rms"] = rms
                elif (t - act["peak_t"]) > cfg["decay_window_s"]:
                    # Sustained energy (speech/music) -> not a clap.
                    self.last_reject_reason = "sustained_no_decay"
                    self._active = None
                    self._prev_rms.append(rms)
                    return False

            if act["decayed"] and self._samples_processed >= act["peak_sample"] + (self._fft_size // 2):
                window = self._capture_centered_window(act["peak_sample"])
                peak_t = act["peak_t"]
                peak_rms = act["peak_rms"]
                decay_rms = act["decay_rms"]
                self._active = None
                self._prev_rms.append(rms)
                if not self._passes_spectral_check(window):
                    return False
                return self._handle_verified_clap(peak_t, peak_rms, current_rms=decay_rms)

            self._prev_rms.append(rms)
            return False

        # Not currently tracking an impulse: check for sharp onset above floor.
        baseline_prev = (
            float(np.median(list(self._prev_rms))) if len(self._prev_rms) >= 2 else self._noise_floor
        )
        trigger_level = max(
            cfg["min_rms"],
            cfg["snr_ratio"] * self._noise_floor,
            cfg["onset_ratio"] * baseline_prev,
        )

        if rms >= trigger_level and sub_abs >= cfg["min_peak"] and t >= self._cooldown_until:
            peak_sample = (self._samples_processed - sub.size) + int(np.argmax(np.abs(sub)))
            self._active = {
                "start_t": t,
                "peak_t": t,
                "peak_rms": rms,
                "peak_abs": sub_abs,
                "peak_sample": peak_sample,
                "decayed": False,
                "decay_rms": rms,
            }
            self._prev_rms.append(rms)
            return False

        # Quiet frame: update adaptive background floor (avoid pulling floor up during decays).
        if self._first_clap is None or rms <= cfg["valley_ratio"] * self._first_clap[1]:
            if rms < max(cfg["min_rms"] * 1.5, cfg["snr_ratio"] * self._noise_floor):
                self._update_noise_floor(rms)
        self._prev_rms.append(rms)
        return False

    def _handle_verified_clap(self, peak_t: float, peak_rms: float, *, current_rms: float) -> bool:
        cfg = self._cfg

        # Burst suppression for rapid typing/applause.
        while self._recent_impulses and (peak_t - self._recent_impulses[0]) > cfg["burst_window_s"]:
            self._recent_impulses.popleft()
        self._recent_impulses.append(peak_t)
        if len(self._recent_impulses) >= int(cfg["burst_max_impulses"]):
            self._suppress_until = peak_t + cfg["burst_suppress_s"]
            self._first_clap = None
            self._valley_seen = False
            return False

        if peak_t < self._suppress_until or peak_t < self._cooldown_until:
            return False

        if self._first_clap is None:
            self._first_clap = (peak_t, peak_rms)
            self._valley_seen = current_rms <= cfg["valley_ratio"] * peak_rms
            return False

        t1, rms1 = self._first_clap
        dt = peak_t - t1

        if dt < 0.085:
            # Room echo / single-clap bounce (up to ~14m) — keep original Clap 1.
            self.last_reject_reason = f"echo_dt:{dt:.3f}"
            return False

        if dt < cfg["min_gap_s"]:
            # Sub-135 ms follow-up impulse indicates rapid tapping/applause, not a double-clap.
            self.last_reject_reason = f"too_fast_dt:{dt:.3f}"
            self._first_clap = None
            self._valley_seen = False
            return False

        amp_ratio = peak_rms / max(rms1, 1.0)
        if (
            cfg["min_gap_s"] <= dt <= cfg["max_gap_s"]
            and self._valley_seen
            and cfg["pair_amp_min"] <= amp_ratio <= cfg["pair_amp_max"]
        ):
            self._cooldown_until = peak_t + cfg["cooldown_s"]
            self._first_clap = None
            self._valley_seen = False
            self._recent_impulses.clear()
            return True

        # Otherwise treat this verified clap as the new Clap 1.
        self._first_clap = (peak_t, peak_rms)
        self._valley_seen = current_rms <= cfg["valley_ratio"] * peak_rms
        return False


class DoubleClapDetector:
    """
    Threaded double-clap detector matching WakeWordDetector's lifecycle.
    Mic callbacks push int16 frames via feed() without blocking; detections invoke
    on_detect() on the worker thread.
    """

    def __init__(
        self,
        on_detect: Callable[[], None],
        sensitivity: str = "medium",
        sample_rate: int = SAMPLE_RATE,
        logger: Callable[[str], None] = print,
        notify: Callable[[str], None] | None = None,
    ):
        self._on_detect = on_detect
        self._logger = logger
        self._notify = notify or (lambda _msg: None)
        self._analyzer = ClapAnalyzer(sensitivity=sensitivity, sample_rate=sample_rate)
        self._queue: queue.Queue = queue.Queue(maxsize=64)
        self._thread: threading.Thread | None = None
        self._running = False
        self._ready = False
        self._reset_pending = False

    def start(self) -> bool:
        """Start the background double-clap worker thread. Idempotent, never raises."""
        if self._running:
            return True
        try:
            self._analyzer.reset(keep_noise_floor=False)
            self._running = True
            self._ready = True
            self._thread = threading.Thread(
                target=self._loop, daemon=True, name="ClapDetectThread"
            )
            self._thread.start()
            self._logger(f"Double-clap: listening (sensitivity={self._analyzer.sensitivity}).")
            return True
        except Exception as e:
            self._running = False
            self._ready = False
            self._logger(f"Double-clap: could not start — {e}")
            return False

    def stop(self) -> None:
        self._running = False
        self._ready = False
        try:
            self._queue.put_nowait(None)
        except Exception:
            pass
        t, self._thread = self._thread, None
        if t is not None and t.is_alive() and threading.current_thread() is not t:
            t.join(timeout=1.0)
        self._drain()

    @property
    def ready(self) -> bool:
        return self._ready

    def set_sensitivity(self, level: str) -> None:
        self._analyzer.set_sensitivity(level)

    def reset(self) -> None:
        """Clear pending clap state (thread-safe flag handled on worker thread)."""
        self._reset_pending = True

    def feed(self, frame_int16) -> None:
        """Called from the mic callback (real-time thread). Non-blocking O(1)."""
        if not self._running:
            return
        try:
            data = (
                frame_int16[:, 0].copy()
                if getattr(frame_int16, "ndim", 1) > 1
                else frame_int16.copy()
            )
            self._queue.put_nowait(data)
        except queue.Full:
            pass
        except Exception:
            pass

    def _loop(self) -> None:
        while self._running:
            try:
                frame = self._queue.get()
                if frame is None or not self._running:
                    break
                if self._reset_pending:
                    self._reset_pending = False
                    self._analyzer.reset(clear_cooldown=True)
                if self._analyzer.process(frame):
                    self._drain()
                    self._analyzer.reset(clear_cooldown=False)
                    try:
                        self._on_detect()
                    except Exception as e:
                        self._logger(f"Double-clap: on_detect error — {e}")
            except Exception as e:
                self._logger(f"Double-clap: analysis error — {e}")

    def _drain(self) -> None:
        try:
            while True:
                self._queue.get_nowait()
        except Exception:
            pass
