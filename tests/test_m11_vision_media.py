"""Milestone 11 tests: Vision & Media (Spotify API, MediaPipe Hand Gestures, Auto-Scroll & Notepad Dictation)."""

from __future__ import annotations

import math
from pathlib import Path
import time


def _make_base_hand() -> list[tuple[float, float, float]]:
    # Curled neutral fist around palm center (wrist at y=0.80, middle_mcp at y=0.55)
    pts = [(0.50, 0.80, 0.0)] * 21
    pts[0] = (0.50, 0.80, 0.0)   # wrist
    pts[1] = (0.56, 0.72, 0.0)
    pts[2] = (0.60, 0.65, 0.0)
    pts[3] = (0.59, 0.61, 0.0)
    pts[4] = (0.54, 0.62, 0.0)   # thumb curled near palm
    for base, x in ((5, 0.56), (9, 0.50), (13, 0.44), (17, 0.38)):
        pts[base] = (x, 0.55, 0.0)
        pts[base + 1] = (x, 0.52, 0.0)
        pts[base + 2] = (x, 0.57, 0.0)
        pts[base + 3] = (x, 0.61, 0.0)  # tip below PIP (curled)
    return pts


def test_spotify_control_missing_creds_and_mocked_api(tmp_path: Path, monkeypatch):
    from core import env_loader
    from plugins import spotify_control

    monkeypatch.delenv("SPOTIPY_CLIENT_ID", raising=False)
    monkeypatch.delenv("SPOTIPY_CLIENT_SECRET", raising=False)
    monkeypatch.setattr(env_loader, "_DOTENV_PATH", tmp_path / "missing.env")
    monkeypatch.setattr(env_loader, "_API_KEYS_PATH", tmp_path / "missing.json")

    st = spotify_control.run({"action": "status"})
    assert "unconfigured" in st

    monkeypatch.setenv("SPOTIPY_CLIENT_ID", "demo_id")
    monkeypatch.setenv("SPOTIPY_CLIENT_SECRET", "demo_secret")

    calls = []

    class _FakeSP:
        def search(self, q, type="track", limit=5):
            return {"tracks": {"items": [{"name": "Time", "artists": [{"name": "Hans Zimmer"}], "uri": "spotify:track:123"}]}}
        def start_playback(self, device_id=None, **kw):
            calls.append(("play", kw))
        def pause_playback(self, device_id=None):
            calls.append(("pause",))
        def volume(self, v, device_id=None):
            calls.append(("volume", v))

    monkeypatch.setattr(spotify_control, "_build_client", lambda user_auth=True: (_FakeSP(), ""))
    assert "Hans Zimmer" in spotify_control.run({"action": "search", "query": "Time"})
    assert "Hans Zimmer" in spotify_control.run({"action": "play", "query": "Time"})
    assert "Paused" in spotify_control.run({"action": "pause"})
    assert "70%" in spotify_control.run({"action": "volume", "volume": 70})
    assert ("volume", 70) in calls


def test_mediapipe_hand_gesture_classification_and_tracker():
    from core.hand_gestures import GESTURE_ACTIONS, GestureTracker, classify_hand_landmarks
    from plugins import hand_gesture_control

    # Open palm
    palm = _make_base_hand()
    palm[4] = (0.72, 0.56, 0.0)
    for base, x in ((5, 0.56), (9, 0.50), (13, 0.44), (17, 0.38)):
        palm[base + 2] = (x, 0.45, 0.0)
        palm[base + 3] = (x, 0.32, 0.0)
    assert classify_hand_landmarks(palm)["gesture"] == "open_palm"

    # Thumbs up
    tup = _make_base_hand()
    tup[4] = (0.64, 0.36, 0.0)
    assert classify_hand_landmarks(tup)["gesture"] == "thumbs_up"

    # Thumbs down
    tdn = _make_base_hand()
    tdn[4] = (0.64, 0.85, 0.0)
    assert classify_hand_landmarks(tdn)["gesture"] == "thumbs_down"

    # Peace (index + middle)
    peace = _make_base_hand()
    peace[7], peace[8] = (0.56, 0.45, 0.0), (0.56, 0.32, 0.0)
    peace[11], peace[12] = (0.50, 0.45, 0.0), (0.50, 0.32, 0.0)
    assert classify_hand_landmarks(peace)["gesture"] == "peace"

    # Pinch (thumb tip close to index tip)
    pinch = list(palm)
    pinch[8] = (0.58, 0.42, 0.0)
    pinch[4] = (0.59, 0.43, 0.0)
    assert classify_hand_landmarks(pinch)["gesture"] == "pinch"

    # Tracker debounce + swipe detection
    tr = GestureTracker(window=3, cooldown_sec=1.0)
    assert tr.update(tup, now=1.0) is None
    assert tr.update(tup, now=1.1) is None
    assert tr.update(tup, now=1.2) == "thumbs_up"
    assert tr.update(tup, now=1.4) is None  # cooldown

    # Swipe right across 4 samples
    sw_tr = GestureTracker(window=3, cooldown_sec=1.0)
    for step, x_w in enumerate((0.20, 0.30, 0.42, 0.56)):
        h = _make_base_hand()
        h[0] = (x_w, 0.80, 0.0)
        out = sw_tr.update(h, now=10.0 + step * 0.1)
    assert out == "swipe_right"

    res = hand_gesture_control.run({"action": "classify", "landmarks": peace})
    assert "peace" in res and GESTURE_ACTIONS["peace"]["action"] in res


def test_desktop_automation_autoscroll_and_notepad_dictation():
    from plugins.desktop_automation import NotepadDictationSession, SmartScrollController, run

    scrolls = []
    sc = SmartScrollController(scroll_fn=lambda d: scrolls.append(d))
    msg = sc.start(direction="down", speed=6, max_steps=3)
    assert "Started Smart Auto-Scroll" in msg
    deadline = time.monotonic() + 2.0
    while len(scrolls) < 3 and time.monotonic() < deadline:
        time.sleep(0.03)
    stop_msg = sc.stop()
    assert "Stopped Smart Auto-Scroll" in stop_msg
    assert len(scrolls) >= 2 and all(s < 0 for s in scrolls)

    launched = []
    written = []
    dic = NotepadDictationSession(writer_fn=lambda t: written.append(t), launcher_fn=lambda: launched.append(True))
    assert "active" in dic.start()
    assert "Dictated" in dic.write("Line one report")
    assert "Ended" in dic.stop()
    assert launched == [True]
    assert written == ["Line one report\n"]
    assert "Smart Auto-Scroll" in run({"action": "status"})
