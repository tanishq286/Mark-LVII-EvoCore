"""
core/goddess_face.py — the goddess's head, and the face that carries her emotions.

Drawn in head-local units, with the origin at the centre of the head. The head
is 1.0 tall, so the crown is at y = -0.5 and the chin at +0.5. The caller
translates and rotates the painter, which is how head tilt costs nothing here.

Every expression is a few continuous numbers from core.goddess_soul (smile,
brow lift, inner-brow sadness, eye openness, blush), never a fixed set of
drawn faces. That is why "shy" can melt into "loving" without a pop.
"""

from __future__ import annotations

import math

from PyQt6.QtCore import QPointF, QRectF, Qt
from PyQt6.QtGui import QBrush, QLinearGradient, QPainter, QPainterPath, QRadialGradient

from core.goddess_figure import (GOLD, GOLD_HI, GOLD_SH, HAIR, HAIR_HI, HAIR_SH, LASH, LINE, LIP,
                                 ROSE, SKIN, SKIN_HI, SKIN_SH, col, gem, limb, mix, pen, shape, spline)

IRIS = (128, 156, 232)


def head(p: QPainter, f: dict) -> None:
    face = shape([(0.0, -0.50), (0.29, -0.42), (0.37, -0.12), (0.355, 0.10), (0.27, 0.32),
                  (0.12, 0.47), (0.0, 0.50), (-0.12, 0.47), (-0.27, 0.32), (-0.355, 0.10),
                  (-0.37, -0.12), (-0.29, -0.42)])
    g = QRadialGradient(-0.08, -0.05, 0.62)
    g.setColorAt(0.0, col(SKIN_HI))
    g.setColorAt(0.65, col(SKIN))
    g.setColorAt(1.0, col(SKIN_SH))
    p.fillPath(face, QBrush(g))
    p.strokePath(face, pen(LINE, 0.40, 0.010))

    for s in (-1, 1):                                    # blush, warmed by the mood
        b = QRadialGradient(s * 0.205, 0.17, 0.12)
        b.setColorAt(0.0, col((255, 150, 182), 0.12 + 0.5 * f["blush"]))
        b.setColorAt(1.0, col((255, 150, 182), 0.0))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QBrush(b))
        p.drawEllipse(QPointF(s * 0.205, 0.17), 0.13, 0.10)

    # nose: a whisper of shadow, not an outline, so it never reads as cartoon
    p.strokePath(spline(QPainterPath(), [(0.030, 0.07), (0.036, 0.12), (0.040, 0.165)]), pen(SKIN_SH, 0.40, 0.011))
    p.strokePath(spline(QPainterPath(), [(-0.032, 0.185), (0.0, 0.198), (0.032, 0.185)]), pen(LINE, 0.38, 0.010))

    gx, gy = f["gaze"]
    for s in (-1, 1):
        wink = f["wink"] if s == 1 else 0.0
        _eye(p, s, max(0.0, f["eye"] * (1.0 - wink)), f["smile"], gx, gy + f["down"], f["sad"], f["t"])
        _brow(p, s, f["brow"], f["sad"])
    _mouth(p, f["smile"], f["mouth"])
    if f["sad"] > 0.55:
        _tear(p, f["t"], (f["sad"] - 0.55) / 0.45)


def _eye(p, s, open_, smile, gx, gy, sad, t) -> None:
    ex, ey, w = s * 0.148, 0.02, 0.090
    inner, outer = (ex - s * w, ey + 0.006), (ex + s * w, ey - 0.010 - 0.006 * smile)
    h = 0.058 * min(1.2, open_)
    if h < 0.007:
        # Closed. A happy close curves up like a smile; a sleepy one droops.
        bow = 0.022 if smile > 0.4 else -0.010
        path = spline(QPainterPath(), [inner, (ex, ey + bow), outer])
        p.strokePath(path, pen(LASH, 0.85, 0.014))
        return
    lower_lift = 0.6 * max(0.0, smile) * 0.035
    up = QPainterPath(QPointF(*inner))
    up.cubicTo(QPointF(ex - s * w * 0.55, ey - h * 1.25 - sad * 0.004),
               QPointF(ex + s * w * 0.45, ey - h * 1.20 + sad * 0.012), QPointF(*outer))
    eye = QPainterPath(up)
    eye.cubicTo(QPointF(ex + s * w * 0.40, ey + h * 0.85 - lower_lift),
                QPointF(ex - s * w * 0.50, ey + h * 0.80 - lower_lift), QPointF(*inner))
    p.fillPath(eye, QBrush(col((250, 251, 255))))
    p.save()
    p.setClipPath(eye)
    ix, iy, r = ex + gx * 0.028, ey + gy * 0.018 + 0.004, 0.041
    ig = QRadialGradient(ix, iy, r)
    ig.setColorAt(0.0, col(mix(IRIS, (255, 255, 255), 0.55)))
    ig.setColorAt(0.7, col(IRIS))
    ig.setColorAt(1.0, col(mix(IRIS, LASH, 0.6)))
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QBrush(ig))
    p.drawEllipse(QPointF(ix, iy), r, r)
    p.setBrush(QBrush(col((40, 34, 64))))
    p.drawEllipse(QPointF(ix, iy), r * 0.38, r * 0.38)
    p.setBrush(QBrush(col((255, 255, 255), 0.95)))
    p.drawEllipse(QPointF(ix - r * 0.35, iy - r * 0.40), r * 0.22, r * 0.22)
    p.setBrush(QBrush(col((255, 255, 255), 0.5 + 0.3 * math.sin(t * 1.3))))
    p.drawEllipse(QPointF(ix + r * 0.35, iy + r * 0.30), r * 0.10, r * 0.10)
    shade = QLinearGradient(0, ey - h * 1.2, 0, ey)            # the lid's soft shadow on the eye
    shade.setColorAt(0.0, col(LINE, 0.35))
    shade.setColorAt(1.0, col(LINE, 0.0))
    p.fillPath(eye, QBrush(shade))
    p.restore()
    p.strokePath(up, pen(LASH, 0.92, 0.017))
    p.setPen(pen(LASH, 0.85, 0.010))                         # lashes flick up at the outer corner
    for k in range(3):
        a = 0.35 + k * 0.28
        p.drawLine(QPointF(outer[0] - s * 0.012 * k, outer[1] - 0.002 * k),
                   QPointF(outer[0] + s * (0.032 - 0.008 * k) * math.cos(a), outer[1] - 0.030 * math.sin(a + 0.4)))
    crease = spline(QPainterPath(), [(ex - s * w * 0.75, ey - h * 1.05 - 0.016),
                                     (ex, ey - h * 1.55 - 0.020), (ex + s * w * 0.85, ey - h * 0.9 - 0.022)])
    p.strokePath(crease, pen(SKIN_SH, 0.55, 0.009))
    low = spline(QPainterPath(), [(ex - s * w * 0.75, ey + h * 0.6 - lower_lift),
                                  (ex, ey + h * 0.92 - lower_lift), (ex + s * w * 0.85, ey + h * 0.3)])
    p.strokePath(low, pen(LINE, 0.25, 0.007))


def _brow(p, s, lift, sad) -> None:
    by = -0.118 - lift * 0.034
    knit = max(0.0, -lift) * 0.028
    inner = (s * 0.065, by + 0.012 - sad * 0.036 + knit)
    peak = (s * 0.165, by - 0.016 + sad * 0.006)
    outer = (s * 0.245, by + 0.014 + sad * 0.022)
    p.strokePath(spline(QPainterPath(), [inner, peak, outer]), pen(mix(LASH, GOLD_SH, 0.45), 0.75, 0.017))


def _mouth(p, smile, open_) -> None:
    cy = 0.300
    hw = 0.074 + 0.014 * max(0.0, smile)
    corner = cy - smile * 0.024
    gap = max(0.0, open_) * 0.062
    lc, rc = (-hw, corner), (hw, corner)
    upper = QPainterPath(QPointF(*lc))                     # cupid's bow
    upper.cubicTo(QPointF(-hw * 0.6, cy - 0.022), QPointF(-0.026, cy - 0.032), QPointF(0.0, cy - 0.020))
    upper.cubicTo(QPointF(0.026, cy - 0.032), QPointF(hw * 0.6, cy - 0.022), QPointF(*rc))
    seam_y = cy + 0.002 - smile * 0.010
    upper.cubicTo(QPointF(hw * 0.4, seam_y + gap * 0.15), QPointF(-hw * 0.4, seam_y + gap * 0.15), QPointF(*lc))
    lower = QPainterPath(QPointF(*lc))
    lower.cubicTo(QPointF(-hw * 0.4, seam_y + gap), QPointF(hw * 0.4, seam_y + gap), QPointF(*rc))
    lower.cubicTo(QPointF(hw * 0.55, cy + 0.048 + gap), QPointF(-hw * 0.55, cy + 0.048 + gap), QPointF(*lc))
    if gap > 0.004:
        inside = QPainterPath(QPointF(*lc))
        inside.cubicTo(QPointF(-hw * 0.4, seam_y), QPointF(hw * 0.4, seam_y), QPointF(*rc))
        inside.cubicTo(QPointF(hw * 0.4, seam_y + gap), QPointF(-hw * 0.4, seam_y + gap), QPointF(*lc))
        p.fillPath(inside, QBrush(col((112, 48, 70))))
        p.save()
        p.setClipPath(inside)
        p.fillRect(QRectF(-hw * 0.6, seam_y, hw * 1.2, gap * 0.32), col((252, 250, 252)))
        p.restore()
    lg = QLinearGradient(0, cy - 0.03, 0, cy + 0.05 + gap)
    lg.setColorAt(0.0, col(mix(LIP, ROSE, 0.2)))
    lg.setColorAt(1.0, col(mix(LIP, (255, 255, 255), 0.15)))
    p.fillPath(upper, QBrush(lg))
    p.fillPath(lower, QBrush(lg))
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QBrush(col((255, 255, 255), 0.55)))
    p.drawEllipse(QPointF(0.012, cy + 0.028 + gap), 0.022, 0.007)
    p.strokePath(upper, pen(mix(LIP, LASH, 0.5), 0.35, 0.006))
    if smile > 0.55:                                       # dimples
        for s in (-1, 1):
            p.strokePath(spline(QPainterPath(), [(s * (hw + 0.012), corner - 0.016), (s * (hw + 0.020), corner + 0.004)]),
                         pen(SKIN_SH, (smile - 0.55) * 1.2, 0.008))


def _tear(p, t, k) -> None:
    f = (t % 3.2) / 3.2
    x, y = 0.205 - 0.01 * f, 0.07 + f * 0.30
    drop = QPainterPath(QPointF(x, y - 0.03))
    drop.cubicTo(QPointF(x + 0.018, y), QPointF(x + 0.014, y + 0.018), QPointF(x, y + 0.018))
    drop.cubicTo(QPointF(x - 0.014, y + 0.018), QPointF(x - 0.018, y), QPointF(x, y - 0.03))
    p.fillPath(drop, QBrush(col((220, 236, 255), 0.75 * k * (1 - f * 0.6))))
    p.strokePath(drop, pen((255, 255, 255), 0.8 * k, 0.004))


def hair_cap(p: QPainter, t: float) -> None:
    cap = shape([(-0.40, 0.26), (-0.43, -0.20), (-0.28, -0.47), (0.0, -0.56), (0.28, -0.47),
                 (0.43, -0.20), (0.40, 0.26), (0.345, 0.02), (0.30, -0.14), (0.18, -0.27),
                 (0.03, -0.33), (-0.12, -0.27), (-0.28, -0.15), (-0.345, 0.02)])
    g = QLinearGradient(-0.4, -0.55, 0.4, 0.05)
    g.setColorAt(0.0, col(HAIR_HI))
    g.setColorAt(0.6, col(HAIR))
    g.setColorAt(1.0, col(HAIR_SH))
    p.fillPath(cap, QBrush(g))
    for i in range(9):                                     # strands sweeping from the parting
        f = i / 8.0
        x = -0.36 + 0.72 * f
        p.strokePath(spline(QPainterPath(), [(0.03, -0.32), (x * 0.6, -0.42 + 0.06 * abs(x)), (x, -0.20 + 0.15 * abs(x))]),
                     pen(HAIR_SH if i % 3 else GOLD, 0.45, 0.008))


def tiara(p: QPainter, pulse: float) -> None:
    arc = spline(QPainterPath(), [(-0.36, -0.20), (-0.20, -0.36), (0.0, -0.41), (0.20, -0.36), (0.36, -0.20)])
    p.strokePath(arc, pen(GOLD_SH, 0.9, 0.030))
    p.strokePath(arc, pen(GOLD, 1.0, 0.018))
    for i in range(-5, 6):                                 # laurel leaves
        if i == 0:
            continue
        a = i / 5.0
        x, y = a * 0.33, -0.41 + 0.20 * a * a
        p.save()
        p.translate(x, y)
        p.rotate(math.degrees(math.atan(0.40 * a)) + (25 if i > 0 else -25))
        leaf = QPainterPath()
        leaf.addEllipse(QRectF(-0.035, -0.012, 0.07, 0.024))
        p.fillPath(leaf, QBrush(col(GOLD_HI if i % 2 else GOLD)))
        p.restore()
    gem(p, 0.0, -0.42, 0.034, pulse)


def earrings(p: QPainter, t: float, pulse: float) -> None:
    for s in (-1, 1):
        sw = 0.012 * math.sin(t * 1.8 + s)
        p.setPen(pen(GOLD, 0.9, 0.007))
        p.drawLine(QPointF(s * 0.36, 0.10), QPointF(s * 0.36 + sw, 0.20))
        p.setPen(pen(GOLD_SH, 0.9, 0.006))
        p.setBrush(QBrush(col(mix(GOLD_HI, ROSE, 0.15 * pulse))))
        p.drawEllipse(QPointF(s * 0.36 + sw, 0.225), 0.018, 0.028)


def front_lock(p: QPainter, root, t: float, wind: float, side: int) -> None:
    """A lock of hair falling forward over the shoulder; the wind carries the other one back."""
    x0, y0 = root
    # Three overlapping S-waved locks per side: the outer one longest. The
    # wind lifts the right-hand side so it falls shorter, behind the shoulder.
    for j, (dx, reach, wid) in enumerate(((0.05, 2.55, 0.21), (-0.04, 2.15, 0.17))):
        if side > 0:
            reach = 1.45 + 0.25 * j
        pts, ws = [], []
        for i in range(9):
            f = i / 8.0
            y = y0 + f * (reach - y0)
            bulge = side * (0.05 + 0.13 * math.sin(f * math.pi * 0.85))
            wave = 0.035 * math.sin(f * math.pi * 2.6 + t * 1.25 + j * 1.3 + side) * f
            pts.append((x0 + side * dx + bulge + wave + 0.10 * f * wind, y))
            ws.append(0.012 + wid * (1.0 - f) ** 0.85)       # full at the root, a fine point at the tip
        path = limb(pts, ws)
        g = QLinearGradient(x0 - 0.25, 0, x0 + 0.25, 0)
        g.setColorAt(0.0, col(HAIR_SH))
        g.setColorAt(0.5, col(HAIR_HI))
        g.setColorAt(1.0, col(HAIR))
        p.fillPath(path, QBrush(g))
        p.strokePath(path, pen(HAIR_SH, 0.22, 0.006))
        for k, off in enumerate((-0.025, 0.0, 0.03)):
            p.strokePath(spline(QPainterPath(), [(x + off * (1 - i / 8), y) for i, (x, y) in enumerate(pts[:-1])]),
                         pen(GOLD if k == 2 else HAIR_SH, 0.35, 0.006))


def necklace(p: QPainter, br: float, pulse: float) -> None:
    for dip, w in ((1.58, 0.012), (1.86, 0.009)):
        p.strokePath(spline(QPainterPath(), [(-0.15, 1.17 - br), (-0.10, dip - 0.12), (0.0, dip),
                                             (0.10, dip - 0.12), (0.15, 1.17 - br)]), pen(GOLD, 0.95, w))
    x, y, r = 0.0, 1.90, 0.050                              # sun pendant
    p.setPen(pen(GOLD, 0.95, 0.008))
    for i in range(12):
        a = i * math.pi / 6
        p.drawLine(QPointF(x + math.cos(a) * r, y + math.sin(a) * r),
                   QPointF(x + math.cos(a) * r * (1.55 + 0.25 * pulse), y + math.sin(a) * r * (1.55 + 0.25 * pulse)))
    gem(p, x, y, r * 0.75, pulse)
