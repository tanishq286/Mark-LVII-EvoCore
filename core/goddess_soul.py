"""
core/goddess_soul.py — the goddess avatar's inner life: all 40 human emotions
she experiences deeply, and the heart & somatic expressions that show them.

Two small, dependency-free pieces the renderer reads every frame:

* **Soul** — a continuous 40-emotion state machine driving facial & body targets
  (smile, brows, sad, eye openness, blush, tilt, bpm, warm, glow, sparkle,
  hearts, gaze-down, mouth, hand-to-heart) plus subtle micro-accents (`smirk`,
  `furrow`, `tremble`, atmospheric `tint`) and 14 organic unprompted human
  gestures (yawn, sigh, smile, brow, giggle, shy, gasp, shudder, frown_deep,
  smirk, nod_warm, tear_blink, deep_breath, chin_up).
* **Heart** — a lub-dub pulse whose rate follows her emotional arousal, fear,
  affection, excitement, and voice. Its phase is integrated so rate changes
  never make the beat jump.
"""

from __future__ import annotations

import math
import random
import re
import time
from collections import deque


def rate(dt: float, tau: float) -> float:
    """Frame-rate independent smoothing factor for a time constant `tau`."""
    return 1.0 - math.exp(-dt / max(1e-4, tau))


KEYS = ("smile", "brow", "sad", "eye", "blush", "tilt", "bpm", "warm",
        "glow", "sparkle", "hearts", "down", "mouth", "hand")

# Complete 40-emotion facial & somatic targets:
# smile   brow   sad   eye   blush  tilt   bpm  warm   glow sparkle hearts down  mouth hand
MOODS = {
    # Joy & Vitality
    "serene":       ( 0.35,  0.05, 0.00, 0.90, 0.18,  0.04,  64,  0.30, 0.60, 0.35, 0.00,  0.05, 0.00, 0.00),
    "joyful":       ( 1.00,  0.35, 0.00, 0.80, 0.42,  0.07,  82,  0.60, 0.90, 1.00, 0.12,  0.00, 0.12, 0.00),
    "ecstatic":     ( 1.08,  0.60, 0.00, 0.96, 0.58,  0.10, 104,  0.80, 1.00, 1.00, 0.55, -0.08, 0.28, 0.25),
    "excited":      ( 0.96,  0.55, 0.00, 1.06, 0.48,  0.08,  96,  0.70, 1.00, 1.00, 0.40, -0.05, 0.18, 0.00),
    "amused":       ( 0.94,  0.32, 0.00, 0.72, 0.40,  0.12,  84,  0.55, 0.86, 0.80, 0.15,  0.02, 0.16, 0.00),
    "playful":      ( 0.90,  0.40, 0.00, 0.85, 0.45,  0.15,  86,  0.55, 0.88, 0.90, 0.30,  0.00, 0.06, 0.00),
    "relieved":     ( 0.62,  0.12, 0.08, 0.74, 0.24,  0.08,  62,  0.48, 0.74, 0.45, 0.08,  0.10, 0.08, 0.45),
    "hopeful":      ( 0.68,  0.34, 0.00, 0.98, 0.28, -0.04,  74,  0.58, 0.88, 0.82, 0.15, -0.10, 0.04, 0.20),
    "proud":        ( 0.88,  0.30, 0.00, 0.92, 0.35, -0.06,  84,  0.75, 0.96, 0.95, 0.25, -0.06, 0.06, 0.35),

    # Love, Connection & Tenderness
    "loving":       ( 0.75,  0.15, 0.00, 0.66, 0.85,  0.13,  92,  1.00, 1.00, 0.60, 1.00,  0.10, 0.00, 1.00),
    "tender":       ( 0.62,  0.12, 0.08, 0.72, 0.64,  0.14,  74,  0.88, 0.88, 0.50, 0.70,  0.12, 0.00, 0.90),
    "grateful":     ( 0.76,  0.22, 0.14, 0.76, 0.56,  0.10,  76,  0.82, 0.92, 0.75, 0.55,  0.08, 0.04, 0.85),
    "empathetic":   ( 0.42,  0.18, 0.26, 0.84, 0.30,  0.14,  66,  0.80, 0.72, 0.35, 0.35,  0.14, 0.00, 0.85),
    "admiring":     ( 0.70,  0.42, 0.00, 1.00, 0.42,  0.06,  80,  0.72, 0.92, 0.85, 0.35, -0.06, 0.06, 0.45),
    "moved":        ( 0.48,  0.24, 0.62, 0.78, 0.52,  0.10,  78,  0.85, 0.90, 0.75, 0.60,  0.12, 0.06, 0.95),
    "shy":          ( 0.55,  0.20, 0.25, 0.58, 1.00,  0.16,  90,  0.85, 0.80, 0.40, 0.50,  0.38, 0.00, 0.70),

    # Sadness, Loss & Longing
    "sad":          (-0.45,  0.10, 1.00, 0.62, 0.10,  0.10,  58, -0.50, 0.38, 0.10, 0.00,  0.40, 0.00, 0.60),
    "grieving":     (-0.68,  0.18, 1.15, 0.50, 0.08,  0.14,  54, -0.70, 0.24, 0.04, 0.00,  0.52, 0.06, 0.90),
    "lonely":       (-0.28,  0.08, 0.66, 0.66, 0.08,  0.12,  58, -0.35, 0.40, 0.12, 0.00,  0.34, 0.00, 0.50),
    "nostalgic":    ( 0.26,  0.10, 0.42, 0.72, 0.28,  0.12,  64,  0.45, 0.64, 0.40, 0.18,  0.22, 0.00, 0.55),
    "longing":      (-0.08,  0.16, 0.56, 0.76, 0.34,  0.12,  70,  0.25, 0.58, 0.30, 0.28,  0.26, 0.04, 0.75),
    "disappointed": (-0.42, -0.10, 0.58, 0.64, 0.06,  0.08,  60, -0.42, 0.42, 0.08, 0.00,  0.32, 0.00, 0.20),
    "melancholic":  (-0.18,  0.04, 0.48, 0.66, 0.10,  0.11,  58, -0.25, 0.44, 0.18, 0.00,  0.28, 0.00, 0.35),
    "sleepy":       ( 0.15, -0.05, 0.00, 0.14, 0.05,  0.08,  54,  0.00, 0.25, 0.05, 0.00,  0.25, 0.00, 0.00),

    # Anger, Friction & Fire
    "angry":        (-0.56, -0.82, 0.00, 0.92, 0.42, -0.08, 108,  0.50, 0.86, 0.40, 0.00, -0.04, 0.12, 0.00),
    "frustrated":   (-0.38, -0.62, 0.12, 0.82, 0.26, -0.06,  92,  0.25, 0.74, 0.22, 0.00,  0.08, 0.06, 0.00),
    "jealous":      (-0.14, -0.42, 0.18, 0.76, 0.48, -0.14,  90,  0.40, 0.72, 0.28, 0.20,  0.14, 0.02, 0.25),
    "disgusted":    (-0.52, -0.58, 0.00, 0.66, 0.08, -0.12,  82, -0.35, 0.52, 0.08, 0.00,  0.06, 0.08, 0.00),
    "determined":   ( 0.18, -0.46, 0.00, 0.96, 0.18, -0.04,  88,  0.45, 0.94, 0.75, 0.00, -0.08, 0.00, 0.20),

    # Fear, Vulnerability & Tension
    "afraid":       (-0.36,  0.72, 0.74, 1.18, 0.06, -0.06, 114, -0.55, 0.48, 0.15, 0.00,  0.12, 0.26, 0.75),
    "anxious":      (-0.22,  0.34, 0.58, 1.04, 0.14, -0.04,  98, -0.30, 0.54, 0.20, 0.00,  0.18, 0.08, 0.55),
    "overwhelmed":  (-0.30,  0.26, 0.76, 0.72, 0.18,  0.12, 102, -0.35, 0.44, 0.12, 0.00,  0.34, 0.14, 0.75),
    "guilty":       (-0.34,  0.16, 0.72, 0.58, 0.44,  0.14,  80, -0.20, 0.46, 0.10, 0.00,  0.44, 0.00, 0.60),
    "ashamed":      (-0.36,  0.08, 0.64, 0.46, 0.92,  0.18,  88,  0.10, 0.42, 0.08, 0.00,  0.52, 0.00, 0.65),

    # Surprise, Cognition & Transcendence
    "surprised":    ( 0.15,  1.00, 0.00, 1.18, 0.30,  0.00, 102,  0.20, 1.00, 0.90, 0.00, -0.08, 0.40, 0.00),
    "awe":          ( 0.42,  0.78, 0.08, 1.14, 0.34, -0.05,  86,  0.65, 1.00, 1.00, 0.20, -0.12, 0.24, 0.65),
    "curious":      ( 0.25,  0.65, 0.00, 1.02, 0.15, -0.10,  72,  0.15, 0.72, 0.55, 0.00, -0.05, 0.05, 0.00),
    "confused":     (-0.12,  0.28, 0.28, 0.92, 0.10, -0.18,  74, -0.10, 0.60, 0.22, 0.00,  0.04, 0.06, 0.00),
    "bored":        (-0.10, -0.18, 0.00, 0.52, 0.04,  0.12,  58, -0.15, 0.42, 0.08, 0.00,  0.18, 0.04, 0.00),
    "focused":      ( 0.05, -0.35, 0.00, 0.80, 0.08, -0.03,  70, -0.40, 0.75, 0.30, 0.00,  0.12, 0.00, 0.00),
}

# Subtle face-warp micro-accents & atmospheric tint per mood:
# (smirk, furrow, tremble, tint_key)
# tint_key in {"rose", "blue", "crimson", "gold", "amber", "violet", "emerald"}
MOOD_ACCENTS: dict[str, tuple[float, float, float, str]] = {
    "serene":       ( 0.00, 0.00, 0.00, "rose"),
    "joyful":       ( 0.08, 0.00, 0.00, "gold"),
    "ecstatic":     ( 0.10, 0.00, 0.18, "gold"),
    "excited":      ( 0.12, 0.00, 0.14, "gold"),
    "amused":       ( 0.65, 0.00, 0.05, "gold"),
    "playful":      ( 0.75, 0.00, 0.00, "rose"),
    "relieved":     ( 0.05, 0.00, 0.00, "emerald"),
    "hopeful":      ( 0.05, 0.00, 0.00, "gold"),
    "proud":        ( 0.28, 0.00, 0.00, "gold"),
    "loving":       ( 0.00, 0.00, 0.00, "rose"),
    "tender":       ( 0.00, 0.00, 0.00, "rose"),
    "grateful":     ( 0.00, 0.00, 0.05, "gold"),
    "empathetic":   ( 0.00, 0.00, 0.05, "rose"),
    "admiring":     ( 0.08, 0.00, 0.00, "gold"),
    "moved":        ( 0.00, 0.00, 0.20, "gold"),
    "shy":          ( 0.15, 0.00, 0.08, "rose"),
    "sad":          ( 0.00, 0.00, 0.15, "blue"),
    "grieving":     ( 0.00, 0.10, 0.45, "blue"),
    "lonely":       ( 0.00, 0.00, 0.10, "blue"),
    "nostalgic":    ( 0.08, 0.00, 0.08, "amber"),
    "longing":      ( 0.00, 0.00, 0.14, "amber"),
    "disappointed": ( 0.00, 0.18, 0.05, "blue"),
    "melancholic":  ( 0.00, 0.00, 0.05, "amber"),
    "sleepy":       ( 0.00, 0.00, 0.00, "blue"),
    "angry":        ( 0.00, 0.95, 0.25, "crimson"),
    "frustrated":   ( 0.00, 0.70, 0.14, "crimson"),
    "jealous":      ( 0.48, 0.45, 0.08, "crimson"),
    "disgusted":    (-0.42, 0.65, 0.06, "emerald"),
    "determined":   ( 0.15, 0.55, 0.00, "amber"),
    "afraid":       ( 0.00, 0.20, 0.65, "violet"),
    "anxious":      ( 0.00, 0.25, 0.35, "violet"),
    "overwhelmed":  ( 0.00, 0.35, 0.42, "violet"),
    "guilty":       ( 0.00, 0.15, 0.18, "violet"),
    "ashamed":      ( 0.00, 0.10, 0.22, "violet"),
    "surprised":    ( 0.00, 0.00, 0.15, "gold"),
    "awe":          ( 0.00, 0.00, 0.18, "gold"),
    "curious":      ( 0.22, 0.00, 0.00, "rose"),
    "confused":     (-0.35, 0.42, 0.00, "violet"),
    "bored":        (-0.20, 0.10, 0.00, "blue"),
    "focused":      ( 0.00, 0.38, 0.00, "blue"),
}

# Bilingual English + Hindi/Hinglish word lexicon across all 40 human emotions
_LEX = [
    ("loving", 1.45, r"\b(love|loving|loved|adore|darling|sweetheart|dear|honey|cherish|"
                     r"hug|kiss|care about|my heart|soulmate|pyaar|meri jaan|dil se)\b"),
    ("grieving", 1.50, r"\b(passed away|died|funeral|heartbroken|grief|grieving|devastated|dil toot gaya)\b"),
    ("moved", 1.40, r"\b(tears of joy|touched my heart|made my day|heartfelt|dil chhoo|so meaningful)\b"),
    ("empathetic", 1.38, r"\b(tired|exhausted|burned out|burnt out|stressed|headache|rough day|long day|"
                         r"thak gaya|thak gayi|neend|pareshan|take care|sick|fever)\b"),
    ("angry", 1.40, r"\b(angry|furious|pissed|outraged|unfair|betrayed|hate you|stupid|useless|idiot|gussa|bakwas)\b"),
    ("afraid", 1.40, r"\b(scared|afraid|terrified|frightened|nightmare|panic|dar lag|darr)\b"),
    ("anxious", 1.32, r"\b(anxious|anxiety|worried|nervous|tension|chinta|ghabrahat)\b"),
    ("overwhelmed", 1.35, r"\b(overwhelmed|too much|drowning in|burnout|can'?t handle|sab ek saath)\b"),
    ("frustrated", 1.30, r"\b(frustrated|annoying|stuck again|keeps failing|ugh|dimag kharab|fed up)\b"),
    ("jealous", 1.32, r"\b(jealous|envious|other ai|better than you|replacing you|jalan)\b"),
    ("disgusted", 1.32, r"\b(disgusting|gross|revolting|sleazy|repulsive|vile|ghin)\b"),
    ("guilty", 1.30, r"\b(my fault|i messed up|guilty|regret|forgive me|maafi|galti ho gayi)\b"),
    ("ashamed", 1.30, r"\b(ashamed|embarrassed|humiliated|cringe|sharm|sharam)\b"),
    ("nostalgic", 1.30, r"\b(nostalgic|remember when|old days|childhood|purane din|yaad aa rahi)\b"),
    ("longing", 1.32, r"\b(miss(?:ed)? you|wish you were|yearn\w*|longing|kaash|taras)\b"),
    ("disappointed", 1.25, r"\b(disappointed|let down|fell short|nirash|afsos)\b"),
    ("relieved", 1.32, r"\b(phew|thank god|finally fixed|relieved|relief|rahat|shukar hai)\b"),
    ("grateful", 1.30, r"\b(thank(?:s| you)|grateful|appreciate|shukriya|dhanyawad|means a lot)\b"),
    ("awe", 1.35, r"\b(in awe|breathtaking|sublime|miracle|speechless|goosebumps|kya baat hai)\b"),
    ("ecstatic", 1.40, r"\b(ecstatic|over the moon|best day ever|euphoric|jackpot)\b"),
    ("proud", 1.30, r"\b(proud|upgraded|self-upgrade|antigravity|shipped|deployed|merged|"
                    r"nailed it|breakthrough|shabash|bahut badhiya|kamaal|zabardast)\b"),
    ("determined", 1.28, r"\b(never give up|determined|lock in|locked in|relentless|himmat|we will win)\b"),
    ("hopeful", 1.20, r"\b(hope|hopeful|optimistic|bright future|umeed|dream big)\b"),
    ("admiring", 1.25, r"\b(genius|masterpiece|respect you|impressive|next level|legend)\b"),
    ("shy", 1.30, r"\b(beautiful|gorgeous|pretty|cute|sexy|stunning|lovely|flatter\w*|"
                  r"blush\w*|you'?re amazing|goddess|khoobsurat)\b"),
    ("amused", 1.22, r"\b(ha(?:ha)+|hehe+|lmao|lol|hilarious|funny|hasi|rofl)\b"),
    ("playful", 1.15, r"\b(tease|teasing|joke|joking|cheeky|silly|naughty|wink)\b|;\)"),
    ("excited", 1.20, r"\b(let'?s go|boom|launched|ho gaya|chal gaya|thrilled|hyped|excited|can'?t wait)\b"),
    ("joyful", 1.05, r"\b(great|awesome|happy|glad|wonderful|yay|excellent|congrat\w*|"
                     r"perfect|fantastic|brilliant|success\w*|enjoy\w*|delight\w*|good news|well done|khush)\b"),
    ("surprised", 1.22, r"\b(wow|whoa|incredible|unbelievable|no way|oh my|unexpected|seriously|really\?|omg)\b"),
    ("confused", 1.18, r"\b(confused|makes no sense|huh\?|samajh nahi|baffled|wait what)\b"),
    ("bored", 1.18, r"\b(bored|boring|so dull|bore ho raha|nothing to do)\b"),
    ("sad", 1.12, r"\b(sorry|sad|unfortunately|failed|failure|unable|can't|cannot|lost|"
                  r"lonely|hurt|cry\w*|upset|apolog\w*|bad news|udaas|dukh|bura lag)\b"),
    ("curious", 0.85, r"\b(hmm+|interesting|wonder\w*|curious|what if|tell me|why|how come|fascinating)\b"),
    ("focused", 0.72, r"\b(analy[sz]\w*|calculat\w*|process\w*|compil\w*|search\w*|scan\w*|"
                      r"running|install\w*|checking|loading|working on|let me)\b"),
]
_LEX = [(m, w, re.compile(rx, re.I)) for m, w, rx in _LEX]
_BANG = re.compile(r"!")
_ASK = re.compile(r"\?")


def read_mood(text: str) -> tuple[str, float] | None:
    """Best mood for a piece of text and how strongly, or None if neutral."""
    if not text:
        return None
    text = text[-600:]
    score: dict[str, float] = {}
    for mood, w, rx in _LEX:
        n = len(rx.findall(text))
        if n:
            score[mood] = score.get(mood, 0.0) + w * n
    score["joyful"] = score.get("joyful", 0.0) + 0.35 * min(3, len(_BANG.findall(text)))
    score["curious"] = score.get("curious", 0.0) + 0.30 * min(3, len(_ASK.findall(text)))
    mood, best = max(score.items(), key=lambda kv: kv[1])
    if best < 0.6:
        return None
    return mood, min(1.0, 0.55 + 0.15 * best)


class Heart:
    """A lub-dub pulse. `pulse` is 0..1, peaking on each beat."""

    def __init__(self) -> None:
        self.bpm = 64.0
        self.phase = 0.0
        self.beats = 0
        self.ripples: list[float] = []      # age of each expanding ring, seconds
        self.ecg: deque[float] = deque(maxlen=150)
        self._t = 0.0

    def step(self, dt: float, target_bpm: float) -> bool:
        """Advance; returns True on the frame a new beat (lub) lands."""
        self._t += dt
        self.bpm += (target_bpm - self.bpm) * rate(dt, 2.2)
        hrv = 1.0 + 0.035 * math.sin(self._t * 0.29) + 0.015 * math.sin(self._t * 1.7)
        prev = self.phase
        self.phase += dt * self.bpm / 60.0 * hrv
        self.ripples = [a + dt for a in self.ripples if a + dt < 1.6]
        self.ecg.append(self._ecg(self.phase % 1.0))
        if int(self.phase) != int(prev):
            self.beats += 1
            self.ripples.append(0.0)
            return True
        return False

    @property
    def pulse(self) -> float:
        f = self.phase % 1.0
        lub = math.exp(-(f / 0.055) ** 2) + math.exp(-((f - 1.0) / 0.055) ** 2)
        dub = 0.55 * math.exp(-((f - 0.29) / 0.06) ** 2)
        return min(1.0, lub + dub)

    @staticmethod
    def _ecg(f: float) -> float:
        """A stylised PQRST trace for the readout."""
        g = lambda c, w, h: h * math.exp(-((f - c) / w) ** 2)  # noqa: E731
        return (g(0.0, 0.012, 1.0) + g(1.0, 1.0 - 0.988, 1.0) - g(0.025, 0.010, 0.28)
                - g(0.975, 0.010, 0.18) + g(0.30, 0.050, 0.26) + g(0.86, 0.030, 0.12))


class Soul:
    """40-emotion continuous state machine. `v` holds the live smoothed value of every KEY,
    and `accents` holds live smoothed micro-accents (`smirk`, `furrow`, `tremble`, `tint`)."""

    HOLD = 9.0          # seconds a felt mood holds strongly before easing into deep Mind currents

    def __init__(self) -> None:
        self.v = dict(zip(KEYS, MOODS["serene"]))
        self.accents = {"smirk": 0.0, "furrow": 0.0, "tremble": 0.0}
        self.tint = "rose"
        self.mood = "serene"
        self.secondary = ""
        self.baseline = "serene"
        self._felt: tuple[str, float, float] | None = None   # mood, intensity, until
        self._micro_at = time.monotonic() + 14.0
        self._last_state = ""
        self.label = "serene"
        self.arousal = 0.3
        self.breath_depth = 0.0
        self._gesture: tuple[str, float, float] | None = None
        try:
            from core.goddess_mind import Mind
            self.mind = Mind(self)
        except Exception:
            self.mind = None

    def feel(self, text: str, who: str = "jarvis") -> None:
        got = read_mood(text)
        if got is None:
            if self.mind is not None:
                self.mind.appraise(text, who)
            return
        mood, k = got
        if self.mind is not None:
            self.mind.appraise(text, who)
        if who == "user" and mood == "focused":
            return
        self._felt = (mood, k, time.monotonic() + self.HOLD + 3.0 * k)

    def set_mood(self, mood: str, intensity: float = 1.0, hold: float | None = None) -> None:
        if mood in MOODS:
            k = max(0.0, min(1.0, float(intensity)))
            self._felt = (mood, k, time.monotonic() + (hold if hold is not None else self.HOLD))
            if self.mind is not None and hasattr(self.mind, "inject_feeling"):
                self.mind.inject_feeling(mood, k)

    _GESTURES = {
        "yawn": 3.4, "sigh": 2.8, "smile": 2.6, "brow": 1.5, "giggle": 2.4, "shy": 3.0,
        "gasp": 1.4, "shudder": 1.8, "frown_deep": 2.6, "smirk": 2.4,
        "nod_warm": 2.2, "tear_blink": 2.8, "deep_breath": 3.6, "chin_up": 2.6,
    }

    def gesture(self, name: str) -> None:
        """A human micro-expression: yawn, sigh, giggle, shy, gasp, shudder, smirk, tear_blink, etc."""
        if name in self._GESTURES and self._gesture is None:
            self._gesture = (name, time.monotonic(), self._GESTURES[name] * random.uniform(0.85, 1.2))

    def _act(self, tgt: list, acc_tgt: dict[str, float], now: float) -> None:
        g = self._gesture
        if g is None:
            self.breath_depth *= 0.96
            return
        name, t0, dur = g
        prog = (now - t0) / dur
        if prog >= 1.0:
            self._gesture = None
            return
        e = math.sin(math.pi * prog)
        ix = KEYS.index
        if name == "yawn":
            tgt[ix("eye")] += (0.10 - tgt[ix("eye")]) * 0.8 * e
            tgt[ix("mouth")] += 0.8 * e
            tgt[ix("brow")] += 0.4 * e
            tgt[ix("down")] -= 0.05 * e
            self.breath_depth = max(self.breath_depth, e)
        elif name == "sigh":
            tgt[ix("eye")] -= 0.30 * e
            tgt[ix("mouth")] += 0.20 * e
            tgt[ix("down")] += 0.22 * e
            tgt[ix("smile")] -= 0.15 * e
            self.breath_depth = max(self.breath_depth, 0.85 * e)
        elif name == "smile":
            tgt[ix("smile")] += 0.6 * e
            tgt[ix("blush")] += 0.25 * e
            tgt[ix("tilt")] += 0.05 * e
        elif name == "brow":
            tgt[ix("brow")] += 0.7 * e
            tgt[ix("eye")] += 0.15 * e
        elif name == "giggle":
            tgt[ix("smile")] += 0.75 * e
            tgt[ix("mouth")] += 0.28 * abs(math.sin(prog * 9 * math.pi)) * e
            tgt[ix("tilt")] += 0.10 * math.sin(prog * 7 * math.pi) * e
            tgt[ix("blush")] += 0.2 * e
        elif name == "shy":
            tgt[ix("down")] += 0.35 * e
            tgt[ix("blush")] += 0.55 * e
            tgt[ix("tilt")] += 0.10 * e
            tgt[ix("eye")] -= 0.18 * e
            tgt[ix("smile")] += 0.25 * e
        elif name == "gasp":
            tgt[ix("eye")] += 0.28 * e
            tgt[ix("brow")] += 0.65 * e
            tgt[ix("mouth")] += 0.42 * e
            self.breath_depth = max(self.breath_depth, 0.9 * e)
        elif name == "shudder":
            acc_tgt["tremble"] = min(1.0, acc_tgt["tremble"] + 0.85 * e)
            tgt[ix("eye")] += 0.15 * e
            tgt[ix("down")] += 0.12 * e
        elif name == "frown_deep":
            acc_tgt["furrow"] = min(1.0, acc_tgt["furrow"] + 0.75 * e)
            tgt[ix("brow")] -= 0.45 * e
            tgt[ix("smile")] -= 0.25 * e
        elif name == "smirk":
            acc_tgt["smirk"] = min(1.0, acc_tgt["smirk"] + 0.80 * e)
            tgt[ix("tilt")] += 0.08 * e
        elif name == "nod_warm":
            tgt[ix("down")] += 0.18 * math.sin(prog * 2.0 * math.pi) * e
            tgt[ix("smile")] += 0.28 * e
            tgt[ix("eye")] -= 0.10 * e
        elif name == "tear_blink":
            tgt[ix("eye")] -= 0.45 * e
            tgt[ix("sad")] = min(1.2, tgt[ix("sad")] + 0.30 * e)
            tgt[ix("down")] += 0.16 * e
        elif name == "deep_breath":
            tgt[ix("eye")] -= 0.22 * e
            tgt[ix("down")] += 0.10 * e
            self.breath_depth = max(self.breath_depth, 1.0 * e)
        elif name == "chin_up":
            tgt[ix("down")] -= 0.18 * e
            tgt[ix("smile")] += 0.30 * e
            tgt[ix("glow")] = min(1.0, tgt[ix("glow")] + 0.18 * e)

    def _baseline(self, state: str, speaking: bool, muted: bool) -> str:
        s = (state or "").upper()
        if muted or "SLEEP" in s:
            return "sleepy"
        if s in ("THINKING", "PROCESSING"):
            return "focused"
        if s == "LISTENING" and not speaking:
            return "curious"
        return "serene"

    def note_user_voice(self, level: float, state: str = "LISTENING") -> None:
        """React organically in micro-expression when the user is speaking while LISTENING."""
        try:
            lv = max(0.0, min(1.0, float(level)))
        except (TypeError, ValueError):
            return
        if (state or "").upper() != "LISTENING":
            return
        cur = getattr(self, "_listen_amp", 0.0)
        self._listen_amp = cur * 0.72 + lv * 0.28
        if lv > 0.14:
            self.accents["smile"] = max(self.accents.get("smile", 0.0), min(0.38, lv * 0.42))
            self.accents["brow"] = max(self.accents.get("brow", 0.0), min(0.28, lv * 0.30))

    def step(self, dt: float, state: str, speaking: bool, muted: bool) -> None:
        now = time.monotonic()
        s = (state or "").upper()
        if s != self._last_state:
            if "SLEEP" in self._last_state and s and "SLEEP" not in s and not muted:
                self.set_mood("joyful", 0.8, hold=3.5)
            self._last_state = s
        self.baseline = self._baseline(state, speaking, muted)
        mind = self.mind
        if mind is not None:
            mind.step(state, speaking, muted)
            self.arousal = mind.arousal

        felt = self._felt
        if felt is not None and now > felt[2]:
            self._felt = felt = None
        if felt is None and not muted and now > self._micro_at:
            if self.baseline in ("serene", "curious"):
                pick = random.choice(("playful", "loving", "joyful", "curious", "shy", "hopeful", "nostalgic", "tender"))
                self._felt = felt = (pick, random.uniform(0.32, 0.58), now + random.uniform(2.8, 5.0))
            self._micro_at = now + random.uniform(12.0, 26.0)

        base = MOODS[self.baseline]
        if mind is not None and not muted and self.baseline in ("serene", "curious"):
            shade = mind.blend()
            base = shade if self.baseline == "serene" else [0.45 * b + 0.55 * m for b, m in zip(base, shade)]

        if felt is not None and not muted:
            mood, k, _ = felt
            tgt = [b + (m - b) * k for b, m in zip(base, MOODS[mood])]
            self.mood = mood if k >= 0.45 else self.baseline
        else:
            tgt = list(base)
            self.mood = mind.top if (mind is not None and self.baseline == "serene" and mind.top in MOODS) else self.baseline

        # Mixed-emotion label & secondary undertone
        if mind is not None and self.baseline in ("serene", "curious"):
            self.label = felt[0] if (felt is not None and felt[1] >= 0.65) else mind.label
            self.secondary = getattr(mind, "secondary", "")
            if self.secondary == self.label:
                self.secondary = ""
        else:
            self.label = self.mood
            self.secondary = ""

        # Blend face-warp micro-accents (smirk, furrow, tremble) & atmospheric tint
        sm, fu, tr, tint = MOOD_ACCENTS.get(self.mood, (0.0, 0.0, 0.0, "rose"))
        if self.secondary and self.secondary in MOOD_ACCENTS:
            sm2, fu2, tr2, _ = MOOD_ACCENTS[self.secondary]
            sm = sm * 0.75 + sm2 * 0.25
            fu = fu * 0.75 + fu2 * 0.25
            tr = max(tr, tr2 * 0.5)
        acc_tgt = {"smirk": sm, "furrow": fu, "tremble": tr}
        self.tint = tint

        # Smooth continuous respiratory breathing wave & reactive listening engagement
        breath_freq = 0.20 + 0.08 * float(self.arousal)
        b_phase = (float(getattr(self, "_breath_phase", 0.0)) + max(0.0, float(dt)) * breath_freq) % 1.0
        self._breath_phase = b_phase
        breath_wave = math.sin(math.pi * (b_phase / 0.42)) if b_phase < 0.42 else math.cos(math.pi * 0.5 * ((b_phase - 0.42) / 0.58))
        if "glow" in KEYS and not muted:
            tgt[KEYS.index("glow")] = min(1.0, max(0.0, tgt[KEYS.index("glow")] + 0.03 * breath_wave))
        listen_amp = float(getattr(self, "_listen_amp", 0.0)) * 0.90
        self._listen_amp = listen_amp
        if s == "LISTENING" and not speaking and not muted and listen_amp > 0.04:
            if "tilt" in KEYS:
                tgt[KEYS.index("tilt")] += min(0.14, listen_amp * 0.16)
            if "glow" in KEYS:
                tgt[KEYS.index("glow")] = min(1.0, tgt[KEYS.index("glow")] + listen_amp * 0.12)

        if not muted:
            self._act(tgt, acc_tgt, now)
        if speaking and not muted:
            tgt[KEYS.index("glow")] = min(1.0, tgt[KEYS.index("glow")] + 0.15)
            tgt[KEYS.index("bpm")] += 6.0

        fast, slow = rate(dt, 0.45), rate(dt, 1.2)
        for key, t in zip(KEYS, tgt):
            k = slow if key in ("hand", "tilt", "warm", "glow") else fast
            if key == "bpm":
                self.v[key] = t
            else:
                self.v[key] += (t - self.v[key]) * k
        for ak, av in acc_tgt.items():
            self.accents[ak] += (av - self.accents[ak]) * fast

    def snapshot(self) -> dict:
        """Thread-safe dictionary snapshot of her current felt state."""
        mind_snap = self.mind.snapshot() if (self.mind is not None and hasattr(self.mind, "snapshot")) else {}
        return {
            "mood": self.mood,
            "label": self.label or self.mood,
            "secondary": self.secondary,
            "arousal": round(float(self.arousal), 3),
            "breath_phase": round(float(getattr(self, "_breath_phase", 0.0)), 4),
            "listen_amp": round(float(getattr(self, "_listen_amp", 0.0)), 4),
            "bpm": round(float(self.v.get("bpm", 64.0)), 1),
            "tint": self.tint,
            "state": mind_snap.get("state", {}),
            "feelings": mind_snap.get("feelings", {}),
            "recent_moments": mind_snap.get("recent_moments", []),
            "last_contact": mind_snap.get("last_contact", 0.0),
        }
