"""JARVIS Closed-Loop Autonomous Screen Agent Plugin (Observe -> Verify -> Act -> Re-Observe).

Controls interactive desktop workflows by combining Windows UIA accessibility
trees, screenshot + Vision/OCR grounding, post-action UI settle verification,
single-step stuck recovery, irreversible CONFIRM/LOCKED safety gates, and an
instant ESC / corner / "Jarvis stop" kill-switch.
"""
from __future__ import annotations

import threading
import time

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
    fn = getattr(player, method, None) if player is not None else None
    if callable(fn):
        try:
            fn(*args)
        except Exception:
            pass


def _run_background(goal: str, max_steps: int, player) -> None:
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
        _call_player(
            player,
            "request_say",
            f"Summarize cleanly in 1-2 short sentences for the user in their language: {summary[:400]}",
        )


def run(parameters: dict, player=None, session_memory=None) -> str:
    params = parameters or {}
    goal = str(params.get("goal") or "").strip()
    raw_action = str(params.get("action") or "").strip().lower()
    action = raw_action or ("run" if goal else "status")

    try:
        max_steps = max(1, min(20, int(params.get("max_steps") or 12)))
    except (TypeError, ValueError):
        max_steps = 12

    from core.evolution import screen_agent

    if action == "status":
        running = screen_agent.is_running()
        last = screen_agent.get_last_report()
        if running:
            return "Autonomous Screen Agent is currently executing on your desktop."
        if last:
            return f"Autonomous Screen Agent idle. Last run: {screen_agent.format_report(last)}"
        return "Autonomous Screen Agent is online and ready (Observe -> Verify -> Act -> Re-Observe, max_steps=12)."

    if action in ("stop", "abort"):
        return screen_agent.request_global_abort("tool: autonomous_screen_agent stop")

    if action == "observe":
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

    if action in ("run", "execute"):
        if not goal:
            return "Provide a desktop GUI `goal` for action='run'."
        if screen_agent.is_running():
            return "Autonomous Screen Agent is already running. Say 'Jarvis stop' or press ESC to abort."
        if bool(params.get("blocking")):
            report = screen_agent.run_screen_goal(goal, max_steps=max_steps, player=player)
            return screen_agent.format_report(report)
        threading.Thread(
            target=_run_background,
            args=(goal, max_steps, player),
            daemon=True,
            name="jarvis-screen-agent",
        ).start()
        return (
            f"Closed-loop Screen Agent engaged for '{goal[:100]}'. "
            "Streaming Observe -> Verify -> Act steps to your HUD (press ESC or say 'Jarvis stop' anytime)."
        )

    return f"Unknown autonomous_screen_agent action: {action!r}. Use status, observe, run, or stop."
