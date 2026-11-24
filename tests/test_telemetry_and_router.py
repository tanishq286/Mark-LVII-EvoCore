"""Tests for SQLite WAL telemetry & Adaptive Hybrid Router."""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from core.evolution import telemetry
from providers import local_router


def test_telemetry_metrics_and_failure_inference(tmp_path: Path):
    db = telemetry.init_db(tmp_path / "telemetry.sqlite")
    telemetry.record_tool_event("alpha_tool", {"action": "run"}, "ok", 100.0, db_path=db)
    telemetry.record_tool_event("alpha_tool", {"action": "run"}, "ok", 200.0, db_path=db)
    telemetry.record_tool_event("alpha_tool", {"action": "run", "password": "secret"}, "status=failed reason=bad", 500.0, db_path=db)
    telemetry.record_tool_event("alpha_tool", {"action": "run"}, "File not found: x.txt", 400.0, db_path=db)
    telemetry.record_tool_event("alpha_tool", {"action": "run"}, "failed: boom", 300.0, db_path=db)

    stats = telemetry.tool_metrics(db_path=db)["alpha_tool"]
    assert stats["calls"] == 5
    assert stats["successes"] == 2
    assert stats["failures"] == 3
    assert stats["consecutive_failures"] == 3
    assert stats["p50_ms"] == 300.0
    assert stats["p95_ms"] == 500.0

    fails = telemetry.recent_failures(tool_name="alpha_tool", limit=5, db_path=db)
    assert len(fails) == 3
    assert all("secret" not in f["args_json"] for f in fails)


def test_adaptive_router_reranks_and_escalates_to_cloud(tmp_path: Path):
    db = telemetry.init_db(tmp_path / "telemetry.sqlite")
    installed = {"qwen3:8b", "qwen2.5:14b", "gemma4:12b"}

    # Cold start preserves static reasoning preference (qwen2.5:14b)
    dec0, _ = local_router.route_prompt(
        "Analyze architectural trade-offs step by step.",
        tier="smart",
        available_models=installed,
        telemetry_db_path=db,
    )
    assert dec0.selected_model == "qwen2.5:14b"
    assert dec0.escalate_to_cloud is False

    # Degrade qwen2.5:14b while gemma4:12b succeeds quickly
    for _ in range(4):
        telemetry.record_route_event("reasoning", "qwen2.5:14b", "ollama", False, 9000.0, db_path=db)
        telemetry.record_route_event("reasoning", "gemma4:12b", "ollama", True, 900.0, db_path=db)

    dec1, ladder1 = local_router.route_prompt(
        "Analyze architectural trade-offs step by step.",
        tier="smart",
        available_models=installed,
        telemetry_db_path=db,
    )
    assert dec1.selected_model == "gemma4:12b"
    assert dec1.escalate_to_cloud is False
    assert ladder1[0] == "gemma4:12b"

    # Now degrade remaining candidates too -> should signal cloud escalation
    for m in ("gemma4:12b", "qwen3:8b"):
        for _ in range(4):
            telemetry.record_route_event("reasoning", m, "ollama", False, 30_000.0, db_path=db)

    dec2, _ = local_router.route_prompt(
        "Analyze architectural trade-offs step by step.",
        tier="smart",
        available_models=installed,
        telemetry_db_path=db,
    )
    assert dec2.escalate_to_cloud is True
    assert "escalate" in dec2.telemetry_note.lower()


def test_core_gemini_keeps_providers_lazy():
    root = Path(__file__).resolve().parent.parent
    code = (
        f"import sys, json; sys.path.insert(0, {str(root)!r}); "
        "import core.gemini; "
        "mods = [k for k in sys.modules if k.startswith('providers')]; "
        "print(json.dumps(mods))"
    )
    proc = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True, stdin=subprocess.DEVNULL)
    assert json.loads(proc.stdout.strip()) == []
