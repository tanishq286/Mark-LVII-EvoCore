"""Milestone 11 tests: Security & System (Voice Lock, Cron SQLite Routines, Network Info, System Cleaner)."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import threading

import numpy as np
import pytest


def _synth_voice(f0: float, formants: tuple[float, float], *, duration: float = 1.4, sr: int = 16000, noise: float = 0.0, seed: int = 7) -> np.ndarray:
    t = np.linspace(0.0, duration, int(sr * duration), endpoint=False)
    sig = np.zeros_like(t)
    for k in range(1, 14):
        freq = f0 * k
        if freq >= sr / 2 - 100:
            break
        w = 0.0
        for f_center in formants:
            w += np.exp(-((freq - f_center) / 320.0) ** 2)
        sig += (0.2 + w) * np.sin(2.0 * np.pi * freq * t + 0.13 * k)
    if noise > 0:
        rng = np.random.default_rng(seed)
        sig = sig + rng.normal(0.0, noise * np.std(sig), size=sig.shape)
    return sig


def test_voice_lock_enrollment_verification_and_safety_gate(tmp_path: Path, monkeypatch):
    from core import confirm
    from core.evolution import safety_guard
    from plugins import voice_lock

    store = tmp_path / "voiceprints.json"
    monkeypatch.setattr(voice_lock, "DEFAULT_STORE_PATH", store)

    sp_a1 = _synth_voice(135.0, (700.0, 1250.0), seed=1)
    sp_a2 = _synth_voice(136.0, (705.0, 1255.0), noise=0.05, seed=2)
    sp_b = _synth_voice(255.0, (420.0, 2150.0), seed=3)

    emb_a1 = voice_lock.extract_voice_embedding(sp_a1, 16000)
    emb_a2 = voice_lock.extract_voice_embedding(sp_a2, 16000)
    emb_b = voice_lock.extract_voice_embedding(sp_b, 16000)
    assert emb_a1.shape == (84,)
    assert abs(float(np.linalg.norm(emb_a1)) - 1.0) < 1e-5
    assert voice_lock.cosine_similarity(emb_a1, emb_a2) > voice_lock.cosine_similarity(emb_a1, emb_b)

    res_enr = voice_lock.run({"action": "enroll", "samples": sp_a1.tolist(), "sample_rate": 16000})
    assert "84-dim" in res_enr
    assert voice_lock.is_enrolled()
    assert voice_lock.is_locked()

    orig_cbs = (confirm._show_cb, confirm._hide_cb, confirm._log_cb)
    confirm.resolve(False)
    try:
        # Locked tier must block file_vault decrypt & computer_settings shutdown until speaker verifies
        blocked = safety_guard.enforce_gate("file_vault", "decrypt", "Unlock", "Detail", lambda: "OK")
        assert "[VOICE_LOCKED]" in blocked
        blocked_pw = safety_guard.enforce_gate("computer_settings", "shutdown", "Shutdown", "Detail", lambda: "OK")
        assert "[VOICE_LOCKED]" in blocked_pw

        # Wrong speaker rejected & remains locked
        bad = voice_lock.run({"action": "verify", "samples": sp_b.tolist(), "sample_rate": 16000})
        assert "VOICE_REJECTED" in bad
        assert voice_lock.is_locked()

        # Enrolled speaker unlocks TTL and clears voice-lock pre-gate (HUD confirm still applies)
        good = voice_lock.run({"action": "verify", "samples": sp_a2.tolist(), "sample_rate": 16000, "threshold": 0.78})
        assert "VOICE_UNLOCKED" in good
        assert not voice_lock.is_locked()

        seen_titles = []
        logs = []
        ran = threading.Event()
        confirm.bind(lambda t, d: seen_titles.append(t), lambda: None, lambda m: logs.append(m))
        allowed = safety_guard.enforce_gate(
            "file_vault", "decrypt", "Unlock Vault", "Detail",
            lambda: (ran.set(), "DECRYPTED")[1],
        )
        assert "[CONFIRMATION_PENDING]" in allowed
        assert any("[LOCKED]" in s for s in seen_titles)
        confirm.resolve(True)
        assert ran.wait(timeout=1.5)

        # Re-lock engages barrier again
        voice_lock.run({"action": "lock"})
        assert voice_lock.is_locked()
    finally:
        confirm.resolve(False)
        confirm.bind(*orig_cbs)


def test_cron_sqlite_routine_persistence_and_history(tmp_path: Path):
    from datetime import timezone as dt_tz
    from core.routines import engine as eng_mod
    from core.routines import rules

    rule = rules.rule_from_args({"cron": "30 9 * * 1-5"}, timezone.utc)
    assert rule == {"type": "cron", "expr": "30 9 * * 1-5"}
    assert "cron" in rules.describe_rule(rule)

    fri_late = datetime(2026, 11, 20, 10, 0, tzinfo=timezone.utc)  # Friday after 09:30
    nxt = rules.next_fire(rule, fri_late)
    assert nxt is not None
    assert nxt.weekday() == 0  # Monday
    assert (nxt.hour, nxt.minute) == (9, 30)

    with pytest.raises(ValueError):
        rules.rule_from_args({"cron": "bad cron expr"}, timezone.utc)

    class _StubHost:
        def __init__(self):
            self.said = []
        def say(self, text):
            self.said.append(text)
        def ready(self):
            return True

    json_path = tmp_path / "routines.json"
    host = _StubHost()
    e1 = eng_mod.RoutineEngine(json_path, host, autostart=False, tz_fn=lambda: timezone.utc)
    added = e1.add("Weekday Standup", "say", text="Standup in 5 minutes", trigger={"type": "time", "rule": rule})
    assert e1.db_path.is_file()

    out = e1.run_now(added["id"])
    assert out == "ran"
    assert host.said == ["Standup in 5 minutes"]
    hist = e1.history(added["id"])
    assert len(hist) == 1 and hist[0]["result"] == "ran"

    # Remove JSON cache to verify SQLite alone restores full routine + run count
    json_path.unlink()
    e2 = eng_mod.RoutineEngine(json_path, host, autostart=False, tz_fn=lambda: timezone.utc)
    loaded = e2.get(added["id"])
    assert loaded is not None
    assert loaded["runs"] == 1
    assert loaded["trigger"]["rule"]["expr"] == "30 9 * * 1-5"


def test_network_info_and_speed_test(monkeypatch):
    from actions import computer_settings as cs

    class _FakeResp:
        def __init__(self, status=200, j=None, content=b"x" * 200000):
            self.status_code = status
            self._j = j or {}
            self.content = content
        def json(self):
            return self._j

    class _FakeClient:
        def __init__(self, *a, **kw):
            pass
        def __enter__(self):
            return self
        def __exit__(self, *a):
            pass
        def get(self, url, **kw):
            if "ipinfo.io" in url:
                return _FakeResp(200, {"ip": "203.0.113.42", "city": "Mumbai", "region": "MH", "country": "IN", "org": "AS0 Demo"})
            return _FakeResp(200, content=b"1" * 500000)
        def post(self, url, **kw):
            return _FakeResp(200)

    monkeypatch.setattr("httpx.Client", _FakeClient)
    info = cs.computer_settings({"action": "network_info"})
    assert "status=done" in info
    assert "203.0.113.42" in info

    spd = cs.computer_settings({"action": "speed_test"})
    assert "status=done" in spd
    assert "Download:" in spd and "Mbps" in spd


def test_system_cleaner_scan_and_confirm_clean(tmp_path: Path):
    import time as _time
    from core import confirm
    from plugins import system_cleaner

    tdir = tmp_path / "temp_cache"
    sub = tdir / "empty_sub"
    sub.mkdir(parents=True)
    f1 = tdir / "old_a.tmp"
    f2 = sub / "old_b.cache"
    f1.write_bytes(b"A" * 2048)
    f2.write_bytes(b"B" * 4096)

    scan = system_cleaner.run({"action": "scan", "targets": [str(tdir)]})
    assert "2 files" in scan
    assert "6.0 KB" in scan
    assert f1.is_file() and f2.is_file()

    orig_cbs = (confirm._show_cb, confirm._hide_cb, confirm._log_cb)
    logs = []
    confirm.resolve(False)
    try:
        confirm.bind(lambda *a: None, lambda: None, lambda m: logs.append(m))
        pending = system_cleaner.run({"action": "clean", "targets": [str(tdir)], "include_recycle_bin": False})
        assert "[CONFIRMATION_PENDING]" in pending
        confirm.resolve(True)
        deadline = _time.monotonic() + 2.0
        while _time.monotonic() < deadline and (f1.exists() or f2.exists()):
            _time.sleep(0.03)
        assert not f1.exists() and not f2.exists() and not sub.exists()
        assert any("Reclaimed: 6.0 KB across 2 files" in m for m in logs)
    finally:
        confirm.resolve(False)
        confirm.bind(*orig_cbs)
