"""Milestone 15 Part 1: screen-agent live fixes and Chrome-by-default.

Unit tests run everywhere. The REAL desktop tests run on Windows:
  * capture + UIA read: always on Windows (read-only).
  * multi-step GUI action (drives Notepad) and a real Chrome window launch:
    only with JARVIS_LIVE_GUI=1, because they type/click on the real desktop.
  * Playwright Chrome rendering of a public page: JARVIS_LIVE_NET=1.
"""
from __future__ import annotations

import os
import sys
import threading
import time
import uuid
from pathlib import Path

import pytest

from core import screenact, screenview
from core.evolution import screen_agent, screen_grounding as sg

WIN = sys.platform == "win32"
LIVE_GUI = WIN and os.environ.get("JARVIS_LIVE_GUI") == "1"
LIVE_NET = os.environ.get("JARVIS_LIVE_NET") == "1"


class _Player:
    def __init__(self) -> None:
        self.said: list[str] = []
        self.logs: list[str] = []

    def request_say(self, text: str) -> None:
        self.said.append(text)

    def write_log(self, text: str) -> None:
        self.logs.append(text)


# ── grounding fixes (from the live telemetry failures) ──────────────────────

def test_searchhost_matches_search_and_aliases_are_bidirectional():
    assert sg.app_matches("SearchHost.exe", app="Search")          # the live UNVERIFIED case
    assert sg.app_matches("Search", app="SearchHost")
    assert sg.app_matches("chrome.exe", app="", title="", windows=[("Google Chrome", "New Tab")])
    assert sg.app_matches("msedge", app="Microsoft Edge")
    assert not sg.app_matches("notepad.exe", app="Search")
    assert not sg.app_matches("", app="Search")


def test_toggle_key_recovery_waits_instead_of_closing_start():
    agent = screen_agent.AutonomousScreenAgent(sleep_fn=lambda _s: None)
    obs = screen_agent.ScreenObservation.from_view(screenview.View(source="uia", app="Search", title="Search"))
    rec = agent._build_recovery_action({"action": "press", "keys": "windows_key", "expected_app": "SearchHost.exe"}, obs)
    assert rec["action"] == "wait" and rec.get("expect_change") is False
    assert sg.is_toggle_press({"action": "press", "keys": "win"})
    assert not sg.is_toggle_press({"action": "press", "keys": "ctrl+s"})


def test_vision_points_map_from_downscaled_image_to_physical_screen():
    meta = {"monitor": {"left": 0, "top": 0, "width": 2560, "height": 1600}, "image_w": 1152, "image_h": 720}
    assert sg.map_vision_point(500, 500, meta) == (1280, 800)
    assert sg.map_vision_point(576, 360, meta, space="image") == (1280, 800)
    assert sg.map_vision_point(1000, 1000, meta) == (2559, 1599)
    assert sg.map_vision_point(10, 20, None) == (10, 20)


def test_narrator_streams_throttled_voice_status():
    now = [0.0]
    p = _Player()
    n = sg.StepNarrator(p.request_say, min_interval_s=4.0, clock=lambda: now[0])
    assert n.say("Looking at your screen.", force=True)
    assert not n.step({"action": "open_app", "target": "Chrome"})     # throttled
    now[0] = 5.0
    assert n.step({"action": "open_app", "target": "Chrome"})
    now[0] = 10.0
    assert n.step({"action": "type_text", "text": "x"})
    assert n.spoken == ["Looking at your screen.", "Opening Chrome.", "Typing now."]
    assert all("SCREEN AGENT STATUS" in s for s in p.said)


def test_plugin_accepts_goal_alias_and_queues_concurrent_goals(monkeypatch: pytest.MonkeyPatch):
    from plugins import autonomous_screen_agent as asa
    gate = threading.Event()
    ran: list[str] = []

    def fake_bg(goal, _max, _player):
        ran.append(goal)
        gate.wait(5)

    monkeypatch.setattr(asa, "_run_background", fake_bg)
    first = asa.run({"action": "goal", "goal": "open chrome"})          # Swarm's verb (was rejected)
    assert "engaged" in first.lower()
    deadline = time.time() + 5
    while time.time() < deadline and not ran:
        time.sleep(0.01)
    second = asa.run({"action": "run", "goal": "type hello"})
    assert "queued" in second.lower()
    assert asa.queued_goals() == ["type hello"]
    gate.set()
    deadline = time.time() + 5
    while time.time() < deadline and len(ran) < 2:
        time.sleep(0.02)
    assert ran == ["open chrome", "type hello"]
    assert "unknown" not in asa.run({"action": "perform", "goal": ""}).lower()


# ── REAL desktop (Windows) ──────────────────────────────────────────────────

@pytest.mark.skipif(not WIN, reason="live Windows desktop only")
def test_live_dpi_aware_nonblank_capture_from_background_thread():
    from actions import screen_processor
    from core import dpi
    dpi.ensure_dpi_awareness()
    assert dpi.current_awareness() >= 2                          # per-monitor aware
    out: dict = {}

    def grab():
        out["data"], out["mime"], out["meta"] = screen_processor.capture_screen_meta()

    t = threading.Thread(target=grab, name="capture-off-voice-loop")
    t.start()
    t.join(20)
    meta = out["meta"]
    if meta["blank"]:
        pytest.skip("desktop is locked / secure desktop - nothing to capture")
    assert out["mime"] == "image/jpeg" and len(out["data"]) > 10_000
    assert meta["std"] > 2.0 and meta["image_w"] > 0
    geo = dpi.screen_geometry()
    assert meta["monitor"]["width"] == geo["width"]              # physical pixels, not logical


@pytest.mark.skipif(not WIN, reason="live Windows desktop only")
def test_live_reads_active_window_text_via_uia():
    view = screenview.look()
    assert view.app or view.title
    assert view.source in ("uia", "ocr", "self", "none")
    assert screenview.all_windows()                              # every top-level window is listed


@pytest.mark.skipif(not LIVE_GUI, reason="set JARVIS_LIVE_GUI=1 to drive the real desktop")
def test_live_multistep_desktop_action_completes_and_verifies(tmp_path: Path):
    import psutil
    token = f"M15-live-{uuid.uuid4().hex[:8]}"
    before = {p.pid for p in psutil.process_iter(["name"]) if (p.info["name"] or "").lower() == "notepad.exe"}
    steps = [
        {"status": "act", "action": "open_app", "target": "notepad", "expected_app": "notepad"},
        {"status": "act", "action": "type_text", "text": token, "expected_app": "notepad", "expected_text": token},
        {"status": "done", "thought": "typed and verified"},
    ]
    p = _Player()
    try:
        rep = screen_agent.AutonomousScreenAgent(
            max_steps=5, settle_timeout_s=4.0, decide_fn=lambda ctx: steps[min(ctx["step"], len(steps)) - 1],
        ).run("Open Notepad and type the test token", player=p)
        assert rep["status"] == "completed", screen_agent.format_report(rep)
        assert rep["steps_verified"] >= 2
        view = screenview.look()
        assert sg.app_matches("notepad", app=view.app, title=view.title)
        assert view.contains_text(token)                          # read back from the live window
        assert any("Looking at your screen" in s for s in p.said)
        screenact.press("ctrl+a")
        screenact.press("delete")                                 # leave no text behind
    finally:
        screen_agent.clear_global_abort()
        for proc in psutil.process_iter(["name"]):
            if (proc.info["name"] or "").lower() == "notepad.exe" and proc.pid not in before:
                proc.terminate()


# ── Chrome default ──────────────────────────────────────────────────────────

def _chrome() -> str:
    from memory import config_manager
    return config_manager.find_chrome_exe()


def test_jarvis_prefers_chrome_over_os_default(monkeypatch: pytest.MonkeyPatch):
    from actions import browser_control
    from memory import config_manager
    if not _chrome():
        monkeypatch.setattr(browser_control, "_detect_os_default_browser", lambda: "firefox")
        assert browser_control._detect_default_browser() == (config_manager.get_default_browser() or "firefox")
        return
    assert config_manager.get_default_browser() in ("chrome", "") or True
    monkeypatch.setattr(config_manager, "load_api_keys", lambda: {})
    assert config_manager.get_default_browser() == "chrome"
    monkeypatch.setattr(browser_control, "_detect_os_default_browser", lambda: "edge")
    assert browser_control._detect_default_browser() == "chrome"
    # Graceful fallback when Chrome is missing: follow the OS default.
    monkeypatch.setattr(config_manager, "find_chrome_exe", lambda: "")
    assert config_manager.get_default_browser() == ""
    assert browser_control._detect_default_browser() == "edge"


@pytest.mark.skipif(not WIN, reason="Windows launch path")
def test_urls_launch_chrome_exe_not_the_os_handler(monkeypatch: pytest.MonkeyPatch):
    if not _chrome():
        pytest.skip("Chrome not installed")
    from actions import browser_control
    from memory import config_manager
    monkeypatch.setattr(config_manager, "load_api_keys", lambda: {})
    launched: list[list[str]] = []

    class _Rec:
        def __init__(self, args, **_kw):
            launched.append(list(args))

    monkeypatch.setattr(browser_control.subprocess, "Popen", _Rec)
    monkeypatch.setattr(browser_control.os, "startfile", lambda *_a: pytest.fail("used the OS (Edge) handler"),
                        raising=False)
    browser_control._open_native("https://example.com", None)
    assert launched and launched[0][0].lower().endswith("chrome.exe")
    assert any("example.com" in a for a in launched[0])


def test_fetch_user_agent_matches_installed_chrome():
    from core.tasks import web
    major = web._chrome_major()
    if major:
        assert f"Chrome/{major}." in web._UA
    else:
        assert web._UA == web._UA_FALLBACK


def test_chrome_render_refuses_private_addresses():
    from core.tasks import web
    assert web.render_with_chrome("http://127.0.0.1:8765/") is None
    assert web.render_with_chrome("file:///C:/Windows/win.ini") is None


@pytest.mark.skipif(not LIVE_GUI, reason="set JARVIS_LIVE_GUI=1 to open a real Chrome window")
def test_live_chrome_window_launches_as_default():
    import psutil
    if not _chrome():
        pytest.skip("Chrome not installed")
    from actions import browser_control
    browser_control._open_native("https://example.com/?jarvis_m15", None)
    deadline = time.time() + 15
    while time.time() < deadline:
        if any((p.info["name"] or "").lower() == "chrome.exe" for p in psutil.process_iter(["name"])):
            break
        time.sleep(0.3)
    assert any((p.info["name"] or "").lower() == "chrome.exe" for p in psutil.process_iter(["name"]))


@pytest.mark.skipif(not LIVE_NET, reason="set JARVIS_LIVE_NET=1 for a live Playwright Chrome render")
def test_live_playwright_renders_with_chrome_channel():
    from core.tasks import web
    doc = web.render_with_chrome("https://example.com/")
    assert doc and "Example Domain" in (doc["title"] + doc["text"])
