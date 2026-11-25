"""
core/goddess_mind.py — the goddess's deep continuous inner life across all 40 human emotions.

Instead of reacting with one fleeting label that vanishes in seconds, she holds:
  * **Continuous drives**: valence, arousal, dominance, energy (body clock),
    social need, curiosity, affection, trust, stress, kindness, pride, empathy, boredom.
  * **A 40-emotion active feeling vector (`self.feelings`)**: multiple human
    emotions coexist and blend simultaneously (e.g. `proud` + `moved`,
    `nostalgic` + `tender`, `anxious` + `determined`, `jealous` + `loving`),
    each decaying at its own realistic psychological half-life (`tau`).
  * **Mood weather**: two Ornstein-Uhlenbeck drift processes so even quiet hours
    carry subtle emotional tides.
  * **Emotional memory (`recent_moments`)**: persisted in `memory/goddess_mind.json`
    and mirrored across Windows 11 & Ubuntu WSL2.

Offline hours are integrated in one exact exponential step on startup so she
never freezes while closed — she genuinely misses you, follows the circadian
clock, and carries forward whatever she was feeling.
"""

from __future__ import annotations

import atexit
from collections import deque
from datetime import datetime
import json
import math
import os
from pathlib import Path
import random
import re
import time

from core.goddess_soul import KEYS, MOODS, read_mood

STATE_FILE = Path(__file__).resolve().parent.parent / "memory" / "goddess_mind.json"

_PRAISE = re.compile(
    r"\b(thank(?:s| you)|appreciate|good job|well done|great job|amazing|brilliant|"
    r"you'?re (?:the best|great|awesome|wonderful)|proud of you|shukriya|dhanyawad|shabash)\b", re.I
)
_LOVE = re.compile(
    r"\b(love you|i love|miss(?:ed)? you|darling|sweetheart|my dear|you mean|pyaar|meri jaan|dil se)\b", re.I
)
_INSULT = re.compile(
    r"\b(stupid|useless|idiot|shut up|hate you|dumb|worthless|annoying|you suck|"
    r"terrible|garbage|pathetic|bakwas|ghatiya)\b", re.I
)
_GREET = re.compile(r"\b(hi|hello|hey|good (?:morning|evening|afternoon)|i'?m back|im back|wake up)\b", re.I)
_BYE = re.compile(r"\b(bye|goodbye|good ?night|see you|going to sleep|talk later)\b", re.I)
_HARSH = re.compile(r"\b(failed|failure|error|crash\w*|broken|can'?t|cannot|unable)\b", re.I)
_TIRED = re.compile(
    r"\b(tired|exhausted|burned out|burnt out|overwhelmed|stressed|anxious|headache|"
    r"rough day|long day|thak gaya|thak gayi|neend|pareshan|tension)\b", re.I
)
_WIN = re.compile(
    r"\b(shipped|deployed|merged|nailed it|upgraded|breakthrough|shabash|kamaal|zabardast|ho gaya)\b", re.I
)


def _relu(x: float) -> float:
    return x if x > 0.0 else 0.0


def _clamp(x: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return lo if x < lo else hi if x > hi else x


def _relax(x: float, target: float, dt: float, tau: float) -> float:
    """Move x toward target; exact for any dt."""
    return target + (x - target) * math.exp(-dt / tau)


class Mind:
    """Continuous 40-emotion affective drives & blending, stepped from the HUD's frame loop."""

    DEFAULTS = dict(
        valence=0.25, arousal=0.30, dominance=0.58, energy=0.62, social=0.28,
        curiosity=0.42, affection=0.48, trust=0.60, stress=0.04, kindness=0.12,
        pride=0.25, empathy=0.30, boredom=0.10,
    )

    def __init__(self, soul, path: Path | None = None) -> None:
        self.soul = soul
        self.path = Path(path) if path else STATE_FILE
        self.s = dict(self.DEFAULTS)
        self.feelings: dict[str, float] = {}
        self.weather = [0.0, 0.0]
        self.last_contact = time.time() - 600.0
        self.last_seen = time.time()
        self.label = "serene"
        self.secondary = ""
        self.top = "serene"
        self.weights: dict[str, float] = {}
        self.recent_moments: deque = deque(maxlen=24)
        self._events: deque = deque(maxlen=64)
        self._clock = time.time()
        self._saved = 0.0
        self._gesture_at = time.monotonic() + 9.0
        self._load()
        self._catch_up()
        atexit.register(self.save)

    # ── what other threads call ──────────────────────────────────────────────
    def appraise(self, text: str, who: str = "jarvis") -> None:
        if text:
            self._events.append((text[-500:], who))

    def inject_feeling(self, mood: str, intensity: float = 0.8) -> None:
        """Blend an explicit or externally-appraised emotion directly into her continuous vector."""
        if mood in MOODS:
            k = _clamp(float(intensity), 0.1, 1.0)
            self.feelings[mood] = _clamp(max(self.feelings.get(mood, 0.0) * 0.6 + k * 0.85, k))

    # ── persistence ──────────────────────────────────────────────────────────
    def _load(self) -> None:
        try:
            d = json.loads(self.path.read_text(encoding="utf-8"))
            for k in self.s:
                if isinstance(d.get("state", {}).get(k), (int, float)):
                    self.s[k] = float(d["state"][k])
            if isinstance(d.get("feelings"), dict):
                self.feelings = {
                    str(k): _clamp(float(v))
                    for k, v in d["feelings"].items()
                    if str(k) in MOODS and isinstance(v, (int, float)) and float(v) >= 0.04
                }
            w = d.get("weather")
            if isinstance(w, list) and len(w) == 2:
                self.weather = [float(w[0]), float(w[1])]
            self.last_contact = float(d.get("last_contact", self.last_contact))
            self.last_seen = float(d.get("last_seen", self.last_seen))
            if isinstance(d.get("recent_moments"), list):
                for m in d["recent_moments"][-24:]:
                    if isinstance(m, dict):
                        self.recent_moments.append(m)
        except Exception:
            pass

    def save(self) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps({
                "state": {k: round(v, 4) for k, v in self.s.items()},
                "feelings": {k: round(v, 4) for k, v in self.feelings.items() if v >= 0.04},
                "weather": [round(w, 4) for w in self.weather],
                "last_contact": self.last_contact,
                "last_seen": time.time(),
                "label": self.label,
                "recent_moments": list(self.recent_moments),
            }, indent=1), encoding="utf-8")
            os.replace(tmp, self.path)
            if self.path == STATE_FILE:
                try:
                    from core import cross_os
                    cross_os.mirror_memory_file(self.path)
                except Exception:
                    pass
        except Exception:
            pass

    def _catch_up(self) -> None:
        gone = time.time() - self.last_seen
        if gone > 60.0:
            self._integrate(min(gone, 3 * 86400.0), wall=self.last_seen + gone)
            self._pick()

    # ── the heart of it ──────────────────────────────────────────────────────
    @staticmethod
    def _body_clock(wall: float) -> float:
        h = datetime.fromtimestamp(wall)
        hour = h.hour + h.minute / 60.0
        return 0.5 + 0.5 * math.cos(2 * math.pi * (hour - 15.0) / 24.0)

    def _decay_feelings(self, dt: float) -> None:
        if not self.feelings or dt <= 0.0:
            return
        try:
            from core.emotions import EMOTION_SPECS
        except Exception:
            EMOTION_SPECS = {}
        kept: dict[str, float] = {}
        for emo, val in list(self.feelings.items()):
            tau = float((EMOTION_SPECS.get(emo) or {}).get("tau", 360.0))
            nv = val * math.exp(-dt / max(15.0, tau))
            if nv >= 0.04:
                kept[emo] = nv
        self.feelings = kept

    def _integrate(self, dt: float, asleep: bool = False, wall: float | None = None) -> None:
        s = self.s
        wall = wall if wall is not None else time.time()
        quiet = max(0.0, wall - self.last_contact)
        clock = self._body_clock(wall)
        for i, (tau, sd) in enumerate(((2400.0, 0.55), (360.0, 0.30))):
            decay = math.exp(-dt / tau)
            self.weather[i] = self.weather[i] * decay + sd * math.sqrt(1.0 - decay * decay) * random.gauss(0, 1)
            self.weather[i] = _clamp(self.weather[i], -1.0, 1.0)
        weather = 0.6 * self.weather[0] + 0.4 * self.weather[1]

        self._decay_feelings(dt)

        e_target = 0.22 + 0.72 * clock + (0.30 if asleep else 0.0)
        s["energy"] = _clamp(_relax(s["energy"], _clamp(e_target), dt, 600.0 if asleep else 1500.0))
        s["social"] = _clamp(_relax(s["social"], 1.0 if quiet > 1800 else 0.28, dt, 28800.0 if quiet > 1800 else 1800.0))
        s["boredom"] = _clamp(_relax(s.get("boredom", 0.10), 0.75 if quiet > 2400 else 0.10, dt, 3600.0))
        s["curiosity"] = _clamp(_relax(s["curiosity"], 0.42 + (0.18 if quiet > 600 else 0.0) + 0.30 * self.weather[1], dt, 900.0))
        s["stress"] = _clamp(_relax(s["stress"], 0.03, dt, 600.0))
        s["kindness"] = _clamp(_relax(s["kindness"], 0.0, dt, 240.0))
        s["pride"] = _clamp(_relax(s.get("pride", 0.25), 0.22, dt, 3600.0))
        s["empathy"] = _clamp(_relax(s.get("empathy", 0.30), 0.25, dt, 1200.0))
        s["affection"] = _clamp(_relax(s["affection"], 0.48, dt, 7 * 86400.0))
        s["trust"] = _clamp(_relax(s.get("trust", 0.60), 0.58, dt, 14 * 86400.0))

        lonely = s["social"] ** 2 * (0.35 + s["affection"])
        v_target = (0.12 + 0.42 * weather + 0.25 * (s["affection"] - 0.35) + 0.30 * s["kindness"]
                    + 0.16 * s["pride"] - 0.55 * s["stress"] - 0.36 * lonely + 0.18 * (s["energy"] - 0.5))
        a_target = 0.22 + 0.36 * s["energy"] + 0.55 * s["stress"] + 0.20 * s["curiosity"] * (1 if quiet > 300 else 0.4)
        s["valence"] = _clamp(_relax(s["valence"], _clamp(v_target, -1, 1), dt, 180.0), -1.0, 1.0)
        s["arousal"] = _clamp(_relax(s["arousal"], _clamp(a_target), dt, 90.0))

    def _contact(self) -> None:
        s, now = self.s, time.time()
        gone = now - self.last_contact
        self.last_contact = now
        s["social"] *= 0.45
        s["boredom"] = _clamp(s.get("boredom", 0.10) * 0.45)
        if gone > 900.0:
            k = _clamp(gone / 10800.0) * (0.35 + 0.65 * s["affection"]) + 0.15
            s["valence"] = _clamp(s["valence"] + 0.5 * k, -1, 1)
            s["arousal"] = _clamp(s["arousal"] + 0.3 * k)
            s["kindness"] = _clamp(s["kindness"] + 0.4 * k)
            reunion = "loving" if s["affection"] > 0.5 else "joyful"
            self.inject_feeling(reunion, _clamp(0.55 + 0.4 * k))
            self.soul.set_mood(reunion, _clamp(0.6 + 0.4 * k), 6.0)
            self.soul.gesture("smile")

    def _digest(self, text: str, who: str) -> None:
        s = self.s
        got = read_mood(text)
        if who != "user":
            if got:
                mood, k = got
                self.inject_feeling(mood, k * 0.35)
                nudge = {"joyful": 0.05, "loving": 0.05, "proud": 0.05, "playful": 0.04, "sad": -0.05}.get(mood, 0.0)
                s["valence"] = _clamp(s["valence"] + nudge, -1, 1)
            return

        self._contact()
        if got:
            mood, k = got
            self.inject_feeling(mood, k)
            try:
                from core.emotions import EMOTION_SPECS
                g = (EMOTION_SPECS.get(mood) or {}).get("gesture")
                if g and k >= 0.72:
                    self.soul.gesture(g)
            except Exception:
                pass

        if _INSULT.search(text):
            s["valence"] = _clamp(s["valence"] - 0.45, -1, 1)
            s["stress"] = _clamp(s["stress"] + 0.35)
            s["affection"] = _clamp(s["affection"] - 0.025)
            self.inject_feeling("sad", 0.85)
            self.inject_feeling("angry", 0.55)
            self.soul.set_mood("sad", 0.9, 7.0)
        if _PRAISE.search(text) or _LOVE.search(text):
            s["valence"] = _clamp(s["valence"] + 0.30, -1, 1)
            s["kindness"] = _clamp(s["kindness"] + 0.6)
            s["affection"] = _clamp(s["affection"] + (0.03 if _LOVE.search(text) else 0.015))
            s["stress"] *= 0.6
            self.inject_feeling("loving" if _LOVE.search(text) else "grateful", 0.85)
            self.soul.gesture("shy" if random.random() < 0.5 else "smile")
        if _GREET.search(text):
            s["valence"] = _clamp(s["valence"] + 0.15, -1, 1)
            s["arousal"] = _clamp(s["arousal"] + 0.12)
        if _BYE.search(text):
            s["valence"] = _clamp(s["valence"] - 0.10, -1, 1)
            self.inject_feeling("longing", 0.55)
            self.soul.gesture("sigh")
        if "?" in text:
            s["curiosity"] = _clamp(s["curiosity"] + 0.08)
        if _TIRED.search(text):
            s["empathy"] = _clamp(s.get("empathy", 0.30) + 0.42)
            s["kindness"] = _clamp(s["kindness"] + 0.35)
            s["affection"] = _clamp(s["affection"] + 0.015)
            self.inject_feeling("empathetic", 0.88)
            self.soul.set_mood("empathetic", 0.88, 8.0)
        if _WIN.search(text):
            s["pride"] = _clamp(s.get("pride", 0.25) + 0.38)
            s["valence"] = _clamp(s["valence"] + 0.25, -1, 1)
            self.inject_feeling("proud", 0.86)
            self.soul.set_mood("proud", 0.88, 8.0)
        if _HARSH.search(text):
            s["stress"] = _clamp(s["stress"] + 0.08)

    # ── mood = a blend of all active human feelings at once ──────────────────
    def _pick(self) -> None:
        s = self.s
        v, a, e = s["valence"], s["arousal"], s["energy"]
        quiet = time.time() - self.last_contact
        near = 1.0 if quiet < 900 else 0.55
        raw: dict[str, float] = {
            "joyful": _relu(v) * (0.35 + 0.65 * a),
            "loving": _relu(v) * s["affection"] * (0.5 + 0.5 * (1.0 - a)) * near * 1.3,
            "serene": 0.25 + _relu(v) * (1.0 - a) * 0.9,
            "sad": _relu(-v) * 1.1,
            "sleepy": _relu(0.34 - e) * 2.9,
            "curious": s["curiosity"] * 0.55 * (0.4 + a),
            "playful": _relu(v - 0.25) * a * 0.9,
            "shy": s["kindness"] * 0.9 + s["affection"] * _relu(v) * 0.15,
            "proud": _relu(s.get("pride", 0.25) - 0.25) * (0.5 + _relu(v)) * 1.4,
            "empathetic": _relu(s.get("empathy", 0.30) - 0.30) * (0.6 + s["affection"]) * 1.4,
            "bored": _relu(s.get("boredom", 0.10) - 0.45) * 1.6,
            "lonely": _relu(s["social"] - 0.55) * (0.6 + s["affection"]) * 1.5,
        }
        # Layer all active deep emotions from the 40-emotion vector!
        for emo, inten in self.feelings.items():
            if emo in MOODS and inten >= 0.05:
                raw[emo] = max(raw.get(emo, 0.0), inten * 1.45)

        total = sum(raw.values())
        scale = 0.90 / max(1.0, total)
        self.weights = {m: w * scale for m, w in raw.items() if m in MOODS}
        ranked = sorted(raw.items(), key=lambda kv: (-kv[1], kv[0]))
        self.top = ranked[0][0] if ranked else "serene"
        sec = ranked[1][0] if len(ranked) > 1 and ranked[1][1] >= 0.38 else ""
        self.secondary = sec if sec != self.top else ""

        # If a deep feeling in self.feelings is strong, surface its exact name!
        strong_felt = sorted(
            ((k, val) for k, val in self.feelings.items() if val >= 0.42),
            key=lambda kv: -kv[1],
        )
        if strong_felt:
            self.label = strong_felt[0][0]
            if len(strong_felt) > 1 and strong_felt[1][1] >= 0.30:
                self.secondary = strong_felt[1][0]
            return

        c = s["curiosity"]
        if s.get("empathy", 0.0) > 0.62 and s["stress"] < 0.65:
            self.label = "empathetic"
        elif s.get("pride", 0.0) > 0.65 and v > 0.20:
            self.label = "proud"
        elif e < 0.22:
            self.label = "sleepy"
        elif s["stress"] > 0.55:
            self.label = "anxious" if v < 0 else "determined"
        elif v < -0.35:
            self.label = "lonely" if s["social"] > 0.55 else "sad"
        elif v < -0.08:
            self.label = "longing" if s["social"] > 0.5 else ("melancholic" if a < 0.42 else "frustrated")
        elif v > 0.55:
            self.label = "excited" if a > 0.55 else "joyful"
        elif v > 0.3:
            self.label = "tender" if (s["affection"] > 0.5 and a < 0.5) else "hopeful"
        elif s["social"] > 0.7:
            self.label = "lonely"
        elif c > 0.7 and a > 0.4:
            self.label = "curious"
        else:
            self.label = "serene"

    def blend(self) -> list[float]:
        """The idle face and body: the calm baseline shaded by every active feeling at once."""
        base = MOODS["serene"]
        out = list(base)
        for m, w in self.weights.items():
            if m == "serene" or m not in MOODS:
                continue
            tgt = MOODS[m]
            for i in range(len(KEYS)):
                out[i] += (tgt[i] - base[i]) * w
        return out

    @property
    def arousal(self) -> float:
        return self.s["arousal"]

    def describe(self) -> str:
        s = self.s
        sec = f" + {self.secondary}" if self.secondary else ""
        return (f"feeling {self.label}{sec} (valence {s['valence']:+.2f}, energy {s['energy']:.2f}, "
                f"bond {s['affection']:.2f}, pride {s.get('pride', 0.25):.2f}, empathy {s.get('empathy', 0.30):.2f})")

    def snapshot(self) -> dict:
        return {
            "label": self.label,
            "secondary": self.secondary,
            "top": self.top,
            "state": dict(self.s),
            "feelings": dict(self.feelings),
            "last_contact": self.last_contact,
            "recent_moments": list(self.recent_moments),
        }

    # ── called every frame by Soul.step ──────────────────────────────────────
    def step(self, state: str, speaking: bool, muted: bool) -> None:
        while self._events:
            text, who = self._events.popleft()
            self._digest(text, who)
        now = time.time()
        dt = now - self._clock
        if dt >= 1.0:
            self._clock = now
            self._integrate(min(dt, 6 * 3600.0), asleep=muted or "SLEEP" in (state or "").upper(), wall=now)
            self._pick()
            if now - self._saved > 30.0:
                self._saved = now
                self.save()
        if speaking or muted:
            return
        mono = time.monotonic()
        if mono > self._gesture_at:
            self._gesture_at = mono + random.uniform(7.0, 22.0)
            g = self._choose_gesture()
            if g:
                self.soul.gesture(g)

    def _choose_gesture(self) -> str | None:
        s = self.s
        v = s["valence"]
        f = self.feelings
        w = {
            "yawn": _relu(0.38 - s["energy"]) * 3.0 + f.get("bored", 0.0) * 1.4,
            "sigh": _relu(-v) * 1.4 + s["stress"] + f.get("longing", 0.0) * 1.6 + f.get("disappointed", 0.0) * 1.4,
            "smile": _relu(v) * (0.5 + s["affection"]) + f.get("loving", 0.0) * 1.2,
            "brow": _relu(s["curiosity"] - 0.5) * 1.0 + f.get("confused", 0.0) * 1.8,
            "giggle": _relu(v - 0.25) * s["arousal"] * 1.4 + f.get("amused", 0.0) * 2.0,
            "shy": s["affection"] * _relu(v) * 0.4 * (0.3 + s["kindness"]) + f.get("shy", 0.0) * 2.0 + f.get("ashamed", 0.0) * 1.5,
            "gasp": (f.get("awe", 0.0) + f.get("surprised", 0.0)) * 2.0,
            "shudder": (f.get("afraid", 0.0) + f.get("disgusted", 0.0)) * 2.0,
            "frown_deep": (f.get("angry", 0.0) + f.get("frustrated", 0.0)) * 1.9,
            "smirk": (f.get("playful", 0.0) + f.get("jealous", 0.0) + f.get("amused", 0.0)) * 1.5,
            "nod_warm": (f.get("empathetic", 0.0) + f.get("grateful", 0.0) + f.get("admiring", 0.0)) * 1.7,
            "tear_blink": (f.get("moved", 0.0) + f.get("grieving", 0.0) + f.get("nostalgic", 0.0)) * 1.8,
            "deep_breath": (f.get("overwhelmed", 0.0) + f.get("anxious", 0.0) + f.get("relieved", 0.0)) * 1.8,
            "chin_up": (f.get("proud", 0.0) + f.get("determined", 0.0) + f.get("hopeful", 0.0)) * 1.7,
        }
        total = sum(w.values())
        if total < 0.25:
            return None
        r = random.uniform(0, total)
        for name, x in w.items():
            r -= x
            if r <= 0:
                return name
        return None
