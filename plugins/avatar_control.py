"""plugins/avatar_control.py — Standalone transparent 2D Avatar overlay & 8 sentiment gesture trigger."""

from __future__ import annotations

from core.avatar_gestures import (
    GESTURES,
    get_active_gesture,
    sentiment_to_gesture,
    trigger_global_gesture,
)

PLUGIN = {
    "name": "avatar_control",
    "brief": "Show/hide standalone transparent 2D desktop avatar overlay and trigger 8 sentiment gestures",
    "description": (
        "Controls the floating transparent 2D holographic avatar overlay wired to core/avatar.py and "
        "core/viseme.py, and triggers any of 8 sentiment-mapped avatar gestures (greeting, approve, "
        "think, alert, empathy, celebrate, focus, rest). Actions: status, show, hide, gesture, list_gestures."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "action": {
                "type": "STRING",
                "description": "Action: status | show | hide | gesture | list_gestures (default: status)",
            },
            "gesture": {
                "type": "STRING",
                "description": "Gesture or sentiment label (e.g. greeting, celebrate, alert, curious, calm).",
            },
            "text": {
                "type": "STRING",
                "description": "Optional phrase to feed into viseme lip-sync during the gesture.",
            },
            "duration": {
                "type": "NUMBER",
                "description": "Gesture duration in seconds.",
            },
        },
        "required": [],
    },
}


def run(parameters: dict, player=None, session_memory=None) -> str:
    """Plugin entrypoint for transparent 2D avatar overlay & sentiment gestures."""
    params = parameters if isinstance(parameters, dict) else {}
    action = str(params.get("action") or "status").strip().lower()

    if action == "status":
        cur = get_active_gesture()
        return (
            f"Avatar Control ready (gestures={len(GESTURES)}, current={cur.get('gesture')}, "
            f"active={cur.get('active', False)})."
        )

    if action == "list_gestures":
        lines = [
            f"- {name} ({spec['sentiment']}): {spec['description']}"
            for name, spec in GESTURES.items()
        ]
        return "8 Sentiment-Triggered Avatar Gestures:\n" + "\n".join(lines)

    if action == "show":
        if player and hasattr(player, "show_avatar_overlay"):
            player.show_avatar_overlay()
            return "Standalone transparent 2D Avatar overlay displayed."
        return "Standalone avatar overlay signal prepared (no active Qt HUD attached in headless mode)."

    if action == "hide":
        if player and hasattr(player, "hide_avatar_overlay"):
            player.hide_avatar_overlay()
            return "Standalone transparent 2D Avatar overlay hidden."
        return "Standalone avatar overlay hide noted (headless mode)."

    if action in ("gesture", "react", "sentiment"):
        cue = str(params.get("gesture") or params.get("sentiment") or params.get("text") or "approve")
        text = str(params.get("text") or "")
        dur = params.get("duration")
        state = trigger_global_gesture(cue, duration=float(dur) if dur is not None else None, text=text)
        g_name = state["gesture"]
        if player and hasattr(player, "trigger_avatar_gesture"):
            player.trigger_avatar_gesture(g_name, text)
        return f"Triggered avatar gesture '{g_name}' (sentiment={state['sentiment']})."

    # Also allow calling by direct gesture name
    if action in GESTURES:
        state = trigger_global_gesture(action, text=str(params.get("text") or ""))
        if player and hasattr(player, "trigger_avatar_gesture"):
            player.trigger_avatar_gesture(state["gesture"], str(params.get("text") or ""))
        return f"Triggered avatar gesture '{state['gesture']}'."

    return f"Unknown avatar_control action '{action}'. Use: status, show, hide, gesture, list_gestures."
