"""plugins/voice_lock.py — Biometric Voice Lock enrollment and speaker verification."""

from __future__ import annotations

import json
import math
from pathlib import Path
import threading
import time
import wave

import numpy as np
from scipy.fft import dct

_BASE_DIR = Path(__file__).resolve().parent.parent
DEFAULT_STORE_PATH = _BASE_DIR / "memory" / "voiceprints.json"
DEFAULT_SAMPLE_RATE = 16000
DEFAULT_THRESHOLD = 0.82
DEFAULT_TTL_SECONDS = 300

_LOCK = threading.RLock()

PLUGIN = {
    "name": "voice_lock",
    "brief": "Biometric speaker enrollment and verification lock for sensitive actions",
    "description": (
        "Enrolls an 84-dimensional MFCC/spectral/pitch speaker voiceprint in memory/voiceprints.json "
        "and provides biometric speaker verification (via WAV file, audio samples, or explicit short "
        "microphone capture without interfering with wake-word streams) to gate Vault, Power, Form Fill, "
        "and Evolution approvals. Actions: status, enroll, verify, lock, clear."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "action": {
                "type": "STRING",
                "description": "Action: status | enroll | verify | lock | clear (default: status)",
            },
            "audio_path": {
                "type": "STRING",
                "description": "Path to a WAV file for enroll or verify.",
            },
            "seconds": {
                "type": "NUMBER",
                "description": "Recording duration in seconds when capturing from microphone (default: 3.0).",
            },
            "use_mic": {
                "type": "BOOLEAN",
                "description": "Whether to capture audio from the default microphone if audio_path is not given.",
            },
            "threshold": {
                "type": "NUMBER",
                "description": "Cosine similarity threshold in [0.5, 0.99] (default: 0.82).",
            },
            "ttl_seconds": {
                "type": "INTEGER",
                "description": "Seconds verification keeps high-risk gates unlocked (default: 300).",
            },
        },
        "required": [],
    },
}


def _store_file(store_path: Path | str | None = None) -> Path:
    return Path(store_path) if store_path is not None else DEFAULT_STORE_PATH


def _load_state(store_path: Path | str | None = None) -> dict:
    path = _store_file(store_path)
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _save_state(data: dict, store_path: Path | str | None = None) -> None:
    path = _store_file(store_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")


def is_enrolled(store_path: Path | str | None = None) -> bool:
    """Return True when a valid 84-dim voiceprint centroid is enrolled."""
    with _LOCK:
        state = _load_state(store_path)
        vec = state.get("embedding")
        return isinstance(vec, list) and len(vec) == 84


def is_locked(store_path: Path | str | None = None, *, now: float | None = None) -> bool:
    """Return True if voice lock is enrolled and verification TTL has expired or is unset."""
    with _LOCK:
        state = _load_state(store_path)
        vec = state.get("embedding")
        if not (isinstance(vec, list) and len(vec) == 84):
            return False
        cur = time.time() if now is None else float(now)
        until = float(state.get("unlocked_until", 0.0) or 0.0)
        return cur >= until


def _mel_filterbank(sample_rate: int, n_fft: int, n_mels: int = 26) -> np.ndarray:
    low_mel = 2595.0 * math.log10(1.0 + 80.0 / 700.0)
    high_mel = 2595.0 * math.log10(1.0 + min(7600.0, sample_rate / 2.0 - 50.0) / 700.0)
    mel_pts = np.linspace(low_mel, high_mel, n_mels + 2)
    hz_pts = 700.0 * (10.0 ** (mel_pts / 2595.0) - 1.0)
    bins = np.floor((n_fft + 1) * hz_pts / sample_rate).astype(int)
    fbank = np.zeros((n_mels, n_fft // 2 + 1), dtype=np.float64)
    for m in range(1, n_mels + 1):
        left, center, right = int(bins[m - 1]), int(bins[m]), int(bins[m + 1])
        center = max(center, left + 1)
        right = max(right, center + 1)
        for k in range(max(0, left), min(center, fbank.shape[1])):
            fbank[m - 1, k] = (k - left) / max(1, center - left)
        for k in range(max(0, center), min(right, fbank.shape[1])):
            fbank[m - 1, k] = (right - k) / max(1, right - center)
    return fbank


def _estimate_f0_and_clarity(frame: np.ndarray, sample_rate: int) -> tuple[float, float]:
    """Estimate frame pitch F0 (80-380 Hz) and normalized autocorrelation peak."""
    centered = frame - np.mean(frame)
    energy = float(np.dot(centered, centered))
    if energy <= 1e-10:
        return 0.0, 0.0
    ac = np.correlate(centered, centered, mode="full")[len(centered) - 1 :]
    if ac[0] <= 1e-10:
        return 0.0, 0.0
    min_lag = max(1, int(sample_rate / 380.0))
    max_lag = min(len(ac) - 1, int(sample_rate / 80.0))
    if max_lag <= min_lag:
        return 0.0, 0.0
    seg = ac[min_lag : max_lag + 1]
    peak_idx = int(np.argmax(seg)) + min_lag
    clarity = float(ac[peak_idx] / ac[0])
    f0 = float(sample_rate) / float(peak_idx) if clarity > 0.25 else 0.0
    return f0, clarity


def extract_voice_embedding(samples: np.ndarray | list[float], sample_rate: int = DEFAULT_SAMPLE_RATE) -> np.ndarray:
    """Compute an L2-normalized 84-dimensional acoustic speaker embedding."""
    wav = np.asarray(samples, dtype=np.float64)
    if wav.ndim > 1:
        wav = np.mean(wav, axis=1)
    wav = wav.flatten()
    if wav.size < max(800, int(0.1 * sample_rate)):
        raise ValueError("Audio clip too short to extract voiceprint.")

    if sample_rate != DEFAULT_SAMPLE_RATE and sample_rate > 0:
        new_len = max(1600, int(round(wav.size * DEFAULT_SAMPLE_RATE / float(sample_rate))))
        x_old = np.linspace(0.0, 1.0, wav.size)
        x_new = np.linspace(0.0, 1.0, new_len)
        wav = np.interp(x_new, x_old, wav)
        sr = DEFAULT_SAMPLE_RATE
    else:
        sr = int(sample_rate) or DEFAULT_SAMPLE_RATE

    peak = float(np.max(np.abs(wav)))
    if peak < 1e-5:
        raise ValueError("Audio signal is silent or too quiet.")
    wav = wav / peak

    # Pre-emphasis
    emph = np.empty_like(wav)
    emph[0] = wav[0]
    emph[1:] = wav[1:] - 0.97 * wav[:-1]

    frame_len = 400  # 25 ms @ 16 kHz
    hop_len = 160    # 10 ms hop
    n_fft = 512
    n_frames = 1 + max(0, (emph.size - frame_len) // hop_len)
    if n_frames < 3:
        raise ValueError("Insufficient speech frames.")

    idx = np.arange(frame_len)[None, :] + (np.arange(n_frames) * hop_len)[:, None]
    frames = emph[idx]
    rms = np.sqrt(np.mean(frames ** 2, axis=1))
    thresh = max(float(np.percentile(rms, 30)) * 0.6, float(np.max(rms)) * 0.08)
    active_mask = rms >= thresh
    if np.sum(active_mask) < 3:
        active_mask = np.ones_like(rms, dtype=bool)
    active_frames = frames[active_mask]

    window = np.hamming(frame_len)
    win_frames = active_frames * window[None, :]
    spec = np.fft.rfft(win_frames, n=n_fft, axis=1)
    power = (np.abs(spec) ** 2) / float(n_fft)

    fbank = _mel_filterbank(sr, n_fft, n_mels=26)
    mel_floor = max(1e-8, float(np.percentile(power, 95)) * 1e-3)
    mel_e = np.maximum(power @ fbank.T, mel_floor)
    log_mel = np.log(mel_e)
    mfccs = dct(log_mel, type=2, axis=1, norm="ortho")
    mfcc_1_20 = mfccs[:, 1:21]
    # Weight core vocal-tract formants (C1..C12) higher than high-quefrency noise components
    cep_w = np.array([1.2 if i < 12 else 0.45 for i in range(20)], dtype=np.float64)
    mfcc_1_20 = mfcc_1_20 * cep_w[None, :]

    # Cepstral mean/std normalized subvectors (40 dims)
    mfcc_mean = np.mean(mfcc_1_20, axis=0)
    mfcc_std = np.std(mfcc_1_20, axis=0)

    # 10 normalized log-mel subband shape features (20 dims)
    mel_10 = np.array([
        np.mean(log_mel[:, i * 2 : i * 2 + 3], axis=1) for i in range(10)
    ]).T
    mel_10_norm = mel_10 - np.mean(mel_10, axis=1, keepdims=True)
    mel_mean = np.mean(mel_10_norm, axis=0)
    mel_std = np.std(mel_10_norm, axis=0)

    # Spectral centroid, bandwidth, rolloff, flatness, zero-crossing rate (10 dims)
    freqs = np.fft.rfftfreq(n_fft, d=1.0 / sr)
    mag = np.sqrt(power) + 1e-10
    mag_sum = np.sum(mag, axis=1)
    centroid = np.sum(mag * freqs[None, :], axis=1) / mag_sum / 4000.0
    bw = np.sqrt(np.sum(mag * ((freqs[None, :] / 4000.0 - centroid[:, None]) ** 2), axis=1) / mag_sum)
    cum = np.cumsum(power, axis=1)
    rolloff_bins = np.argmax(cum >= 0.85 * cum[:, -1:], axis=1)
    rolloff = freqs[rolloff_bins] / 4000.0
    flatness = np.exp(np.mean(np.log(power + 1e-12), axis=1)) / (np.mean(power, axis=1) + 1e-12)
    signs = (active_frames[:, 1:] * active_frames[:, :-1]) < 0
    zcr = np.mean(signs, axis=1)
    spec_feats = np.stack([centroid, bw, rolloff, flatness, zcr], axis=1)
    spec_mean = np.mean(spec_feats, axis=0)
    spec_std = np.std(spec_feats, axis=0)

    # Pitch F0 & delta stats (4 pitch + 10 delta = 14 dims)
    f0s: list[float] = []
    clarities: list[float] = []
    raw_active = wav[idx][active_mask]
    for fr in raw_active[:: max(1, len(raw_active) // 60)]:
        f0, cl = _estimate_f0_and_clarity(fr, sr)
        clarities.append(cl)
        if f0 > 0:
            f0s.append(f0)
    if f0s:
        log_f0 = np.log(np.array(f0s) / 160.0)
        f0_stats = np.array([
            float(np.mean(log_f0)),
            float(np.std(log_f0)),
            float(np.median(log_f0)),
            float(np.mean(clarities)),
        ])
    else:
        f0_stats = np.array([0.0, 0.0, 0.0, float(np.mean(clarities) if clarities else 0.0)])

    deltas = np.diff(mfcc_1_20[:, :10], axis=0, prepend=mfcc_1_20[:1, :10])
    delta_mean = np.mean(np.abs(deltas), axis=0)

    def _unit(v: np.ndarray, weight: float) -> np.ndarray:
        nrm = float(np.linalg.norm(v))
        return (v / nrm * weight) if nrm > 1e-10 else np.zeros_like(v)

    vec = np.concatenate([
        _unit(mfcc_mean, 0.50),
        _unit(mfcc_std, 0.12),
        _unit(mel_mean, 0.16),
        _unit(mel_std, 0.06),
        _unit(spec_mean, 0.06),
        _unit(spec_std, 0.03),
        f0_stats * 0.25,
        _unit(delta_mean, 0.05),
    ])
    total_norm = float(np.linalg.norm(vec))
    if total_norm <= 1e-10 or vec.size != 84:
        raise ValueError("Failed to normalize voice embedding.")
    return (vec / total_norm).astype(np.float64)


def cosine_similarity(a: np.ndarray | list[float], b: np.ndarray | list[float]) -> float:
    """Return cosine similarity in [-1.0, 1.0]."""
    va = np.asarray(a, dtype=np.float64).flatten()
    vb = np.asarray(b, dtype=np.float64).flatten()
    na = float(np.linalg.norm(va))
    nb = float(np.linalg.norm(vb))
    if na <= 1e-10 or nb <= 1e-10 or va.shape != vb.shape:
        return 0.0
    return float(np.clip(np.dot(va, vb) / (na * nb), -1.0, 1.0))


def _load_audio_input(params: dict) -> tuple[np.ndarray, int]:
    samples = params.get("samples")
    if samples is not None:
        sr = int(params.get("sample_rate") or DEFAULT_SAMPLE_RATE)
        return np.asarray(samples, dtype=np.float64), sr

    audio_path = str(params.get("audio_path") or "").strip()
    if audio_path:
        p = Path(audio_path)
        if not p.is_file():
            raise FileNotFoundError(f"Audio file not found: {p}")
        try:
            with wave.open(str(p), "rb") as wf:
                sr = wf.getframerate()
                sw = wf.getsampwidth()
                ch = wf.getnchannels()
                raw = wf.readframes(wf.getnframes())
            if sw == 2:
                arr = np.frombuffer(raw, dtype=np.int16).astype(np.float64) / 32768.0
            elif sw == 4:
                arr = np.frombuffer(raw, dtype=np.int32).astype(np.float64) / 2147483648.0
            elif sw == 1:
                arr = (np.frombuffer(raw, dtype=np.uint8).astype(np.float64) - 128.0) / 128.0
            else:
                raise ValueError(f"Unsupported WAV sample width: {sw}")
            if ch > 1:
                arr = arr.reshape(-1, ch).mean(axis=1)
            return arr, sr
        except wave.Error:
            from scipy.io import wavfile
            sr, data = wavfile.read(str(p))
            arr = np.asarray(data, dtype=np.float64)
            if np.issubdtype(data.dtype, np.integer):
                arr = arr / float(np.iinfo(data.dtype).max)
            return arr, int(sr)

    if params.get("use_mic"):
        seconds = max(1.0, min(10.0, float(params.get("seconds") or 3.0)))
        sr = int(params.get("sample_rate") or DEFAULT_SAMPLE_RATE)
        import sounddevice as sd
        rec = sd.rec(int(round(seconds * sr)), samplerate=sr, channels=1, dtype="float32")
        sd.wait()
        return rec.flatten().astype(np.float64), sr

    raise ValueError("Provide audio_path, samples, or use_mic=True.")


def enroll(
    params: dict | None = None,
    *,
    store_path: Path | str | None = None,
) -> str:
    """Enroll or update the primary speaker voiceprint (starts locked)."""
    p = params or {}
    threshold = max(0.5, min(0.99, float(p.get("threshold") or DEFAULT_THRESHOLD)))
    ttl = max(30, min(3600, int(p.get("ttl_seconds") or DEFAULT_TTL_SECONDS)))

    with _LOCK:
        if is_enrolled(store_path) and is_locked(store_path):
            return (
                "[VOICE_LOCKED] A voiceprint is already enrolled and locked. "
                "Run verify before re-enrolling or clear after verification."
            )

    samples, sr = _load_audio_input(p)
    emb = extract_voice_embedding(samples, sample_rate=sr)

    with _LOCK:
        prev = _load_state(store_path)
        prev_vec = prev.get("embedding")
        count = int(prev.get("sample_count") or 0)
        if isinstance(prev_vec, list) and len(prev_vec) == 84 and count > 0 and p.get("append"):
            merged = (np.asarray(prev_vec, dtype=np.float64) * count + emb) / float(count + 1)
            nrm = float(np.linalg.norm(merged))
            final_vec = (merged / nrm) if nrm > 1e-10 else emb
            count += 1
        else:
            final_vec = emb
            count = 1

        state = {
            "embedding": [round(float(x), 6) for x in final_vec.tolist()],
            "dim": 84,
            "sample_count": count,
            "threshold": threshold,
            "ttl_seconds": ttl,
            "enrolled_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "unlocked_until": 0.0,
        }
        _save_state(state, store_path)
    return (
        f"Biometric voiceprint enrolled (84-dim spectral centroid, samples={count}, "
        f"threshold={threshold:.2f}, ttl={ttl}s). State is LOCKED until verified."
    )


def verify(
    params: dict | None = None,
    *,
    store_path: Path | str | None = None,
) -> str:
    """Verify speaker audio against enrolled centroid and unlock TTL on match."""
    p = params or {}
    with _LOCK:
        state = _load_state(store_path)
        ref = state.get("embedding")
        if not (isinstance(ref, list) and len(ref) == 84):
            return "No biometric voiceprint enrolled yet. Run voice_lock(action='enroll') first."

    samples, sr = _load_audio_input(p)
    probe = extract_voice_embedding(samples, sample_rate=sr)
    threshold = max(0.5, min(0.99, float(p.get("threshold") or state.get("threshold") or DEFAULT_THRESHOLD)))
    ttl = max(30, min(3600, int(p.get("ttl_seconds") or state.get("ttl_seconds") or DEFAULT_TTL_SECONDS)))
    score = cosine_similarity(ref, probe)

    with _LOCK:
        state = _load_state(store_path)
        if score >= threshold:
            state["unlocked_until"] = time.time() + ttl
            state["last_score"] = round(score, 4)
            _save_state(state, store_path)
            return f"VOICE_UNLOCKED: Speaker verified (similarity={score:.3f} >= {threshold:.2f}, ttl={ttl}s)."
        state["unlocked_until"] = 0.0
        state["last_score"] = round(score, 4)
        _save_state(state, store_path)
        return f"VOICE_REJECTED: Speaker mismatch (similarity={score:.3f} < {threshold:.2f}). Lock remains active."


def lock(store_path: Path | str | None = None) -> str:
    """Immediately re-lock the biometric gate."""
    with _LOCK:
        state = _load_state(store_path)
        if not is_enrolled(store_path):
            return "Voice lock is not enrolled."
        state["unlocked_until"] = 0.0
        _save_state(state, store_path)
    return "Biometric voice lock engaged (LOCKED)."


def _clear_unlocked(store_path: Path | str | None = None) -> str:
    path = _store_file(store_path)
    with _LOCK:
        if path.is_file():
            path.unlink()
    return "Biometric voiceprint cleared."


def status(store_path: Path | str | None = None) -> str:
    """Return current voice lock enrollment and lock status."""
    with _LOCK:
        state = _load_state(store_path)
        if not (isinstance(state.get("embedding"), list) and len(state["embedding"]) == 84):
            return "Voice Lock: not enrolled (0 voiceprints stored)."
        until = float(state.get("unlocked_until", 0.0) or 0.0)
        rem = int(max(0.0, until - time.time()))
        lock_state = f"UNLOCKED ({rem}s remaining)" if rem > 0 else "LOCKED"
        thr = float(state.get("threshold", DEFAULT_THRESHOLD))
        samples = int(state.get("sample_count", 1))
        return f"Voice Lock: {lock_state} | samples={samples} | threshold={thr:.2f} | dim=84"


def run(parameters: dict, player=None, session_memory=None) -> str:
    """Plugin entrypoint for biometric voice lock."""
    params = parameters if isinstance(parameters, dict) else {}
    action = str(params.get("action") or "status").strip().lower()
    store_path = params.get("store_path")

    if action == "status":
        return status(store_path)
    if action == "enroll":
        return enroll(params, store_path=store_path)
    if action == "verify":
        return verify(params, store_path=store_path)
    if action == "lock":
        return lock(store_path=store_path)
    if action in ("clear", "delete", "reset"):
        from core.evolution import safety_guard
        return safety_guard.enforce_gate(
            "voice_lock",
            "clear",
            "Clear Biometric Voiceprint",
            "Permanently delete the enrolled speaker voiceprint from memory.",
            lambda: _clear_unlocked(store_path=store_path),
            key="voice_lock_clear",
        )
    return f"Unknown voice_lock action '{action}'. Use: status, enroll, verify, lock, clear."
