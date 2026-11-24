"""Milestone 12 tests: Bidirectional PC <-> Android WS bridge, PWA endpoints, and safety gates."""

from __future__ import annotations

import json
from pathlib import Path
import threading

from starlette.testclient import TestClient


def test_android_pair_pwa_and_bidirectional_websocket_rpc(tmp_path: Path, monkeypatch):
    from core import confirm
    from dashboard import server as srv_mod

    monkeypatch.setattr(srv_mod, "ANDROID_DEVICES_FILE", tmp_path / "android_devices.json")
    srv = srv_mod.DashboardServer()
    pin = srv.new_key()

    tool_calls: list[tuple[str, dict]] = []
    events_seen: list[dict] = []
    srv.set_tool_executor(lambda name, params: (tool_calls.append((name, params)), f"ran:{name}")[1])
    srv.set_event_callback(lambda ev: events_seen.append(ev))

    with TestClient(srv.app) as client:
        # 1. PWA shell assets served cleanly
        assert client.get("/pwa").status_code == 200
        assert "MARK-LVII" in client.get("/pwa").text
        assert client.get("/manifest.webmanifest").status_code == 200
        assert client.get("/sw.js").status_code == 200
        assert client.get("/pwa-icon.svg").status_code == 200

        # 2. Wrong PIN rejected (401); valid PIN returns token + protocol v2 metadata
        bad = client.post("/api/android/pair", json={"pin": "WRONGPIN", "device_id": "px9"})
        assert bad.status_code == 401
        pair = client.post("/api/android/pair", json={
            "pin": pin, "device_id": "px9", "name": "Pixel 9", "model": "Google Pixel 9",
        })
        assert pair.status_code == 200
        pdata = pair.json()
        assert pdata["ok"] is True and pdata["protocol_version"] == 2
        tok = pdata["token"]

        # 3. Unauthenticated WS rejected (4001), authenticated WS runs bidirectional RPC
        try:
            with client.websocket_connect("/ws?token=invalid"):
                raise AssertionError("Expected WS close on invalid token")
        except Exception:
            pass

        with client.websocket_connect(f"/ws?token={tok}") as ws:
            ws.send_json({
                "type": "device_register",
                "device_id": "px9",
                "name": "Pixel 9",
                "model": "Pixel 9 Pro",
                "android_version": "15",
                "capabilities": ["sos", "camerax", "toggles"],
                "battery": 91,
                "charging": True,
            })
            reg_ack = ws.receive_json()
            assert reg_ack["type"] == "registered" and reg_ack["device_id"] == "px9"
            # Consume android_connected broadcast triggered on registration
            conn_ev = ws.receive_json()
            assert conn_ev["type"] == "android_connected" and conn_ev["device_id"] == "px9"

            # Heartbeat updates live telemetry
            ws.send_json({"type": "heartbeat", "device_id": "px9", "battery": 89, "charging": False})
            hb_ack = ws.receive_json()
            assert hb_ack["type"] == "heartbeat_ack"

            # Android -> PC tool invocation executes through tool_executor
            ws.send_json({
                "type": "tool_invoke",
                "request_id": "r_pc_1",
                "tool": "computer_settings",
                "parameters": {"action": "volume_up"},
            })
            tres = ws.receive_json()
            assert tres["type"] == "tool_result" and tres["ok"] is True
            assert tres["result"] == "ran:computer_settings"
            assert tool_calls == [("computer_settings", {"action": "volume_up"})]

            # Android -> PC event logged & stored
            ws.send_json({
                "type": "android_event",
                "event": "anti_theft_triggered",
                "payload": {"reason": "motion_tamper"},
            })
            ev_bcast = ws.receive_json()
            ev_ack = ws.receive_json()
            assert {ev_bcast["type"], ev_ack["type"]} == {"android_event", "event_ack"}
            assert any(e["event"] == "anti_theft_triggered" for e in events_seen)

            # PC -> Android RPC in background thread while phone WS answers it
            holder: dict = {}

            def _call_phone():
                holder["resp"] = client.post(
                    "/api/android/command",
                    headers={"Authorization": f"Bearer {tok}"},
                    json={"device_id": "px9", "action": "toggle", "params": {"target": "flashlight", "value": "on"}},
                ).json()

            t = threading.Thread(target=_call_phone, daemon=True)
            t.start()
            pc_msg = ws.receive_json()
            assert pc_msg["type"] == "pc_to_android" and pc_msg["action"] == "toggle"
            ws.send_json({
                "type": "android_result",
                "request_id": pc_msg["request_id"],
                "ok": True,
                "data": {"flashlight": True},
            })
            t.join(timeout=3.0)
            assert holder["resp"]["ok"] is True
            assert holder["resp"]["data"]["flashlight"] is True

        # Paired device persisted without tokens
        devs_resp = client.get("/api/android/devices", headers={"Authorization": f"Bearer {tok}"})
        assert devs_resp.status_code == 200
        saved_text = (tmp_path / "android_devices.json").read_text(encoding="utf-8")
        assert tok not in saved_text


def test_android_bridge_plugin_safety_and_voice_lock(tmp_path: Path, monkeypatch):
    import numpy as np
    from core import confirm
    from core.evolution import safety_guard
    from dashboard import server as srv_mod
    from plugins import android_bridge, voice_lock

    monkeypatch.setattr(srv_mod, "ANDROID_DEVICES_FILE", tmp_path / "devs.json")
    srv = srv_mod.DashboardServer()
    srv._android_devices["px9"] = {
        "device_id": "px9", "name": "Pixel", "model": "Pixel 9",
        "connected": True, "privacy_mode": False, "battery": 75, "ws": object(),
    }

    assert safety_guard.classify_risk("android_bridge", "toggle") == safety_guard.SAFE
    assert safety_guard.classify_risk("android_bridge", "siren") == safety_guard.CONFIRM
    assert safety_guard.classify_risk("android_bridge", "camera") == safety_guard.CONFIRM
    assert safety_guard.classify_risk("android_bridge", "whatsapp") == safety_guard.CONFIRM
    assert safety_guard.classify_risk("android_bridge", "sos") == safety_guard.LOCKED

    assert "1 connected" in android_bridge.run({"action": "status"})
    assert "Android Pairing PIN:" in android_bridge.run({"action": "pair_code"})

    # Mock phone dispatch and test CONFIRM gate on siren
    monkeypatch.setattr(srv_mod, "dispatch_android_command_sync", lambda act, p, **kw: {"ok": True, "data": {"act": act}})
    orig_cbs = (confirm._show_cb, confirm._hide_cb, confirm._log_cb)
    logs = []
    confirm.resolve(False)
    try:
        confirm.bind(lambda *a: None, lambda: None, lambda m: logs.append(m))
        pending = android_bridge.run({"action": "siren"})
        assert "[CONFIRMATION_PENDING]" in pending
        confirm.resolve(True)

        # Enroll Biometric Voice Lock -> LOCKED sos blocked until speaker verifies
        monkeypatch.setattr(voice_lock, "DEFAULT_STORE_PATH", tmp_path / "vp.json")
        t = np.linspace(0.0, 1.2, 19200, endpoint=False)
        sig = np.sin(2.0 * np.pi * 145.0 * t) + 0.5 * np.sin(2.0 * np.pi * 435.0 * t)
        voice_lock.run({"action": "enroll", "samples": sig.tolist(), "sample_rate": 16000})
        blocked_sos = android_bridge.run({"action": "sos", "contact": "+15550199"})
        assert "[VOICE_LOCKED]" in blocked_sos
    finally:
        confirm.resolve(False)
        confirm.bind(*orig_cbs)
