"""Milestone 11 tests: Dev Workspace, Encrypted Form Filler, YouTube Whisper Fallback, Market Finance, 2D Avatar."""

from __future__ import annotations

import json
import os
from pathlib import Path
import threading

import pytest


def test_dev_workspace_testgen_runner_and_profiles(tmp_path: Path):
    from plugins import dev_workspace

    py_src = tmp_path / "math_utils.py"
    py_src.write_text("def add_nums(a, b):\n    return a + b\n", encoding="utf-8")

    gen_py = dev_workspace.run({"action": "generate_tests", "source_path": str(py_src), "run_after": True})
    assert "pytest tests" in gen_py
    assert "Test exit=0" in gen_py
    assert (tmp_path / "test_math_utils.py").is_file()

    js_src = tmp_path / "calc.js"
    js_src.write_text("export function multiply(x, y) { return x * y; }\n", encoding="utf-8")
    gen_js = dev_workspace.run({"action": "generate_tests", "source_path": str(js_src)})
    assert "jest tests" in gen_js and (tmp_path / "calc.test.js").is_file()

    store = tmp_path / "project_profiles.json"
    dev_workspace.run({
        "action": "set_profile",
        "project_name": "mark-lvii",
        "profile": {"test_runner": "pytest", "linter": "ruff", "language": "python"},
        "store_path": str(store),
    })
    got = json.loads(dev_workspace.run({"action": "get_profile", "project_name": "mark-lvii", "store_path": str(store)}))
    assert got["mark-lvii"]["linter"] == "ruff"
    assert "settings.json" in dev_workspace.run({"action": "vscode_config", "project_dir": str(tmp_path)})


def test_smart_form_filler_aes_vault_and_dom_fill(tmp_path: Path):
    from cryptography.exceptions import InvalidTag
    from core import confirm
    from plugins import smart_form_filler

    jvault = tmp_path / "form_profile.jvault"
    res = smart_form_filler.run({
        "action": "save_profile",
        "password": "vault-pass-99",
        "profile": {"full_name": "Ada Lovelace", "email": "ada@analytical.org", "phone": "555-0100"},
        "store_path": str(jvault),
    })
    assert "Encrypted form profile saved" in res
    raw_bytes = jvault.read_bytes()
    assert b"Ada Lovelace" not in raw_bytes
    assert b"ada@analytical.org" not in raw_bytes

    with pytest.raises(InvalidTag):
        smart_form_filler.load_encrypted_profile("wrong-pass", store_path=jvault)

    form_html = """
    <form>
      <label for="n1">Full Name</label><input id="n1" name="fullName" type="text"/>
      <input id="e1" name="email" type="email" placeholder="Email Address"/>
      <input id="p1" name="phone" type="tel"/>
    </form>
    """
    mapped = json.loads(smart_form_filler.run({
        "action": "preview_mapping",
        "password": "vault-pass-99",
        "html": form_html,
        "store_path": str(jvault),
    }))
    assert [m["profile_key"] for m in mapped] == ["full_name", "email", "phone"]

    orig_cbs = (confirm._show_cb, confirm._hide_cb, confirm._log_cb)
    logs = []
    confirm.resolve(False)
    try:
        confirm.bind(lambda *a: None, lambda: None, lambda m: logs.append(m))
        pending = smart_form_filler.run({
            "action": "fill_form",
            "password": "vault-pass-99",
            "html": form_html,
            "store_path": str(jvault),
        })
        assert "[CONFIRMATION_PENDING]" in pending
        confirm.resolve(True)
        import time as _time
        deadline = _time.monotonic() + 2.0
        while _time.monotonic() < deadline and not any("matched 3/3 fields" in m for m in logs):
            _time.sleep(0.03)
        assert any("matched 3/3 fields" in m for m in logs)
    finally:
        confirm.resolve(False)
        confirm.bind(*orig_cbs)


def test_youtube_whisper_audio_fallback(monkeypatch):
    from actions import youtube_video as yt

    monkeypatch.setattr(yt, "_get_transcript", lambda vid: None)
    monkeypatch.setattr(yt, "_transcribe_with_whisper_fallback", lambda url: "Whisper decoded speech about neural compilers.")
    monkeypatch.setattr(yt, "_summarize_with_gemini", lambda tr, url: f"Summary from: {tr}")

    out = yt._handle_summarize({"url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ"}, player=None, speak=None)
    assert "neural compilers" in out


def test_market_finance_quotes_and_aliases(monkeypatch):
    from plugins import market_finance

    assert market_finance.resolve_symbol("nifty") == "^NSEI"
    assert market_finance.resolve_symbol("btc") == "BTC-USD"
    assert market_finance.resolve_symbol("usdinr") == "USDINR=X"

    class _FakeResp:
        def __init__(self, sym):
            self.sym = sym
        def raise_for_status(self):
            pass
        def json(self):
            return {
                "chart": {
                    "result": [{
                        "meta": {"regularMarketPrice": 250.5, "chartPreviousClose": 245.0, "currency": "USD"}
                    }]
                }
            }

    class _FakeClient:
        def __init__(self, *a, **kw):
            pass
        def __enter__(self):
            return self
        def __exit__(self, *a):
            pass
        def get(self, url, **kw):
            return _FakeResp(url)

    monkeypatch.setattr("httpx.Client", _FakeClient)
    q = market_finance.run({"action": "crypto", "symbol": "eth"})
    assert "ETH-USD: USD 250.50 (+2.24%)" in q
    wl = market_finance.run({"action": "watchlist", "symbols": ["NVDA", "usdinr"]})
    assert "USDINR=X" in wl


def test_avatar_overlay_and_eight_sentiment_gestures():
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PyQt6.QtWidgets import QApplication
    from core.avatar import HoloAvatar
    from core.avatar_gestures import GESTURES, sentiment_to_gesture
    from gui.avatar_overlay import AvatarOverlayWindow
    from plugins import avatar_control

    assert len(GESTURES) == 8
    assert sentiment_to_gesture("urgent warning") == "alert"
    assert sentiment_to_gesture("hello friend") == "greeting"
    assert sentiment_to_gesture("hmm analyzing question") == "think"
    assert sentiment_to_gesture("all tests passed 100% awesome") == "celebrate"
    assert sentiment_to_gesture("sorry to hear that") == "empathy"
    assert sentiment_to_gesture("focus and concentrate") == "focus"
    assert sentiment_to_gesture("sleep calm idle") == "rest"
    assert sentiment_to_gesture("positive confirmed") == "approve"

    app = QApplication.instance() or QApplication([])
    av = HoloAvatar()
    assert av.trigger_gesture("celebrate") == "celebrate"
    av.step(0.03, 0.5, speaking=True, state="SPEAKING")

    ov = AvatarOverlayWindow()
    assert ov.trigger_gesture("alert", "Core temperature rising") == "alert"
    ov._tick()
    pix = ov.grab()
    assert pix.width() > 100 and pix.height() > 100
    ov.close()

    res = avatar_control.run({"action": "gesture", "gesture": "curious", "text": "Analyzing trajectory"})
    assert "think" in res
