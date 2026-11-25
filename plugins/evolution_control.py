"""plugins/evolution_control.py — Unified Control Surface for J.A.R.V.I.S. EvoCore.

Exposes Milestone 10's Safe Auto-Evolution Engine as a single voice/HUD plugin:
  - status / telemetry / digest / approve_upgrade / rollback_last
  - synthesize / self_heal / habits / compile_habit / run_habit / consolidate_memory
"""
from __future__ import annotations

import json
from pathlib import Path
import sys

if getattr(sys, "frozen", False):
    BASE_DIR = Path(sys.executable).parent
else:
    BASE_DIR = Path(__file__).resolve().parent.parent

PLUGIN = {
    "name": "evolution_control",
    "description": (
        "Control JARVIS's EvoCore Auto-Evolution Engine, Telemetry, Habit Compiler, "
        "Self-Healer, Reflection Digest, RAG Consolidation, and Git Rollback. "
        "Actions: "
        "'status' (default) = health, telemetry counts, pending digest & compiled habits; "
        "'telemetry' = per-tool P95 latency, success rates & router escalations; "
        "'digest' = run reflection cycle to consolidate RAG & generate numbered upgrades; "
        "'approve_upgrade' = approve a numbered upgrade (LOCKED confirm gate); "
        "'rollback_last' = revert last auto-evolution commit cleanly (LOCKED confirm gate); "
        "'synthesize' = create + pytest-sandbox a new skill on skill/* branch; "
        "'self_heal' = repair an unhealthy plugin on autofix/* branch with replay benchmark; "
        "'habits' / 'compile_habit' / 'run_habit' = detect, compile, or execute 1-voice macros; "
        "'consolidate_memory' = deduplicate & prune local knowledge_rag SQLite chunks; "
        "'pause_agents' / 'resume_agents' = pause or resume the 24/7 research and engineering agents; "
        "'agents_today' = show what the researcher and engineer built today; "
        "'agents_status' / 'run_agents_now' = 24/7 agent state, or force one idle research+engineer pass."
    ),
    "brief": (
        "EvoCore status, telemetry, reflection digest, upgrade approval, self-heal, "
        "habit macros, memory consolidation, evolution rollback, 24/7 agents pause/resume/today."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "action": {
                "type": "STRING",
                "enum": [
                    "status",
                    "telemetry",
                    "digest",
                    "reflect",
                    "approve_upgrade",
                    "rollback_last",
                    "synthesize",
                    "self_heal",
                    "meta_optimize",
                    "habits",
                    "compile_habit",
                    "run_habit",
                    "consolidate_memory",
                    "pause_agents",
                    "resume_agents",
                    "agents_today",
                    "agents_status",
                    "run_agents_now",
                ],
                "description": "EvoCore operation to run (default 'status').",
            },
            "upgrade_id": {
                "type": "INTEGER",
                "description": "Numbered proposal ID from Evolution Digest for approve_upgrade.",
            },
            "skill_name": {
                "type": "STRING",
                "description": "Plugin slug for synthesize, self_heal, or habit name.",
            },
            "specification": {
                "type": "STRING",
                "description": "Specification text for synthesize or trigger phrase for run_habit.",
            },
            "steps_json": {
                "type": "STRING",
                "description": "Optional JSON list of {tool, parameters} dicts for compile_habit.",
            },
        },
        "required": [],
    },
}


def _notify(player, log_line: str, title: str = "", body: str = "") -> None:
    if player is None:
        return
    try:
        if hasattr(player, "write_log"):
            player.write_log(f"JARVIS: {log_line}")
    except Exception:
        pass
    if title and body:
        try:
            if hasattr(player, "show_content"):
                player.show_content(title, body[:4000])
        except Exception:
            pass


_AGENT_ACTION_ALIASES = {
    "pause": "pause_agents", "pause_247": "pause_agents", "pause_research": "pause_agents",
    "resume": "resume_agents", "resume_247": "resume_agents", "resume_research": "resume_agents",
    "today": "agents_today", "built_today": "agents_today", "what_built_today": "agents_today",
    "agents": "agents_status", "run_agents": "run_agents_now",
}


def _agents_action(action: str, player) -> str:
    from core.evolution import agents_247
    if action == "pause_agents":
        msg = agents_247.pause("voice")
    elif action == "resume_agents":
        msg = agents_247.resume()
    elif action == "agents_today":
        body = agents_247.built_today_report()
        _notify(player, "24/7 agents: today's report is on the HUD.", "24/7 AGENTS TODAY", body)
        return body[:1500]
    elif action == "run_agents_now":
        res = agents_247.run_once(player=player, force=True)
        eng = res.get("engineer") or {}
        msg = (f"Research pass done: {res.get('research', {}).get('specs', 0)} spec(s). "
               + (f"Engineer built {eng.get('branch')} - say 'Approve upgrade {eng.get('upgrade_id')}'."
                  if eng.get("ok") else f"Engineer: {eng.get('reason', 'nothing built')}."))
    else:
        st = agents_247.status()
        msg = (f"24/7 agents {'PAUSED' if st['paused'] else 'active'}, scheduler "
               f"{'running' if st['thread_running'] else 'stopped'}, {st['pending_specs']} pending spec(s), "
               f"budget {st['budget']}.")
    _notify(player, msg)
    return msg


def run(parameters: dict, player=None, session_memory=None) -> str:
    """Entry point discovered by `core.plugin_loader`. Never raises."""
    _ = session_memory
    try:
        from core.evolution import (
            habit_compiler,
            reflection_loop,
            safety_guard,
            self_healer,
            skill_synthesizer,
            telemetry,
        )

        params = parameters if isinstance(parameters, dict) else {}
        action = str(params.get("action") or "status").strip().lower()
        action = _AGENT_ACTION_ALIASES.get(action, action)

        if action in ("pause_agents", "resume_agents", "agents_today", "agents_status", "run_agents_now"):
            return _agents_action(action, player)
        skill_name = str(params.get("skill_name") or "").strip()
        spec = str(params.get("specification") or "").strip()

        if action == "status":
            t_stats = telemetry.tool_metrics()
            habits = habit_compiler.list_habits()
            digest = reflection_loop.load_digest()
            commits = safety_guard.list_evolution_commits()
            pending = [p for p in (digest.get("proposals") or []) if p.get("status") == "pending"]
            unhealthy = self_healer.detect_unhealthy_plugins(repo_root=BASE_DIR)
            msg = (
                f"EvoCore active: {len(t_stats)} tool(s) metered, {len(unhealthy)} unhealthy plugin(s), "
                f"{len(pending)} pending upgrade(s), {len(habits)} compiled habit(s), "
                f"{len(commits)} tracked evolution commit(s)."
            )
            _notify(player, msg)
            return msg

        if action == "telemetry":
            t_stats = telemetry.tool_metrics()
            r_stats = telemetry.route_metrics()
            lines = [f"Tracked Tools ({len(t_stats)}):"]
            for name, m in sorted(t_stats.items()):
                lines.append(
                    f"  - {name}: calls={m['calls']}, ok={m['success_rate']:.0%}, "
                    f"p50={m['p50_ms']:.0f}ms, p95={m['p95_ms']:.0f}ms"
                )
            lines.append(f"\nModel Routes ({len(r_stats)}):")
            for (cat, mod), rm in sorted(r_stats.items()):
                lines.append(
                    f"  - {cat}/{mod}: calls={rm['calls']}, ok={rm['success_rate']:.0%}, "
                    f"p95={rm['p95_ms']:.0f}ms, esc={rm['escalations']}"
                )
            body = "\n".join(lines)
            _notify(player, f"Telemetry covers {len(t_stats)} tools and {len(r_stats)} routes.", "EVOCORE TELEMETRY", body)
            return body[:1500]

        if action in ("digest", "reflect"):
            digest = reflection_loop.run_reflection_cycle(BASE_DIR, consolidate_rag=True)
            props = digest.get("proposals") or []
            lines = [
                f"Evolution Digest ({digest.get('generated_at')}) — {len(props)} proposal(s):",
                *[f"  [{p['id']}] ({p['kind']}, {p['status']}) {p['title']}\n      {p['detail']}" for p in props],
            ]
            body = "\n".join(lines)
            _notify(player, f"Evolution Digest ready with {len(props)} proposal(s).", "EVOLUTION DIGEST", body)
            return body[:1500]

        if action == "approve_upgrade":
            uid = int(params.get("upgrade_id") or 0)
            if uid <= 0:
                return "Specify positive integer 'upgrade_id' from the Evolution Digest."
            # Milestone 15: conversations.sqlite + knowledge_graph.sqlite are
            # gitignored (a merge cannot touch them) AND snapshotted first.
            try:
                from core.evolution import engineer_daemon
                engineer_daemon.snapshot_memory_dbs()
            except Exception as exc:
                return f"Upgrade #{uid} not merged: memory snapshot failed ({exc})."
            return reflection_loop.approve_upgrade(uid, repo_root=BASE_DIR, player=player, require_confirm=True)

        if action == "rollback_last":
            return safety_guard.enforce_gate(
                "evolution_control",
                "rollback_last",
                "Rollback Last Auto-Evolution Commit",
                "Reverts the most recent evolution commit cleanly using `git revert --no-edit`.",
                lambda: json.dumps(safety_guard.rollback_last_evolution(BASE_DIR)),
                key="rollback_last",
            )

        if action == "synthesize":
            res = skill_synthesizer.synthesize_plugin(BASE_DIR, skill_name or "evolved_skill", spec, player=player)
            msg = (
                f"Synthesized & pytest-verified '{res['plugin_name']}' on branch '{res['branch']}'."
                if res.get("ok")
                else f"Skill synthesis failed: {res.get('error')}"
            )
            _notify(player, msg, "SKILL SYNTHESIZER", res.get("diff") or msg)
            return msg

        if action == "self_heal":
            if not skill_name:
                return "Specify 'skill_name' to self-heal."
            res = self_healer.heal_plugin(BASE_DIR, skill_name, player=player)
            msg = (
                f"Staged verified autofix on '{res['branch']}'."
                if res.get("ok")
                else f"Self-heal stopped (reverted={res.get('reverted')}): {res.get('error')}"
            )
            _notify(player, msg, "SELF HEALER", res.get("diff") or msg)
            return msg

        if action == "habits":
            compiled = habit_compiler.list_habits()
            cands = habit_compiler.detect_habit_candidates()
            lines = [f"Compiled Habits ({len(compiled)}):"]
            lines.extend(f"  - {h['name']} ({len(h['steps'])} steps, confirm={h['requires_confirm']})" for h in compiled)
            lines.append(f"\nDetected Candidates ({len(cands)}):")
            lines.extend(
                f"  - {c['suggested_name']} (seen {c['support_count']}x): "
                + " -> ".join(s[0] for s in c["signature"])
                for c in cands
            )
            body = "\n".join(lines)
            _notify(player, f"{len(compiled)} compiled habit(s), {len(cands)} candidate(s).", "HABIT COMPILER", body)
            return body[:1500]

        if action == "compile_habit":
            steps_raw = str(params.get("steps_json") or "").strip()
            steps = json.loads(steps_raw) if steps_raw else []
            rec = habit_compiler.compile_habit(skill_name or "custom_habit", steps)
            msg = f"Compiled habit '{rec['name']}' ({len(rec['steps'])} steps, confirm={rec['requires_confirm']})."
            _notify(player, msg)
            return msg

        if action == "run_habit":
            run_fn = getattr(player, "run_tool", None) if player else None
            if not callable(run_fn):
                return "Cannot execute habit macro without an active tool runner."
            return habit_compiler.run_habit(skill_name or spec, run_fn, player=player)

        if action == "consolidate_memory":
            from plugins import knowledge_rag
            return knowledge_rag.run({"mode": "consolidate"}, player=player)

        if action == "meta_optimize":
            from core.evolution import meta_optimizer
            res = meta_optimizer.run_idle_optimization(BASE_DIR, force=True)
            staged = len(res.get("staged_branches") or [])
            queued = len(res.get("queued_proposals") or [])
            rejected = len(res.get("rejected_candidates") or [])
            msg = (
                f"Meta-Optimizer completed: {staged} measurably better evolve/* branch(es) staged, "
                f"{queued} upgrade(s) queued in Evolution Digest, {rejected} non-improving candidate(s) rejected."
            )
            _notify(player, msg, "META-OPTIMIZER", json.dumps(res.get("queued_proposals") or [], indent=2))
            return msg

        return f"Unknown evolution_control action '{action}'."
    except Exception as exc:
        err = f"evolution_control error: {exc}"
        _notify(player, err)
        return err
