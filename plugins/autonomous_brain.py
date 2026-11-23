"""JARVIS Autonomous Multi-Step Executive Brain & Cross-OS Orchestrator.

Decomposes complex goals into an adaptive Plan -> Execute -> Verify -> Adapt
loop over JARVIS's entire live tool catalog (core actions + hot-loaded plugins),
streaming every step and decision to the JARVIS HUD in real time.
"""
from __future__ import annotations

import json
import threading
import time
from pathlib import Path

PLUGIN = {
    "name": "autonomous_brain",
    "brief": "Plan and chain multiple JARVIS tools for complex multi-step goals.",
    "description": (
        "JARVIS's high-intelligence multi-step executive planner and autonomous orchestrator. "
        "Use this when the user gives a complex multi-stage goal that requires planning, chaining "
        "multiple JARVIS tools in sequence, verifying results, or adapting automatically. "
        "Actions: "
        "'status' = view the active or most recent autonomous mission plan and step trace; "
        "'execute' = decompose a complex goal into steps and execute them autonomously with live HUD tracking; "
        "'cancel' = abort the currently running autonomous mission."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "action": {
                "type": "STRING",
                "enum": ["status", "execute", "cancel"],
                "description": "Operation to run (default 'status', or 'execute' when goal is provided).",
            },
            "goal": {
                "type": "STRING",
                "description": "For 'execute': the complete high-level mission or multi-step request to accomplish.",
            },
            "max_steps": {
                "type": "INTEGER",
                "description": "Maximum autonomous tool steps to run (default 6, max 12).",
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


def _execute_mission(goal: str, max_steps: int, player) -> None:
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
        from core import gemini
        _, catalog_text = _catalog_summary(player)
        run_tool_fn = getattr(player, "run_tool", None) if player else None

        plan_prompt = f"""You are JARVIS's Autonomous Executive Brain. Break this user mission into a concrete, verifiable plan of at most {max_steps} steps using JARVIS's available tools.

Mission: {goal}

Available JARVIS tools:
{catalog_text or '(standard desktop, web_search, code_helper, dev_agent, claude_code, self_evolve tools)'}

Return ONLY valid JSON:
{{
  "strategy": "One-sentence executive summary of how you will solve this",
  "steps": [
    {{
      "id": "step_1",
      "title": "Short title of step 1",
      "tool": "tool_name_or_none_for_reasoning",
      "parameters": {{}},
      "expected": "What this step produces"
    }}
  ]
}}"""
        plan = gemini.as_json(plan_prompt, tier=gemini.SMART) or {}
        strategy = str((plan.get("strategy") if isinstance(plan, dict) else "") or "Direct multi-step reasoning and execution.")
        steps = (plan.get("steps") if isinstance(plan, dict) else None) or []
        if not isinstance(steps, list):
            steps = []
        steps = steps[:max_steps]

        with _state_lock:
            _MISSION["summary"] = strategy
            for idx, s in enumerate(steps, 1):
                if isinstance(s, dict):
                    key = f"Step {idx}: {s.get('title') or s.get('tool') or 'Task'}"
                    _MISSION["files"][key] = {
                        "action": "edit",
                        "what": str(s.get("expected") or s.get("tool") or ""),
                        "state": "waiting",
                    }
        _publish(player)
        _step_log(player, f"Strategy: {strategy} ({len(steps)} planned steps)", status="executing")

        observations: list[str] = []
        for idx, s in enumerate(steps, 1):
            if _CANCEL.is_set():
                raise RuntimeError("Mission cancelled by user.")
            if not isinstance(s, dict):
                continue
            title = str(s.get("title") or f"Step {idx}")
            tool_name = str(s.get("tool") or "").strip()
            params = s.get("parameters") if isinstance(s.get("parameters"), dict) else {}
            step_key = f"Step {idx}: {title}"

            _step_log(player, f"▶ {step_key} (tool={tool_name or 'reasoning'})", status="executing",
                      step_key=step_key, step_state="writing")

            if tool_name and tool_name.lower() != "none" and callable(run_tool_fn):
                out = str(run_tool_fn(tool_name, params) or "Done.")
            else:
                reason_prompt = (
                    f"Mission: {goal}\n"
                    f"Prior observations:\n" + "\n".join(observations[-4:]) + "\n\n"
                    f"Execute reasoning step {idx} ({title}): {s.get('expected', '')}\n"
                    "Provide a concise, factual result."
                )
                out = gemini.text(reason_prompt, tier=gemini.SMART) or "Completed reasoning step."

            observations.append(f"[Step {idx} - {title} ({tool_name or 'think'})]: {out[:1200]}")
            _step_log(player, f"✓ {step_key} → {out[:110]}", status="verifying",
                      step_key=step_key, step_state="done")

        _step_log(player, "Synthesizing final mission outcome…", status="synthesizing")
        synth_prompt = f"""You are JARVIS reporting the completion of an autonomous multi-step mission.

Mission: {goal}
Strategy: {strategy}
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
    msg = "Autonomous Executive Brain is online and ready for multi-step goals."
    _call(player, "show_content", "AUTONOMOUS BRAIN", msg)
    return msg


def run(parameters: dict, player=None, session_memory=None) -> str:
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
        return "Signaled the Autonomous Brain to stop the active mission."
    if not goal:
        return _status(player)
    if not _lock.acquire(blocking=False):
        _publish(player)
        return "An autonomous mission is already executing — its live step progress is on the HUD."
    threading.Thread(
        target=_execute_mission,
        args=(goal, max_steps, player),
        daemon=True,
        name="jarvis-autonomous-brain",
    ).start()
    return (
        "Autonomous Executive Brain engaged. I am decomposing the goal and streaming "
        "each execution step live to your HUD."
    )
