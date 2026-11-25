"""
core/goddess_figure.py — the drawing of the goddess: body, gown, hair, face.

Everything here is drawn in *figure units*: 1.0 is the height of her head,
x = 0 is her centre line, and y runs down from the crown (0) to the hem (~8).
The caller sets up the painter's transform, so these functions never see a
pixel and the figure scales cleanly to any HUD size.

The palette is porcelain and pearl: white skin with lilac marble shadows, a
white gown with an opal sheen, platinum hair, and gold for the jewellery.
Colour only comes in where it means something: rose for the heart, blush and
lips, and the iris tint.
"""

from __future__ import annotations

import math

from PyQt6.QtCore import QPointF, QRectF, Qt
from PyQt6.QtGui import QBrush, QColor, QLinearGradient, QPainter, QPainterPath, QPen, QRadialGradient


def col(rgb, a: float = 1.0) -> QColor:
    c = QColor(int(rgb[0]), int(rgb[1]), int(rgb[2]))
    c.setAlphaF(max(0.0, min(1.0, a)))
    return c


def mix(a, b, t: float):
    return tuple(x + (y - x) * t for x, y in zip(a, b))


SKIN_HI, SKIN, SKIN_SH = (255, 253, 253), (246, 240, 246), (212, 202, 226)
GOWN_HI, GOWN, GOWN_SH = (255, 255, 255), (244, 242, 252), (206, 202, 228)
HAIR_HI, HAIR, HAIR_SH = (255, 254, 250), (240, 236, 242), (198, 190, 212)
GOLD_HI, GOLD, GOLD_SH = (255, 238, 178), (232, 196, 118), (168, 126, 58)
LINE, LASH = (150, 136, 172), (96, 78, 104)
ROSE, LIP = (255, 120, 160), (222, 128, 152)
OPAL = ((255, 214, 236), (208, 248, 238), (224, 212, 255), (255, 240, 205))


def pen(rgb, a: float, w: float) -> QPen:
    p = QPen(col(rgb, a), w)
    p.setCapStyle(Qt.PenCapStyle.RoundCap)
    p.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    return p


# ── geometry helpers ─────────────────────────────────────────────────────────
def spline(path: QPainterPath, pts, closed=False, move=True) -> QPainterPath:
    """Catmull-Rom through `pts` as cubic Béziers: organic curves from a few points."""
    n = len(pts)
    if move:
        path.moveTo(QPointF(*pts[0]))
    else:
        path.lineTo(QPointF(*pts[0]))
    for i in range(n if closed else n - 1):
        p0 = pts[i - 1] if (i > 0 or closed) else pts[i]
        p1, p2 = pts[i], pts[(i + 1) % n]
        p3 = pts[(i + 2) % n] if (i + 2 < n or closed) else p2
        path.cubicTo(QPointF(p1[0] + (p2[0] - p0[0]) / 6, p1[1] + (p2[1] - p0[1]) / 6),
                     QPointF(p2[0] - (p3[0] - p1[0]) / 6, p2[1] - (p3[1] - p1[1]) / 6),
                     QPointF(*p2))
    if closed:
        path.closeSubpath()
    return path


def shape(pts) -> QPainterPath:
    return spline(QPainterPath(), pts, closed=True)


def limb(pts, widths) -> QPainterPath:
    """A smooth tapered body along a polyline: arms, a leg, a lock of hair."""
    n = len(pts)
    left, right = [], []
    for i, (x, y) in enumerate(pts):
        a, b = pts[max(0, i - 1)], pts[min(n - 1, i + 1)]
        dx, dy = b[0] - a[0], b[1] - a[1]
        ln = math.hypot(dx, dy) or 1.0
        nx, ny, w = -dy / ln, dx / ln, widths[i] / 2
        left.append((x + nx * w, y + ny * w))
        right.append((x - nx * w, y - ny * w))
    path = spline(QPainterPath(), left)

    def cap(i, j):
        """Rounded end: a control point pushed out past the tip along the limb."""
        tx, ty = pts[i][0] - pts[j][0], pts[i][1] - pts[j][1]
        ln = math.hypot(tx, ty) or 1.0
        w = widths[i] * 0.62
        return QPointF(pts[i][0] + tx / ln * w, pts[i][1] + ty / ln * w)

    path.quadTo(cap(n - 1, n - 2), QPointF(*right[-1]))
    spline(path, right[::-1], move=False)
    path.quadTo(cap(0, 1), QPointF(*left[0]))
    path.closeSubpath()
    return path


def ik(s, t, l1, l2, bend):
    """Two-bone IK: elbow and hand for shoulder `s` reaching toward `t`."""
    dx, dy = t[0] - s[0], t[1] - s[1]
    d = max(abs(l1 - l2) + 1e-3, min(l1 + l2 - 1e-3, math.hypot(dx, dy)))
    a = math.atan2(dy, dx)
    b = math.acos(max(-1.0, min(1.0, (l1 * l1 + d * d - l2 * l2) / (2 * l1 * d))))
    e = (s[0] + math.cos(a + bend * b) * l1, s[1] + math.sin(a + bend * b) * l1)
    return e, (s[0] + math.cos(a) * d, s[1] + math.sin(a) * d)


def heart_path(x, y, s) -> QPainterPath:
    p = QPainterPath(QPointF(x, y + s * 0.95))
    p.cubicTo(QPointF(x - s * 1.25, y + s * 0.15), QPointF(x - s * 0.95, y - s * 0.95), QPointF(x, y - s * 0.35))
    p.cubicTo(QPointF(x + s * 0.95, y - s * 0.95), QPointF(x + s * 1.25, y + s * 0.15), QPointF(x, y + s * 0.95))
    return p


def _hgrad(x0, x1, c_edge, c_mid, a=1.0) -> QLinearGradient:
    g = QLinearGradient(x0, 0, x1, 0)
    g.setColorAt(0.0, col(c_edge, a))
    g.setColorAt(0.5, col(c_mid, a))
    g.setColorAt(1.0, col(c_edge, a))
    return g


def opal(p: QPainter, path: QPainterPath, t: float, y0: float, y1: float, a: float) -> None:
    """Lay an iridescent pearl sheen over `path`. The bands drift slowly, like moving light on silk."""
    g = QLinearGradient(-2.0, y0, 2.0, y1)
    off = (t * 0.035) % 1.0
    for i in range(9):
        f = (i / 8.0 + off) % 1.0
        g.setColorAt(f, col(OPAL[i % 4], a if i % 2 else 0.0))
    p.fillPath(path, QBrush(g))


# ── parts, back to front ─────────────────────────────────────────────────────
def cape(p: QPainter, t: float, wind: float) -> None:
    w = lambda y, ph: 0.13 * math.sin(t * 0.8 + y * 0.9 + ph) * wind  # noqa: E731
    pts = [(-0.60, 1.42), (-1.20 + w(2.6, 0), 2.6), (-1.70 + w(4.8, 0), 4.8),
           (-1.95 + w(7.0, 0), 7.0), (-1.70, 8.05)]
    pts += [(x, 8.08 + 0.05 * math.sin(t * 1.4 + x * 2.0)) for x in (-0.9, 0.0, 0.9)]
    pts += [(1.85, 8.10), (2.05 + w(7.0, 1), 7.0), (1.85 + w(4.8, 1), 4.8),
            (1.30 + w(2.6, 1), 2.6), (0.60, 1.42), (0.0, 1.30)]
    path = shape(pts)
    g = QLinearGradient(0, 1.3, 0, 8.1)
    g.setColorAt(0.0, col(GOWN_HI, 0.08))
    g.setColorAt(1.0, col(GOWN_HI, 0.24))
    p.fillPath(path, QBrush(g))
    p.strokePath(path, pen(GOWN_HI, 0.30, 0.010))


def back_hair(p: QPainter, hx: float, t: float, wind: float) -> None:
    # Long, voluminous waves to the hips, lifted toward the viewer's right by
    # the wind. The edges scallop in and out so the outline reads as curls,
    # not a sheet.
    ys = [0.45 + i * 0.22 for i in range(15)]            # 0.45 .. 3.53

    def side(sgn, ph):
        out = []
        for i, y in enumerate(ys):
            k = (y - 0.45) / 3.1
            curl = 0.07 * math.sin(y * 5.2 + t * 1.2 + ph) * min(1.0, k * 3)
            out.append((hx + sgn * (0.44 + 0.62 * math.sin(min(1.0, k * 1.3) * 1.45)) + curl
                        + 0.34 * k * k * wind + 0.03 * math.sin(t * 0.8 + ph), y))
        return out
    right, left = side(1, 0.0), side(-1, 1.7)
    tips = [(hx + x + 0.34 * wind + 0.05 * math.sin(t * 1.3 + x * 4),
             3.62 + 0.14 * math.cos(x * 6.0 + t)) for x in (0.7, 0.35, 0.0, -0.35, -0.7)]
    pts = [(hx - 0.38, 0.16), (hx, -0.10), (hx + 0.38, 0.16)] + right + tips + left[::-1]
    path = shape(pts)
    g = QLinearGradient(0, 0.0, 0, 3.7)
    g.setColorAt(0.0, col(HAIR_HI))
    g.setColorAt(0.55, col(HAIR))
    g.setColorAt(1.0, col(HAIR_SH))
    p.fillPath(path, QBrush(g))
    p.fillPath(path, QBrush(_hgrad(hx - 1.2, hx + 1.4, (255, 255, 255), HAIR_SH, 0.35)))
    for i in range(22):                       # strands, every fourth a gold glint
        f = i / 21.0 - 0.5
        ph = i * 0.77
        sp = []
        for y in ys[::2]:
            k = (y - 0.45) / 3.1
            sp.append((hx + f * 2.0 * (0.40 + 0.60 * math.sin(min(1.0, k * 1.3) * 1.45))
                       + 0.05 * math.sin(y * 5.2 + t * 1.2 + ph) + 0.34 * k * k * wind, y))
        glint = i % 4 == 0
        p.strokePath(spline(QPainterPath(), sp),
                     pen(GOLD if glint else HAIR_SH, 0.35 if glint else 0.45, 0.010 if glint else 0.008))


def skirt(p: QPainter, t: float, sway: float, wind: float, leg_fn) -> None:
    hem = lambda x, ph=0.0: 7.96 + 0.045 * math.sin(t * 1.6 + x * 3.1 + ph) + 0.03 * max(0.0, x)  # noqa: E731
    wl = 0.06 * math.sin(t * 0.9) * wind
    wr = 0.09 * math.sin(t * 0.9 + 0.8) * wind
    hip = 0.86
    back = [(-0.39, 2.80), (-hip + sway, 3.45), (-0.84, 4.4), (-0.98, 5.6), (-1.16 + wl, 7.0), (-1.32, hem(-1.32))]
    back += [(x, hem(x)) for x in (-0.6, 0.2, 0.9)]
    back += [(1.48, hem(1.48)), (1.22 + wr, 7.0), (0.99, 5.6), (0.86, 4.4), (hip + sway, 3.45), (0.39, 2.80)]
    p.fillPath(shape(back), QBrush(col(mix(GOWN_SH, GOWN, 0.25))))   # the gown's shadowed inside
    leg_fn()
    # Front panels: A wraps the outer thigh, B is the main fall; the slit between
    # them is where the leg shows.
    a = [(-0.39, 2.80), (-hip + sway, 3.45), (-0.85, 4.4), (-0.99, 5.6), (-1.17 + wl, 7.0), (-1.31, hem(-1.31)),
         (-0.82, hem(-0.82, 1.0)), (-0.72, 6.9), (-0.60, 5.8), (-0.42, 4.9), (-0.22, 4.25), (-0.30, 3.6)]
    b = [(-0.39, 2.80), (-0.30, 3.6), (-0.22, 4.25), (-0.12, 5.2), (-0.06 + wl * 0.5, 6.6),
         (-0.02, hem(-0.02))] + [(x, hem(x)) for x in (0.5, 1.05)]
    b += [(1.47, hem(1.47)), (1.21 + wr, 7.0), (0.98, 5.6), (0.85, 4.4), (hip + sway, 3.45), (0.39, 2.80)]
    for pts in (a, b):
        path = shape(pts)
        p.fillPath(path, QBrush(_hgrad(-1.4, 1.5, mix(GOWN_SH, GOWN, 0.35), GOWN_HI)))
        if pts is b:                          # the sheen only where it catches the eye; it is the costliest fill
            opal(p, path, t, 3.0, 8.0, 0.20)
        p.strokePath(path, pen(LINE, 0.28, 0.010))
    # folds: soft shadow lines and highlight streaks that sway with the hem
    for i, x0 in enumerate((-0.62, -0.36, 0.08, 0.30, 0.55, 0.78)):
        x1 = x0 * 1.75 + 0.05 * math.sin(t * 1.1 + i)
        bend = 0.07 * math.sin(t * 0.9 + i * 1.7) * wind
        fold = spline(QPainterPath(), [(x0 * 0.55, 3.2), (x0 * 1.05 + bend, 4.6),
                                       (x0 * 1.3 - bend, 6.1), (x1, 7.85)])
        if -0.66 < x1 < -0.02 and x0 < 0:
            continue                          # no folds across the slit
        p.strokePath(fold, pen(GOWN_SH, 0.35, 0.028))
        p.strokePath(fold.translated(0.04, 0), pen(GOWN_HI, 0.55, 0.012))


def leg(p: QPainter, t: float, sway: float) -> None:
    pts = [(-0.30 + sway, 4.1), (-0.33, 4.7), (-0.36, 5.4), (-0.37, 5.7), (-0.39, 6.25), (-0.40, 6.9), (-0.41, 7.5)]
    path = limb(pts, [0.37, 0.33, 0.25, 0.20, 0.235, 0.17, 0.105])
    p.fillPath(path, QBrush(_hgrad(-0.58, -0.18, SKIN_SH, SKIN_HI)))
    p.strokePath(path, pen(LINE, 0.35, 0.010))
    foot = QPainterPath()
    foot.addEllipse(QRectF(-0.53, 7.58, 0.20, 0.11))
    p.fillPath(foot, QBrush(col(SKIN)))
    p.strokePath(foot, pen(LINE, 0.30, 0.008))
    for y in (7.40, 7.50, 7.60):              # gold sandal straps criss-crossing the ankle
        p.setPen(pen(GOLD, 0.9, 0.014))
        p.drawLine(QPointF(-0.47, y), QPointF(-0.35, y + 0.06))
        p.drawLine(QPointF(-0.35, y), QPointF(-0.47, y + 0.06))


def torso(p: QPainter, br: float, sway: float) -> None:
    # Neck, shoulders and chest only: the bodice and skirt own everything from
    # the bust down, so skin drawn lower would show as a box over the hips.
    def side(s):
        return [(s * 0.115, 0.90), (s * 0.13, 1.12), (s * 0.20, 1.24 - br), (s * 0.46, 1.32 - br),
                (s * 0.74, 1.43 - br), (s * 0.70, 1.64), (s * 0.60, 1.86), (s * 0.60, 2.3)]
    path = shape(side(1) + [(0.0, 2.4)] + side(-1)[::-1])
    p.fillPath(path, QBrush(_hgrad(-0.8, 0.8, SKIN_SH, SKIN_HI)))
    p.strokePath(path, pen(LINE, 0.35, 0.011))
    shadow = QLinearGradient(0, 0.92, 0, 1.16)          # the chin's shadow on the neck
    shadow.setColorAt(0.0, col(SKIN_SH, 0.85))
    shadow.setColorAt(1.0, col(SKIN_SH, 0.0))
    p.fillRect(QRectF(-0.13, 0.92, 0.26, 0.24), QBrush(shadow))
    for s in (1, -1):                          # neck tendons and collarbones
        p.strokePath(spline(QPainterPath(), [(s * 0.08, 0.98), (s * 0.07, 1.12), (s * 0.03, 1.24)]),
                     pen(SKIN_SH, 0.45, 0.010))
        p.strokePath(spline(QPainterPath(), [(s * 0.05, 1.31), (s * 0.20, 1.27), (s * 0.40, 1.31)]),
                     pen(SKIN_SH, 0.6, 0.014))


def bodice(p: QPainter, t: float, br: float) -> None:
    def side(s):
        return [(s * 0.36, 1.30 - br), (s * 0.50, 1.34 - br), (s * 0.61, 1.86), (s * 0.645, 2.08),
                (s * 0.53, 2.40), (s * 0.395, 2.78)]
    # The draped V-neck runs strap to strap, then the outline goes down the
    # right side, across the waist and back up the left.
    path = QPainterPath(QPointF(-0.36, 1.30 - br))
    path.cubicTo(QPointF(-0.30, 1.75), QPointF(-0.09, 2.14), QPointF(0.0, 2.30))
    path.cubicTo(QPointF(0.09, 2.14), QPointF(0.30, 1.75), QPointF(0.36, 1.30 - br))
    spline(path, side(1)[1:] + [(0.0, 2.84)] + side(-1)[::-1], move=False)
    path.closeSubpath()
    p.fillPath(path, QBrush(_hgrad(-0.7, 0.7, mix(GOWN_SH, GOWN, 0.4), GOWN_HI)))
    for s in (1, -1):                          # the soft volume of the drape over the bust
        g = QRadialGradient(s * 0.30, 1.96, 0.30)
        g.setColorAt(0.0, col(GOWN_HI, 0.85))
        g.setColorAt(1.0, col(GOWN_HI, 0.0))
        p.fillPath(path, QBrush(g))
        p.strokePath(spline(QPainterPath(), [(s * 0.07, 2.27), (s * 0.30, 2.33), (s * 0.55, 2.22)]),
                     pen(GOWN_SH, 0.45, 0.020))
        for k in range(4):                     # Grecian drape folds sweeping to the waist
            f = k / 3.0
            p.strokePath(spline(QPainterPath(), [(s * (0.40 + 0.08 * f), 1.35), (s * (0.36 + 0.1 * f), 1.95 + 0.08 * f),
                                                 (s * (0.10 + 0.08 * f), 2.70)]), pen(GOWN_SH, 0.28, 0.010))
    opal(p, path, t, 1.3, 2.9, 0.22)
    p.strokePath(path, pen(LINE, 0.35, 0.011))
    p.strokePath(spline(QPainterPath(), [(-0.04, 2.12), (0.0, 2.28), (0.04, 2.12)]), pen(SKIN_SH, 0.35, 0.012))


def belt(p: QPainter, pulse: float) -> None:
    path = shape([(-0.425, 2.69), (0.0, 2.73), (0.425, 2.69), (0.41, 2.86), (0.0, 2.90), (-0.41, 2.86)])
    g = QLinearGradient(0, 2.69, 0, 2.90)
    g.setColorAt(0.0, col(GOLD_HI))
    g.setColorAt(0.5, col(GOLD))
    g.setColorAt(1.0, col(GOLD_SH))
    p.fillPath(path, QBrush(g))
    p.setPen(pen(GOLD_SH, 0.8, 0.008))
    for i in range(-4, 5):
        x = i * 0.085
        p.drawLine(QPointF(x - 0.025, 2.79), QPointF(x, 2.75))
        p.drawLine(QPointF(x, 2.75), QPointF(x + 0.025, 2.79))
    for i, x in enumerate((-0.16, 0.0, 0.16)):  # hanging gold chains
        p.strokePath(spline(QPainterPath(), [(x, 2.88), (x * 1.1, 3.25 + 0.1 * (i == 1)), (x * 1.2, 3.6 + 0.2 * (i == 1))]),
                     pen(GOLD, 0.75, 0.010))
    gem(p, 0.0, 2.80, 0.065, pulse)


def gem(p: QPainter, x: float, y: float, r: float, pulse: float) -> None:
    halo = QRadialGradient(x, y, r * 3.2)
    halo.setColorAt(0.0, col(ROSE, 0.30 + 0.45 * pulse))
    halo.setColorAt(1.0, col(ROSE, 0.0))
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QBrush(halo))
    p.drawEllipse(QPointF(x, y), r * 3.2, r * 3.2)
    g = QRadialGradient(x - r * 0.3, y - r * 0.3, r * 1.2)
    g.setColorAt(0.0, col((255, 255, 255)))
    g.setColorAt(1.0, col(mix(ROSE, (255, 255, 255), 0.35 - 0.3 * pulse)))
    p.setBrush(QBrush(g))
    p.setPen(pen(GOLD, 1.0, r * 0.35))
    p.drawEllipse(QPointF(x, y), r, r)


def arm(p: QPainter, sh, target, bend, elbow=None, pin: float = 0.0) -> tuple:
    """Draw an arm reaching for `target`; returns (elbow, hand, forearm angle).

    A flat drawing cannot foreshorten, so a hand drawn back to the chest by
    plain IK throws the elbow far out sideways. `elbow` with `pin` 0..1 pulls
    the elbow toward a hand-placed spot and puts the hand right on the target,
    which reads as the forearm turning toward the viewer."""
    el, hd = ik(sh, target, 1.32, 1.12, bend)
    if elbow is not None and pin > 0.0:
        el = (el[0] + (elbow[0] - el[0]) * pin, el[1] + (elbow[1] - el[1]) * pin)
        hd = (hd[0] + (target[0] - hd[0]) * pin, hd[1] + (target[1] - hd[1]) * pin)
    out = 1.0 if sh[0] > 0 else -1.0
    # A slight outward swell at the deltoid and a softer one along the forearm
    # keep the arm from reading as a tube.
    mid1 = ((sh[0] + el[0]) / 2 + out * 0.015, (sh[1] + el[1]) / 2)
    q1 = (sh[0] + (el[0] - sh[0]) * 0.2 + out * 0.02, sh[1] + (el[1] - sh[1]) * 0.2)
    mid2 = ((el[0] + hd[0]) / 2, (el[1] + hd[1]) / 2)
    path = limb([sh, q1, mid1, el, mid2, hd], [0.20, 0.205, 0.165, 0.12, 0.11, 0.075])
    lo, hi = min(sh[0], hd[0]) - 0.2, max(sh[0], hd[0]) + 0.2
    p.fillPath(path, QBrush(_hgrad(lo, hi, SKIN_SH, SKIN_HI)))
    p.strokePath(path, pen(LINE, 0.38, 0.010))
    ang = math.atan2(hd[1] - el[1], hd[0] - el[0])
    # gold armlet on the upper arm and a bracelet at the wrist
    for (a, b, f, w) in ((sh, el, 0.42, 0.17), (el, hd, 0.88, 0.11)):
        cx, cy = a[0] + (b[0] - a[0]) * f, a[1] + (b[1] - a[1]) * f
        ux, uy = b[0] - a[0], b[1] - a[1]
        ln = math.hypot(ux, uy) or 1.0
        nx, ny = -uy / ln * w / 2, ux / ln * w / 2
        p.setPen(pen(GOLD, 0.95, 0.026))
        p.drawLine(QPointF(cx - nx, cy - ny), QPointF(cx + nx, cy + ny))
    p.save()
    p.translate(hd[0], hd[1])
    p.rotate(math.degrees(ang))
    hand = QPainterPath()
    hand.addEllipse(QRectF(-0.02, -0.055, 0.17, 0.11))
    p.fillPath(hand, QBrush(col(SKIN)))
    p.strokePath(hand, pen(LINE, 0.35, 0.008))
    p.setPen(pen(SKIN_SH, 0.6, 0.007))
    for k in (-0.025, 0.0, 0.025):
        p.drawLine(QPointF(0.09, k), QPointF(0.15, k * 1.2))
    p.restore()
    return el, hd, ang


def rose(p: QPainter, x: float, y: float, pulse: float, s: float = 0.11) -> None:
    p.strokePath(spline(QPainterPath(), [(x, y), (x + 0.04, y + 0.25), (x + 0.02, y + 0.55)]), pen(mix(GOLD, (160, 200, 160), 0.5), 0.9, 0.014))
    glow = QRadialGradient(x, y, s * 2.4)
    glow.setColorAt(0.0, col(ROSE, 0.25 + 0.35 * pulse))
    glow.setColorAt(1.0, col(ROSE, 0.0))
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QBrush(glow))
    p.drawEllipse(QPointF(x, y), s * 2.4, s * 2.4)
    for i in range(7):                         # petals spiralling in to a blushing heart
        a = i * 2.4
        r = s * (1.0 - i * 0.12)
        g = QRadialGradient(x, y, r)
        g.setColorAt(0.0, col(mix(ROSE, (255, 255, 255), 0.3 + i * 0.05)))
        g.setColorAt(1.0, col((255, 250, 252)))
        p.setBrush(QBrush(g))
        p.setPen(pen(mix(ROSE, LINE, 0.5), 0.35, 0.006))
        p.drawEllipse(QPointF(x + math.cos(a) * r * 0.18, y + math.sin(a) * r * 0.18), r * 0.95, r * 0.8)
