"""
core/goddess_warp.py — live expressions for a painted face.

A painting cannot change its expression, so we move the paint. Each emotion is
a small displacement field over the face: a smile lifts the mouth corners and
cheeks, a worried look raises the inner brows, an open mouth lowers the lower
lip and chin. The fields add together and fade to zero at the patch edge, so
the warped patch drops back into the unwarped picture with no seam.

Coordinates are pixels of the stored picture (assets/goddess_portrait.jpg).
`field()` works on arrays *and* on single points, which is how the caller finds
where the mouth corners and eyes ended up after the warp.
"""

from __future__ import annotations

import math

import numpy as np
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QImage

# The warped region: head, hair and shoulders. (x0, y0, x1, y1)
PATCH = (560, 30, 1060, 400)
EDGE = 46.0                      # width of the fade to "no warp" at the patch border

# Landmarks, read off the painting.
L_EYE, R_EYE = (776.0, 226.0), (837.0, 226.0)
MOUTH = (808.0, 280.0)
L_CORNER, R_CORNER = (789.0, 280.0), (828.0, 280.0)
L_BROW, R_BROW = (778.0, 209.0), (841.0, 207.0)
L_INNER, R_INNER = (796.0, 212.0), (825.0, 212.0)
L_OUTER, R_OUTER = (760.0, 217.0), (857.0, 215.0)
L_CHEEK, R_CHEEK = (765.0, 262.0), (851.0, 262.0)
NECK = (806.0, 340.0)

DEFAULTS = dict(smile=0.0, mouth=0.0, brow=0.0, sad=0.0, eye=0.9, gx=0.0, gy=0.0,
                tilt=0.0, nod=0.0, t=0.0, hair=1.0, smirk=0.0, furrow=0.0, tremble=0.0)


def _g(x, y, c, sx, sy=None):
    sy = sx if sy is None else sy
    return np.exp(-(((x - c[0]) / sx) ** 2 + ((y - c[1]) / sy) ** 2))


def field(x, y, P):
    """Displacement (dx, dy), in picture pixels, of the paint that starts at (x, y)."""
    s, m, b, sad = P["smile"], P["mouth"], P["brow"], P["sad"]
    smirk = float(P.get("smirk", 0.0))
    furrow = float(P.get("furrow", 0.0))
    tremble = float(P.get("tremble", 0.0))
    dx = np.zeros_like(x, dtype=np.float32) if isinstance(x, np.ndarray) else 0.0
    dy = dx * 1.0

    # Smile: corners rise and spread, the cheeks follow, and the lower lids squint.
    for c, sg in ((L_CORNER, -1), (R_CORNER, 1)):
        g = _g(x, y, c, 8.5, 7.0)
        dy = dy - 7.0 * s * g
        dx = dx + sg * 3.2 * s * g
    # Smirk / playful asymmetry / subtle disgust sneer: one corner lifts, opposite stays poised
    if abs(smirk) > 0.02:
        g_r = _g(x, y, R_CORNER, 9.0, 7.5)
        dy = dy - 5.2 * smirk * g_r
        dx = dx + 2.4 * smirk * g_r
        dy = dy - 3.2 * max(0.0, smirk) * _g(x, y, R_OUTER, 12.0, 7.5)
    for c in (L_CHEEK, R_CHEEK):
        dy = dy - 3.6 * s * _g(x, y, c, 13.0)
    for c in (L_EYE, R_EYE):
        dy = dy - 2.6 * max(s, 0.0) * _g(x, y, (c[0], c[1] + 8), 8.0, 5.0)
    dy = dy - 1.4 * s * _g(x, y, MOUTH, 11.0, 5.0)          # the lips bow with the corners

    # Open mouth: the lower lip and chin drop, the upper lip lifts a hair.
    dy = dy + 12.0 * m * _g(x, y, (MOUTH[0], 289.0), 19.0, 8.0)
    dy = dy + 9.0 * m * _g(x, y, (MOUTH[0], 304.0), 20.0, 12.0)
    dy = dy - 0.8 * m * _g(x, y, (MOUTH[0], 274.0), 12.0, 5.0)
    dx = dx * 1.0

    # Brows: lift, knit (negative lift), angry/determined furrow, and the worried slant of sadness.
    for c in (L_BROW, R_BROW):
        dy = dy - 7.5 * b * _g(x, y, c, 22.0, 8.0)
    dy = dy - 1.2 * b * _g(x, y, (MOUTH[0], 190.0), 30.0, 14.0)
    for c in (L_INNER, R_INNER):
        dy = dy - 6.8 * sad * _g(x, y, c, 9.0, 7.0)
    for c in (L_OUTER, R_OUTER):
        dy = dy + 3.6 * sad * _g(x, y, c, 11.0, 7.0)
    for c, sg in ((L_INNER, 1), (R_INNER, -1)):              # a frown/furrow pulls the inner brows down & together
        k_knit = max(0.0, -b) + 0.9 * max(0.0, furrow)
        dx = dx + sg * 2.6 * k_knit * _g(x, y, c, 10.0, 7.0)
        dy = dy + 4.4 * max(0.0, furrow) * _g(x, y, c, 10.0, 7.0)

    # Subtle emotional micro-tremble (fear, grief, overwhelming awe)
    if tremble > 0.04:
        q = tremble * 0.9 * math.sin(float(P["t"]) * 26.0)
        dy = dy + q * _g(x, y, (MOUTH[0], 292.0), 24.0, 16.0)

    # Eyes: narrowed when soft, wide when startled; and a glance, which slides
    # the whole eye a little.
    for c in (L_EYE, R_EYE):
        dy = dy + 11.0 * (0.9 - P["eye"]) * _g(x, y, (c[0], c[1] - 8), 9.0, 5.0)
        g = _g(x, y, c, 7.5, 6.0)
        dx = dx + 3.2 * P["gx"] * g
        dy = dy + 2.0 * P["gy"] * g

    # The head: tilt about the neck, a nod, and the hair swaying. All of it is
    # faded out toward the patch border.
    x0, y0, x1, y1 = PATCH
    env = np.minimum(np.minimum((x - x0) / EDGE, (x1 - x) / EDGE), np.minimum((y - y0) / EDGE, (y1 - y) / EDGE))
    env = np.clip(env, 0.0, 1.0)
    env = env * env * (3.0 - 2.0 * env)
    head = np.clip((NECK[1] + 10.0 - y) / 70.0, 0.0, 1.0)  # solid above the neck, fading out over the shoulders
    w = env * head * head * (3.0 - 2.0 * head)
    th = P["tilt"]
    dx = dx - th * (y - NECK[1]) * w
    dy = dy + th * (x - NECK[0]) * w + P["nod"] * 3.0 * w
    t, h = P["t"], P["hair"]
    for cx, sg in ((610.0, -1), (1000.0, 1)):                # the two lobes of hair
        g = _g(x, y, (cx, 230.0), 55.0, 120.0) * env
        dx = dx + h * 2.6 * g * np.sin(t * 1.15 + y / 38.0 + sg)
        dy = dy + h * 1.2 * g * np.sin(t * 0.9 + x / 45.0)
    return dx, dy


class FaceWarp:
    """Warps the head patch at display resolution. Cheap: the patch is only a few
    tens of thousands of pixels at HUD size."""

    def __init__(self, picture: QImage) -> None:
        self._pic = picture
        self._key = None
        self._src = None          # float32 (h, w, 3), display resolution
        self._gx = self._gy = None
        self.size = (0, 0)
        self.image: QImage | None = None
        self._buf = None

    def prepare(self, scale: float) -> None:
        """(Re)build the display-resolution source and sample grid for `scale` px per picture px."""
        key = round(scale, 3)
        if key == self._key:
            return
        self._key = key
        x0, y0, x1, y1 = PATCH
        w, h = max(8, int((x1 - x0) * scale)), max(8, int((y1 - y0) * scale))
        patch = self._pic.copy(x0, y0, x1 - x0, y1 - y0).scaled(
            w, h, Qt.AspectRatioMode.IgnoreAspectRatio, Qt.TransformationMode.SmoothTransformation
        ).convertToFormat(QImage.Format.Format_RGB888)
        ptr = patch.constBits()
        ptr.setsize(patch.sizeInBytes())
        raw = np.frombuffer(ptr, dtype=np.uint8).reshape(h, patch.bytesPerLine())[:, :w * 3]
        self._src = raw.reshape(h, w, 3).astype(np.float32)
        ix, iy = np.meshgrid(np.arange(w, dtype=np.float32), np.arange(h, dtype=np.float32))
        self._ix, self._iy = ix, iy
        self._px = (ix + 0.5) / scale + x0            # picture px of every output pixel
        self._py = (iy + 0.5) / scale + y0
        self._scale = scale
        self.size = (w, h)

    def render(self, P) -> QImage:
        """The patch with the expression in P applied. out(x) = src(x - d(x))."""
        dx, dy = field(self._px, self._py, P)
        sx = np.clip(self._ix - dx * self._scale, 0.0, self.size[0] - 1.001)
        sy = np.clip(self._iy - dy * self._scale, 0.0, self.size[1] - 1.001)
        x0, y0 = sx.astype(np.int32), sy.astype(np.int32)
        fx, fy = (sx - x0)[..., None], (sy - y0)[..., None]
        s = self._src
        out = (s[y0, x0] * (1 - fx) * (1 - fy) + s[y0, x0 + 1] * fx * (1 - fy)
               + s[y0 + 1, x0] * (1 - fx) * fy + s[y0 + 1, x0 + 1] * fx * fy)
        self._buf = np.ascontiguousarray(np.clip(out, 0, 255).astype(np.uint8))
        h, w = self._buf.shape[:2]
        self.image = QImage(self._buf.data, w, h, w * 3, QImage.Format.Format_RGB888)
        return self.image


def pt(c, P):
    """Where the painted point `c` ends up under expression P."""
    dx, dy = field(c[0], c[1], P)
    return c[0] + float(dx), c[1] + float(dy)


def rect_units(h_px: float):
    """The patch as (x, y, w, h) in layout units (1.0 = picture height)."""
    x0, y0, x1, y1 = PATCH
    return x0 / h_px, y0 / h_px, (x1 - x0) / h_px, (y1 - y0) / h_px


# ── the gown and cape ────────────────────────────────────────────────────────
CLOTH = (0, 520, 1600, 1536)


class ClothWarp:
    """Wind in the sheer cape and gown: rows of the picture slide sideways in a slow
    travelling wave, strongest at the hem, fading to nothing at the waist and in
    the background beyond the cape. Sideways-only keeps it cheap."""

    def __init__(self, picture: QImage) -> None:
        self._pic = picture
        self._key = None
        self.image: QImage | None = None
        self._buf = None

    def prepare(self, scale: float) -> None:
        key = round(scale, 3)
        if key == self._key:
            return
        self._key = key
        x0, y0, x1, y1 = CLOTH
        w, h = max(8, int((x1 - x0) * scale)), max(8, int((y1 - y0) * scale))
        patch = self._pic.copy(x0, y0, x1 - x0, y1 - y0).scaled(
            w, h, Qt.AspectRatioMode.IgnoreAspectRatio, Qt.TransformationMode.SmoothTransformation
        ).convertToFormat(QImage.Format.Format_RGB888)
        ptr = patch.constBits()
        ptr.setsize(patch.sizeInBytes())
        raw = np.frombuffer(ptr, dtype=np.uint8).reshape(h, patch.bytesPerLine())[:, :w * 3]
        self._src = raw.reshape(h, w, 3).copy()          # must own its memory: the QImage behind raw is freed on return
        # The wave is smooth, so it is worked out on a grid 4x coarser and then stretched.
        cw, ch = (w + 3) // 4, (h + 3) // 4
        ix, iy = np.meshgrid(np.arange(cw, dtype=np.float32) * 4.0, np.arange(ch, dtype=np.float32) * 4.0)
        px = (ix + 0.5) / scale + x0
        py = (iy + 0.5) / scale + y0
        depth = np.clip((py - 520.0) / 880.0, 0.0, 1.0)                  # 0 at the waist, 1 at the hem
        half = 120.0 + (py - 520.0) * 0.95                               # the cape widens like a bell
        edge = np.clip((half - np.abs(px - 800.0)) / 120.0, 0.0, 1.0)    # fades out beyond the cape
        self._amp = (depth ** 1.4 * edge * edge * (3.0 - 2.0 * edge)).astype(np.float32)
        self._px, self._py = px.astype(np.float32), py.astype(np.float32)
        self._xs = np.arange(w, dtype=np.float32)[None, :]
        self._rows = np.arange(h)[:, None]
        self._scale = scale
        self._w, self._h = w, h

    def render(self, t: float, wind: float) -> QImage:
        wave = np.sin(t * 1.15 + self._py / 85.0 + self._px / 230.0) + 0.5 * np.sin(t * 0.7 + self._py / 40.0 - self._px / 120.0)
        shift = wave * self._amp * (7.0 * wind) * self._scale            # display pixels, coarse grid
        shift = np.repeat(np.repeat(shift, 4, axis=0), 4, axis=1)[:self._h, :self._w]
        sx = np.clip(self._xs - shift, 0.0, self._w - 1.001)
        x0 = sx.astype(np.int32)
        f8 = ((sx - x0) * 256.0).astype(np.int32)[..., None]
        a = self._src[self._rows, x0]
        b = self._src[self._rows, x0 + 1]
        self._buf = (a.astype(np.int32) + (((b.astype(np.int32) - a) * f8) >> 8)).astype(np.uint8)
        h, w = self._buf.shape[:2]
        self.image = QImage(self._buf.data, w, h, w * 3, QImage.Format.Format_RGB888)
        return self.image


def cloth_rect_units(h_px: float):
    x0, y0, x1, y1 = CLOTH
    return x0 / h_px, y0 / h_px, (x1 - x0) / h_px, (y1 - y0) / h_px
