"""
core/confirm.py — a confirmation the model cannot forge.

THE PROBLEM WITH THE OLD GATE
    computer_settings guarded shutdown and restart like this:

        confirmed = str(params.get("confirmed", "")).lower()
        if confirmed not in ("yes", "true", "1", "confirm"):
            return "Please confirm by calling again with confirmed=yes."

    `confirmed` is a tool parameter, which means the *model* writes it. Nothing
    stops it from sending confirmed=yes on the first call, and nothing checks
    that a human was ever involved. It is a convention, not a gate — and its
    coverage was two actions, so deleting files and switching off the WiFi the
    assistant is talking over went through with no gate at all.

THE DESIGN HERE
    The confirmation token is issued by the *interface*, never by the model:

      1. An action calls `request(...)` with a callable that does the real work.
      2. This module hands the UI a banner with CONFIRM / CANCEL and returns
         IMMEDIATELY with a sentence for the model to say out loud.
      3. If — and only if — the user presses CONFIRM, the UI calls `resolve()`,
         which runs the stored callable off the Qt thread.

    Nothing blocks. The model keeps talking while the banner is up, so this
    costs no latency at all; in fact it is cheaper than the old gate, which
    burned two tool round trips (reject, then re-call) on every shutdown.

WHAT BELONGS HERE AND WHAT DOES NOT
    Only genuinely irreversible things. Anything that can be reversed should be
    done at once and pushed onto core/undo.py instead — undo is faster than a
    question, and an assistant that asks before every action is one nobody uses.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from typing import Callable, Optional

# A pending confirmation is abandoned after this long. Chosen to outlast a
# normal "hang on, let me look at the screen" pause without leaving a live
# shutdown button sitting on the HUD for the rest of the day.
TIMEOUT_SECONDS = 90.0


@dataclass
class _Pending:
    key:     str
    title:   str
    detail:  str
    run:     Callable[[], str]
    at:      float


@dataclass
class _PendingChoice:
    key:       str
    title:     str
    detail:    str
    options:   list[str]
    on_select: Callable[[int, str], str]
    at:        float


_pending: Optional[_Pending] = None
_pending_choice: Optional[_PendingChoice] = None
_lock = threading.Lock()

# Set once at startup by main.py. Signature: (title, detail) -> None for show,
# and () -> None for hide. Both are marshalled onto the Qt thread by the UI.
_show_cb: Optional[Callable[[str, str], None]] = None
_hide_cb: Optional[Callable[[], None]] = None
_show_choice_cb: Optional[Callable[[str, str, list[str]], None]] = None
_hide_choice_cb: Optional[Callable[[], None]] = None
_log_cb:  Optional[Callable[[str], None]] = None


def bind(show, hide, log=None) -> None:
    """Wire this module to the HUD. Called once from main.py at startup."""
    global _show_cb, _hide_cb, _log_cb
    _show_cb, _hide_cb, _log_cb = show, hide, log


def bind_choice(show_choice, hide_choice) -> None:
    """Wire the multi-option ChoiceBanner to the HUD. Called at startup by main.py."""
    global _show_choice_cb, _hide_choice_cb
    _show_choice_cb, _hide_choice_cb = show_choice, hide_choice


def _log(msg: str) -> None:
    if _log_cb:
        try:
            _log_cb(msg)
        except Exception:
            pass


def request(key: str, title: str, detail: str, run: Callable[[], str]) -> str:
    """Park an irreversible action behind the on-screen gate.

    Returns the sentence the tool should hand back to the model — phrased as an
    instruction so the assistant asks the user out loud in their own language,
    rather than reading an English string verbatim."""
    global _pending

    if _show_cb is None:
        # No interface bound (headless, or a very early call). Refuse rather
        # than silently performing something irreversible.
        return (f"I cannot confirm '{title}' right now because the interface is "
                f"not available, so I have not done it.")

    with _lock:
        _pending = _Pending(key=key, title=title, detail=detail,
                            run=run, at=time.monotonic())

    try:
        _show_cb(title, detail)
    except Exception as e:
        with _lock:
            _pending = None
        return f"Could not ask for confirmation: {e}. Nothing was done."

    _log(f"SYS: Awaiting confirmation — {title}")
    return (
        f"[CONFIRMATION_PENDING] I have put a confirmation on screen for: {title}. "
        f"Say ONE short sentence in the user's own language telling them you need "
        f"them to confirm it on the HUD before you do it. Do not claim it is done."
    )


def resolve(accepted: bool) -> None:
    """Called by the UI when the user presses CONFIRM or CANCEL.

    Runs the stored callable on a worker thread — this is invoked from the Qt
    thread, and shutting the machine down from inside a button handler would
    freeze the interface on its way out."""
    global _pending

    with _lock:
        p, _pending = _pending, None

    if _hide_cb:
        try:
            _hide_cb()
        except Exception:
            pass

    if p is None:
        return

    if time.monotonic() - p.at > TIMEOUT_SECONDS:
        _log(f"SYS: Confirmation expired — {p.title}")
        return

    if not accepted:
        _log(f"SYS: Cancelled — {p.title}")
        return

    def _worker():
        try:
            result = p.run() or "Done."
            _log(f"SYS: Confirmed — {p.title}. {result}")
        except Exception as e:
            _log(f"ERR: {p.title} failed — {e}")

    threading.Thread(target=_worker, daemon=True,
                     name=f"confirm-{p.key}").start()


def pending_title() -> str:
    """'' when nothing is waiting. Lets an action avoid stacking two banners."""
    with _lock:
        if _pending is None:
            return ""
        if time.monotonic() - _pending.at > TIMEOUT_SECONDS:
            return ""
        return _pending.title


def request_choice(
    key: str,
    title: str,
    detail: str,
    options: list[str],
    on_select: Callable[[int, str], str],
) -> str:
    """Display 2-6 numbered choices on the HUD and resolve by button click or voice."""
    global _pending_choice
    clean_opts = [str(o).strip()[:100] for o in (options or []) if str(o).strip()]
    if len(clean_opts) < 2 or len(clean_opts) > 6:
        return "Choice picker requires between 2 and 6 non-empty options."
    if _show_choice_cb is None:
        return (
            f"I cannot ask you to choose '{title}' right now because the choice "
            f"interface is not available, so I have not proceeded."
        )

    with _lock:
        _pending_choice = _PendingChoice(
            key=key,
            title=title,
            detail=detail,
            options=clean_opts,
            on_select=on_select,
            at=time.monotonic(),
        )

    try:
        _show_choice_cb(title, detail, list(clean_opts))
    except Exception as exc:
        with _lock:
            _pending_choice = None
        return f"Could not display options for '{title}': {exc}"

    numbered = "; ".join(f"{i}. {opt}" for i, opt in enumerate(clean_opts, 1))
    _log(f"SYS: Awaiting choice — {title} ({numbered})")
    return (
        f"[CHOICE_PENDING] Options displayed for '{title}': {numbered}. "
        "Ask the user in one concise sentence to click an option on the HUD or say an option number."
    )


def resolve_choice(selection: int | str | None, *, sync: bool = False) -> str:
    """Resolve a pending choice by 0-based index, 1-based digit string, label substring, or cancel."""
    global _pending_choice

    with _lock:
        p, _pending_choice = _pending_choice, None

    if _hide_choice_cb:
        try:
            _hide_choice_cb()
        except Exception:
            pass

    if p is None:
        return "No pending choice."

    if time.monotonic() - p.at > TIMEOUT_SECONDS:
        _log(f"SYS: Choice expired — {p.title}")
        return f"Choice expired — {p.title}"

    if selection is None:
        _log(f"SYS: Choice cancelled — {p.title}")
        return f"Cancelled — {p.title}"

    chosen_idx: int | None = None
    if isinstance(selection, bool):
        chosen_idx = None
    elif isinstance(selection, int):
        if selection >= 0 and selection < len(p.options):
            chosen_idx = selection
    else:
        txt = str(selection).strip().lower()
        if txt in ("cancel", "none", "abort", "-1"):
            chosen_idx = None
        else:
            import re as _re
            m = _re.search(r"\b(\d+)\b", txt)
            if m:
                n = int(m.group(1))
                if 1 <= n <= len(p.options):
                    chosen_idx = n - 1
                elif n == 0 and len(p.options) > 0:
                    chosen_idx = 0
            if chosen_idx is None:
                for i, opt in enumerate(p.options):
                    if txt == opt.lower() or txt in opt.lower() or opt.lower() in txt:
                        chosen_idx = i
                        break

    if chosen_idx is None:
        _log(f"SYS: Choice cancelled — {p.title}")
        return f"Cancelled — {p.title}"

    chosen_label = p.options[chosen_idx]

    def _run_selected() -> str:
        try:
            res = str(p.on_select(chosen_idx, chosen_label) or f"Selected {chosen_label}.")
            _log(f"SYS: Choice '{chosen_label}' -> {res[:160]}")
            return res
        except Exception as exc:
            _log(f"ERR: Choice '{chosen_label}' failed — {exc}")
            return f"Choice failed: {exc}"

    if sync:
        return _run_selected()

    threading.Thread(
        target=_run_selected,
        daemon=True,
        name=f"choice-{p.key}",
    ).start()
    return f"Selected option {chosen_idx + 1}: {chosen_label}"


def pending_choice_info() -> dict | None:
    """Return active choice metadata, or `None` if nothing is pending/expired."""
    with _lock:
        if _pending_choice is None:
            return None
        if time.monotonic() - _pending_choice.at > TIMEOUT_SECONDS:
            return None
        return {
            "key": _pending_choice.key,
            "title": _pending_choice.title,
            "detail": _pending_choice.detail,
            "options": list(_pending_choice.options),
        }
