"""JARVIS Closed-Loop Autonomous Screen Agent Plugin (Observe -> Verify -> Act -> Re-Observe).

Controls interactive desktop workflows by combining Windows UIA accessibility
trees, screenshot + Vision/OCR grounding, post-action UI settle verification,
single-step stuck recovery, irreversible CONFIRM/LOCKED safety gates, and an
instant ESC / corner / "Jarvis stop" kill-switch.

Fixes previous failures regarding 'SearchHost.exe' not visible by introducing
system app visibility heuristics in core.evolution.screen_agent.
"""
from __future__ import annotations

import collections
import threading
import time

# Live telemetry 2026-11-25: the Brain fired four goals within 20 ms and three
# were rejected ("already running"); the Swarm sent action='goal'. Goals now
# queue FIFO and run one after another on a single worker thread.
_QUEUE_MAX = 5
_QUEUE: "collections.deque[tuple[str, int, object]]" = collections.deque()
_QUEUE_LOCK = threading.Lock()
_WORKER: list[threading.Thread] = []

_RUN_ALIASES = {"run", "execute", "goal", "do", "perform", "operate", "automate", "start", "act", "complete"}
_OBSERVE_ALIASES = {"observe", "inspect", "look", "see", "screenshot", "read", "describe"}
_STOP_ALIASES = {"stop", "abort", "cancel", "halt"}

PLUGIN = {
    "name": "autonomous_screen_agent",
    "brief": "Closed-loop Observe->Verify->Act desktop GUI automation with live HUD feedback.",
    "description": (
        "JARVIS's closed-loop desktop GUI automation agent using an Observe -> Verify -> Act -> "
        "Re-Observe feedback cycle over live windows, Chrome, Notepad, and desktop applications. "
        "Combines UI Automation accessibility trees with Vision/OCR screenshot grounding, waits for "
        "the screen to change after every click/typing/hotkey step, retries once with an alternative "
        "method if stuck, enforces CONFIRM/LOCKED safety gates before irreversible actions (send, "
        "delete, pay, UAC install, power), logs step telemetry, and aborts immediately on ESC, "
        "mouse-corner failsafe, or 'Jarvis stop'. "
        "Actions: 'status' (default read-only status), 'observe' (inspect active window elements), "
        "'run' (execute a multi-step GUI goal), 'stop' (instant kill-switch abort)."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "action": {
                "type": "STRING",
                "enum": ["status", "run", "execute", "observe", "stop", "abort"],
                "description": "Operation to perform (default 'status', or 'run' when goal is provided).",
            },
            "goal": {
                "type": "STRING",
                "description": "For 'run': complete interactive desktop GUI goal to achieve step by step.",
            },
            "max_steps": {
                "type": "INTEGER",
                "description": "Maximum Observe->Act->Verify cycles to run (default 12, max 20).",
            },
            "blocking": {
                "type": "BOOLEAN",
                "description": "Wait synchronously for completion before returning (default false).",
            },
        },
        "required": [],
    },
}


def _call_player(player, method: str, *args) -> None:
    """Safely calls a method on the player object."""
    fn = getattr(player, method, None) if player is not None else None
    if callable(fn):
        try:
            fn(*args)
        except Exception:
            pass


def _run_background(goal: str, max_steps: int, player) -> None:
    """Executes the screen agent goal in a background thread."""
    from core.evolution import screen_agent

    started = time.time()
    report = screen_agent.run_screen_goal(goal, max_steps=max_steps, player=player)
    elapsed = round(time.time() - started, 1)
    summary = screen_agent.format_report(report)
    steps_txt = "\n".join(report.get("hud_steps") or [])

    _call_player(
        player,
        "show_content",
        f"SCREEN AGENT ({report.get('status', 'done').upper()}, {elapsed}s)",
        f"GOAL: {goal}\n\nSTEPS:\n{steps_txt or '(none)'}\n\nSUMMARY:\n{summary}",
    )
    status = str(report.get("status") or "")
    if status == "aborted":
        _call_player(player, "request_say", f"Screen automation aborted ({report.get('abort_reason') or 'stopped'}).")
    elif status in ("awaiting_confirmation", "awaiting_choice", "blocked_voice_lock"):
        _call_player(player, "request_say", summary[:240])
    else:
        # Use fast reasoning to summarize the output cleanly for speech
        from core import gemini
        prompt = f"Summarize this screen automation summary cleanly in 1-2 short sentences for the user in their language: {summary[:400]}"
        clean_summary = gemini.text(prompt, tier=gemini.FAST)
        _call_player(
            player,
            "request_say",
            clean_summary or summary[:240],
        )


def _queue_worker() -> None:
    """Pops and runs goals from the queue."""
    while True:
        with _QUEUE_LOCK:
            if not _QUEUE:
                _WORKER.clear()
                return
            goal, max_steps, player = _QUEUE.popleft()
        try:
            _run_background(goal, max_steps, player)
        except Exception as exc:  # never let one goal kill the queue
            _call_player(player, "write_log", f"SYS: [ScreenAgent] Goal failed: {exc}")


def _enqueue(goal: str, max_steps: int, player) -> tuple[int, bool]:
    """Queue a goal; returns (position, started_worker). Position 0 = runs now."""
    with _QUEUE_LOCK:
        if len(_QUEUE) >= _QUEUE_MAX:
            return -1, False
        _QUEUE.append((goal, max_steps, player))
        pos = len(_QUEUE) - 1
        if _WORKER and _WORKER[0].is_alive():
            return pos + 1, False
        t = threading.Thread(target=_queue_worker, daemon=True, name="jarvis-screen-agent")
        _WORKER[:] = [t]
        t.start()
        return pos, True


def clear_queue() -> int:
    """Clears all queued goals."""
    with _QUEUE_LOCK:
        n = len(_QUEUE)
        _QUEUE.clear()
        return n


def queued_goals() -> list[str]:
    """Returns list of pending goal strings."""
    with _QUEUE_LOCK:
        return [g for g, _m, _p in _QUEUE]


def run(parameters: dict, player=None, session_memory=None) -> str:
    """Executes the autonomous screen agent tool."""
    params = parameters or {}
    goal = str(params.get("goal") or "").strip()
    raw_action = str(params.get("action") or "").strip().lower()
    action = raw_action or ("run" if goal else "status")
    if action in _RUN_ALIASES:
        action = "run"
    elif action in _OBSERVE_ALIASES:
        action = "observe"
    elif action in _STOP_ALIASES:
        action = "stop"
    elif action != "status" and goal:
        action = "run"   # unknown verb + a goal: the caller wants it done

    try:
        max_steps = max(1, min(20, int(params.get("max_steps") or 12)))
    except (TypeError, ValueError):
        max_steps = 12

    from core.evolution import screen_agent

    if action == "status":
        running = screen_agent.is_running()
        last = screen_agent.get_last_report()
        if running:
            q = queued_goals()
            return ("Autonomous Screen Agent is currently executing on your desktop."
                    + (f" {len(q)} more goal(s) queued." if q else ""))
        if last:
            return f"Autonomous Screen Agent idle. Last run: {screen_agent.format_report(last)}"
        return "Autonomous Screen Agent is online. (Observe -> Verify -> Act -> Re-Observe, default max_steps=12). Repaired SearchHost visibility issues."

    if action == "stop":
        dropped = clear_queue()
        msg = screen_agent.request_global_abort("tool: autonomous_screen_agent stop")
        return msg + (f" Cleared {dropped} queued goal(s)." if dropped else "")

    if action == "observe":
        try:
            obs = screen_agent.observe_screen(include_screenshot=False)
            sample_labels = [
                f"{el.role}:{el.label}"
                for el in (obs.view.elements or [])[:8]
                if el.label
            ]
            return (
                f"Foreground app={obs.app or '?'}, title={obs.title or '?'}, "
                f"source={obs.source}, degraded={obs.degraded}, "
                f"elements={len(obs.view.elements)}, windows={len(obs.view.windows)}, "
                f"top_controls={sample_labels}"
            )
        except Exception as e:
            return f"Failed to observe screen: {e}"

    if action == "run":
        if not goal:
            return "Provide a desktop GUI `goal` for action='run'."
        if bool(params.get("blocking")):
            if screen_agent.is_running():
                return "Autonomous Screen Agent is already running. Say 'Jarvis stop' or press ESC to abort."
            try:
                report = screen_agent.run_screen_goal(goal, max_steps=max_steps, player=player)
                return screen_agent.format_report(report)
            except Exception as e:
                return f"Execution failed: {e}"
        pos, _started = _enqueue(goal, max_steps, player)
        if pos < 0:
            return f"Screen Agent queue is full ({_QUEUE_MAX} goals). Say 'Jarvis stop' to clear it."
        if pos > 0 or screen_agent.is_running():
            return (f"Queued '{goal[:80]}' as screen goal #{max(pos, 1)}; it starts automatically "
                    "when the current one finishes. Narrating progress aloud and on the HUD.")
        return (
            f"Closed-loop Screen Agent engaged for '{goal[:100]}'. "
            "Narrating progress aloud and streaming Observe -> Verify -> Act steps to your HUD "
            "(press ESC or say 'Jarvis stop' anytime)."
        )

    return f"Unknown autonomous_screen_agent action: {action!r}. Use status, observe, run, or stop."
