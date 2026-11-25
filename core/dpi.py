"""Process-wide Windows DPI awareness, set once and early.

WHY THIS EXISTS (measured on the live machine, 2026-11-25)
    The panel is 2560x1600 at 150 % scaling. A fresh Python process starts
    DPI-UNAWARE: GetSystemMetrics says 1707x1067 and every window rect is
    virtualised. `mss` then silently flips the process to per-monitor aware
    (V1) on its FIRST grab, so the coordinate space changes underneath the
    screen agent mid-run - UIA rects read before the first capture are in one
    space, clicks issued after it in another, and Vision coordinates in a third.

    Declaring Per-Monitor-V2 before anything else (Qt, mss, pyautogui, UIA)
    pins every API to the same physical-pixel space for the whole process.

Safe to call any number of times, from any thread, on any OS (no-op off
Windows). Never raises.
"""
from __future__ import annotations

import sys
import threading

_LOCK = threading.Lock()
_STATE: dict = {}

# DPI_AWARENESS_CONTEXT pseudo-handles (winuser.h)
_CTX_PER_MONITOR_AWARE_V2 = -4
_CTX_PER_MONITOR_AWARE = -3

# GetAwarenessFromDpiAwarenessContext results
AWARENESS_NAMES = {-1: "invalid", 0: "unaware", 1: "system_aware", 2: "per_monitor_aware"}


def current_awareness() -> int:
    """Awareness of the calling thread: 0 unaware, 1 system, 2 per-monitor, -1 unknown."""
    if sys.platform != "win32":
        return -1
    try:
        import ctypes
        u = ctypes.windll.user32
        u.GetThreadDpiAwarenessContext.restype = ctypes.c_void_p
        u.GetAwarenessFromDpiAwarenessContext.argtypes = [ctypes.c_void_p]
        ctx = u.GetThreadDpiAwarenessContext()
        return int(u.GetAwarenessFromDpiAwarenessContext(ctx))
    except Exception:
        return -1


def ensure_dpi_awareness() -> dict:
    """Make this process Per-Monitor-V2 DPI aware (falls back to V1, then system).

    Returns ``{"awareness": int, "method": str, "platform": str}``.
    """
    with _LOCK:
        if _STATE:
            return dict(_STATE)
        result = {"awareness": -1, "method": "not_windows", "platform": sys.platform}
        if sys.platform == "win32":
            result["method"] = "failed"
            try:
                import ctypes
                user32 = ctypes.windll.user32
                already = current_awareness()
                if already == 2:
                    result["method"] = "already_per_monitor"
                else:
                    ok = False
                    try:
                        user32.SetProcessDpiAwarenessContext.argtypes = [ctypes.c_void_p]
                        ok = bool(user32.SetProcessDpiAwarenessContext(
                            ctypes.c_void_p(_CTX_PER_MONITOR_AWARE_V2)))
                        if ok:
                            result["method"] = "SetProcessDpiAwarenessContext(PER_MONITOR_AWARE_V2)"
                        else:
                            ok = bool(user32.SetProcessDpiAwarenessContext(
                                ctypes.c_void_p(_CTX_PER_MONITOR_AWARE)))
                            if ok:
                                result["method"] = "SetProcessDpiAwarenessContext(PER_MONITOR_AWARE)"
                    except Exception:
                        ok = False
                    if not ok:
                        try:
                            hr = ctypes.windll.shcore.SetProcessDpiAwareness(2)
                            ok = hr == 0
                            if ok:
                                result["method"] = "shcore.SetProcessDpiAwareness(2)"
                        except Exception:
                            ok = False
                    if not ok:
                        try:
                            ok = bool(user32.SetProcessDPIAware())
                            if ok:
                                result["method"] = "SetProcessDPIAware()"
                        except Exception:
                            ok = False
                    if not ok and current_awareness() >= 1:
                        # Access denied because something (Qt, mss) already set it.
                        result["method"] = "already_set_by_host"
            except Exception as exc:
                result["method"] = f"error: {type(exc).__name__}"
            result["awareness"] = current_awareness()
        _STATE.update(result)
        return dict(_STATE)


def screen_geometry() -> dict:
    """Primary monitor in physical pixels plus the effective scale factor."""
    info = {"width": 0, "height": 0, "left": 0, "top": 0, "scale": 1.0}
    if sys.platform != "win32":
        return info
    ensure_dpi_awareness()
    try:
        import ctypes
        u = ctypes.windll.user32
        info["width"] = int(u.GetSystemMetrics(0))
        info["height"] = int(u.GetSystemMetrics(1))
        try:
            dpi = int(u.GetDpiForSystem())
            info["scale"] = round(dpi / 96.0, 3) if dpi else 1.0
        except Exception:
            pass
    except Exception:
        pass
    return info
