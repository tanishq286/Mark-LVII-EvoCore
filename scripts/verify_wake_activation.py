"""scripts/verify_wake_activation.py — Verification suite for JARVIS dual wake triggers.

Verifies:
  1. Syntax/compile check on all modified wake & UI modules
  2. openWakeWord ONNX model readiness + live WakeWordDetector inference (silence/noise)
  3. Non-blocking wake chime WAV synthesis
  4. Double-clap DSP true-positive matrix (wideband, BT HFP <=3.2kHz, noisy room, threaded)
  5. Double-clap DSP true-negative matrix (silence, single+echo, >0.9s gap, speech, thuds, chime, typing, applause)
  6. Config persistence (wake_word_enabled, clap_wake_enabled, clap_sensitivity)
  7. Core TOOL_DECLARATIONS AST parse + sleep_jarvis + zero tool name collisions
"""
from __future__ import annotations

import ast
import io
import os
from pathlib import Path
import py_compile
import sys
import threading
import time
import wave

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

SAMPLE_RATE = 16000
CHUNK_SIZE = 1024


def _make_clap(rng: np.random.Generator, amp: float = 18000.0, decay_s: float = 0.012,
               dur_s: float = 0.070) -> np.ndarray:
    n = int(SAMPLE_RATE * dur_s)
    t = np.arange(n, dtype=np.float32) / SAMPLE_RATE
    env = np.exp(-t / decay_s)
    env[: min(8, n)] = np.linspace(0.2, 1.0, min(8, n), dtype=np.float32)
    burst = rng.normal(0.0, 1.0, n).astype(np.float32)
    # Shape slightly toward 700-4500 Hz like hands slapping.
    sig = amp * env * burst
    return np.clip(sig, -32767.0, 32767.0).astype(np.float32)


def _lowpass_fft(sig: np.ndarray, cutoff_hz: float = 3200.0, sr: int = SAMPLE_RATE) -> np.ndarray:
    spec = np.fft.rfft(sig.astype(np.float32))
    freqs = np.fft.rfftfreq(sig.size, 1.0 / sr)
    gain = 1.0 / np.sqrt(1.0 + (freqs / cutoff_hz) ** 8)
    return np.fft.irfft(spec * gain, n=sig.size).astype(np.float32)


def _feed_signal(analyzer, sig_f32: np.ndarray) -> int:
    pcm = np.clip(sig_f32, -32767.0, 32767.0).astype(np.int16)
    hits = 0
    for i in range(0, pcm.size, CHUNK_SIZE):
        if analyzer.process(pcm[i : i + CHUNK_SIZE]):
            hits += 1
    return hits


def _build_double_clap(rng: np.random.Generator, gap_s: float, *,
                       amp1: float = 17000.0, amp2: float = 16500.0,
                       noise_rms: float = 40.0, bt_hfp: bool = False) -> np.ndarray:
    total_s = 0.5 + gap_s + 0.6
    buf = rng.normal(0.0, noise_rms, int(SAMPLE_RATE * total_s)).astype(np.float32)
    c1 = _make_clap(rng, amp=amp1)
    c2 = _make_clap(rng, amp=amp2)
    p1 = int(0.45 * SAMPLE_RATE)
    p2 = p1 + int(gap_s * SAMPLE_RATE)
    buf[p1 : p1 + c1.size] += c1
    buf[p2 : p2 + c2.size] += c2
    if bt_hfp:
        buf = _lowpass_fft(buf, cutoff_hz=3200.0)
    return buf


def main() -> int:
    results: list[tuple[str, bool, str]] = []

    def check(name: str, ok: bool, detail: str) -> None:
        results.append((name, bool(ok), detail))
        tag = "PASS" if ok else "FAIL"
        print(f"[{tag}] {name}: {detail}")

    # 1. Syntax & compile check
    targets = [
        "core/wake_word.py",
        "core/clap_detector.py",
        "memory/config_manager.py",
        "main.py",
        "ui.py",
        "gui/window.py",
    ]
    try:
        for rel in targets:
            py_compile.compile(str(ROOT / rel), doraise=True)
        check("1. Python compile check", True, f"{len(targets)} files compiled cleanly")
    except Exception as exc:
        check("1. Python compile check", False, str(exc))

    # 2. Wake word ONNX readiness + live inference check
    try:
        import openwakeword
        from core import wake_word

        models_dir = Path(openwakeword.__file__).resolve().parent / "resources" / "models"
        onnx_files = {
            prefix: [
                (p.name, p.stat().st_size)
                for p in models_dir.glob(f"{prefix}*.onnx")
                if p.is_file() and p.stat().st_size > 1024
            ]
            for prefix in ("hey_jarvis", "melspectrogram", "embedding_model")
        }
        ready_ok = wake_word.is_installed() and wake_word.is_ready() and all(onnx_files.values())

        det_errors: list[str] = []
        det_hits: list[float] = []
        det = wake_word.WakeWordDetector(
            on_detect=lambda: det_hits.append(time.monotonic()),
            logger=lambda m: det_errors.append(m) if "error" in m.lower() else None,
        )
        started = det.start()
        if started:
            rng = np.random.default_rng(42)
            for _ in range(20):
                det.feed(np.zeros(CHUNK_SIZE, dtype=np.int16))
            for _ in range(20):
                det.feed(rng.normal(0, 60, CHUNK_SIZE).astype(np.int16))
            time.sleep(0.35)
            det.reset()
            time.sleep(0.05)
            det.stop()
        check(
            "2. openWakeWord ONNX + WakeWordDetector",
            bool(ready_ok and started and not det_errors and len(det_hits) == 0),
            f"ready={ready_ok}, started={started}, errors={len(det_errors)}, false_hits={len(det_hits)}, models={onnx_files}",
        )
    except Exception as exc:
        check("2. openWakeWord ONNX + WakeWordDetector", False, str(exc))

    # 3. Wake chime WAV synthesis check
    try:
        from core.wake_word import build_wake_chime_wav

        wav_data = build_wake_chime_wav()
        with wave.open(io.BytesIO(wav_data), "rb") as wf:
            ch = wf.getnchannels()
            sw = wf.getsampwidth()
            sr = wf.getframerate()
            nf = wf.getnframes()
            frames = np.frombuffer(wf.readframes(nf), dtype=np.int16)
        dur_ms = (nf / sr) * 1000.0
        peak = int(np.max(np.abs(frames))) if frames.size else 0
        ok_chime = ch == 1 and sw == 2 and sr == 24000 and 100.0 <= dur_ms <= 200.0 and peak > 1000
        check(
            "3. Wake chime WAV synthesis",
            ok_chime,
            f"bytes={len(wav_data)}, sr={sr}, ch={ch}, sw={sw}, dur={dur_ms:.1f}ms, peak={peak}",
        )
    except Exception as exc:
        check("3. Wake chime WAV synthesis", False, str(exc))

    # 4. Double-clap DSP True Positive Matrix
    try:
        from core.clap_detector import ClapAnalyzer, DoubleClapDetector

        rng = np.random.default_rng(2026)
        tp_cases = [
            ("wideband_300ms", _build_double_clap(rng, 0.30)),
            ("wideband_500ms", _build_double_clap(rng, 0.50)),
            ("wideband_750ms", _build_double_clap(rng, 0.75)),
            ("bt_hfp_3.2khz_400ms", _build_double_clap(rng, 0.40, amp1=15000.0, amp2=14500.0, bt_hfp=True)),
            ("noisy_room_rms300", _build_double_clap(rng, 0.42, amp1=17000.0, amp2=16500.0, noise_rms=300.0)),
        ]
        tp_failures = []
        for label, sig in tp_cases:
            az = ClapAnalyzer(sensitivity="medium")
            hits = _feed_signal(az, sig)
            if hits != 1:
                tp_failures.append(f"{label}:hits={hits}")

        # Threaded DoubleClapDetector end-to-end callback + cooldown + reset re-arm
        cb_event = threading.Event()
        cb_count = 0

        def _on_clap():
            nonlocal cb_count
            cb_count += 1
            cb_event.set()

        cd = DoubleClapDetector(on_detect=_on_clap, sensitivity="medium")
        cd_started = cd.start()
        sig1 = np.clip(_build_double_clap(rng, 0.38), -32767, 32767).astype(np.int16)
        for i in range(0, sig1.size, CHUNK_SIZE):
            cd.feed(sig1[i : i + CHUNK_SIZE])
        fired1 = cb_event.wait(timeout=1.5)
        # Immediate second double clap during cooldown should NOT fire (until reset)
        cb_event.clear()
        for i in range(0, sig1.size, CHUNK_SIZE):
            cd.feed(sig1[i : i + CHUNK_SIZE])
        time.sleep(0.25)
        count_after_cooldown = cb_count

        # After reset(), a new double clap must fire cleanly
        cd.reset()
        for i in range(0, sig1.size, CHUNK_SIZE):
            cd.feed(sig1[i : i + CHUNK_SIZE])
        fired2 = cb_event.wait(timeout=1.5)
        cd.stop()

        threaded_ok = cd_started and fired1 and count_after_cooldown == 1 and fired2 and cb_count == 2
        if not threaded_ok:
            tp_failures.append(
                f"threaded(started={cd_started},fired1={fired1},cd_cnt={count_after_cooldown},fired2={fired2},cnt={cb_count})"
            )

        check(
            "4. Double-clap true positive matrix",
            len(tp_failures) == 0,
            "all 5 DSP + threaded callbacks passed" if not tp_failures else "; ".join(tp_failures),
        )
    except Exception as exc:
        check("4. Double-clap true positive matrix", False, str(exc))

    # 5. Double-clap DSP True Negative Matrix (0% false positives)
    try:
        from core.clap_detector import ClapAnalyzer

        rng = np.random.default_rng(777)

        def _silence(sec: float = 2.0) -> np.ndarray:
            return rng.normal(0.0, 35.0, int(SAMPLE_RATE * sec)).astype(np.float32)

        # Single clap + 60 ms echo
        s_echo = _silence(1.5)
        c1 = _make_clap(rng, 17000.0)
        echo = _make_clap(rng, 7500.0)
        p0 = int(0.4 * SAMPLE_RATE)
        s_echo[p0 : p0 + c1.size] += c1
        s_echo[p0 + int(0.06 * SAMPLE_RATE) : p0 + int(0.06 * SAMPLE_RATE) + echo.size] += echo

        # Two claps 1.25 s apart (outside 0.90s max_gap_s)
        s_far = _build_double_clap(rng, 1.25)

        # Sustained speech-like harmonic vowel bursts (250 ms each, 400 ms apart)
        s_speech = _silence(2.0)
        t_v = np.arange(int(0.25 * SAMPLE_RATE), dtype=np.float32) / SAMPLE_RATE
        env_v = np.sin(np.pi * np.arange(t_v.size, dtype=np.float32) / max(1, t_v.size - 1))
        vowel = 11000.0 * env_v * (
            0.60 * np.sin(2 * np.pi * 160 * t_v)
            + 0.30 * np.sin(2 * np.pi * 500 * t_v)
            + 0.15 * np.sin(2 * np.pi * 1800 * t_v)
        )
        for pos in (int(0.4 * SAMPLE_RATE), int(0.8 * SAMPLE_RATE)):
            s_speech[pos : pos + vowel.size] += vowel

        # Low-frequency double desk thud (150-250 Hz impulsive thumps 350 ms apart)
        s_thud = _silence(1.5)
        t_th = np.arange(int(0.06 * SAMPLE_RATE), dtype=np.float32) / SAMPLE_RATE
        thud = 18000.0 * np.exp(-t_th / 0.014) * (
            0.7 * np.sin(2 * np.pi * 150 * t_th) + 0.5 * np.sin(2 * np.pi * 240 * t_th)
        )
        for pos in (int(0.4 * SAMPLE_RATE), int(0.75 * SAMPLE_RATE)):
            s_thud[pos : pos + thud.size] += thud

        # Dual-tone melodic chime bursts (440 + 880 Hz)
        s_chime = _silence(1.5)
        t_ch = np.arange(int(0.07 * SAMPLE_RATE), dtype=np.float32) / SAMPLE_RATE
        chime = 14000.0 * np.exp(-t_ch / 0.015) * (
            0.6 * np.sin(2 * np.pi * 440 * t_ch) + 0.4 * np.sin(2 * np.pi * 880 * t_ch)
        )
        for pos in (int(0.4 * SAMPLE_RATE), int(0.75 * SAMPLE_RATE)):
            s_chime[pos : pos + chime.size] += chime

        # Rapid 6-tap typing burst (6 impulses in 0.75 s, every 120 ms)
        s_typing = _silence(2.0)
        for k in range(6):
            pos = int((0.40 + k * 0.12) * SAMPLE_RATE)
            tap = _make_clap(rng, amp=9000.0, decay_s=0.008, dur_s=0.04)
            s_typing[pos : pos + tap.size] += tap

        # Continuous loud broadband noise (fan/applause)
        s_noise = rng.normal(0.0, 2600.0, int(SAMPLE_RATE * 2.0)).astype(np.float32)

        tn_cases = [
            ("silence_2s", _silence(2.0)),
            ("single_clap_plus_60ms_echo", s_echo),
            ("two_claps_1.25s_apart", s_far),
            ("speech_harmonic_vowels", s_speech),
            ("double_desk_thud_150_240hz", s_thud),
            ("melodic_chime_440_880hz", s_chime),
            ("rapid_6_tap_typing", s_typing),
            ("continuous_broadband_noise", s_noise),
        ]
        tn_failures = []
        for label, sig in tn_cases:
            az = ClapAnalyzer(sensitivity="medium")
            hits = _feed_signal(az, sig)
            if hits != 0:
                tn_failures.append(f"{label}:hits={hits}")

        check(
            "5. Double-clap true negative matrix",
            len(tn_failures) == 0,
            "0 false positives across all 8 negative classes" if not tn_failures else "; ".join(tn_failures),
        )
    except Exception as exc:
        check("5. Double-clap true negative matrix", False, str(exc))

    # 6. Config persistence check (non-secret keys only)
    try:
        from memory.config_manager import (
            get_clap_sensitivity,
            get_clap_wake_enabled,
            get_wake_word_enabled,
        )

        w_on = get_wake_word_enabled()
        c_on = get_clap_wake_enabled()
        c_sens = get_clap_sensitivity()
        check(
            "6. Wake config persistence",
            w_on is True and c_on is True and c_sens == "medium",
            f"wake_word_enabled={w_on}, clap_wake_enabled={c_on}, clap_sensitivity={c_sens}",
        )
    except Exception as exc:
        check("6. Wake config persistence", False, str(exc))

    # 7. Core TOOL_DECLARATIONS AST parse + sleep_jarvis + zero name collisions
    try:
        from core.action_loader import discover_actions
        from core.plugin_loader import discover_plugins

        tree = ast.parse((ROOT / "main.py").read_text(encoding="utf-8", errors="ignore"))
        core_names: list[str] = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign) and any(
                getattr(t, "id", "") == "TOOL_DECLARATIONS" for t in node.targets
            ):
                decls = ast.literal_eval(node.value)
                core_names = [str(d["name"]) for d in decls if isinstance(d, dict) and d.get("name")]
                break

        act_reg = discover_actions(ROOT / "actions", logger=lambda _m: None)
        act_names = [str(d["name"]) for d in act_reg.get_tool_declarations() if d.get("name")]
        reserved = set(core_names) | set(act_names)
        plug_reg = discover_plugins(ROOT / "plugins", reserved, logger=lambda _m: None)
        ui_plugins = plug_reg.list_for_ui()
        rejected = [p["name"] for p in ui_plugins if not p["valid"]]
        core_act_overlap = sorted(set(core_names) & set(act_names))
        ok_tools = (
            "sleep_jarvis" in core_names
            and "shutdown_jarvis" in core_names
            and not core_act_overlap
            and not rejected
        )
        check(
            "7. TOOL_DECLARATIONS AST & plugin collision check",
            ok_tools,
            f"core={len(core_names)} (sleep_jarvis={'sleep_jarvis' in core_names}), "
            f"actions={len(act_names)}, plugins_rejected={len(rejected)}, overlap={core_act_overlap or 'none'}",
        )
    except Exception as exc:
        check("7. TOOL_DECLARATIONS AST & plugin collision check", False, str(exc))

    failed = [r for r in results if not r[1]]
    print(f"\nSUMMARY: {len(results) - len(failed)}/{len(results)} passed")
    return 0 if not failed else 1


if __name__ == "__main__":
    raise SystemExit(main())
