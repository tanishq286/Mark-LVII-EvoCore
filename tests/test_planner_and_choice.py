"""Tests for Verified Multi-Step Planner (Plan -> Execute -> Verify -> Recover) and Multi-Option Choice HUD."""
from __future__ import annotations

from pathlib import Path

from core import confirm
from core.evolution import telemetry
from core.evolution.planner import VerifiedPlanner
from plugins import autonomous_brain


def test_verified_planner_injects_immunity_and_recovers(tmp_path: Path):
    db = telemetry.init_db(tmp_path / "telemetry.sqlite")
    telemetry.record_tool_event("flaky_fetch", {"url": "bad"}, "failed: 503 error", 300.0, success=False, db_path=db)
    telemetry.record_tool_event("flaky_fetch", {"url": "bad"}, "failed: 503 error", 350.0, success=False, db_path=db)

    planner = VerifiedPlanner(db_path=db)
    captured_prompts: list[str] = []

    def fake_llm_json(prompt: str):
        captured_prompts.append(prompt)
        return {
            "strategy": "Fetch docs with fallback",
            "ambiguous": False,
            "options": [],
            "steps": [
                {
                    "id": "s1",
                    "title": "Fetch documentation",
                    "tool": "flaky_fetch",
                    "parameters": {"url": "primary"},
                    "expected": "doc text",
                    "fallback_tool": "cached_fetch",
                    "fallback_parameters": {"key": "doc"},
                },
                {
                    "id": "s2",
                    "title": "Check status",
                    "tool": "status_tool",
                    "parameters": {},
                    "expected": "status string",
                },
            ],
        }

    plan = planner.plan("Get docs and status", "- flaky_fetch\n- cached_fetch", llm_json_fn=fake_llm_json)
    assert plan["immunity_injected"] is True
    assert "flaky_fetch" in captured_prompts[0]

    def fake_run(tool: str, params: dict) -> str:
        if tool == "flaky_fetch":
            return "status=failed reason=upstream_down"
        if tool == "cached_fetch":
            return "Verified cached documentation content"
        if tool == "status_tool":
            return "All systems green"
        return "unknown"

    res = planner.execute_plan(plan, fake_run)
    assert res["status"] == "done"
    assert res["steps_verified"] == 2
    assert res["steps_recovered"] == 1
    assert res["steps_failed"] == 0


def test_choice_banner_confirm_api_and_resolution():
    orig_choice_cbs = (confirm._show_choice_cb, confirm._hide_choice_cb)
    shown: list[tuple[str, str, list[str]]] = []
    hidden: list[bool] = []
    selected: list[tuple[int, str]] = []

    try:
        # Headless refusal
        confirm.bind_choice(None, None)
        refused = confirm.request_choice("k", "Pick store", "d", ["Amazon", "Flipkart"], lambda i, l: "ok")
        assert "not available" in refused.lower()

        # Bound HUD callbacks
        confirm.bind_choice(
            lambda t, d, opts: shown.append((t, d, opts)),
            lambda: hidden.append(True),
        )
        msg = confirm.request_choice(
            "k1",
            "Choose format",
            "Select output format",
            ["Markdown", "PowerPoint", "Excel"],
            lambda i, l: (selected.append((i, l)), f"Picked {l}")[1],
        )
        assert msg.startswith("[CHOICE_PENDING]")
        assert shown and shown[0][2] == ["Markdown", "PowerPoint", "Excel"]
        assert confirm.pending_choice_info()["key"] == "k1"

        # Resolve by 1-based digit string (synchronous test mode)
        out = confirm.resolve_choice("option 2", sync=True)
        assert out == "Picked PowerPoint"
        assert selected == [(1, "PowerPoint")]
        assert hidden == [True]

        # Label substring resolution & cancel
        confirm.request_choice("k2", "Choose target", "", ["Local GPU", "Cloud Gemini"], lambda i, l: l)
        assert confirm.resolve_choice("gemini", sync=True) == "Cloud Gemini"

        confirm.request_choice("k3", "Choose target", "", ["A", "B"], lambda i, l: l)
        assert confirm.resolve_choice(None, sync=True).startswith("Cancelled")
    finally:
        confirm.bind_choice(*orig_choice_cbs)


def test_autonomous_brain_status_smoke():
    out = autonomous_brain.run({"action": "status"}, player=None)
    assert isinstance(out, str) and len(out) > 0
