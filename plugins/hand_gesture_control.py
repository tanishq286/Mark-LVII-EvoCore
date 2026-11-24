"""plugins/hand_gesture_control.py — Touchless camera hand-gesture controller plugin."""

from __future__ import annotations

from core.hand_gestures import (
    GESTURE_ACTIONS,
    classify_hand_landmarks,
    get_controller,
)

PLUGIN = {
    "name": "hand_gesture_control",
    "brief": "Touchless camera hand-gesture recognition for media, volume, and scroll",
    "description": (
        "Starts, stops, or tests MediaPipe/OpenCV touchless hand-gesture control mapped to Windows "
        "media play/pause, volume up/down, mute, scrolling, and tab navigation. "
        "Actions: status, start, stop, list_gestures, classify."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "action": {
                "type": "STRING",
                "description": "Action: status | start | stop | list_gestures | classify (default: status)",
            },
            "landmarks": {
                "type": "ARRAY",
                "items": {"type": "STRING"},
                "description": "Optional 21 [x, y, z] normalized hand landmarks for action='classify'.",
            },
            "handedness": {
                "type": "STRING",
                "description": "Handedness ('Right' or 'Left') for classify.",
            },
        },
        "required": [],
    },
}


def run(parameters: dict, player=None, session_memory=None) -> str:
    """Plugin entrypoint for MediaPipe touchless hand gestures."""
    params = parameters if isinstance(parameters, dict) else {}
    action = str(params.get("action") or "status").strip().lower()
    ctrl = get_controller()

    if action == "status":
        running = ctrl.is_running()
        return (
            f"Hand Gesture Control: {'RUNNING' if running else 'IDLE'} "
            f"| backend={ctrl.backend} | last_gesture={ctrl.last_gesture} | events={ctrl.events_count}"
        )
    if action == "start":
        return ctrl.start()
    if action == "stop":
        return ctrl.stop()
    if action in ("list", "list_gestures"):
        lines = [f"- {g} -> {spec['action']} ({spec['description']})" for g, spec in GESTURE_ACTIONS.items()]
        return "Supported Hand Gestures:\n" + "\n".join(lines)
    if action == "classify":
        lms = params.get("landmarks") or []
        res = classify_hand_landmarks(lms, handedness=str(params.get("handedness") or "Right"))
        g = res["gesture"]
        mapped = GESTURE_ACTIONS.get(g, {}).get("action", "none")
        return f"Gesture: {g} (confidence={res['confidence']:.2f}, mapped_action={mapped})"
    return f"Unknown hand_gesture_control action '{action}'. Use: status, start, stop, list_gestures, classify."
