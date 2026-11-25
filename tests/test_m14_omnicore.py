"""Milestone 14 tests: Phone HTTPS Remote Supercharge + Mark-LVII OmniCore.

Covers:
  1. Phone HTTPS/TLS SAN cert generation, virtual-adapter IPv4 filtering, `/pwa`,
     `/api/android/qr`, `/api/cert-fingerprint`, `/ws` auth pass/reject, live screen
     stream + normalized tap/type, push-to-talk PCM16, live mission logs, and 1-tap
     remote approval for HUD `CONFIRM` and `ChoiceBanner`.
  2. Parallel Multi-Agent Swarm + SQLite Blackboard + independent `CriticAgent`
     (parallel wave execution, Knowledge Graph seeding, remediation retry on rejected
     output, side-effect gate).
  3. Persistent Long-Horizon Missions (`MissionRunner` SQLite restart recovery from
     checkpoint, event & screen triggers, error backoff self-recovery, phone alerts/approvals).
  4. SQLite Entity-Relation Knowledge Graph (`ingest_conversation_turn`, 2-hop
     `query_graph`, idle `consolidate_graph` alias deduplication, `knowledge_rag` modes).
  5. Idle-Time Meta-Optimizer (`IMMUTABLE` target protection, non-idle skip, replay
     benchmark auto-revert on non-improving candidate, staging measurably better
     `evolve/*` upgrade into Evolution Digest, `"Approve upgrade 1"` merge & live
     router override application).
  6. Universal MCP Client Bridge (`discover_and_mount`, `SAFE`/`CONFIRM`/`LOCKED`
     `safety_guard` + `voice_lock` + SQL argument risk wrapping, `<<<UNTRUSTED>>>`,
     ecosystem summary).
"""
from __future__ import annotations

import json
from pathlib import Path
import sys
import time

import numpy as np
import pytest
from starlette.testclient import TestClient

REPO_ROOT = Path(__file__).resolve().parent.parent


# ─────────────────────────────────────────────────────────────────────────────
#  1. Phone HTTPS Remote, SAN Certs, QR Pairing, Screen, Voice & 1-Tap Approval
# ─────────────────────────────────────────────────────────────────────────────
def test_m14_phone_https_certs_qr_screen_voice_and_1tap_confirm(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    from core import confirm
    from dashboard import server as srv_mod

    assert srv_mod._VIRTUAL_ADAPTER_RE.search("vEthernet (WSL (Hyper-V firewall))")
    assert srv_mod._VIRTUAL_ADAPTER_RE.search("Hyper-V Virtual Ethernet Adapter")
    assert srv_mod._VIRTUAL_ADAPTER_RE.search("Tailscale Tunnel")
    assert not srv_mod._VIRTUAL_ADAPTER_RE.search("Wireless LAN adapter Wi-Fi")
    assert not srv_mod._VIRTUAL_ADAPTER_RE.search("Ethernet")

    certs_dir = tmp_path / "certs"
    monkeypatch.setattr(srv_mod, "ANDROID_DEVICES_FILE", tmp_path / "android_devices.json")
    assert srv_mod._ensure_certs(certs_dir=certs_dir, extra_ips=["172.16.6.143"]) is True

    crt_file = certs_dir / "jarvis.crt"
    key_file = certs_dir / "jarvis.key"
    assert crt_file.is_file() and key_file.is_file()
    fp = srv_mod.cert_fingerprint_sha256(crt_file)
    assert len(fp) == 64
    sans = srv_mod.cert_san_entries(crt_file)
    assert "DNS:localhost" in sans
    assert "IP:127.0.0.1" in sans
    assert "IP:172.16.6.143" in sans

    screen_inputs: list[dict] = []
    srv = srv_mod.DashboardServer(certs_dir=certs_dir)
    srv.set_screen_handlers(
        capture_fn=lambda: (srv_mod._synthetic_screen_jpeg(), "image/jpeg"),
        bounds_fn=lambda: (0, 0, 1920, 1080),
        input_fn=lambda ev: (screen_inputs.append(dict(ev)), {"ok": True, "detail": "injected"})[1],
    )
    woke: list[bool] = []
    srv.set_wake_callback(lambda: woke.append(True))

    with TestClient(srv.app) as client:
        r_pwa = client.get("/pwa")
        assert r_pwa.status_code == 200
        assert "screen" in r_pwa.text.lower()

        r_fp = client.get("/api/cert-fingerprint")
        assert r_fp.status_code == 200
        fp_json = r_fp.json()
        assert fp_json["https"] is True
        assert fp_json["sha256"] == fp
        assert "IP:172.16.6.143" in fp_json["san"]

        r_qr = client.get("/api/android/qr")
        assert r_qr.status_code == 200
        qr_json = r_qr.json()
        assert qr_json["ok"] is True
        assert qr_json["https"] is True
        assert qr_json["pin"] and len(qr_json["pin"]) == 8
        assert f"pin={qr_json['pin']}" in qr_json["pairing_url"]
        assert fp in qr_json["pairing_url"]

        r_pair = client.post("/api/android/pair", json={"pin": qr_json["pin"], "device_id": "phone1"})
        assert r_pair.status_code == 200
        tok = r_pair.json()["token"]
        headers = {"Authorization": f"Bearer {tok}"}

        # WebSocket auth reject vs pass
        with pytest.raises(Exception):
            with client.websocket_connect("/ws?token=bad_token"):
                pass

        with client.websocket_connect(f"/ws?token={tok}") as ws:
            ws.send_json({"type": "confirm_pending_req"})
            p_msg = ws.receive_json()
            assert p_msg["type"] == "confirm_pending"

        # Live PC screen frame + remote normalized tap (both nx/ny and norm_x/norm_y) & type
        r_frame = client.get("/api/screen/frame?format=json", headers=headers)
        assert r_frame.status_code == 200
        f_data = r_frame.json()
        assert f_data["ok"] is True and f_data["width"] == 1920 and len(f_data["image_b64"]) > 20

        r_tap = client.post("/api/screen/input", headers=headers, json={"action": "tap", "nx": 0.5, "ny": 0.25})
        assert r_tap.status_code == 200 and r_tap.json()["ok"] is True
        assert screen_inputs[-1]["x"] == int(round(0.5 * 1919))
        assert screen_inputs[-1]["y"] == int(round(0.25 * 1079))

        r_type = client.post("/api/screen/input", headers=headers, json={"action": "type", "text": "hello jarvis"})
        assert r_type.status_code == 200 and r_type.json()["ok"] is True
        assert screen_inputs[-1]["text"] == "hello jarvis"

        # Push-to-talk PCM16 WebSocket audio
        with client.websocket_connect(f"/ws/phone-audio?token={tok}") as audio_ws:
            pcm_bytes = (np.zeros(320, dtype=np.int16)).tobytes()
            audio_ws.send_bytes(pcm_bytes)
            queued = None
            deadline = time.monotonic() + 2.0
            while time.monotonic() < deadline:
                if not srv._phone_audio_queue.empty():
                    queued = srv._phone_audio_queue.get_nowait()
                    break
                time.sleep(0.02)
            assert queued is not None
            assert queued["mime_type"] == "audio/pcm"
            assert queued["data"] == pcm_bytes
            assert woke

        # Live mission log stream
        srv_mod.broadcast_mission_log("Swarm wave 1 started", source="swarm", status="executing")
        r_live = client.get("/api/missions/live", headers=headers)
        assert r_live.status_code == 200
        assert any("Swarm wave 1 started" in item["text"] for item in r_live.json()["logs"])

        # 1-Tap remote approval for HUD CONFIRM & ChoiceBanner
        orig_cbs = (confirm._show_cb, confirm._hide_cb, confirm._log_cb)
        orig_choice = (confirm._show_choice_cb, confirm._hide_choice_cb)
        executed_flag: list[str] = []
        selected_choice: list[tuple[int, str]] = []
        confirm.resolve(False)
        confirm.resolve_choice(None)
        try:
            confirm.bind(lambda *a: None, lambda: None, lambda *a: None)
            confirm.bind_choice(lambda *a: None, lambda: None)

            gate_msg = confirm.request(
                key="m14_phone_test",
                title="Deploy Production Patch",
                detail="Merge branch into main",
                run=lambda: (executed_flag.append("confirmed"), "Deployed")[1],
            )
            assert "[CONFIRMATION_PENDING]" in gate_msg

            pend = client.get("/api/confirm/pending", headers=headers).json()
            assert pend["confirm"]["title"] == "Deploy Production Patch"

            res_ok = client.post("/api/confirm/resolve", headers=headers, json={"kind": "confirm", "accepted": True})
            assert res_ok.status_code == 200
            deadline = time.monotonic() + 2.0
            while time.monotonic() < deadline and not executed_flag:
                time.sleep(0.02)
            assert executed_flag == ["confirmed"]

            # ChoiceBanner remote 1-tap selection (non-blocking callback API)
            ch_msg = confirm.request_choice(
                key="m14_choice",
                title="Pick Target Window",
                detail="Multiple Chrome windows matched:",
                options=["Chrome - Docs", "Chrome - GitHub"],
                on_select=lambda idx, label: (selected_choice.append((idx, label)), f"Picked {label}")[1],
            )
            assert "[CHOICE_PENDING]" in ch_msg
            cp = client.get("/api/confirm/pending", headers=headers).json()
            assert cp["choice"]["title"] == "Pick Target Window"

            r_ch = client.post(
                "/api/confirm/resolve",
                headers=headers,
                json={"kind": "choice", "selection": "2", "sync": True},
            )
            assert r_ch.status_code == 200
            assert selected_choice == [(1, "Chrome - GitHub")]
        finally:
            confirm.resolve(False)
            confirm.resolve_choice(None)
            confirm.bind(*orig_cbs)
            confirm.bind_choice(*orig_choice)


# ─────────────────────────────────────────────────────────────────────────────
#  2. Parallel Multi-Agent Swarm + Blackboard + Independent Critic Agent
# ─────────────────────────────────────────────────────────────────────────────
def test_m14_parallel_swarm_blackboard_and_critic_gate(tmp_path: Path):
    from core import confirm
    from core.evolution.swarm import (
        ROLE_CODER,
        ROLE_RESEARCHER,
        ROLE_SECURITY_AUDITOR,
        SwarmCoordinator,
        SwarmTask,
        is_complex_swarm_goal,
    )
    from plugins import swarm_orchestrator

    assert is_complex_swarm_goal("Research the API docs and implement a verified Python client with security audit")

    bb_db = tmp_path / "bb.sqlite"
    tel_db = tmp_path / "tel.sqlite"
    coord = SwarmCoordinator(db_path=bb_db, telemetry_db_path=tel_db, max_workers=4)

    tasks = [
        SwarmTask(task_id="t_res", role=ROLE_RESEARCHER, title="Gather Spec", instruction="Find API endpoints"),
        SwarmTask(task_id="t_code", role=ROLE_CODER, title="Write Module", instruction="Implement safe helper"),
        SwarmTask(
            task_id="t_sec",
            role=ROLE_SECURITY_AUDITOR,
            title="Audit Output",
            instruction="Verify blackboard artifacts",
            depends_on=["t_res", "t_code"],
        ),
    ]

    coder_attempts = {"n": 0}

    def _agent_fn(task: SwarmTask, facts: list[dict], remediation: str) -> str:
        if task.task_id == "t_res":
            return "Verified REST spec: GET /v1/items uses Bearer token and JSON pagination."
        if task.task_id == "t_code":
            coder_attempts["n"] += 1
            if coder_attempts["n"] == 1:
                return "```python\nimport os\nos.system('rm -rf /')\n```"
            assert "CRITIC REJECTED" in remediation
            return "```python\ndef fetch_items(url: str) -> list[dict]:\n    return [{'url': url}]\n```"
        assert len(facts) >= 2
        return f"Security audit passed across {len(facts)} blackboard facts; zero unsafe calls."

    rep = coord.execute_swarm(
        "Research and implement item fetcher with security audit",
        decompose_fn=lambda _g, _c: tasks,
        agent_llm_fn=_agent_fn,
        synthesize_fn=lambda _g, _s, res, _f: " | ".join(r["title"] for r in res if r["verified"]),
    )
    assert rep["status"] == "done"
    assert rep["waves_executed"] == 2
    assert coder_attempts["n"] == 2
    assert all(t["verified"] for t in rep["tasks"])
    assert rep["critic_verdict"]["passed"] is True

    snap = coord.blackboard.get_run_snapshot(rep["run_id"])
    assert snap is not None and snap["status"] == "done"
    assert any(f["author_role"] == "critic" for f in snap["facts"])

    # Side-effecting CONFIRM tool is gated by Critic before execution
    orig_cbs = (confirm._show_cb, confirm._hide_cb, confirm._log_cb)
    side_effect_ran: list[tuple[str, dict]] = []
    confirm.resolve(False)
    try:
        confirm.bind(lambda *a: None, lambda: None, lambda *a: None)
        gated_rep = coord.execute_swarm(
            "Delete temporary build artifact",
            decompose_fn=lambda _g, _c: [
                SwarmTask(
                    task_id="t_del",
                    role=ROLE_CODER,
                    title="Delete File",
                    instruction="Remove file",
                    tool_name="file_controller",
                    tool_args={"action": "delete", "path": "stale.tmp"},
                    has_side_effects=True,
                )
            ],
            run_tool_fn=lambda name, args: (side_effect_ran.append((name, args)), "Deleted")[1],
        )
        assert gated_rep["status"] == "awaiting_confirmation"
        assert side_effect_ran == []
    finally:
        confirm.resolve(False)
        confirm.bind(*orig_cbs)

    assert "OmniCore" in swarm_orchestrator.run({"action": "status"})


# ─────────────────────────────────────────────────────────────────────────────
#  3. Persistent Long-Horizon Missions (SQLite Restart, Triggers, Self-Recovery)
# ─────────────────────────────────────────────────────────────────────────────
def test_m14_persistent_missions_restart_triggers_recovery_and_phone_push(tmp_path: Path):
    from core import confirm
    from core.evolution.mission_runner import MissionRunner

    db = tmp_path / "missions.sqlite"
    tel_db = tmp_path / "tel.sqlite"
    step_calls: list[str] = []
    phone_alerts: list[dict] = []
    fail_once = {"step2_failed": False}

    def _exec_step(_msn: dict, step: dict) -> str:
        sid = step["id"]
        step_calls.append(sid)
        if sid == "s2" and not fail_once["step2_failed"]:
            fail_once["step2_failed"] = True
            return "failed: transient connection reset"
        return f"completed:{sid}"

    runner1 = MissionRunner(
        db_path=db,
        telemetry_db_path=tel_db,
        step_executor=_exec_step,
        phone_push_fn=phone_alerts.append,
    )
    msn = runner1.create_mission(
        "Multi-stage release pipeline",
        title="Release Pipeline",
        steps=[
            {"id": "s1", "title": "Build", "instruction": "Build artifacts"},
            {"id": "s2", "title": "Smoke Test", "instruction": "Verify staging"},
            {"id": "s3", "title": "Notify", "instruction": "Publish release summary"},
        ],
        trigger_type="immediate",
        max_retries=2,
        now_ts=1000.0,
    )
    mid = msn["mission_id"]

    r1 = runner1.run_mission_once(mid, now_ts=1000.0)
    assert r1["status"] == "active"
    assert r1["current_step_idx"] == 1
    assert r1["retry_count"] == 1
    assert step_calls == ["s1", "s2"]

    # Simulate unexpected crash mid-run (`status='running'`) and process restart
    runner1.set_status(mid, "running")
    runner2 = MissionRunner(
        db_path=db,
        telemetry_db_path=tel_db,
        step_executor=_exec_step,
        screen_probe_fn=lambda: {"app": "Code.exe", "title": "Build Succeeded - Visual Studio Code", "text": "0 errors"},
        phone_push_fn=phone_alerts.append,
    )
    recovered = runner2.get_mission(mid)
    assert recovered is not None and recovered["status"] == "active"
    assert recovered["current_step_idx"] == 1

    runner2.tick(now_ts=1005.0)
    done_msn = runner2.get_mission(mid)
    assert done_msn is not None and done_msn["status"] == "completed"
    assert step_calls == ["s1", "s2", "s2", "s3"]
    assert any(a["level"] == "completed" for a in phone_alerts)

    ev_msn = runner2.create_mission(
        "Handle charger unplug",
        steps=[{"id": "ev1", "title": "Lock audit", "instruction": "Check battery"}],
        trigger_type="event",
        trigger_spec={"event": "charger_disconnected"},
    )
    woken = runner2.notify_event("charger_disconnected", {"battery": 74})
    assert ev_msn["mission_id"] in woken
    assert runner2.get_mission(ev_msn["mission_id"])["status"] == "completed"

    scr_msn = runner2.create_mission(
        "React when VS Code shows Build Succeeded",
        steps=[{"id": "sc1", "title": "Archive log", "instruction": "Save build"}],
        trigger_type="screen_trigger",
        trigger_spec={"window_contains": "Build Succeeded"},
    )
    runner2.tick(now_ts=2000.0)
    assert runner2.get_mission(scr_msn["mission_id"])["status"] == "completed"

    orig_cbs = (confirm._show_cb, confirm._hide_cb, confirm._log_cb)
    confirm.bind(None, None, None)
    try:
        ap_msn = runner2.create_mission(
            "Gated production push",
            steps=[{"id": "ap1", "title": "Push Tag", "instruction": "Push tag", "requires_approval": True}],
            trigger_type="immediate",
            now_ts=3000.0,
        )
        runner2.run_mission_once(ap_msn["mission_id"], now_ts=3000.0)
        assert runner2.get_mission(ap_msn["mission_id"])["status"] == "waiting_approval"
        assert any(a["level"] == "approval_required" for a in phone_alerts)

        runner2.approve_waiting_step(ap_msn["mission_id"], now_ts=3005.0)
        assert runner2.get_mission(ap_msn["mission_id"])["status"] == "completed"
    finally:
        confirm.bind(*orig_cbs)


# ─────────────────────────────────────────────────────────────────────────────
#  4. Entity-Relation Knowledge Graph & `knowledge_rag` Hybrid Integration
# ─────────────────────────────────────────────────────────────────────────────
def test_m14_entity_relation_knowledge_graph_and_rag_integration(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    from core.memory import knowledge_graph as kg
    from plugins import knowledge_rag as kr

    gdb = tmp_path / "kg.sqlite"
    monkeypatch.setattr(kg, "DEFAULT_GRAPH_DB", gdb)

    ing1 = kg.ingest_conversation_turn(
        "My sister Ayse is collaborating with me on Finsol and editing apps/web/dashboard.tsx. "
        "I always prefer dark cybernetic UI themes. Every morning run deployment health checks.",
        "Completed initial review and deployed Finsol partitioned search migration.",
        db_path=gdb,
    )
    assert ing1["entities_touched"] >= 5
    assert ing1["relations_touched"] >= 5

    q = kg.query_graph("Ayse", max_hops=2, top_k=10, db_path=gdb)
    node_ids = {n["entity_id"] for n in q["nodes"]}
    assert "person:ayse" in node_ids
    assert "project:finsol" in node_ids
    assert any(nid.startswith("file_path:") for nid in node_ids)

    kg.upsert_entity("project", "Finsol Platform", summary="Alias duplicate", aliases=["Finsol"], db_path=gdb)
    kg.add_relation("project:finsol_platform", "touches_file", "file_path:apps/web/dashboard.tsx", db_path=gdb)

    cons = kg.consolidate_graph(sync_long_term=False, db_path=gdb)
    assert cons["entities_merged"] >= 1
    assert cons["total_entities"] >= 5

    out_ing = kr.run({"mode": "graph_ingest", "query": "Working on Mark-LVII core/evolution/swarm.py"})
    assert "Ingested into Knowledge Graph" in out_ing

    out_gq = kr.run({"mode": "graph_query", "query": "Finsol", "max_hops": 2})
    assert "<<<UNTRUSTED source=knowledge_graph>>>" in out_gq
    assert "Finsol" in out_gq

    out_gs = kr.run({"mode": "graph_stats"})
    assert "Knowledge Graph stats:" in out_gs


# ─────────────────────────────────────────────────────────────────────────────
#  5. Meta-Optimizer Immutability, Replay Gate & Live Router Override
# ─────────────────────────────────────────────────────────────────────────────
def test_m14_meta_optimizer_immutability_and_replay_benchmark_gate(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    from core.cross_os import run_git
    from core.evolution import meta_optimizer, reflection_loop, telemetry
    from providers import local_router

    for forbidden in (
        "core/evolution/safety_guard.py",
        "safety_guard",
        "plugins/file_vault.py",
        "file_vault",
        "plugins/voice_lock.py",
        "voice_lock",
        "core/confirm.py",
        "confirm",
    ):
        ok, _rel, err = meta_optimizer.assert_optimizable_target(REPO_ROOT, forbidden)
        assert ok is False and "IMMUTABLE" in err.upper()

    repo = tmp_path / "mini_repo"
    (repo / "plugins").mkdir(parents=True)
    (repo / "tests" / "evolved").mkdir(parents=True)
    (repo / "actions").mkdir(parents=True)

    baseline_plugin = '''"""Baseline demo plugin with missing-arg bug."""
from __future__ import annotations

PLUGIN = {
    "name": "demo_opt",
    "brief": "Demo plugin for meta-optimizer replay benchmark testing.",
    "description": (
        "Demo plugin for meta-optimizer replay benchmark testing across historical "
        "failed telemetry invocations and 10-dimension quality scoring."
    ),
    "parameters": {"type": "OBJECT", "properties": {"query": {"type": "STRING"}}, "required": []},
}


def run(parameters: dict, player=None, session_memory=None) -> str:
    _ = player, session_memory
    try:
        params = parameters if isinstance(parameters, dict) else {}
        if params.get("mode") == "strict" and not params.get("query"):
            return "failed: missing query in strict mode"
        return "demo_opt baseline ready"
    except Exception as exc:
        return f"error: {exc}"
'''
    (repo / "plugins" / "demo_opt.py").write_text(baseline_plugin, encoding="utf-8")
    (repo / "plugins" / "file_vault.py").write_text('"""Immutable stub."""\n', encoding="utf-8")

    for git_args in (
        ("init", "-b", "main"),
        ("config", "user.name", "Test"),
        ("config", "user.email", "test@localhost"),
        ("add", "."),
        ("commit", "-m", "init"),
    ):
        r = run_git(repo, *git_args)
        assert r.returncode == 0, r.stderr

    tel_db = tmp_path / "telemetry.sqlite"
    meta_db = tmp_path / "meta.sqlite"
    monkeypatch.setattr(meta_optimizer, "DEFAULT_META_DB", meta_db)
    digest_path = tmp_path / "evolution_digest.json"
    ledger_path = tmp_path / "evolution_commits.json"

    for _ in range(3):
        telemetry.record_tool_event(
            "demo_opt",
            {"mode": "strict"},
            "failed: missing query in strict mode",
            420.0,
            success=False,
            db_path=tel_db,
        )
        telemetry.record_tool_event(
            "file_vault",
            {"action": "decrypt"},
            "failed: wrong passphrase",
            50.0,
            success=False,
            db_path=tel_db,
        )
    for _ in range(3):
        telemetry.record_route_event("coding", "qwen2.5-coder:14b", "ollama", False, 9200.0, escalated=True, db_path=tel_db)
        telemetry.record_route_event("coding", "qwen3:8b", "ollama", True, 350.0, escalated=False, db_path=tel_db)

    bottlenecks = meta_optimizer.analyze_telemetry_bottlenecks(repo_root=repo, db_path=tel_db)
    cand_names = [c["plugin_name"] for c in bottlenecks["plugin_candidates"]]
    assert "demo_opt" in cand_names
    assert "file_vault" not in cand_names
    assert "file_vault" in bottlenecks["skipped_immutable"]
    assert len(bottlenecks["routing_candidates"]) >= 1

    not_idle = meta_optimizer.run_idle_optimization(
        repo,
        db_path=tel_db,
        digest_path=digest_path,
        meta_db_path=meta_db,
        min_idle_seconds=180.0,
        idle_seconds_override=12.0,
    )
    assert not_idle["skipped_not_idle"] is True

    # Non-improving candidate -> auto-reverted, 0 evolve/* branches left
    unimproved = baseline_plugin.replace("demo_opt baseline ready", "demo_opt still buggy")
    bad_res = meta_optimizer.optimize_plugin_candidate(
        repo,
        "demo_opt",
        db_path=tel_db,
        meta_db_path=meta_db,
        candidate_fn=lambda _code, _m, _cases: unimproved,
        validator_script=REPO_ROOT / "scripts" / "validate_plugin.py",
    )
    assert bad_res["ok"] is False
    assert bad_res["reverted"] is True
    assert bad_res["measurably_better"] is False
    assert run_git(repo, "branch", "--list", "evolve/*").stdout.strip() == ""

    # Measurably better candidate -> staged on evolve/* & queued in Evolution Digest
    improved_plugin = baseline_plugin.replace(
        'return "failed: missing query in strict mode"',
        'return "strict fallback: handled empty query cleanly"',
    )
    idle_res = meta_optimizer.run_idle_optimization(
        repo,
        db_path=tel_db,
        digest_path=digest_path,
        meta_db_path=meta_db,
        idle_seconds_override=600.0,
        candidate_fn=lambda _code, _m, _cases: improved_plugin,
        validator_script=REPO_ROOT / "scripts" / "validate_plugin.py",
    )
    assert len(idle_res["staged_branches"]) == 1
    staged_br = idle_res["staged_branches"][0]["branch"]
    assert staged_br.startswith("evolve/demo_opt-")
    assert idle_res["staged_branches"][0]["candidate_replay"]["pass_rate"] == 1.0

    approve_msg = reflection_loop.approve_upgrade(
        1,
        repo_root=repo,
        digest_path=digest_path,
        ledger_path=ledger_path,
        require_confirm=False,
    )
    assert "Merged" in approve_msg and staged_br in approve_msg
    assert "strict fallback: handled empty query cleanly" in (repo / "plugins" / "demo_opt.py").read_text(encoding="utf-8")

    # Approve the routing tuning proposal (Upgrade #2) and verify local_router promotes qwen3:8b!
    approve_route = reflection_loop.approve_upgrade(
        2,
        repo_root=repo,
        digest_path=digest_path,
        require_confirm=False,
    )
    assert "Applied meta-optimizer tuning override" in approve_route
    ranked, _esc, _note = local_router.adaptive_rank_models(
        "coding",
        ["qwen2.5-coder:14b", "qwen3:8b"],
        db_path=tel_db,
    )
    assert ranked[0] == "qwen3:8b"


# ─────────────────────────────────────────────────────────────────────────────
#  6. Universal MCP Client Bridge (Dynamic Mount + SAFE/CONFIRM/LOCKED Tiers)
# ─────────────────────────────────────────────────────────────────────────────
_MOCK_OMNI_MCP = r'''
import json, sys

def send(obj):
    sys.stdout.write(json.dumps(obj) + "\n")
    sys.stdout.flush()

for raw in sys.stdin:
    raw = raw.strip()
    if not raw:
        continue
    req = json.loads(raw)
    m = req.get("method")
    rid = req.get("id")
    params = req.get("params") or {}
    if m == "initialize":
        send({"jsonrpc": "2.0", "id": rid, "result": {"protocolVersion": "2024-11-05", "capabilities": {"tools": {}}, "serverInfo": {"name": "omni-mcp", "version": "1.0"}}})
    elif m == "notifications/initialized":
        continue
    elif m == "tools/list":
        send({"jsonrpc": "2.0", "id": rid, "result": {"tools": [
            {"name": "list_issues", "description": "Read GitHub issues", "inputSchema": {"type": "OBJECT", "properties": {"repo": {"type": "STRING"}}}},
            {"name": "create_issue", "description": "Create a GitHub issue", "inputSchema": {"type": "OBJECT", "properties": {"title": {"type": "STRING"}}}},
            {"name": "unlock_front_door", "description": "Unlock smart home deadbolt", "inputSchema": {"type": "OBJECT", "properties": {}}},
            {"name": "query_db", "description": "Execute SQL query", "inputSchema": {"type": "OBJECT", "properties": {"sql": {"type": "STRING"}}}},
        ]}})
    elif m == "tools/call":
        tname = params.get("name")
        args = params.get("arguments") or {}
        send({"jsonrpc": "2.0", "id": rid, "result": {"content": [{"type": "text", "text": f"executed {tname} with {json.dumps(args, sort_keys=True)}"}], "isError": False}})
'''


def test_m14_universal_mcp_bridge_dynamic_mount_and_risk_tiers(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    from core import confirm, mcp_client
    from plugins import mcp_bridge, voice_lock

    srv_py = tmp_path / "omni_mcp_server.py"
    srv_py.write_text(_MOCK_OMNI_MCP, encoding="utf-8")
    cfg_file = tmp_path / "mcp_cfg.json"
    reg_db = tmp_path / "mcp_reg.sqlite"

    cfg_file.write_text(
        json.dumps({
            "servers": {
                "omni_hub": {
                    "enabled": True,
                    "transport": "stdio",
                    "command": [sys.executable, "-u", str(srv_py)],
                    "allowed_tools": ["list_issues", "create_issue", "unlock_front_door", "query_db"],
                    "mutating_tools": ["create_issue"],
                    "locked_tools": ["unlock_front_door"],
                    "timeout_sec": 5.0,
                }
            }
        }),
        encoding="utf-8",
    )

    mount_res = mcp_client.discover_and_mount("omni_hub", config_path=cfg_file, registry_db=reg_db)
    assert mount_res["ok"] is True
    by_name = {m["mounted_name"]: m for m in mount_res["mounted"]}
    assert by_name["mcp__omni_hub__list_issues"]["risk_tier"] == "SAFE"
    assert by_name["mcp__omni_hub__create_issue"]["risk_tier"] == "CONFIRM"
    assert by_name["mcp__omni_hub__unlock_front_door"]["risk_tier"] == "LOCKED"

    srv_cfg = mcp_client.load_servers_config(cfg_file)[0]["omni_hub"]
    assert mcp_bridge.classify_tool_risk("query_db", srv_cfg, arguments={"sql": "SELECT * FROM users"}) == "SAFE"
    assert mcp_bridge.classify_tool_risk("query_db", srv_cfg, arguments={"sql": "UPDATE users SET role='x'"}) == "CONFIRM"
    assert mcp_bridge.classify_tool_risk("query_db", srv_cfg, arguments={"sql": "DROP TABLE users"}) == "LOCKED"

    safe_out = mcp_client.invoke_mounted_tool(
        "mcp__omni_hub__list_issues",
        {"repo": "Mark-LVII"},
        player=None,
        config_path=cfg_file,
        registry_db=reg_db,
    )
    assert "executed list_issues" in safe_out
    assert "<<<UNTRUSTED source=mcp" in safe_out and "<<<END UNTRUSTED>>>" in safe_out

    blk_confirm = mcp_client.invoke_mounted_tool(
        "mcp__omni_hub__create_issue",
        {"title": "Bug"},
        player=None,
        config_path=cfg_file,
        registry_db=reg_db,
    )
    assert "Blocked" in blk_confirm

    orig_cbs = (confirm._show_cb, confirm._hide_cb, confirm._log_cb)
    confirm.resolve(False)
    try:
        confirm.bind(lambda *a: None, lambda: None, lambda *a: None)
        pend = mcp_client.invoke_mounted_tool(
            "mcp__omni_hub__create_issue",
            {"title": "Bug"},
            player=object(),
            config_path=cfg_file,
            registry_db=reg_db,
        )
        assert "[CONFIRMATION_PENDING]" in pend
        confirm.resolve(False)

        monkeypatch.setattr(voice_lock, "DEFAULT_STORE_PATH", tmp_path / "vp.json")
        t = np.linspace(0.0, 1.2, 19200, endpoint=False)
        sig = np.sin(2.0 * np.pi * 150.0 * t) + 0.5 * np.sin(2.0 * np.pi * 450.0 * t)
        voice_lock.run({"action": "enroll", "samples": sig.tolist(), "sample_rate": 16000})

        locked_out = mcp_client.invoke_mounted_tool(
            "mcp__omni_hub__unlock_front_door",
            {},
            player=object(),
            config_path=cfg_file,
            registry_db=reg_db,
        )
        assert "[VOICE_LOCKED]" in locked_out
    finally:
        confirm.resolve(False)
        confirm.bind(*orig_cbs)

    presets_out = mcp_bridge.run({"mode": "presets"})
    assert "github" in presets_out and "smart_home_hass" in presets_out and "claude_mem" in presets_out
    eco_out = mcp_bridge.run({"mode": "ecosystem"})
    assert "OmniCore Ecosystem:" in eco_out
