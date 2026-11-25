"""
core/goddess_avatar.py — a full-body goddess for the HUD centre: porcelain
white, pearl and gold, with feelings and a beating heart.

It is a drop-in sibling of core.avatar.HoloAvatar: the same `step()` inputs
(audio level, speaking, muted, state, viseme openness) and `glance()`, plus:

* `feel(text, who)` — thread-safe. Hand it what JARVIS or the user just said
  and her mood follows: she blushes at a compliment, lights up at good news,
  softens and lifts a hand to her heart at "love", tears up at bad news.
* A heart you can see. It glows through the gown at her chest, beats lub-dub
  at a rate set by her mood and her voice, and sends a ring of light out
  through the figure on every beat. A small readout shows BPM, an ECG trace
  and what she is feeling.

Software QPainter only, like the head: no GPU, no new dependency. Drawing is
split across goddess_figure (body, gown) and goddess_face (face, hair,
jewellery); goddess_soul owns the mood and the heart.
"""

from __future__ import annotations

import math
import random

from PyQt6.QtCore import QPointF, QRectF, Qt
from PyQt6.QtGui import QBrush, QColor, QFont, QPainter, QPainterPath, QPen, QRadialGradient

from core import goddess_face as FA
from core import goddess_figure as F
from core.goddess_figure import GOLD, GOLD_HI, ROSE, col, heart_path, mix, pen
from core.goddess_soul import Heart, Soul, rate

_HEART_AT = (0.17, 2.02)


class GoddessAvatar:
    SPAN = 9.15      # layout height in figure units, from the top of the halo to the floor glow
    TOP = 0.95       # units of room above her crown, for the halo
    WIDTH = 5.6      # layout width in figure units (cape plus aura)

    def __init__(self) -> None:
        self.soul = Soul()
        self.heart = Heart()
        self._t = 0.0
        self._amp = 0.0
        self._amp_slow = 0.0
        self._mouth = 0.0
        self._gesture = 0.0
        self._blink_t, self._blink_at = -1.0, 2.0
        self._wink_t, self._wink_at = -1.0, 4.0
        self._gaze, self._gaze_tgt, self._gaze_at = [0.0, 0.0], [0.0, 0.0], 0.0
        self._glance = None
        self._muted = False
        self._petals: list[list[float]] = []   # x, y, vx, vy, rot, vrot, size
        self._sparks: list[list[float]] = []   # x, y, age, life, size
        self._hearts: list[list[float]] = []   # x, y, vx, age
        self._petal_due = 0.0

    # ── inputs ───────────────────────────────────────────────────────────────
    def feel(self, text: str, who: str = "jarvis") -> None:
        self.soul.feel(text, who)

    def set_mood(self, mood: str, intensity: float = 1.0, hold: float | None = None) -> None:
        self.soul.set_mood(mood, intensity, hold)

    def glance(self, dx: float, dy: float, hold: float = 1.1) -> None:
        self._glance = (max(-1.0, min(1.0, float(dx))), max(-1.0, min(1.0, float(dy))), self._t + hold)

    # ── animation ────────────────────────────────────────────────────────────
    def step(self, dt: float, amp: float, speaking: bool = False, muted: bool = False,
             state: str = "", v_open: float | None = None, **_ignored) -> None:
        dt = max(0.001, min(0.10, float(dt)))
        self._t += dt
        t = self._t
        live = speaking and not muted
        self._muted = muted
        amp = max(0.0, min(1.0, float(amp)))
        self._amp += (amp - self._amp) * rate(dt, 0.06)
        self._amp_slow += ((amp if live else 0.0) - self._amp_slow) * rate(dt, 0.9)

        self.soul.step(dt, state, speaking, muted)
        v = self.soul.v
        beat = self.heart.step(dt, v["bpm"] + 14.0 * self._amp_slow)

        # Lips follow the voice only while she is the one speaking.
        if live:
            shape = 1.0 if v_open is None else 0.35 + 0.65 * max(0.0, min(1.0, float(v_open)))
            tgt = min(1.0, self._amp * 2.1) * shape
        else:
            tgt = v["mouth"]
        self._mouth += (tgt - self._mouth) * rate(dt, 0.03 if tgt > self._mouth else 0.022)
        self._gesture += ((min(1.0, self._amp_slow * 2.5) if live else 0.0) - self._gesture) * rate(dt, 0.6)

        if t > self._blink_at:
            self._blink_t = t
            self._blink_at = t + random.uniform(2.2, 5.8) * (0.6 if self.soul.mood == "shy" else 1.0)
        if self.soul.mood == "playful" and t > self._wink_at:
            self._wink_t, self._wink_at = t, t + random.uniform(3.0, 5.0)

        g = self._glance
        if g is not None and t < g[2]:
            self._gaze_tgt = [g[0], g[1]]
        elif t > self._gaze_at:
            self._glance = None
            reach = 0.25 if (state or "").upper() == "LISTENING" else 0.7
            self._gaze_tgt = [random.uniform(-reach, reach), random.uniform(-0.4, 0.4) * reach]
            self._gaze_at = t + random.uniform(0.9, 3.2)
        for i in (0, 1):
            self._gaze[i] += (self._gaze_tgt[i] - self._gaze[i]) * rate(dt, 0.07)

        self._particles(dt, beat, v)

    def _particles(self, dt: float, beat: bool, v: dict) -> None:
        t = self._t
        self._petal_due -= dt
        if self._petal_due <= 0 and len(self._petals) < 26:
            self._petal_due = random.uniform(0.5, 1.4)
            self._petals.append([random.uniform(-2.7, 2.7), -1.1, random.uniform(0.05, 0.2),
                                 random.uniform(0.22, 0.42), random.uniform(0, 360),
                                 random.uniform(-90, 90), random.uniform(0.7, 1.2)])
        wind = 0.12 * math.sin(t * 0.4)
        for pt in self._petals:
            pt[0] += (pt[2] + wind + 0.08 * math.sin(t * 1.3 + pt[4])) * dt
            pt[1] += pt[3] * dt
            pt[4] += pt[5] * dt
        self._petals = [pt for pt in self._petals if pt[1] < 8.25]

        n = v["sparkle"] * 4.0 * dt + (3 if beat else 0) * v["glow"]
        while n > 0 and len(self._sparks) < 60:
            if n < 1 and random.random() > n:
                break
            n -= 1
            a, r = random.uniform(0, 2 * math.pi), random.uniform(0.4, 2.6)
            self._sparks.append([math.cos(a) * r * 0.75, 3.2 + math.sin(a) * r * 1.4, 0.0,
                                 random.uniform(0.6, 1.3), random.uniform(0.05, 0.10)])
        for s in self._sparks:
            s[2] += dt
        self._sparks = [s for s in self._sparks if s[2] < s[3]]

        if beat and random.random() < v["hearts"] and len(self._hearts) < 14:
            self._hearts.append([_HEART_AT[0] + random.uniform(-0.1, 0.1), _HEART_AT[1],
                                 random.uniform(-0.25, 0.35), 0.0])
        for h in self._hearts:
            h[3] += dt
            h[0] += (h[2] + 0.15 * math.sin(h[3] * 3)) * dt
            h[1] -= 0.55 * dt
        self._hearts = [h for h in self._hearts if h[3] < 2.6]

    # ── drawing ──────────────────────────────────────────────────────────────
    def paint(self, p: QPainter, cx: float, top: float, u: float,
              primary: QColor, accent: QColor, bg: QColor | None = None) -> None:
        """Draw her centred on `cx`. `top` is the pixel y of the layout's top
        edge and `u` is pixels per figure unit; `SPAN * u` is the full height."""
        t, v, pulse = self._t, self.soul.v, self.heart.pulse
        wind = 0.7 + 0.3 * math.sin(t * 0.21)
        br = 0.012 * math.sin(t * 2 * math.pi / 4.4)
        sway = 0.035 * math.sin(t * 0.45)
        hx = 0.02 * math.sin(t * 0.45 + 0.4)
        p.save()
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.translate(cx, top + self.TOP * u)
        p.scale(u, u)
        if self._muted:
            p.setOpacity(0.55)

        self._aura(p, v, pulse, accent)
        self._mandala(p, hx, v, pulse)
        self._floor(p, pulse)
        p.translate(0, 8.0)
        p.rotate(0.8 * math.sin(t * 0.37))
        p.translate(0, -8.0)

        F.cape(p, t, wind)
        F.back_hair(p, hx, t, wind)
        F.skirt(p, t, sway, wind, lambda: F.leg(p, t, sway))
        F.torso(p, br, sway)
        F.bodice(p, t, br)
        F.belt(p, pulse)
        FA.necklace(p, br, pulse)

        lift = self._gesture
        tl = (-0.84 + (-0.40) * lift, 3.84 + (-0.95 + 0.14 * math.sin(t * 2.1)) * lift)
        F.arm(p, (-0.68, 1.47 - br), tl, +1)
        hand = v["hand"]
        tr = (0.84 + (_HEART_AT[0] + 0.03 - 0.84) * hand, 3.80 + (_HEART_AT[1] - 3.80) * hand)
        _, hd, ang = F.arm(p, (0.68, 1.47 - br), tr, -1, elbow=(0.80, 2.66), pin=min(1.0, hand * 1.25))
        F.rose(p, hd[0] + math.cos(ang) * 0.13, hd[1] + math.sin(ang) * 0.13 - 0.02, pulse)
        self._heart_glow(p, pulse, v)

        # Locks before the head, so the hair cap covers their roots.
        FA.front_lock(p, (hx - 0.38, 0.40), t, wind, -1)
        FA.front_lock(p, (hx + 0.38, 0.40), t, wind, +1)
        p.save()                                       # the head, with its tilt
        p.translate(hx, 0.52 - br * 0.5)
        p.rotate(v["tilt"] * 14.0 + 1.6 * math.sin(t * 0.5))
        bt = t - self._blink_t
        blink = max(0.0, 1.0 - abs(bt - 0.075) / 0.075) if 0 <= bt < 0.15 else 0.0
        wt = t - self._wink_t
        wink = max(0.0, min(1.0, 1.0 - abs(wt - 0.25) / 0.25) * 1.6) if 0 <= wt < 0.5 else 0.0
        FA.head(p, {"smile": v["smile"], "brow": v["brow"], "sad": v["sad"], "blush": v["blush"],
                    "eye": v["eye"] * (1.0 - blink), "mouth": max(self._mouth, 0.0), "down": v["down"],
                    "gaze": self._gaze, "wink": min(1.0, wink), "t": t})
        FA.hair_cap(p, t)
        FA.tiara(p, pulse)
        FA.earrings(p, t, pulse)
        p.restore()

        self._draw_particles(p)
        p.restore()
        self._readout(p, cx, top, u)

    def _aura(self, p, v, pulse, accent) -> None:
        warm = v["warm"]
        base = mix((255, 248, 250), (255, 176, 208) if warm > 0 else (182, 208, 255), abs(warm) * 0.6)
        base = mix(base, (accent.red(), accent.green(), accent.blue()), 0.18)
        a = (0.10 + 0.22 * v["glow"]) * (1.0 + 0.35 * pulse)
        g = QRadialGradient(0, 3.6, 4.6)
        g.setColorAt(0.0, col(base, a))
        g.setColorAt(0.45, col(base, a * 0.42))
        g.setColorAt(1.0, col(base, 0.0))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QBrush(g))
        p.drawEllipse(QPointF(0, 3.6), 4.6, 4.6)

    def _mandala(self, p, hx, v, pulse) -> None:
        """The sun halo behind her head: slow-turning gold rays that flare on each beat."""
        c = QPointF(hx, 0.42)
        g = QRadialGradient(c, 1.35)
        g.setColorAt(0.0, col((255, 250, 235), 0.45 * v["glow"]))
        g.setColorAt(1.0, col((255, 250, 235), 0.0))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QBrush(g))
        p.drawEllipse(c, 1.35, 1.35)
        p.save()
        p.translate(c)
        p.rotate(self._t * 5.0)
        p.setBrush(QBrush(col(GOLD, 0.16 + 0.22 * pulse)))
        for i in range(24):
            p.rotate(15)
            p.drawEllipse(QRectF(-0.035, -1.05, 0.07, 0.30 + 0.05 * (i % 2)))
        p.rotate(-self._t * 11.0)
        p.setBrush(QBrush(col(GOLD_HI, 0.55)))
        for i in range(36):
            a = i * math.pi / 18
            p.drawEllipse(QPointF(math.cos(a) * 0.95, math.sin(a) * 0.95), 0.012, 0.012)
        p.restore()
        p.setBrush(Qt.BrushStyle.NoBrush)
        for r, a in ((0.74, 0.30), (0.86, 0.18), (1.14, 0.26)):
            p.setPen(pen(GOLD, a * (0.7 + 0.6 * v["glow"]), 0.011))
            p.drawEllipse(c, r, r)

    def _floor(self, p, pulse) -> None:
        g = QRadialGradient(0.1, 8.02, 2.3)
        g.setColorAt(0.0, col((255, 250, 252), 0.32 * (1 + 0.3 * pulse)))
        g.setColorAt(1.0, col((255, 250, 252), 0.0))
        p.save()
        p.translate(0.1, 8.02)
        p.scale(1.0, 0.11)
        p.translate(-0.1, -8.02)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QBrush(g))
        p.drawEllipse(QPointF(0.1, 8.02), 2.3, 2.3)
        p.restore()

    def _heart_glow(self, p, pulse, v) -> None:
        x, y = _HEART_AT
        g = QRadialGradient(x, y, 0.62)
        g.setColorAt(0.0, col(ROSE, 0.18 + 0.42 * pulse))
        g.setColorAt(0.4, col(ROSE, 0.06 + 0.12 * pulse))
        g.setColorAt(1.0, col(ROSE, 0.0))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QBrush(g))
        p.drawEllipse(QPointF(x, y), 0.62, 0.62)
        s = 0.085 * (1.0 + 0.22 * pulse)
        hp = heart_path(x, y, s)
        hg = QRadialGradient(x, y - s * 0.2, s * 1.3)
        hg.setColorAt(0.0, col((255, 255, 255), 0.55 + 0.4 * pulse))
        hg.setColorAt(1.0, col(ROSE, 0.40 + 0.45 * pulse))
        p.fillPath(hp, QBrush(hg))
        p.setBrush(Qt.BrushStyle.NoBrush)
        for age in self.heart.ripples:                   # a ring of light per beat
            k = 1.0 - age / 1.6
            r = 0.15 + age * 2.4
            p.setPen(pen(mix(ROSE, (255, 255, 255), 0.4), 0.45 * k * k * (0.5 + v["glow"]), 0.02 * k + 0.004))
            p.drawEllipse(QPointF(x, y), r, r * 1.08)

    def _draw_particles(self, p) -> None:
        p.setPen(Qt.PenStyle.NoPen)
        for x, y, _, _, rot, _, s in self._petals:
            p.save()
            p.translate(x, y)
            p.rotate(rot)
            g = QRadialGradient(0, 0, 0.06 * s)
            g.setColorAt(0.0, col((255, 255, 255), 0.9))
            g.setColorAt(1.0, col((255, 196, 216), 0.75))
            p.setBrush(QBrush(g))
            p.drawEllipse(QRectF(-0.055 * s, -0.032 * s, 0.11 * s, 0.064 * s))
            p.restore()
        for x, y, age, life, size in self._sparks:
            k = math.sin(math.pi * age / life)
            sz = size * k
            p.setPen(pen((255, 250, 230), 0.9 * k, 0.010))
            p.drawLine(QPointF(x - sz, y), QPointF(x + sz, y))
            p.drawLine(QPointF(x, y - sz * 1.4), QPointF(x, y + sz * 1.4))
        for x, y, _, age in self._hearts:
            k = 1.0 - age / 2.6
            p.fillPath(heart_path(x, y, 0.05 + 0.03 * (1 - k)), QBrush(col(mix(ROSE, (255, 255, 255), 0.25), 0.8 * k)))

    def _readout(self, p, cx, top, u) -> None:
        """♥ BPM, a live ECG trace and the mood, beside her feet."""
        W = p.device().width() if p.device() is not None else cx * 2
        w, h = 118.0, 22.0
        x = cx + 2.15 * u
        if x + w > W - 8:
            x = max(8.0, cx - 2.15 * u - w)
        y = top + (self.TOP + 6.55) * u
        pulse = self.heart.pulse
        p.save()
        p.fillPath(heart_path(x + 8, y + 7, 6.5 * (1 + 0.25 * pulse)), QBrush(col(ROSE, 0.75 + 0.25 * pulse)))
        f = QFont()
        f.setPixelSize(13)
        f.setBold(True)
        f.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, 1.5)
        p.setFont(f)
        p.setPen(QPen(col((255, 244, 248), 0.92)))
        p.drawText(QRectF(x + 20, y - 2, w, 18), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                   f"{self.heart.bpm:.0f} BPM")
        ecg = list(self.heart.ecg)
        if len(ecg) > 2:
            path = QPainterPath()
            y0 = y + 22 + h * 0.65
            for i, e in enumerate(ecg):
                pt = QPointF(x + w * i / self.heart.ecg.maxlen, y0 - e * h * 0.8)
                path.moveTo(pt) if i == 0 else path.lineTo(pt)
            p.strokePath(path, pen(ROSE, 0.85, 1.4))
            last = path.currentPosition()
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QBrush(col((255, 255, 255), 0.9)))
            p.drawEllipse(last, 2.2, 2.2)
        f.setPixelSize(11)
        f.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, 3)
        p.setFont(f)
        p.setPen(QPen(col(mix(ROSE, (255, 255, 255), 0.45), 0.9)))
        p.drawText(QRectF(x, y + 48, w + 40, 16), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                   self.soul.mood.upper())
        p.restore()
