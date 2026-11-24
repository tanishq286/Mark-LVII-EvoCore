"""core/hand_gestures.py — MediaPipe/OpenCV touchless hand-gesture recognition & dispatch."""

from __future__ import annotations

from collections import Counter, deque
import math
import threading
import time
from typing import Callable, Sequence


GESTURE_ACTIONS: dict[str, dict[str, str]] = {
    "open_palm": {"action": "media_play_pause", "description": "Pause / stop active playback or scroll"},
    "peace": {"action": "media_play_pause", "description": "Toggle play/pause"},
    "thumbs_up": {"action": "volume_up", "description": "Increase system volume"},
    "thumbs_down": {"action": "volume_down", "description": "Decrease system volume"},
    "fist": {"action": "mute", "description": "Mute system audio"},
    "point_up": {"action": "scroll_up", "description": "Scroll up"},
    "pinch": {"action": "enter", "description": "Select / confirm focused element"},
    "swipe_left": {"action": "prev_tab", "description": "Previous tab / track"},
    "swipe_right": {"action": "next_tab", "description": "Next tab / track"},
}


def _xy(pt) -> tuple[float, float]:
    if isinstance(pt, dict):
        return float(pt.get("x", 0.0)), float(pt.get("y", 0.0))
    if hasattr(pt, "x") and hasattr(pt, "y"):
        return float(pt.x), float(pt.y)
    return float(pt[0]), float(pt[1])


def _dist(a: tuple[float, float], b: tuple[float, float]) -> float:
    return math.hypot(a[0] - b[0], a[1] - b[1])


def classify_hand_landmarks(landmarks: Sequence, handedness: str = "Right") -> dict:
    """Classify 21 hand landmarks into a touchless control gesture.

    Works on normalized MediaPipe/list landmarks `[(x, y[, z]), ...]` where `y` grows downward.
    Returns `{"gesture": str, "confidence": float, "fingers": dict}`.
    """
    if not landmarks or len(landmarks) < 21:
        return {"gesture": "none", "confidence": 0.0, "fingers": {}}

    pts = [_xy(p) for p in landmarks[:21]]
    wrist = pts[0]
    palm_scale = max(1e-4, _dist(wrist, pts[9]))

    # Finger tip vs PIP/MCP extension (image Y grows downwards)
    idx_up = pts[8][1] < pts[6][1] - 0.08 * palm_scale
    mid_up = pts[12][1] < pts[10][1] - 0.08 * palm_scale
    ring_up = pts[16][1] < pts[14][1] - 0.08 * palm_scale
    pinky_up = pts[20][1] < pts[18][1] - 0.08 * palm_scale

    # Thumb extension (distance from pinky MCP 17 + vertical thumbs up/down)
    thumb_spread = _dist(pts[4], pts[17]) > 0.72 * palm_scale and _dist(pts[4], pts[5]) > 0.35 * palm_scale
    thumb_dy = pts[4][1] - pts[2][1]
    thumb_up = thumb_dy < -0.45 * palm_scale and _dist(pts[4], pts[5]) > 0.35 * palm_scale
    thumb_down = thumb_dy > 0.45 * palm_scale and _dist(pts[4], pts[5]) > 0.35 * palm_scale

    pinch_ratio = _dist(pts[4], pts[8]) / palm_scale
    fingers = {
        "thumb": bool(thumb_spread or thumb_up or thumb_down),
        "index": idx_up,
        "middle": mid_up,
        "ring": ring_up,
        "pinky": pinky_up,
    }
    ext_non_thumb = sum((idx_up, mid_up, ring_up, pinky_up))

    if pinch_ratio < 0.28 and (mid_up or ext_non_thumb >= 1):
        return {"gesture": "pinch", "confidence": 0.90, "fingers": fingers}

    if ext_non_thumb == 4 and thumb_spread:
        return {"gesture": "open_palm", "confidence": 0.95, "fingers": fingers}

    if ext_non_thumb == 0 and thumb_up:
        return {"gesture": "thumbs_up", "confidence": 0.92, "fingers": fingers}

    if ext_non_thumb == 0 and thumb_down:
        return {"gesture": "thumbs_down", "confidence": 0.92, "fingers": fingers}

    if ext_non_thumb == 0 and not thumb_spread:
        return {"gesture": "fist", "confidence": 0.90, "fingers": fingers}

    if idx_up and mid_up and not ring_up and not pinky_up:
        return {"gesture": "peace", "confidence": 0.91, "fingers": fingers}

    if idx_up and not mid_up and not ring_up and not pinky_up:
        return {"gesture": "point_up", "confidence": 0.88, "fingers": fingers}

    return {"gesture": "none", "confidence": 0.0, "fingers": fingers}


class GestureTracker:
    """Debounces hand-gesture classifications and detects horizontal swipes."""

    def __init__(self, window: int = 3, cooldown_sec: float = 1.2):
        self.window = max(1, int(window))
        self.cooldown_sec = max(0.1, float(cooldown_sec))
        self._recent: deque[str] = deque(maxlen=self.window)
        self._wrist_x: deque[tuple[float, float]] = deque(maxlen=8)
        self._last_fired: dict[str, float] = {}

    def update(self, landmarks: Sequence, *, now: float | None = None) -> str | None:
        """Process one frame's landmarks; return debounced gesture name or None."""
        ts = time.monotonic() if now is None else float(now)
        if not landmarks or len(landmarks) < 21:
            return None

        wrist_x = _xy(landmarks[0])[0]
        self._wrist_x.append((ts, wrist_x))

        # Check swipe across recent <=0.6s window
        if len(self._wrist_x) >= 4:
            t0, x0 = self._wrist_x[0]
            if ts - t0 <= 0.65:
                dx = wrist_x - x0
                if abs(dx) >= 0.24:
                    swipe = "swipe_right" if dx > 0 else "swipe_left"
                    self._wrist_x.clear()
                    return self._maybe_fire(swipe, ts)

        res = classify_hand_landmarks(landmarks)
        g = res["gesture"]
        if g == "none":
            self._recent.clear()
            return None
        self._recent.append(g)
        if len(self._recent) < self.window:
            return None
        top, cnt = Counter(self._recent).most_common(1)[0]
        if cnt < max(2, (self.window + 1) // 2):
            return None
        return self._maybe_fire(top, ts)

    def _maybe_fire(self, gesture: str, ts: float) -> str | None:
        last = self._last_fired.get(gesture, -1e9)
        if ts - last < self.cooldown_sec:
            return None
        self._last_fired[gesture] = ts
        return gesture


def _dispatch_default_action(gesture: str) -> str:
    spec = GESTURE_ACTIONS.get(gesture)
    if not spec:
        return f"Ignored unmapped gesture: {gesture}"
    from actions.computer_settings import computer_settings
    return computer_settings({"action": spec["action"]})


class HandGestureController:
    """Background camera hand-gesture controller supporting MediaPipe and OpenCV fallback."""

    def __init__(self, dispatcher: Callable[[str], str] | None = None, camera_index: int = 0):
        self._dispatcher = dispatcher or _dispatch_default_action
        self._camera_index = int(camera_index)
        self._lock = threading.RLock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._tracker = GestureTracker(window=3, cooldown_sec=1.2)
        self.last_gesture: str = "none"
        self.backend: str = "idle"
        self.events_count: int = 0
        self.last_error: str = ""

    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self) -> str:
        with self._lock:
            if self.is_running():
                return f"Hand gesture controller already running (backend={self.backend})."
            self._stop.clear()
            self.last_error = ""
            self._thread = threading.Thread(target=self._run_loop, daemon=True, name="hand-gestures")
            self._thread.start()
            return "Hand gesture controller started."

    def stop(self) -> str:
        with self._lock:
            self._stop.set()
            th = self._thread
        if th is not None and th.is_alive():
            th.join(timeout=2.0)
        with self._lock:
            self._thread = None
            self.backend = "stopped"
        return "Hand gesture controller stopped."

    def _open_mediapipe_hands(self):
        try:
            import mediapipe as mp
            if hasattr(mp, "solutions") and hasattr(mp.solutions, "hands"):
                self.backend = "mediapipe.solutions.hands"
                return mp.solutions.hands.Hands(
                    static_image_mode=False,
                    max_num_hands=1,
                    min_detection_confidence=0.6,
                    min_tracking_confidence=0.5,
                )
        except Exception:
            pass
        self.backend = "opencv.fallback"
        return None

    def _run_loop(self) -> None:
        try:
            import cv2
        except Exception as exc:
            self.last_error = f"cv2 unavailable: {exc}"
            self.backend = "unavailable"
            return

        cap = cv2.VideoCapture(self._camera_index)
        if not cap or not cap.isOpened():
            self.last_error = f"Camera index {self._camera_index} unavailable."
            self.backend = "no_camera"
            return

        hands = self._open_mediapipe_hands()
        try:
            while not self._stop.is_set():
                ok, frame = cap.read()
                if not ok or frame is None:
                    time.sleep(0.05)
                    continue
                if hands is not None:
                    rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                    res = hands.process(rgb)
                    lm_list = getattr(res, "multi_hand_landmarks", None)
                    if lm_list:
                        fired = self._tracker.update(lm_list[0].landmark)
                        if fired:
                            self.last_gesture = fired
                            self.events_count += 1
                            try:
                                self._dispatcher(fired)
                            except Exception as exc:
                                self.last_error = str(exc)
                time.sleep(0.04)
        finally:
            try:
                cap.release()
            except Exception:
                pass
            if hands is not None and hasattr(hands, "close"):
                try:
                    hands.close()
                except Exception:
                    pass


_GLOBAL_CONTROLLER = HandGestureController()


def get_controller() -> HandGestureController:
    return _GLOBAL_CONTROLLER
