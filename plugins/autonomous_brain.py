"""JARVIS Autonomous Multi-Step Executive Brain, Parallel Swarm & Mission Orchestrator.

Decomposes complex goals into:
  1. Parallel Multi-Agent Swarm + Critic (`core/evolution/swarm.py`) for complex
     multi-domain goals (Researcher, Coder, Visual Screen Operator, Security Auditor),
  2. Closed-Loop Autonomous Screen Agent (`core/evolution/screen_agent.py`) for
     interactive desktop GUI goals,
  3. Verified Multi-Step Planner (`core/evolution/planner.py`) for sequential tool chains,
  4. Persistent Long-Horizon Missions (`core/evolution/mission_runner.py`) that
     survive restarts in SQLite and push alerts/approvals straight to the phone.
"""
from __future__ import annotations

import json
import threading
import time
from pathlib import Path

PLUGIN = {
    "name": "autonomous_brain",
    "brief": "Plan, spawn parallel Swarm + Critic sub-agents, or schedule persistent missions.",
    "description": (
        "JARVIS's OmniCore multi-step executive planner, parallel multi-agent Swarm + Critic "
        "orchestrator, and persistent background mission controller. "
        "Actions: "
        "'status' = view active or recent mission/swarm plan and step trace; "
        "'execute' = execute a complex goal (automatically spawning parallel Researcher/Coder/"
        "Screen/Security sub-agents with Critic verification for multi-domain goals); "
        "'swarm' = explicitly run the goal on the parallel Multi-Agent Swarm + SQLite blackboard; "
        "'schedule_mission' / 'list_missions' / 'pause_mission' / 'resume_mission' = manage "
        "SQLite-persisted long-horizon background missions that survive restarts; "
        "'cancel' = abort active brain, swarm, or screen execution."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "action": {
                "type": "STRING",
                "enum": [
                    "status",
                    "execute",
                    "swarm",
                    "schedule_mission",
                    "list_missions",
                    "pause_mission",
                    "resume_mission",
                    "cancel",
                ],
                "description": "Operation to run (default 'status', or 'execute' when goal is provided).",
            },
            "goal": {
                "type": "STRING",
                "description": "Complete high-level mission or multi-step request to accomplish.",
            },
            "max_steps": {
                "type": "INTEGER",
                "description": "Maximum autonomous steps or parallel swarm tasks (default 6, max 12).",
            },
            "trigger_type": {
                "type": "STRING",
                "enum": ["immediate", "interval", "cron", "event", "screen_trigger"],
                "description": "For schedule_mission: wake trigger type (default 'interval').",
            },
            "interval_s": {
                "type": "INTEGER",
                "description": "For interval missions: seconds between wakes (default 300).",
            },
            "cron_expr": {
                "type": "STRING",
                "description": "For cron missions: 5-field cron schedule (e.g. '0 9 * * *').",
            },
            "mission_id": {
                "type": "STRING",
                "description": "Target mission_id for pause_mission or resume_mission.",
            },
        },
        "required": ["action"],
    },
}

_BASE = Path(__file__).resolve().parent.parent
_MISSION_LOG = _BASE / "memory" / "autonomous_missions.json"
_lock = threading.Lock()
_state_lock = threading.Lock()
_CANCEL = threading.Event()
_MISSION: dict = {}


def _call(player, method: str, *args) -> None:
    fn = getattr(player, method, None) if player else None
    if callable(fn):
        try:
            fn(*args)
        except Exception as e:
            print(f"[autonomous_brain] {method} failed: {e}")


def _catalog_summary(player) -> tuple[list[dict], str]:
    cat_fn = getattr(player, "tool_catalog", None) if player else None
    decls = []
    if callable(cat_fn):
        try:
            decls = cat_fn() or []
        except Exception:
            decls = []
    lines = []
    for d in decls:
        name = d.get("name", "")
        if not name or name == "autonomous_brain":
            continue
        brief = d.get("brief") or (d.get("description") or "")[:180]
        props = list(((d.get("parameters") or {}).get("properties") or {}).keys())
        lines.append(f"- {name}({', '.join(props)}): {brief}")
    return decls, "\n".join(lines)


def _publish(player) -> None:
    if not player:
        return
    with _state_lock:
        if not _MISSION:
            return
        snap = json.loads(json.dumps(_MISSION))
    _call(player, "show_code_job", snap)


def _step_log(player, text: str, status: str | None = None, step_key: str | None = None, step_state: str | None = None) -> None:
    with _state_lock:
        if _MISSION:
            if status:
                _MISSION["status"] = status
            elapsed = round(time.time() - _MISSION.get("started", time.time()), 1)
            _MISSION.setdefault("steps", []).append([elapsed, text])
            if step_key and step_state:
                _MISSION.setdefault("files", {}).setdefault(
                    step_key, {"action": "edit", "what": text[:90], "state": step_state}
                )["state"] = step_state
    _publish(player)
    _call(player, "write_log", f"SYS: [Brain] {text[:140]}")
    try:
        from dashboard.server import broadcast_mission_log
        broadcast_mission_log(text, source="brain", status=status or "")
    except Exception:
        pass


def _save_mission_history(entry: dict) -> None:
    try:
        _MISSION_LOG.parent.mkdir(parents=True, exist_ok=True)
        history = []
        if _MISSION_LOG.exists():
            history = json.loads(_MISSION_LOG.read_text(encoding="utf-8"))
            if not isinstance(history, list):
                history = []
        history.append(entry)
        _MISSION_LOG.write_text(json.dumps(history[-30:], indent=2, ensure_ascii=False), encoding="utf-8")
    except Exception:
        pass


def _execute_mission(goal: str, max_steps: int, player, *, force_swarm: bool = False) -> None:
    started = time.time()
    _CANCEL.clear()
    with _state_lock:
        _MISSION.clear()
        _MISSION.update({
            "folder": "Autonomous Executive Brain",
            "path": str(_BASE),
            "model": "core.gemini.SMART",
            "kind": "improve",
            "phases": ["planning", "executing", "verifying", "synthesizing", "done"],
            "request": goal,
            "status": "planning",
            "started": started,
            "finished": None,
            "summary": f"Autonomous mission: {goal[:120]}",
            "files": {},
            "steps": [],
            "result": "",
        })
    _step_log(player, f"Analyzing mission & building execution plan for: {goal[:100]}", status="planning")

    try:
        from core import confirm, gemini
        from core.evolution.planner import VerifiedPlanner, is_gui_screen_goal
        from core.evolution import screen_agent, swarm

        # 1. Closed-Loop Screen Agent for interactive desktop GUI chains
        if not force_swarm and is_gui_screen_goal(goal):
            gui_max = max(max_steps, 12) if max_steps == 6 else max_steps
            _step_log(
                player,
                f"Routing interactive desktop GUI mission to Closed-Loop Screen Agent (max_steps={gui_max})…",
                status="executing",
            )
            gui_report = screen_agent.run_screen_goal(
                goal,
                max_steps=gui_max,
                player=player,
                cancel_event=_CANCEL,
                on_step=lambda text: _step_log(player, text, status="verifying"),
            )
            gui_status = str(gui_report.get("status") or "")
            if gui_status == "aborted" or _CANCEL.is_set():
                raise RuntimeError(f"Mission cancelled by user ({gui_report.get('abort_reason') or 'aborted'}).")
            elapsed = round(time.time() - started, 1)
            gui_summary = screen_agent.format_report(gui_report)
            with _state_lock:
                _MISSION["status"] = "done"
                _MISSION["finished"] = time.time()
                _MISSION["result"] = gui_summary[:2000]
            _publish(player)
            if gui_status in ("awaiting_confirmation", "awaiting_choice", "blocked_voice_lock", "cancelled_by_user"):
                _call(player, "request_say", gui_summary[:260])
                return

            _save_mission_history({
                "ts": time.strftime("%Y-%m-%d %H:%M:%S"),
                "goal": goal,
                "strategy": "Closed-loop Observe -> Verify -> Act -> Re-Observe screen agent",
                "steps": gui_report.get("steps_executed", 0),
                "elapsed_s": elapsed,
                "result": gui_summary[:1500],
            })
            _call(
                player,
                "show_content",
                f"AUTONOMOUS SCREEN MISSION ({gui_status.upper()}, {elapsed}s)",
                f"GOAL: {goal}\n\nSTEPS:\n"
                + "\n".join(gui_report.get("hud_steps") or [])
                + f"\n\nSUMMARY:\n{gui_summary}",
            )
            _call(
                player,
                "request_say",
                f"[AUTONOMOUS MISSION COMPLETE] Screen mission '{goal[:120]}' finished ({gui_status}) in {elapsed}s: {gui_summary[:400]}",
            )
            return

        _, catalog_text = _catalog_summary(player)
        run_tool_fn = getattr(player, "run_tool", None) if player else None

        # 2. Parallel Multi-Agent Swarm + Critic for complex multi-specialist goals
        if force_swarm or swarm.is_complex_swarm_goal(goal):
            _step_log(
                player,
                "Spawning parallel OmniCore Sub-Agents (Researcher, Coder, Visual, Security) + Critic over SQLite blackboard…",
                status="executing",
            )
            coord = swarm.SwarmCoordinator()
            sw_report = coord.execute_swarm(
                goal,
                catalog_text=catalog_text,
                max_tasks=min(8, max(3, max_steps)),
                run_tool_fn=run_tool_fn if callable(run_tool_fn) else None,
                on_progress=lambda msg, st, k, fst: _step_log(player, msg, status=st, step_key=k, step_state=fst),
            )
            elapsed = round(time.time() - started, 1)
            final_answer = str(sw_report.get("final_report") or "Swarm complete.")
            sw_status = str(sw_report.get("status") or "done")
            with _state_lock:
                _MISSION["status"] = "done" if sw_status != "failed" else "failed"
                _MISSION["finished"] = time.time()
                _MISSION["summary"] = sw_report.get("strategy", "")
                _MISSION["result"] = final_answer[:2000]
            _publish(player)
            _save_mission_history({
                "ts": time.strftime("%Y-%m-%d %H:%M:%S"),
                "goal": goal,
                "strategy": f"OmniCore Parallel Swarm ({sw_report.get('run_id')})",
                "steps": len(sw_report.get("tasks") or []),
                "elapsed_s": elapsed,
                "result": final_answer[:1500],
            })
            _call(
                player,
                "show_content",
                f"OMNICORE SWARM COMPLETE ({elapsed}s)",
                f"GOAL: {goal}\nRUN ID: {sw_report.get('run_id')}\n"
                f"CRITIC VERDICT: {json.dumps(sw_report.get('critic_verdict'))}\n\n"
                f"SYNTHESIS:\n{final_answer}",
            )
            _call(
                player,
                "request_say",
                f"[SWARM MISSION COMPLETE] '{goal[:120]}' verified by Critic in {elapsed}s:\n{final_answer[:600]}",
            )
            return

        # 3. Verified Sequential Multi-Step Planner
        planner = VerifiedPlanner()
        plan = planner.plan(goal, catalog_text, max_steps=max_steps)
        strategy = str(plan.get("strategy") or "Direct verified execution.")
        steps = list(plan.get("steps") or [])

        if plan.get("ambiguous") and len(plan.get("options") or []) >= 2:
            opts = list(plan["options"])
            choice_msg = confirm.request_choice(
                key="autonomous_brain_disambiguate",
                title=f"Clarify mission: {goal[:70]}",
                detail=strategy,
                options=opts,
                on_select=lambda _idx, label: run(
                    {"action": "execute", "goal": f"{goal} (Chosen option: {label})", "max_steps": max_steps},
                    player=player,
                ),
            )
            _step_log(player, choice_msg, status="done")
            _call(player, "show_content", "CHOICE REQUIRED", f"{strategy}\n\n" + "\n".join(f"{i}. {o}" for i, o in enumerate(opts, 1)))
            return

        with _state_lock:
            _MISSION["summary"] = strategy
            for idx, s in enumerate(steps, 1):
                key = f"Step {idx}: {s.get('title') or s.get('tool') or 'Task'}"
                _MISSION["files"][key] = {
                    "action": "edit",
                    "what": str(s.get("expected") or s.get("tool") or ""),
                    "state": "waiting",
                }
        _publish(player)
        _step_log(
            player,
            f"Strategy: {strategy} ({len(steps)} steps, failure_immunity={plan.get('immunity_injected')})",
            status="executing",
        )

        exec_res = planner.execute_plan(
            plan,
            run_tool_fn,
            on_step=lambda msg, st, k, fst: _step_log(player, msg, status=st, step_key=k, step_state=fst),
            cancel_event=_CANCEL,
        )
        if exec_res["status"] == "cancelled":
            raise RuntimeError("Mission cancelled by user.")

        observations = [
            f"[Step {t['index']} - {t['title']} ({t['tool'] or 'think'}) | verified={t['verified']} recovered={t['recovered']}]: {t['output']}"
            for t in exec_res.get("trace") or []
        ]

        _step_log(player, "Synthesizing verified mission outcome…", status="synthesizing")
        synth_prompt = f"""You are JARVIS reporting the completion of a verified multi-step mission.

Mission: {goal}
Strategy: {strategy}
Verification summary: {exec_res['steps_verified']} verified, {exec_res['steps_recovered']} recovered, {exec_res['steps_failed']} failed
Step Observations:
{chr(10).join(observations) or 'Direct synthesis'}

Write a clear, high-intelligence executive report answering the mission completely, followed by a 2-sentence spoken summary."""
        final_answer = gemini.text(synth_prompt, tier=gemini.SMART) or "\n".join(observations) or "Mission complete."

        elapsed = round(time.time() - started, 1)
        with _state_lock:
            _MISSION["status"] = "done"
            _MISSION["finished"] = time.time()
            _MISSION["result"] = final_answer[:2000]
        _publish(player)

        _save_mission_history({
            "ts": time.strftime("%Y-%m-%d %H:%M:%S"),
            "goal": goal,
            "strategy": strategy,
            "steps": len(steps),
            "elapsed_s": elapsed,
            "result": final_answer[:1500],
        })

        _call(
            player,
            "show_content",
            f"AUTONOMOUS MISSION COMPLETE ({elapsed}s)",
            f"GOAL: {goal}\nSTRATEGY: {strategy}\n\nSTEP TRACE:\n"
            + "\n".join(observations)
            + f"\n\nEXECUTIVE SYNTHESIS:\n{final_answer}",
        )
        _call(
            player,
            "request_say",
            f"[AUTONOMOUS MISSION COMPLETE] Mission '{goal[:140]}' finished in {elapsed}s. "
            f"Summarize this result for the user in one to three sentences in their language:\n{final_answer[:1200]}",
        )
    except Exception as e:
        err = f"Autonomous mission stopped: {e}"
        with _state_lock:
            if _MISSION:
                _MISSION["status"] = "failed"
                _MISSION["finished"] = time.time()
                _MISSION["result"] = err
        _publish(player)
        _call(player, "write_log", f"ERR: [Brain] {err}")
    finally:
        _lock.release()


def _status(player) -> str:
    with _state_lock:
        m = json.loads(json.dumps(_MISSION)) if _MISSION else {}
    if m:
        _publish(player)
        running = m.get("finished") is None
        elapsed = round((m.get("finished") or time.time()) - m.get("started", time.time()), 1)
        steps_str = "\n".join(f"  [{t}s] {msg}" for t, msg in (m.get("steps") or [])[-12:])
        text = (
            f"Mission: {m.get('request')}\n"
            f"Status: {m.get('status')} ({'running' if running else 'finished'}, {elapsed}s)\n"
            f"Strategy: {m.get('summary')}\n\n"
            f"Steps:\n{steps_str}\n\n"
            f"Result:\n{m.get('result') or '(in progress)'}"
        )
        _call(player, "show_content", "AUTONOMOUS BRAIN STATUS", text[:4000])
        return f"Autonomous Brain is {m.get('status')} on '{m.get('request', '')[:80]}' ({elapsed}s). Details are on the HUD."

    if _MISSION_LOG.exists():
        try:
            hist = json.loads(_MISSION_LOG.read_text(encoding="utf-8"))
            if isinstance(hist, list) and hist:
                last = hist[-1]
                text = (
                    f"Last Mission ({last.get('ts')}):\n"
                    f"Goal: {last.get('goal')}\n"
                    f"Strategy: {last.get('strategy')}\n"
                    f"Result:\n{last.get('result')}"
                )
                _call(player, "show_content", "AUTONOMOUS BRAIN HISTORY", text[:4000])
                return f"No mission is currently running. Last completed mission: {last.get('goal', '')[:90]}."
        except Exception:
            pass
    msg = "Autonomous Executive Brain (Planner + Parallel Swarm + Persistent Missions) is online."
    _call(player, "show_content", "AUTONOMOUS BRAIN", msg)
    return msg


def run(parameters: dict, player=None, session_memory=None) -> str:
    _ = session_memory
    params = parameters or {}
    goal = str(params.get("goal") or "").strip()
    raw_action = str(params.get("action") or "").strip().lower()
    action = raw_action or ("execute" if goal else "status")
    try:
        max_steps = max(1, min(12, int(params.get("max_steps") or 6)))
    except (TypeError, ValueError):
        max_steps = 6

    if action == "status":
        return _status(player)

    if action == "cancel":
        _CANCEL.set()
        try:
            from core.evolution import screen_agent, swarm
            screen_agent.request_global_abort("autonomous_brain cancel")
            swarm.request_swarm_cancel()
        except Exception:
            pass
        return "Signaled the Autonomous Brain, Multi-Agent Swarm, and Screen Agent to stop active execution."

    if action in ("schedule_mission", "list_missions", "pause_mission", "resume_mission"):
        from core.evolution import mission_runner
        runner = mission_runner.get_default_runner()
        if action == "list_missions":
            items = runner.list_missions(limit=15)
            if not items:
                return "No persistent background missions are stored yet."
            body = json.dumps(items, indent=2, ensure_ascii=False)
            _call(player, "show_content", "PERSISTENT MISSIONS", body[:4000])
            return f"{len(items)} persistent mission(s): " + ", ".join(f"{m['mission_id']}({m['status']})" for m in items[:6])
        if action == "pause_mission":
            mid = str(params.get("mission_id") or "").strip()
            return f"Paused {mid}." if runner.set_status(mid, "paused") else f"Mission '{mid}' not found."
        if action == "resume_mission":
            mid = str(params.get("mission_id") or "").strip()
            return f"Resumed {mid}." if runner.set_status(mid, "active") else f"Mission '{mid}' not found."
        if not goal:
            return "Provide a 'goal' for schedule_mission."
        ttype = str(params.get("trigger_type") or "interval").strip().lower()
        tspec: dict = {}
        if ttype == "interval":
            tspec["interval_s"] = max(10, int(params.get("interval_s") or 300))
        elif ttype == "cron":
            tspec["cron"] = str(params.get("cron_expr") or "0 * * * *")
        rec = runner.create_mission(goal, trigger_type=ttype, trigger_spec=tspec, notify_phone=True)
        runner.start_background_loop()
        return f"Scheduled persistent SQLite mission '{rec['mission_id']}' (trigger={ttype}, survives restarts, phone alerts enabled)."

    if not goal:
        return _status(player)
    if not _lock.acquire(blocking=False):
        _publish(player)
        return "An autonomous mission is already executing — its live step progress is on the HUD."
    threading.Thread(
        target=_execute_mission,
        args=(goal, max_steps, player),
        kwargs={"force_swarm": action == "swarm"},
        daemon=True,
        name="jarvis-autonomous-brain",
    ).start()
    return (
        "Autonomous Executive Brain engaged. Decomposing the goal and streaming "
        "each execution step live to your HUD and phone."
    )
