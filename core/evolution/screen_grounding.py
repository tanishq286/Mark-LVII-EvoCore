"""Grounding helpers for the closed-loop screen agent (Milestone 15 live fixes).

Three defects found in the live telemetry/runtime on 2026-11-25:

1. App postconditions never matched shell hosts. The planner asked for
   ``expected_app='SearchHost.exe'`` while the OS reported the foreground as
   ``app='Search'``; the old check (``'searchhost.exe' in 'search'``) was
   one-directional and kept the ``.exe``, so every Start-menu step came back
   UNVERIFIED and the recovery pressed the Windows key AGAIN - closing Start.
2. Vision coordinates were treated as screen pixels although the screenshot
   is downscaled (2560x1600 -> 1152x720), so Vision clicks landed at ~45 %
   of the intended position. Gemini is now asked for 0-1000 normalised
   coordinates which are mapped onto the physical monitor rect.
3. Status was only written to the HUD log. ``StepNarrator`` speaks short,
   throttled updates ("Looking at your screen...", "Opening Chrome...",
   "Typing now...") through the thread-safe ``player.request_say`` channel.
"""
from __future__ import annotations

import re
import threading
import time
from typing import Any, Callable

# Processes that host shell surfaces whose window/app name differs from the exe.
_ALIAS_GROUPS: tuple[frozenset[str], ...] = (
    frozenset({"searchhost", "searchapp", "search", "windowssearch", "startmenuexperiencehost",
               "start", "startmenu", "shellexperiencehost", "searchforappssettingsanddocuments"}),
    frozenset({"explorer", "fileexplorer", "windowsexplorer"}),
    frozenset({"chrome", "googlechrome"}),
    frozenset({"msedge", "edge", "microsoftedge"}),
    frozenset({"winword", "word", "microsoftword"}),
    frozenset({"excel", "microsoftexcel"}),
    frozenset({"powerpnt", "powerpoint", "microsoftpowerpoint"}),
    frozenset({"notepad", "windowsnotepad"}),
    frozenset({"code", "vscode", "visualstudiocode"}),
    frozenset({"windowsterminal", "terminal", "wt"}),
    frozenset({"applicationframehost", "settings", "systemsettings", "calculator", "calculatorapp"}),
)

# Keys that toggle a surface open/closed: pressing them again UNDOES the step.
TOGGLE_KEYS = frozenset({"win", "windows", "windows_key", "windowskey", "winkey", "super",
                         "meta", "lwin", "rwin", "start"})


def norm_app(name: str) -> str:
    """Fold an app/process/window name to comparable alphanumerics without `.exe`."""
    raw = str(name or "").strip().lower()
    raw = re.sub(r"\.(exe|app|lnk)$", "", raw)
    return re.sub(r"[^a-z0-9]+", "", raw)


def _aliases(name: str) -> set[str]:
    n = norm_app(name)
    out = {n} if n else set()
    for grp in _ALIAS_GROUPS:
        if n in grp:
            out |= set(grp)
    return out


def app_matches(expected: str, app: str = "", title: str = "", windows: Any = None) -> bool:
    """Bidirectional, alias-aware match of an expected app against what is on screen."""
    want = _aliases(expected)
    if not want:
        return False
    have_names = [app, title]
    for item in (windows or []):
        if isinstance(item, (tuple, list)) and item:
            have_names.extend(str(x) for x in item[:2])
        else:
            have_names.append(str(item))
    for raw in have_names:
        have = norm_app(raw)
        if not have:
            continue
        for w in want:
            if len(w) >= 3 and (w == have or w in have or (len(have) >= 4 and have in w)):
                return True
    return False


def is_toggle_press(step: dict[str, Any]) -> bool:
    if str(step.get("action") or "").strip().lower() != "press":
        return False
    keys = str(step.get("keys") or step.get("target") or "").strip().lower()
    return norm_app(keys) in {norm_app(k) for k in TOGGLE_KEYS}


def map_vision_point(x: float, y: float, meta: dict | None, *, space: str = "norm1000") -> tuple[int, int]:
    """Map a Vision coordinate onto physical screen pixels using capture metadata.

    ``space='norm1000'``: x,y in 0..1000 of the image. ``space='image'``: pixels
    of the (downscaled) image. Without metadata the point is returned unchanged.
    """
    meta = meta or {}
    mon = meta.get("monitor") or {}
    mw, mh = int(mon.get("width") or 0), int(mon.get("height") or 0)
    if mw <= 0 or mh <= 0:
        return int(round(x)), int(round(y))
    left, top = int(mon.get("left") or 0), int(mon.get("top") or 0)
    if space == "image":
        iw, ih = int(meta.get("image_w") or mw), int(meta.get("image_h") or mh)
        fx, fy = float(x) / max(1, iw), float(y) / max(1, ih)
    else:
        fx, fy = float(x) / 1000.0, float(y) / 1000.0
    fx, fy = min(max(fx, 0.0), 1.0), min(max(fy, 0.0), 1.0)
    return left + int(round(fx * (mw - 1))), top + int(round(fy * (mh - 1)))


def narration_for(step: dict[str, Any]) -> str:
    """A short spoken status line for one planned step (English; Live rephrases)."""
    act = str(step.get("action") or "").strip().lower()
    target = str(step.get("target") or "").strip()[:40]
    if act == "open_app":
        return f"Opening {target}." if target else "Opening the app."
    if act == "focus_app":
        return f"Switching to {target}." if target else "Switching windows."
    if act == "type_text":
        return "Typing now."
    if act in ("click", "double_click", "right_click"):
        return f"Clicking {target}." if target else "Clicking."
    if act == "press":
        keys = str(step.get("keys") or target).strip()
        return f"Pressing {keys}." if keys else "Pressing a key."
    if act == "scroll":
        return "Scrolling."
    if act == "wait":
        return "Waiting for the screen."
    return ""


class StepNarrator:
    """Throttled voice status channel. Never blocks the agent; never raises."""

    def __init__(self, say_fn: Callable[[str], None] | None, *, min_interval_s: float = 4.0,
                 clock: Callable[[], float] | None = None) -> None:
        self._say = say_fn if callable(say_fn) else None
        self._min = float(min_interval_s)
        self._clock = clock or time.monotonic
        self._last = -1e9
        self._last_text = ""
        self._lock = threading.Lock()
        self.spoken: list[str] = []

    @classmethod
    def for_player(cls, player: Any, **kw: Any) -> "StepNarrator":
        return cls(getattr(player, "request_say", None) if player is not None else None, **kw)

    def say(self, text: str, *, force: bool = False) -> bool:
        text = str(text or "").strip()
        if not text:
            return False
        with self._lock:
            now = self._clock()
            if text == self._last_text and not force:
                return False
            if not force and now - self._last < self._min:
                return False
            self._last, self._last_text = now, text
            self.spoken.append(text)
        if self._say is None:
            return True
        try:
            self._say(
                "[SCREEN AGENT STATUS - say ONLY this short status aloud, in the user's language, "
                f"no tool calls, no follow-up question]: {text}"
            )
        except Exception:
            pass
        return True

    def step(self, step: dict[str, Any]) -> bool:
        return self.say(narration_for(step))
