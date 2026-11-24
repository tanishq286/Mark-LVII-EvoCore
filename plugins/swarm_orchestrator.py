"""plugins/swarm_orchestrator.py — Parallel Multi-Agent Swarm + Critic Plugin.

Spawns specialized sub-agents in parallel (Researcher, Coder, Visual Screen
Operator, Security Auditor) sharing a structured SQLite blackboard, verified by
an independent Critic Agent before reporting back or executing side effects.
"""
from __future__ import annotations

import json
import threading

PLUGIN = {
    "name": "swarm_orchestrator",
    "brief": "Spawn parallel Researcher, Coder, Screen, and Security sub-agents verified by a Critic.",
    "description": (
        "Orchestrates Mark-LVII's parallel multi-agent Swarm over a shared SQLite blackboard "
        "with an independent Critic Agent that verifies every sub-agent deliverable and gates "
        "side effects through safety_guard. Supports actions: "
        "'status' (default summary of recent swarm runs), "
        "'run' (execute a goal across parallel Researcher/Coder/Screen/Security sub-agents), "
        "'blackboard' (inspect shared SQLite blackboard facts & critic scores for a run_id), "
        "'history' (list recent swarm runs), and 'cancel' (abort active swarm execution)."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "action": {
                "type": "STRING",
                "enum": ["status", "run", "execute", "blackboard", "history", "cancel"],
                "description": "Operation to perform (default 'status', or 'run' when goal is set).",
            },
            "goal": {
                "type": "STRING",
                "description": "Complex multi-domain goal to decompose and execute in parallel.",
            },
            "run_id": {
                "type": "STRING",
                "description": "Swarm run_id to inspect when action='blackboard'.",
            },
            "max_tasks": {
                "type": "INTEGER",
                "description": "Maximum specialized sub-agent tasks to spawn (default 4, max 8).",
            },
            "blocking": {
                "type": "BOOLEAN",
                "description": "If true, wait for swarm completion synchronously before returning.",
            },
        },
        "required": [],
    },
}

_SWARM_LOCK = threading.Lock()


def _notify(player, log_msg: str, title: str = "", body: str = "") -> None:
    if player is None:
        return
    try:
        if hasattr(player, "write_log"):
            player.write_log(f"SYS: [Swarm] {log_msg[:180]}")
    except Exception:
        pass
    if title and body:
        try:
            if hasattr(player, "show_content"):
                player.show_content(title[:48], body[:4000])
        except Exception:
            pass


def _catalog_text(player) -> str:
    cat_fn = getattr(player, "tool_catalog", None) if player is not None else None
    if not callable(cat_fn):
        return ""
    try:
        decls = cat_fn() or []
    except Exception:
        return ""
    lines = []
    for d in decls:
        name = d.get("name", "")
        if not name or name in ("swarm_orchestrator", "autonomous_brain"):
            continue
        brief = d.get("brief") or str(d.get("description") or "")[:140]
        lines.append(f"- {name}: {brief}")
    return "\n".join(lines)


def _execute_swarm_sync(goal: str, max_tasks: int, player) -> dict:
    from core.evolution.swarm import SwarmCoordinator

    coord = SwarmCoordinator()
    run_tool_fn = getattr(player, "run_tool", None) if player is not None else None
    cat_txt = _catalog_text(player)
    report = coord.execute_swarm(
        goal,
        catalog_text=cat_txt,
        max_tasks=max_tasks,
        run_tool_fn=run_tool_fn if callable(run_tool_fn) else None,
        on_progress=lambda msg, _st, _k, _fs: _notify(player, msg),
    )
    panel = (
        f"RUN ID: {report['run_id']}\n"
        f"STATUS: {report['status']} (waves={report['waves_executed']}, critic={report['critic_verdict'].get('score')})\n"
        f"STRATEGY: {report['strategy']}\n\n"
        f"REPORT:\n{report['final_report']}"
    )
    _notify(player, f"Swarm {report['run_id']} finished ({report['status']}).", "OMNICORE SWARM REPORT", panel)
    return report


def run(parameters: dict, player=None, session_memory=None) -> str:
    """Plugin entrypoint for parallel Multi-Agent Swarm + Critic."""
    _ = session_memory
    try:
        from core.evolution import swarm as swarm_mod

        params = parameters if isinstance(parameters, dict) else {}
        goal = str(params.get("goal") or "").strip()
        raw_action = str(params.get("action") or "").strip().lower()
        action = raw_action or ("run" if goal else "status")
        try:
            max_tasks = max(1, min(8, int(params.get("max_tasks") or 4)))
        except (TypeError, ValueError):
            max_tasks = 4

        bb = swarm_mod.SwarmBlackboard()

        if action == "status":
            recent = bb.list_recent_runs(limit=5)
            busy = _SWARM_LOCK.locked()
            if not recent:
                return f"OmniCore Multi-Agent Swarm + Critic online (busy={busy}, 0 prior runs)."
            last = recent[0]
            return (
                f"OmniCore Swarm online (busy={busy}). Last run {last['run_id']}: "
                f"status={last['status']}, goal={str(last['goal'])[:80]!r}."
            )

        if action == "history":
            recent = bb.list_recent_runs(limit=10)
            if not recent:
                return "No swarm runs recorded yet."
            body = json.dumps(recent, indent=2, ensure_ascii=False)
            _notify(player, f"Loaded {len(recent)} swarm run(s).", "SWARM HISTORY", body)
            return body[:1800]

        if action == "blackboard":
            rid = str(params.get("run_id") or "").strip()
            if not rid:
                recent = bb.list_recent_runs(limit=1)
                rid = recent[0]["run_id"] if recent else ""
            if not rid:
                return "No swarm blackboard runs found yet."
            snap = bb.get_run_snapshot(rid)
            if snap is None:
                return f"Swarm run '{rid}' not found in SQLite blackboard."
            body = json.dumps(snap, indent=2, ensure_ascii=False)
            _notify(player, f"Blackboard snapshot for {rid}.", f"BLACKBOARD · {rid}", body)
            return body[:2000]

        if action == "cancel":
            swarm_mod.request_swarm_cancel()
            return "Signaled OmniCore Swarm sub-agents to halt execution."

        if action in ("run", "execute"):
            if not goal:
                return "Provide a non-empty 'goal' to run the Multi-Agent Swarm."
            if bool(params.get("blocking", False)) or player is None:
                rep = _execute_swarm_sync(goal, max_tasks, player)
                return (
                    f"Swarm {rep['run_id']} ({rep['status']}, critic_score={rep['critic_verdict'].get('score')}):\n"
                    f"{rep['final_report'][:1600]}"
                )
            if not _SWARM_LOCK.acquire(blocking=False):
                return "A multi-agent swarm mission is already running — check status or blackboard."

            def _bg():
                try:
                    rep = _execute_swarm_sync(goal, max_tasks, player)
                    say = getattr(player, "request_say", None) if player else None
                    if callable(say):
                        say(f"Swarm mission finished ({rep['status']}): {rep['final_report'][:320]}")
                finally:
                    _SWARM_LOCK.release()

            threading.Thread(target=_bg, daemon=True, name="jarvis-swarm-bg").start()
            return (
                f"OmniCore Multi-Agent Swarm engaged on '{goal[:90]}'. "
                "Spawning parallel specialists over SQLite blackboard with Critic verification."
            )

        return f"Unknown swarm_orchestrator action '{action}'. Use status, run, blackboard, history, or cancel."
    except Exception as exc:
        return f"swarm_orchestrator error: {exc}"
