"""
core/goddess_portrait.py — the painted goddess, brought to life.

The picture (assets/goddess_portrait.jpg) is the face and figure. Everything
that moves is drawn on top of it, in the same soft light the painting uses:

* her heart glows and beats lub-dub, sending rings of light outward, and the
  halo behind her head flares with each beat;
* she breathes, blinks, and her eyelids droop when she is sleepy;
* her mood (from core.goddess_soul, fed by the conversation) tints the whole
  scene warm or cool, blushes her cheeks, brings tears, floats hearts up from
  her chest, and sets the heart rate;
* when she speaks, light blooms around her and her halo ripples with the voice.

A painting cannot change its expression, so the mouth and brows stay as drawn.
Same interface as core.goddess_avatar.GoddessAvatar, which remains the fallback
if the picture is missing.
"""

from __future__ import annotations

import math
import random
from pathlib import Path

from PyQt6.QtCore import QPointF, QRectF, Qt
from PyQt6.QtGui import (QBrush, QColor, QFont, QImage, QPainter, QPainterPath, QPen, QPixmap,
                         QRadialGradient)

from core import goddess_warp as W
from core.goddess_figure import ROSE, col, heart_path, mix, pen
from core.goddess_soul import Heart, Soul, rate

_PATH = Path(__file__).resolve().parent.parent / "assets" / "goddess_portrait.jpg"

# Landmarks, in pixels of the stored picture (1600 x 1536).
_W, _H = 1600.0, 1536.0
_ASPECT = _W / _H


def _x(px: float) -> float:        # picture pixels -> layout units (1.0 = picture height)
    return px / _H


_HEART = (_x(809), 408 / _H)
_HEAD = (_x(806), 240 / _H)
_EYES = ((_x(776), 226 / _H), (_x(837), 226 / _H))
_CHEEKS = ((_x(758), 270 / _H), (_x(852), 270 / _H))
_MOUTH = (_x(808), 280 / _H)
_EYE_W, _EYE_H = 40 / _H, 16 / _H


class PortraitGoddess:
    SPAN = 1.0
    WIDTH = _ASPECT

    def __init__(self) -> None:
        img = QImage(str(_PATH))
        if img.isNull():
            raise FileNotFoundError(str(_PATH))
        self._img = img
        self._pm = QPixmap.fromImage(img)
        self._scaled: QPixmap | None = None
        self._scaled_key = None
        # Eyelid colour, sampled from the skin just under each eye (above it is
        # dark eyeshadow and lashes), then a touch deeper, like a lid in shade.
        cols = []
        for ex, ey in _EYES:
            px, py = int(ex * _H), int(ey * _H) + 26
            samples = [QColor(img.pixel(px + dx, py + dy)) for dx in (-10, 0, 10) for dy in (-3, 0, 3)]
            cols.append((sum(c.red() for c in samples) / 9, sum(c.green() for c in samples) / 9,
                         sum(c.blue() for c in samples) / 9))
        self._lid = tuple(0.93 * sum(c[i] for c in cols) / 2 for i in range(3))

        try:
            self._warp: W.FaceWarp | None = W.FaceWarp(img)
        except Exception:
            self._warp = None
        try:
            self._cloth: W.ClothWarp | None = W.ClothWarp(img)
        except Exception:
            self._cloth = None
        self._cloth_at = 0.0
        self._P = dict(W.DEFAULTS)         # the expression, as the warp sees it
        self._warp_at = 0.0
        self._mouth = 0.0
        self._nod = 0.0
        self._brow_speech = 0.0
        self._gaze, self._gaze_tgt, self._gaze_at = [0.0, 0.0], [0.0, 0.0], 0.0
        self._glance = None
        self._smile_s = 0.0

        self.soul = Soul()
        self.heart = Heart()
        self._t = 0.0
        self._amp = 0.0
        self._amp_slow = 0.0
        self._blink_t, self._blink_at = -1.0, 2.0
        self._bph = 0.0                    # breath phase, integrated so a change of rate never jumps
        self._muted = False
        self._petals: list[list[float]] = []
        self._sparks: list[list[float]] = []
        self._hearts: list[list[float]] = []
        self._petal_due = 0.0

    # ── inputs (same as GoddessAvatar) ───────────────────────────────────────
    def feel(self, text: str, who: str = "jarvis") -> None:
        self.soul.feel(text, who)

    def set_mood(self, mood: str, intensity: float = 1.0, hold: float | None = None) -> None:
        self.soul.set_mood(mood, intensity, hold)

    def glance(self, dx: float, dy: float, hold: float = 1.1) -> None:
        self._glance = (max(-1.0, min(1.0, float(dx))), max(-1.0, min(1.0, float(dy))), self._t + hold)

    def step(self, dt: float, amp: float, speaking: bool = False, muted: bool = False,
             state: str = "", v_open: float | None = None, **_ignored) -> None:
        dt = max(0.001, min(0.10, float(dt)))
        self._t += dt
        t = self._t
        live = speaking and not muted
        self._muted = muted
        amp = max(0.0, min(1.0, float(amp)))
        self._amp += (amp - self._amp) * rate(dt, 0.06)
        self._amp_slow += ((amp if live else 0.0) - self._amp_slow) * rate(dt, 0.7)

        self.soul.step(dt, state, speaking, muted)
        v = self.soul.v
        self._bph += dt / (6.8 - 3.2 * self.soul.arousal - 1.0 * self._amp_slow)   # calmer breath when calm
        beat = self.heart.step(dt, v["bpm"] + 14.0 * self._amp_slow)

        self._animate_face(dt, live, v_open, state)

        if t > self._blink_at:
            self._blink_t = t
            gap = random.uniform(2.4, 6.0) * (0.6 if self.soul.mood == "shy" else 1.0) * (1.4 - 0.7 * self.soul.arousal)
            if random.random() < 0.18:                    # now and then a quick double blink
                gap = 0.28
            self._blink_at = t + gap
        self._particles(dt, beat, v)

    def _animate_face(self, dt: float, live: bool, v_open, state: str) -> None:
        """Turn the mood and the voice into the numbers the warp reads."""
        t, v, P = self._t, self.soul.v, self._P
        # Lips follow the voice only while she is the one speaking.
        if live:
            shape = 1.0 if v_open is None else 0.35 + 0.65 * max(0.0, min(1.0, float(v_open)))
            tgt = min(1.0, self._amp * 2.2) * shape
        else:
            tgt = v["mouth"]
        self._mouth += (tgt - self._mouth) * rate(dt, 0.03 if tgt > self._mouth else 0.022)
        # Expressive speech: the brows flick and the head nods on emphasis.
        emph = max(0.0, self._amp - self._amp_slow * 0.9) if live else 0.0
        self._brow_speech += (min(1.0, emph * 3.0) - self._brow_speech) * rate(dt, 0.12)
        self._nod += ((min(1.0, self._amp * 1.6) if live else 0.0) * math.sin(t * 5.2) * 0.5 - self._nod) * rate(dt, 0.15)

        # Where she looks: drifting saccades, steadier when she is listening to you.
        g = self._glance
        if g is not None and t < g[2]:
            self._gaze_tgt = [g[0], g[1]]
        elif t > self._gaze_at:
            self._glance = None
            reach = 0.3 if (state or "").upper() == "LISTENING" else 0.9
            self._gaze_tgt = [random.uniform(-reach, reach), random.uniform(-0.5, 0.5) * reach]
            self._gaze_at = t + random.uniform(0.8, 3.0)
        for i in (0, 1):
            self._gaze[i] += (self._gaze_tgt[i] - self._gaze[i]) * rate(dt, 0.06)

        sway = math.sin(t * 0.45)
        acc = getattr(self.soul, "accents", {}) or {}
        P.update(smile=v["smile"] * (0.85 + 0.15 * self.heart.pulse), mouth=self._mouth,
                 brow=v["brow"] + 0.5 * self._brow_speech, sad=v["sad"], eye=v["eye"],
                 gx=self._gaze[0], gy=self._gaze[1] + v["down"] * 2.2,
                 tilt=v["tilt"] * 0.30 + 0.012 * sway, nod=v["down"] * 0.8 + self._nod, t=t,
                 hair=0.6 + 0.8 * self._amp_slow + 0.3 * math.sin(t * 0.3),
                 smirk=float(acc.get("smirk", 0.0)),
                 furrow=float(acc.get("furrow", 0.0)),
                 tremble=float(acc.get("tremble", 0.0)))

    def _particles(self, dt: float, beat: bool, v: dict) -> None:
        t = self._t
        self._petal_due -= dt
        if self._petal_due <= 0 and len(self._petals) < 18:
            self._petal_due = random.uniform(0.7, 1.8)
            px = random.uniform(0.0, _ASPECT - 0.28)       # falls to either side of her face, not across it
            px += 0.28 if px > 0.40 else 0.0
            self._petals.append([px, -0.03, random.uniform(-0.01, 0.03),
                                 random.uniform(0.035, 0.075), random.uniform(0, 360),
                                 random.uniform(-90, 90), random.uniform(0.7, 1.2)])
        for pt in self._petals:
            pt[0] += (pt[2] + 0.012 * math.sin(t * 1.3 + pt[4])) * dt
            pt[1] += pt[3] * dt
            pt[4] += pt[5] * dt
        self._petals = [pt for pt in self._petals if pt[1] < 1.04]

        n = v["sparkle"] * 5.0 * dt + (2 if beat else 0) * v["glow"]
        while n > 0 and len(self._sparks) < 50:
            if n < 1 and random.random() > n:
                break
            n -= 1
            a, r = random.uniform(0, 2 * math.pi), random.uniform(0.08, 0.42)
            self._sparks.append([_HEAD[0] + math.cos(a) * r, 0.5 + math.sin(a) * r * 1.2, 0.0,
                                 random.uniform(0.6, 1.4), random.uniform(0.006, 0.014)])
        for s in self._sparks:
            s[2] += dt
        self._sparks = [s for s in self._sparks if s[2] < s[3]]

        if beat and random.random() < v["hearts"] and len(self._hearts) < 12:
            self._hearts.append([_HEART[0] + random.uniform(-0.03, 0.03), _HEART[1],
                                 random.uniform(-0.03, 0.04), 0.0])
        for h in self._hearts:
            h[3] += dt
            h[0] += (h[2] + 0.02 * math.sin(h[3] * 3)) * dt
            h[1] -= 0.06 * dt
        self._hearts = [h for h in self._hearts if h[3] < 1.5]

    # ── drawing ──────────────────────────────────────────────────────────────
    def paint(self, p: QPainter, cx: float, top: float, u: float,
              primary: QColor, accent: QColor, bg: QColor | None = None) -> None:
        t, v, pulse = self._t, self.soul.v, self.heart.pulse
        w_px, h_px = max(2, int(u * _ASPECT)), max(2, int(u))
        left = cx - w_px / 2.0
        key = (w_px, h_px)
        if self._scaled_key != key:                      # one smooth rescale per size, not per frame
            self._scaled = self._pm.scaled(w_px, h_px, Qt.AspectRatioMode.IgnoreAspectRatio,
                                           Qt.TransformationMode.SmoothTransformation)
            self._scaled_key = key

        p.save()
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        p.setClipRect(QRectF(left, top, w_px, h_px))
        if self._muted:
            p.setOpacity(0.6)

        # She breathes: a slow swell of the whole picture, pivoting at her feet.
        breath = 1.0 + (0.0045 + 0.007 * self.soul.breath_depth) * math.sin(self._bph * 2 * math.pi) + 0.0015 * pulse
        p.translate(left + w_px / 2.0, top + h_px)
        p.scale(breath, breath)
        p.translate(-w_px / 2.0, -h_px)
        p.drawPixmap(0, 0, self._scaled)

        p.scale(u, u)                                    # draw the rest in layout units
        self._fabric(p, u)
        self._expression(p, u)
        self._mood_grade(p, v)
        self._halo(p, v, pulse)
        self._face(p, v, t)
        self._heart_light(p, v, pulse)
        self._draw_particles(p)
        p.restore()
        self._readout(p, left, top, w_px, h_px)

    def _fabric(self, p: QPainter, u: float) -> None:
        """The cape and gown stir in a breeze that picks up when she speaks."""
        cw = self._cloth
        if cw is None:
            return
        try:
            cw.prepare(u / _H)
            if cw.image is None or (self._t - self._cloth_at >= 1 / 14 and self._t - self._warp_at > 0.012):
                cw.render(self._t, 0.7 + 0.3 * math.sin(self._t * 0.31) + 0.6 * self._amp_slow)
                self._cloth_at = self._t
            x, y, w, h = W.cloth_rect_units(_H)
            p.drawImage(QRectF(x, y, w, h), cw.image)
        except Exception:
            self._cloth = None

    def _expression(self, p: QPainter, u: float) -> None:
        """Her face, with this moment's expression painted into it."""
        wp = self._warp
        if wp is None:
            return
        try:
            wp.prepare(u / _H)
            now = self._t
            if wp.image is None or now - self._warp_at >= 1 / 26:   # the warp runs at 26 Hz, drawn every frame
                wp.render(self._P)
                self._warp_at = now
            x, y, w, h = W.rect_units(_H)
            p.drawImage(QRectF(x, y, w, h), wp.image)
        except Exception:
            self._warp = None                                       # never let a cosmetic break the HUD
            return
        self._mouth_interior(p)

    def _mouth_interior(self, p: QPainter) -> None:
        """When she opens her mouth there is a mouth to see: dark, with a flash of teeth."""
        m = self._P["mouth"]
        if m < 0.06:
            return
        P = self._P
        lc, rc = W.pt(W.L_CORNER, P), W.pt(W.R_CORNER, P)
        cx = (lc[0] + rc[0]) / 2 / _H
        half = (rc[0] - lc[0]) / 2 / _H * 0.80
        seam = W.pt((W.MOUTH[0], 279.5), P)[1] / _H
        h = 11.0 * m / _H
        path = QPainterPath()
        path.addEllipse(QPointF(cx, seam + h * 0.42), half * (0.8 + 0.2 * min(1.0, m * 2)), h * 0.60)
        p.save()
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QBrush(col((104, 40, 56))))
        p.drawPath(path)
        p.setClipPath(path)
        if m > 0.22:                                                 # teeth, only once she opens wide
            p.setBrush(QBrush(col((246, 240, 242), min(0.95, (m - 0.22) * 2.5))))
            p.drawRect(QRectF(cx - half, seam - h * 0.2, half * 2, h * 0.30))
        tongue = QRadialGradient(cx, seam + h * 1.05, half * 0.8)
        tongue.setColorAt(0.0, col((214, 104, 124), 0.85))
        tongue.setColorAt(1.0, col((214, 104, 124), 0.0))
        p.setBrush(QBrush(tongue))
        p.drawRect(QRectF(cx - half, seam + h * 0.45, half * 2, h))
        p.restore()

    def _plus(self, p: QPainter, on: bool) -> None:
        p.setCompositionMode(QPainter.CompositionMode.CompositionMode_Plus if on
                             else QPainter.CompositionMode.CompositionMode_SourceOver)

    def _mood_grade(self, p, v) -> None:
        """Tint the whole scene by live human emotion family: rose, gold, crimson, amber, violet, emerald, or cool blue."""
        full = QRectF(-0.1, -0.1, _ASPECT + 0.2, 1.2)
        warm = v["warm"]
        tint = getattr(self.soul, "tint", "rose")
        palette = {
            "rose":    ((255, 140, 180), 0.13),
            "gold":    ((255, 214, 130), 0.13),
            "crimson": ((235,  72,  82), 0.14),
            "amber":   ((240, 178, 112), 0.13),
            "violet":  ((152, 128, 232), 0.15),
            "emerald": ((124, 212, 180), 0.11),
            "blue":    ((120, 150, 220), 0.18),
        }
        rgb, max_a = palette.get(tint, ((255, 140, 180), 0.12))
        strength = max(abs(warm), 0.45 if tint not in ("rose", "blue") else 0.0)
        if strength > 0.05:
            p.fillRect(full, col(rgb, max_a * min(1.0, strength)))
        dim = max(0.0, 0.7 - v["glow"]) * 0.5
        if dim > 0.01:
            p.fillRect(full, col((8, 10, 30), dim))

    def _halo(self, p, v, pulse) -> None:
        """Light behind her: flares on each heartbeat, swells with her voice."""
        hx, hy = _HEAD
        k = 0.05 + 0.16 * v["glow"] * (0.5 + 0.5 * pulse) + 0.18 * self._amp_slow
        c = mix((255, 244, 226), (255, 196, 220), max(0.0, v["warm"]) * 0.7)
        g = QRadialGradient(hx, hy, 0.42)
        g.setColorAt(0.0, col(c, k))
        g.setColorAt(1.0, col(c, 0.0))
        self._plus(p, True)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QBrush(g))
        p.drawEllipse(QPointF(hx, hy), 0.42, 0.42)
        if self._amp_slow > 0.04:                        # her voice, as rings spreading from her
            p.setBrush(Qt.BrushStyle.NoBrush)
            for i in range(3):
                ph = (self._t * 0.7 + i / 3.0) % 1.0
                p.setPen(pen((255, 240, 230), 0.30 * self._amp_slow * (1 - ph), 0.004))
                p.drawEllipse(QPointF(hx, hy + 0.03), 0.14 + 0.30 * ph, 0.16 + 0.34 * ph)
        self._plus(p, False)

    def _lm(self, c):
        """A painted landmark's position, in layout units, after the warp."""
        if self._warp is None:
            return c[0] / _H, c[1] / _H
        x, y = W.pt(c, self._P)
        return x / _H, y / _H

    def _face(self, p, v, t) -> None:
        p.setPen(Qt.PenStyle.NoPen)
        eyes = [self._lm(W.L_EYE), self._lm(W.R_EYE)]
        for cxp, cyp in (self._lm(W.L_CHEEK), self._lm(W.R_CHEEK)):    # blush
            k = 0.35 * max(0.0, v["blush"] - 0.15)
            if k > 0.005:
                g = QRadialGradient(cxp, cyp, 0.030)
                g.setColorAt(0.0, col((255, 120, 160), k))
                g.setColorAt(1.0, col((255, 120, 160), 0.0))
                p.setBrush(QBrush(g))
                p.drawEllipse(QPointF(cxp, cyp), 0.030, 0.022)

        bt = t - self._blink_t
        blink = max(0.0, 1.0 - abs(bt - 0.08) / 0.08) if 0 <= bt < 0.16 else 0.0
        # Only a genuinely low mood (sleepy) lowers her lids; a shy or soft one keeps them open.
        sleepy = max(0.0, min(1.0, (0.5 - v["eye"]) / 0.34))
        cover = min(1.0, max(blink, sleepy * 0.95))
        if cover > 0.04:
            lr, lg_, lb = (int(c) for c in self._lid)
            for ex, ey in eyes:
                wr, hr = _EYE_W * 0.62, _EYE_H * 0.80 * cover + 0.0006
                cy = ey - _EYE_H * 0.55 + hr * 0.95
                p.save()
                p.translate(ex, cy)
                p.scale(1.0, hr / wr)                    # a soft-edged lid: opaque centre, feathered rim
                g = QRadialGradient(0, 0, wr)
                g.setColorAt(0.0, QColor(lr, lg_, lb, 255))
                g.setColorAt(0.72, QColor(lr, lg_, lb, 245))
                g.setColorAt(1.0, QColor(lr, lg_, lb, 0))
                p.setBrush(QBrush(g))
                p.drawEllipse(QPointF(0, 0), wr, wr)
                p.restore()
                edge = cy + hr * 0.78
                lash = QPainterPath(QPointF(ex - _EYE_W * 0.52, edge - 0.0012))
                lash.quadTo(QPointF(ex, edge + _EYE_H * 0.28 * min(1.0, cover * 1.3)),
                            QPointF(ex + _EYE_W * 0.52, edge - 0.0012))
                p.strokePath(lash, pen((86, 58, 66), 0.50 * min(1.0, cover * 1.5), 0.0021))

        if v["sad"] > 0.55:                              # tears — single shimmer or double tears in deep grief / moved
            k = min(1.0, (v["sad"] - 0.55) / 0.45)
            tear_rgb = (255, 244, 224) if self.soul.mood == "moved" else (226, 240, 255)
            eye_specs = [(eyes[1], 0.006, 0.0)]
            if v["sad"] > 0.85:
                eye_specs.append((eyes[0], -0.006, 1.45))
            for eye_pt, ox, phase_off in eye_specs:
                f = ((t + phase_off) % 3.4) / 3.4
                x, y = eye_pt[0] + ox, eye_pt[1] + 0.012 + f * 0.085
                drop = QPainterPath(QPointF(x, y - 0.007))
                drop.cubicTo(QPointF(x + 0.005, y), QPointF(x + 0.004, y + 0.005), QPointF(x, y + 0.005))
                drop.cubicTo(QPointF(x - 0.004, y + 0.005), QPointF(x - 0.005, y), QPointF(x, y - 0.007))
                p.fillPath(drop, QBrush(col(tear_rgb, 0.85 * k * (1 - f * 0.5))))

    def _heart_light(self, p, v, pulse) -> None:
        x, y = _HEART
        self._plus(p, True)
        g = QRadialGradient(x, y, 0.20)
        g.setColorAt(0.0, col((255, 170, 200), 0.10 + 0.50 * pulse * (0.5 + 0.5 * v["glow"])))
        g.setColorAt(0.35, col((255, 140, 180), 0.04 + 0.16 * pulse))
        g.setColorAt(1.0, col((255, 140, 180), 0.0))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QBrush(g))
        p.drawEllipse(QPointF(x, y), 0.20, 0.20)
        p.setBrush(Qt.BrushStyle.NoBrush)
        for age in self.heart.ripples:                   # a ring of light per beat
            k = 1.0 - age / 1.6
            r = 0.03 + age * 0.075                       # flat rings that stay around her chest, never across her face
            p.setPen(pen(mix(ROSE, (255, 255, 255), 0.5), 0.42 * k * k, 0.005 * k + 0.0012))
            p.drawEllipse(QPointF(x, y + 0.012), r, r * 0.5)
        self._plus(p, False)

    def _draw_particles(self, p) -> None:
        p.setPen(Qt.PenStyle.NoPen)
        for x, y, _, _, rot, _, s in self._petals:
            p.save()
            p.translate(x, y)
            p.rotate(rot)
            p.setBrush(QBrush(col((255, 214, 228), 0.8)))
            p.drawEllipse(QRectF(-0.011 * s, -0.0065 * s, 0.022 * s, 0.013 * s))
            p.restore()
        self._plus(p, True)
        for x, y, age, life, size in self._sparks:
            k = math.sin(math.pi * age / life)
            sz = size * k
            p.setPen(pen((255, 246, 220), 0.9 * k, 0.0018))
            p.drawLine(QPointF(x - sz, y), QPointF(x + sz, y))
            p.drawLine(QPointF(x, y - sz * 1.4), QPointF(x, y + sz * 1.4))
        self._plus(p, False)
        p.setPen(Qt.PenStyle.NoPen)
        for x, y, _, age in self._hearts:
            k = 1.0 - age / 1.5
            p.fillPath(heart_path(x, y, 0.010 + 0.007 * (1 - k)), QBrush(col((255, 150, 190), 0.85 * k)))

    def _readout(self, p, left, top, w_px, h_px) -> None:
        """♥ BPM, a live heartbeat trace and the mood, in a dark glass panel at the bottom right."""
        pw, ph = 156.0, 68.0
        x, y = left + w_px - pw - 10, top + h_px - ph - 10
        if w_px < 260:
            return
        pulse = self.heart.pulse
        p.save()
        p.setClipping(False)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QBrush(col((8, 10, 24), 0.58)))
        p.drawRoundedRect(QRectF(x, y, pw, ph), 8, 8)
        x += 10
        p.fillPath(heart_path(x + 6, y + 14, 6.2 * (1 + 0.25 * pulse)), QBrush(col(ROSE, 0.8 + 0.2 * pulse)))
        f = QFont()
        f.setPixelSize(13)
        f.setBold(True)
        f.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, 1.5)
        p.setFont(f)
        p.setPen(QPen(col((255, 244, 248), 0.95)))
        p.drawText(QRectF(x + 18, y + 5, pw, 18), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                   f"{self.heart.bpm:.0f} BPM")
        ecg = list(self.heart.ecg)
        if len(ecg) > 2:
            path = QPainterPath()
            y0, wd = y + 44, pw - 20
            for i, e in enumerate(ecg):
                pt = QPointF(x + wd * i / self.heart.ecg.maxlen, y0 - e * 14)
                path.moveTo(pt) if i == 0 else path.lineTo(pt)
            p.strokePath(path, pen(ROSE, 0.9, 1.4))
        f.setPixelSize(9)
        f.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, 2.0)
        p.setFont(f)
        p.setPen(QPen(col(mix(ROSE, (255, 255, 255), 0.5), 0.95)))
        primary_lbl = (self.soul.label or self.soul.mood).upper()
        sec_lbl = str(getattr(self.soul, "secondary", "") or "").upper()
        display_lbl = f"{primary_lbl} · {sec_lbl}" if (sec_lbl and sec_lbl != primary_lbl) else primary_lbl
        p.drawText(QRectF(x, y + 49, pw - 14, 14), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                   display_lbl[:22])
        p.restore()


def make_goddess():
    """The painted goddess if her picture is there, otherwise the drawn one."""
    try:
        return PortraitGoddess()
    except Exception:
        from core.goddess_avatar import GoddessAvatar
        return GoddessAvatar()
