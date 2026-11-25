"""gui/avatar_overlay.py — Standalone transparent 2D desktop Hologram Avatar overlay."""

from __future__ import annotations

import math
import time

from PyQt6.QtCore import QPoint, QRectF, Qt, QTimer
from PyQt6.QtGui import QBrush, QColor, QPainter, QPen, QRadialGradient
from PyQt6.QtWidgets import QApplication, QWidget

from core.avatar import HoloAvatar
from core.avatar_gestures import get_active_gesture, trigger_global_gesture
from core.viseme import VISEMES, VisemeStream
from gui.style import hud_font, mono_font
from gui.theme import C, qcol


class AvatarOverlayWindow(QWidget):
    """Frameless, always-on-top, transparent draggable 2D holographic avatar."""

    def __init__(self, parent=None, *, size: int = 230):
        flags = (
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
        )
        super().__init__(parent, flags)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        self.setFixedSize(int(size), int(size) + 28)

        self._avatar = HoloAvatar()
        self._viseme_sched = VisemeStream()
        self._gesture_label = "focus"
        self._gesture_seq = 0
        self._state = "LISTENING"
        self._mood = "serene"
        self._speaking = False
        self._muted = False
        self._amp = 0.0
        self._listen_amp = 0.0
        self._breath_phase = 0.0
        self._blink_timer = 2.6
        self._gaze_timer = 3.2
        self._gaze_target = (0.0, 0.0)
        self._drag_pos: QPoint | None = None
        self._last_t = time.monotonic()

        self._tmr = QTimer(self)
        self._tmr.timeout.connect(self._tick)
        self._tmr.start(33)

        try:
            screen = QApplication.primaryScreen().availableGeometry()
            self.move(screen.right() - self.width() - 28, screen.bottom() - self.height() - 56)
        except Exception:
            pass

    def set_state(self, state: str) -> None:
        self._state = str(state or "LISTENING").strip().upper()

    def set_speaking(self, speaking: bool) -> None:
        self._speaking = bool(speaking)
        if self._speaking:
            self._state = "SPEAKING"

    def set_muted(self, muted: bool) -> None:
        self._muted = bool(muted)

    def set_mood(self, mood: str) -> None:
        self._mood = str(mood or "serene").strip().lower()

    def set_listening_level(self, level: float) -> None:
        """Thread-safe: store incoming user voice level for reactive listening expressions."""
        try:
            lv = max(0.0, min(1.0, float(level)))
        except (TypeError, ValueError):
            return
        if lv > self._listen_amp:
            self._listen_amp = lv

    def get_presence_state(self) -> dict[str, Any]:
        """Snapshot of smooth breathing, gaze, listening reactivity, and expression state."""
        return {
            "state": self._state,
            "mood": self._mood,
            "speaking": bool(self._speaking),
            "muted": bool(self._muted),
            "gesture": self._gesture_label,
            "breath_phase": round(self._breath_phase, 4),
            "listen_amp": round(self._listen_amp, 4),
            "gaze_target": tuple(round(v, 3) for v in self._gaze_target),
        }

    def trigger_gesture(self, sentiment_or_gesture: str, text: str = "") -> str:
        """Apply one of the 8 sentiment gestures and optionally feed text into visemes."""
        state = trigger_global_gesture(sentiment_or_gesture, text=text)
        g_name = state["gesture"]
        self._gesture_label = g_name
        self._avatar.trigger_gesture(g_name)
        if text.strip():
            self._viseme_sched.reset()
            self._viseme_sched.feed_text(text)
            self._speaking = True
        self.update()
        return g_name

    def _tick(self) -> None:
        now = time.monotonic()
        dt = max(0.005, min(0.10, now - self._last_t))
        self._last_t = now

        bus = get_active_gesture(now=now)
        seq = int(bus.get("seq") or 0)
        if seq != self._gesture_seq:
            self._gesture_seq = seq
            g = str(bus.get("gesture") or "rest")
            self._gesture_label = g
            if bus.get("active"):
                self._avatar.trigger_gesture(g)
                txt = str(bus.get("text") or "").strip()
                if txt:
                    self._viseme_sched.reset()
                    self._viseme_sched.feed_text(txt)
                    self._speaking = True

        # Smooth continuous idle breathing (asymmetric respiratory wave)
        self._breath_phase = (self._breath_phase + dt * 0.24) % 1.0
        self._listen_amp *= 0.88

        # Natural blink & gaze variation depending on conversational state
        self._gaze_timer -= dt
        if self._gaze_timer <= 0.0:
            if self._state in ("THINKING", "PROCESSING"):
                gx = 0.22 if int(now) % 2 == 0 else -0.20
                gy = -0.18
                hold = 1.6
                self._gaze_timer = 2.6
            elif self._speaking:
                gx = ((int(now * 10) % 7) - 3) * 0.04
                gy = -0.02
                hold = 1.1
                self._gaze_timer = 2.2
            else:
                gx = ((int(now * 7) % 5) - 2) * 0.06
                gy = 0.04 if self._listen_amp > 0.12 else -0.02
                hold = 1.3
                self._gaze_timer = 3.4
            self._gaze_target = (gx, gy)
            self._avatar.glance(gx, gy, hold=hold)

        self._blink_timer -= dt
        if self._blink_timer <= 0.0:
            self._blink_timer = 0.28 if (int(now * 10) % 5 == 0) else 3.2
            try:
                if hasattr(self._avatar, "blink"):
                    self._avatar.blink()
            except Exception:
                pass

        # Reactive listening micro-expression while user speaks
        if (not self._speaking) and (not self._muted) and self._state == "LISTENING" and self._listen_amp > 0.16:
            self._gesture_label = "attentive"

        v_open = None
        v_wide = 0.0
        v_level = None
        if self._viseme_sched.pending > 0:
            frames = self._viseme_sched.frames([(0.55, 0.5, 0.0)], hop=dt)
            if frames:
                v_level, v_open, v_wide = frames[0]
            self._amp = 0.55
            self._speaking = True
        else:
            if self._speaking:
                # Drain tail syllable gracefully
                tail = VISEMES.get("REST", (0.0, 0.0, 0.0))
                v_open, v_wide = tail[0], tail[1]
            self._amp *= 0.85
            if self._amp < 0.02:
                self._amp = 0.0
                self._speaking = False

        drive_amp = self._amp if self._speaking else min(0.45, self._listen_amp * 0.8)
        eff_state = "SPEAKING" if self._speaking else (self._state or "LISTENING")
        self._avatar.step(
            dt,
            drive_amp,
            speaking=self._speaking,
            muted=self._muted,
            state=eff_state,
            v_open=v_open,
            v_wide=v_wide,
            v_level=v_level,
        )
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        w, h = self.width(), self.height()
        cx, cy = w * 0.5, (h - 24) * 0.5
        breath = math.sin(self._breath_phase * math.tau)
        rad = min(w, h - 24) * (0.30 + 0.012 * breath)

        pri = QColor(C.PRI)
        acc = QColor(C.ACC)
        bg = QColor(C.BG)

        halo_r = min(w, h - 24) * 0.46
        grad = QRadialGradient(cx, cy, halo_r)
        grad.setColorAt(0.0, qcol(C.BG, 180))
        grad.setColorAt(0.72, qcol(C.PANEL, 130))
        grad.setColorAt(1.0, qcol(C.PRI, 0))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QBrush(grad))
        p.drawEllipse(QRectF(cx - halo_r, cy - halo_r, halo_r * 2, halo_r * 2))

        p.setBrush(Qt.BrushStyle.NoBrush)
        p.setPen(QPen(qcol(C.PRI, 140), 1.5))
        ring_r = halo_r * 0.90
        p.drawEllipse(QRectF(cx - ring_r, cy - ring_r, ring_r * 2, ring_r * 2))

        self._avatar.paint(p, cx, cy, rad, pri, acc, bg)

        # Gesture pill badge
        badge_rect = QRectF(w * 0.16, h - 24, w * 0.68, 20)
        p.setBrush(QBrush(qcol(C.PANEL, 210)))
        p.setPen(QPen(qcol(C.PRI, 160), 1.0))
        p.drawRoundedRect(badge_rect, 8.0, 8.0)
        p.setFont(mono_font(8))
        p.setPen(QPen(QColor(C.WHITE)))
        p.drawText(badge_rect, Qt.AlignmentFlag.AlignCenter, f"AVATAR · {self._gesture_label.upper()}")

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_pos = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
            event.accept()

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        if self._drag_pos is not None and (event.buttons() & Qt.MouseButton.LeftButton):
            self.move(event.globalPosition().toPoint() - self._drag_pos)
            event.accept()

    def mouseDoubleClickEvent(self, event) -> None:  # noqa: N802
        self.hide()
        event.accept()
