"""plugins/android_bridge.py — Bidirectional PC <-> Android MAX companion controller plugin."""

from __future__ import annotations

import json

PLUGIN = {
    "name": "android_bridge",
    "brief": "Control paired Android MAX companion (toggles, GPS, camera, WhatsApp, siren, SOS)",
    "description": (
        "Commands connected Android phones running the JARVIS MAX companion app or mobile PWA fallback "
        "over dashboard/server.py's authenticated WebSocket bridge. Supports device listing, pairing PIN "
        "generation, battery/GPS inspection, SAFE device toggles (flashlight, wifi, bluetooth, dnd, "
        "brightness, sound), accessibility scroll/speech, CONFIRM-gated siren/lock/camera/WhatsApp/calls, "
        "and LOCKED-gated Emergency SOS. Actions: status, devices, pair_code, battery_gps, toggle, "
        "scroll, speak, siren, lock_screen, camera, whatsapp, call, sos."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "action": {
                "type": "STRING",
                "description": (
                    "Action: status | devices | pair_code | battery_gps | toggle | scroll | speak | "
                    "siren | lock_screen | camera | whatsapp | call | sos (default: status)"
                ),
            },
            "device_id": {
                "type": "STRING",
                "description": "Optional target Android device_id (defaults to active connected phone).",
            },
            "target": {
                "type": "STRING",
                "description": "For toggle: flashlight | wifi | bluetooth | dnd | brightness | sound | alarm | launch_app.",
            },
            "value": {
                "type": "STRING",
                "description": "Toggle state ('on', 'off', level integer, or app package/label) or spoken message.",
            },
            "contact": {
                "type": "STRING",
                "description": "Contact name or phone number for whatsapp, call, or emergency SOS.",
            },
            "message": {
                "type": "STRING",
                "description": "Message payload for whatsapp, speak, or sos reason.",
            },
            "mode": {
                "type": "STRING",
                "description": "Camera mode ('photo', 'selfie', 'video', 'zoom') or scroll direction ('down', 'up').",
            },
        },
        "required": [],
    },
}

_GATED_ACTIONS = {"siren", "lock_screen", "camera", "whatsapp", "call", "sos"}


def _dispatch_to_phone(action: str, payload: dict, *, device_id: str | None = None) -> str:
    from dashboard import server as srv_mod
    res = srv_mod.dispatch_android_command_sync(action, payload, device_id=device_id, timeout=8.0)
    if res.get("ok"):
        return f"Android '{action}' completed: {json.dumps(res.get('data') or res, ensure_ascii=False)}"
    return f"Android '{action}' unavailable: {res.get('error', 'unknown error')}"


def run(parameters: dict, player=None, session_memory=None) -> str:
    """Plugin entrypoint for bidirectional PC <-> Android commands."""
    params = parameters if isinstance(parameters, dict) else {}
    action = str(params.get("action") or "status").strip().lower()
    device_id = str(params.get("device_id") or "").strip() or None

    from dashboard import server as srv_mod
    srv = srv_mod.get_active_server()

    if action == "status":
        if srv is None:
            return "Android Bridge ready (dashboard server standby — start JARVIS dashboard to pair a phone)."
        devs = srv.list_android_devices()
        connected = [d for d in devs if d.get("connected")]
        return (
            f"Android Bridge online ({srv.get_url()}/pwa): "
            f"{len(connected)} connected / {len(devs)} paired device(s)."
        )

    if action == "devices":
        if srv is None:
            return "No active dashboard server."
        devs = srv.list_android_devices()
        if not devs:
            return "No Android companion devices paired yet. Generate a pairing code via pair_code."
        return "Android Devices:\n" + json.dumps(devs, indent=2, ensure_ascii=False)

    if action == "pair_code":
        if srv is None:
            return "Cannot generate pairing PIN while dashboard server is offline."
        pin = srv.new_key(expiry_secs=600)
        fp = srv_mod.cert_fingerprint_sha256()
        return (
            f"Android Pairing PIN: {pin} (valid 10 min) | PWA URL: {srv.get_url()}/pwa"
            + (f" | Cert SHA-256: {fp[:16]}..." if fp else "")
        )

    if action == "battery_gps":
        if srv is not None and not srv.list_connected_device_ids() and srv.list_android_devices():
            return "Last known device snapshots:\n" + json.dumps(srv.list_android_devices(), indent=2)
        return _dispatch_to_phone("battery_gps", {}, device_id=device_id)

    if action == "toggle":
        target = str(params.get("target") or "").strip().lower()
        if not target:
            return "Provide target='flashlight|wifi|bluetooth|dnd|brightness|sound|alarm|launch_app'."
        payload = {"target": target, "value": params.get("value")}
        return _dispatch_to_phone("toggle", payload, device_id=device_id)

    if action == "scroll":
        payload = {
            "direction": str(params.get("mode") or params.get("direction") or "down").strip().lower(),
            "steps": int(params.get("steps") or 1),
            "label": str(params.get("target") or "").strip(),
        }
        return _dispatch_to_phone("scroll", payload, device_id=device_id)

    if action == "speak":
        text = str(params.get("message") or params.get("value") or "").strip()
        return _dispatch_to_phone("speak", {"text": text}, device_id=device_id)

    if action in _GATED_ACTIONS:
        from core.evolution import safety_guard
        payload = {
            k: v for k, v in params.items()
            if k not in ("action", "device_id") and v is not None
        }
        title_map = {
            "sos": "Trigger Android Emergency SOS + Live GPS",
            "siren": "Sound Anti-Theft Max Siren on Phone",
            "lock_screen": "Lock Paired Android Device Immediately",
            "camera": "Capture Photo / Video via Android CameraX",
            "whatsapp": "Dispatch Android WhatsApp Action",
            "call": "Initiate Android Speakerphone Call",
        }
        detail = f"Device: {device_id or 'active companion'} | Payload: {json.dumps(payload, ensure_ascii=False)[:140]}"
        return safety_guard.enforce_gate(
            "android_bridge",
            action,
            title_map.get(action, f"Android Action: {action}"),
            detail,
            lambda: _dispatch_to_phone(action, payload, device_id=device_id),
            key=f"android_{action}",
        )

    return (
        f"Unknown android_bridge action '{action}'. Use: "
        "status, devices, pair_code, battery_gps, toggle, scroll, speak, siren, lock_screen, camera, whatsapp, call, sos."
    )
