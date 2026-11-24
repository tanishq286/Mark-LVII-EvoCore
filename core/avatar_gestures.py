"""core/avatar_gestures.py — 8 sentiment-triggered avatar gestures and thread-safe gesture bus."""

from __future__ import annotations

import threading
import time

GESTURES: dict[str, dict] = {
    "greeting": {
        "sentiment": "friendly",
        "duration": 2.2,
        "yaw": 0.14,
        "pitch": -0.06,
        "brow": 0.45,
        "lids": 1.05,
        "glow": 0.30,
        "gaze": (0.0, 0.0),
        "description": "Warm welcome nod and lifted brow greeting",
    },
    "approve": {
        "sentiment": "positive",
        "duration": 1.8,
        "yaw": 0.0,
        "pitch": 0.14,
        "brow": 0.35,
        "lids": 1.00,
        "glow": 0.25,
        "gaze": (0.0, 0.05),
        "description": "Affirmatory nod with calm positive gaze",
    },
    "think": {
        "sentiment": "curious",
        "duration": 2.6,
        "yaw": -0.22,
        "pitch": -0.09,
        "brow": -0.22,
        "lids": 0.90,
        "glow": 0.15,
        "gaze": (0.55, 0.35),
        "description": "Analytical upward-right glance and furrowed brow",
    },
    "alert": {
        "sentiment": "urgent",
        "duration": 2.4,
        "yaw": 0.0,
        "pitch": -0.04,
        "brow": 0.70,
        "lids": 1.15,
        "glow": 0.65,
        "gaze": (0.0, 0.0),
        "description": "High-alert posture with widened eyes and bright core glow",
    },
    "empathy": {
        "sentiment": "empathetic",
        "duration": 2.5,
        "yaw": 0.16,
        "pitch": 0.06,
        "brow": 0.20,
        "lids": 0.85,
        "glow": 0.20,
        "gaze": (0.0, 0.0),
        "description": "Gentle head tilt and softened empathetic expression",
    },
    "celebrate": {
        "sentiment": "excited",
        "duration": 2.5,
        "yaw": 0.20,
        "pitch": -0.10,
        "brow": 0.80,
        "lids": 1.12,
        "glow": 0.70,
        "gaze": (0.0, -0.05),
        "description": "Uplifted celebratory expression with energetic sway",
    },
    "focus": {
        "sentiment": "focused",
        "duration": 2.4,
        "yaw": 0.0,
        "pitch": 0.03,
        "brow": -0.32,
        "lids": 0.82,
        "glow": 0.35,
        "gaze": (0.0, 0.0),
        "description": "Steadfast locked gaze and laser concentration",
    },
    "rest": {
        "sentiment": "calm",
        "duration": 3.0,
        "yaw": 0.0,
        "pitch": 0.05,
        "brow": -0.05,
        "lids": 0.42,
        "glow": 0.0,
        "gaze": (0.0, -0.15),
        "description": "Relaxed standby breathing with lowered lids",
    },
}

_SENTIMENT_KEYWORDS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("alert", ("urgent", "alert", "warning", "error", "critical", "danger", "failed", "threat")),
    ("celebrate", ("excited", "celebrate", "congrats", "awesome", "victory", "passed", "100%", "win")),
    ("empathy", ("empathetic", "empathy", "sorry", "sad", "concerned", "care", "gentle", "support")),
    ("greeting", ("friendly", "hello", "hi", "welcome", "greeting", "morning", "hey")),
    ("approve", ("positive", "approve", "good", "verified", "done", "confirmed", "agreed", "yes")),
    ("think", ("curious", "think", "analyzing", "wonder", "question", "research", "hmm")),
    ("focus", ("focused", "focus", "serious", "concentrate", "lock", "execute", "coding")),
    ("rest", ("calm", "rest", "sleep", "idle", "standby", "quiet", "relax")),
)

_BUS_LOCK = threading.RLock()
_ACTIVE_STATE: dict = {
    "gesture": "rest",
    "sentiment": "calm",
    "started_at": 0.0,
    "expires_at": 0.0,
    "text": "",
    "seq": 0,
}


def sentiment_to_gesture(sentiment_or_text: str) -> str:
    """Resolve an explicit sentiment label or natural-language text to one of 8 gesture names."""
    low = str(sentiment_or_text or "").strip().lower()
    if not low:
        return "focus"
    if low in GESTURES:
        return low
    for gesture, words in _SENTIMENT_KEYWORDS:
        if any(w in low for w in words):
            return gesture
    return "approve"


def trigger_global_gesture(sentiment_or_gesture: str, *, duration: float | None = None, text: str = "") -> dict:
    """Publish a gesture + optional spoken text for viseme sync to desktop avatar viewers."""
    gesture = sentiment_to_gesture(sentiment_or_gesture)
    spec = GESTURES[gesture]
    dur = float(duration) if duration is not None else float(spec["duration"])
    now = time.monotonic()
    with _BUS_LOCK:
        _ACTIVE_STATE["gesture"] = gesture
        _ACTIVE_STATE["sentiment"] = spec["sentiment"]
        _ACTIVE_STATE["started_at"] = now
        _ACTIVE_STATE["expires_at"] = now + max(0.3, dur)
        _ACTIVE_STATE["text"] = str(text or "")
        _ACTIVE_STATE["seq"] = int(_ACTIVE_STATE["seq"]) + 1
        return dict(_ACTIVE_STATE)


def get_active_gesture(*, now: float | None = None) -> dict:
    """Return currently active global gesture metadata (`None` expired fallback -> `rest`)."""
    cur = time.monotonic() if now is None else float(now)
    with _BUS_LOCK:
        st = dict(_ACTIVE_STATE)
    if cur >= float(st.get("expires_at") or 0.0):
        st["active"] = False
        return st
    st["active"] = True
    return st
