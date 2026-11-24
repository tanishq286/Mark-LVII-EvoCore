"""plugins/desktop_automation.py — Continuous Smart Auto-Scroll and Streaming Notepad Dictation."""

from __future__ import annotations

from pathlib import Path
import platform
import subprocess
import threading
import time

PLUGIN = {
    "name": "desktop_automation",
    "brief": "Voice-controlled continuous Smart Auto-Scroll and streaming Notepad dictation",
    "description": (
        "Provides continuous hands-free auto-scrolling (down/up, speed up/down, pause/stop) "
        "and live streaming text dictation into Windows Notepad (notepad.exe). "
        "Actions: status, scroll_start, scroll_down, scroll_up, scroll_faster, scroll_slower, "
        "scroll_stop, dictate_start, dictate_text, dictate_stop, notepad_dictate."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "action": {
                "type": "STRING",
                "description": (
                    "Action: status | scroll_start | scroll_down | scroll_up | scroll_faster | "
                    "scroll_slower | scroll_stop | dictate_start | dictate_text | dictate_stop | notepad_dictate"
                ),
            },
            "direction": {
                "type": "STRING",
                "description": "Scroll direction: 'down' or 'up' (default: 'down').",
            },
            "speed": {
                "type": "INTEGER",
                "description": "Scroll speed level 1-10 (default: 3).",
            },
            "text": {
                "type": "STRING",
                "description": "Text chunk to dictate into Notepad.",
            },
            "max_steps": {
                "type": "INTEGER",
                "description": "Optional max scroll ticks before stopping automatically.",
            },
            "skip_launch": {
                "type": "BOOLEAN",
                "description": "Skip launching notepad.exe when dictating into already focused editor.",
            },
        },
        "required": [],
    },
}


class SmartScrollController:
    """Thread-safe continuous vertical auto-scroll loop."""

    def __init__(self, scroll_fn=None):
        self._lock = threading.RLock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._direction = "down"
        self._speed = 3  # 1..10
        self._ticks = 0
        self._max_steps: int | None = None
        self._scroll_fn = scroll_fn

    def _do_scroll(self, delta: int) -> None:
        if self._scroll_fn is not None:
            self._scroll_fn(delta)
            return
        import pyautogui
        pyautogui.scroll(delta)

    def _loop(self) -> None:
        while not self._stop.is_set():
            with self._lock:
                speed = self._speed
                direction = self._direction
                max_steps = self._max_steps
                if max_steps is not None and self._ticks >= max_steps:
                    self._stop.set()
                    break
                notches = (80 + speed * 40) * (-1 if direction == "down" else 1)
                interval = max(0.06, 0.45 - speed * 0.035)
                self._ticks += 1
            try:
                self._do_scroll(notches)
            except Exception:
                self._stop.set()
                break
            self._stop.wait(interval)

    def start(self, *, direction: str = "down", speed: int = 3, max_steps: int | None = None) -> str:
        with self._lock:
            self._direction = "up" if str(direction).strip().lower() == "up" else "down"
            self._speed = max(1, min(10, int(speed or 3)))
            self._max_steps = int(max_steps) if max_steps else None
            if self._thread is not None and self._thread.is_alive():
                return f"Updated Smart Auto-Scroll: direction={self._direction}, speed={self._speed}/10."
            self._ticks = 0
            self._stop.clear()
            self._thread = threading.Thread(target=self._loop, daemon=True, name="smart-auto-scroll")
            self._thread.start()
            return f"Started Smart Auto-Scroll: direction={self._direction}, speed={self._speed}/10."

    def adjust(self, *, delta_speed: int = 0, direction: str | None = None) -> str:
        with self._lock:
            if direction is not None:
                self._direction = "up" if direction == "up" else "down"
            self._speed = max(1, min(10, self._speed + int(delta_speed)))
            active = self._thread is not None and self._thread.is_alive()
            if not active:
                return self.start(direction=self._direction, speed=self._speed)
            return f"Smart Auto-Scroll updated: direction={self._direction}, speed={self._speed}/10."

    def stop(self) -> str:
        with self._lock:
            self._stop.set()
            th = self._thread
            ticks = self._ticks
        if th is not None and th.is_alive():
            th.join(timeout=1.5)
        with self._lock:
            self._thread = None
        return f"Stopped Smart Auto-Scroll (ticks={ticks})."

    def status(self) -> str:
        with self._lock:
            active = self._thread is not None and self._thread.is_alive()
            return (
                f"Smart Auto-Scroll: {'RUNNING' if active else 'IDLE'} "
                f"| direction={self._direction} | speed={self._speed}/10 | ticks={self._ticks}"
            )


class NotepadDictationSession:
    """Maintains streaming dictation state and Notepad launch/paste injection."""

    def __init__(self, writer_fn=None, launcher_fn=None):
        self._lock = threading.RLock()
        self.active = False
        self.chunks: list[str] = []
        self._writer_fn = writer_fn
        self._launcher_fn = launcher_fn

    def _launch_notepad(self) -> None:
        if self._launcher_fn is not None:
            self._launcher_fn()
            return
        if platform.system() == "Windows":
            subprocess.Popen(["notepad.exe"], shell=False)
            time.sleep(0.5)

    def _inject_text(self, text: str, append_newline: bool = True) -> None:
        payload = text + ("\n" if append_newline and not text.endswith("\n") else "")
        if self._writer_fn is not None:
            self._writer_fn(payload)
            return
        import pyperclip
        import pyautogui
        pyperclip.copy(payload)
        pyautogui.hotkey("ctrl", "v")

    def start(self, *, skip_launch: bool = False) -> str:
        with self._lock:
            if not self.active and not skip_launch:
                self._launch_notepad()
            self.active = True
            return "Notepad dictation session active. Send chunks via dictate_text."

    def write(self, text: str, *, skip_launch: bool = False, append_newline: bool = True) -> str:
        chunk = str(text or "")
        if not chunk.strip():
            return "Provide non-empty text to dictate into Notepad."
        with self._lock:
            if not self.active:
                if not skip_launch:
                    self._launch_notepad()
                self.active = True
            self._inject_text(chunk, append_newline=append_newline)
            self.chunks.append(chunk)
            total_chars = sum(len(c) for c in self.chunks)
            return f"Dictated {len(chunk)} chars to Notepad (session chunks={len(self.chunks)}, total={total_chars})."

    def stop(self) -> str:
        with self._lock:
            count = len(self.chunks)
            self.active = False
            self.chunks.clear()
        return f"Ended Notepad dictation session ({count} chunks written)."

    def status(self) -> str:
        with self._lock:
            return f"Notepad Dictation: {'ACTIVE' if self.active else 'IDLE'} | chunks={len(self.chunks)}"


_SCROLL = SmartScrollController()
_DICTATION = NotepadDictationSession()


def run(parameters: dict, player=None, session_memory=None) -> str:
    """Plugin entrypoint for Smart Auto-Scroll and Notepad dictation."""
    params = parameters if isinstance(parameters, dict) else {}
    action = str(params.get("action") or "status").strip().lower()

    if action == "status":
        return f"{_SCROLL.status()}\n{_DICTATION.status()}"
    if action in ("scroll_start", "scroll_down"):
        return _SCROLL.start(
            direction=str(params.get("direction") or "down"),
            speed=int(params.get("speed") or 3),
            max_steps=params.get("max_steps"),
        )
    if action == "scroll_up":
        return _SCROLL.start(
            direction="up",
            speed=int(params.get("speed") or 3),
            max_steps=params.get("max_steps"),
        )
    if action == "scroll_faster":
        return _SCROLL.adjust(delta_speed=2)
    if action == "scroll_slower":
        return _SCROLL.adjust(delta_speed=-2)
    if action in ("scroll_stop", "stop_scroll"):
        return _SCROLL.stop()

    skip_launch = bool(params.get("skip_launch", False))
    if action == "dictate_start":
        return _DICTATION.start(skip_launch=skip_launch)
    if action in ("dictate_text", "notepad_dictate"):
        txt = str(params.get("text") or "").strip()
        if not txt and action == "notepad_dictate":
            return _DICTATION.start(skip_launch=skip_launch)
        return _DICTATION.write(
            txt,
            skip_launch=skip_launch,
            append_newline=bool(params.get("append_newline", True)),
        )
    if action == "dictate_stop":
        return _DICTATION.stop()

    return (
        f"Unknown desktop_automation action '{action}'. Use: "
        "status, scroll_start, scroll_down, scroll_up, scroll_faster, scroll_slower, "
        "scroll_stop, dictate_start, dictate_text, dictate_stop, notepad_dictate."
    )
