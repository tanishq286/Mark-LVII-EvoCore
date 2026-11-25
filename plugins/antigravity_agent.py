"""plugins/antigravity_agent.py — Direct Google Antigravity Bridge & Autonomous Self-Upgrader.

Lets JARVIS collaborate live with Google Antigravity (`agy`) to run deep coding,
architectural, and research workflows OR autonomously upgrade herself with new
4-gate-verified plugins synced across Windows 11 and Ubuntu WSL2.
"""
from __future__ import annotations

PLUGIN = {
    "name": "antigravity_agent",
    "brief": "Collaborate with Google Antigravity or autonomously self-upgrade JARVIS with 4-gate safety.",
    "description": (
        "Direct live-streaming bridge between JARVIS and Google Antigravity (`agy`). "
        "Use this whenever the user asks JARVIS to upgrade herself with Antigravity, build a new "
        "capability or plugin with Antigravity, auto-heal broken tools via Antigravity, delegate "
        "multi-file engineering or research tasks to Antigravity, resume an Antigravity session, "
        "or check Antigravity's live background status. "
        "Actions: "
        "'status' (default) = check Antigravity bridge health, active job, and dual-OS sync; "
        "'upgrade_self' = have Antigravity design, 4-gate validate, dual-OS mirror, and hot-load a new JARVIS capability; "
        "'auto_heal' = scan telemetry & runtime failures and let Antigravity repair degraded plugins automatically; "
        "'run' = run any coding, repo, or research task on Antigravity with live HUD streaming; "
        "'resume' = continue the most recent (or specified) Antigravity conversation; "
        "'history' = view past Antigravity sessions and self-upgrades on the HUD; "
        "'watch' = pop open a live terminal tailing the real-time Antigravity stream; "
        "'cancel' = stop the active Antigravity background job."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "action": {
                "type": "STRING",
                "enum": [
                    "status",
                    "upgrade_self",
                    "auto_heal",
                    "run",
                    "resume",
                    "history",
                    "watch",
                    "cancel",
                ],
                "description": "Operation to perform (defaults to 'upgrade_self' when capability is set, 'run' when task is set, else 'status').",
            },
            "task": {
                "type": "STRING",
                "description": "Task or follow-up instruction for 'run', 'resume', or 'upgrade_self'.",
            },
            "capability": {
                "type": "STRING",
                "description": "For 'upgrade_self': description of the new skill, emotion, or upgrade JARVIS should build into herself with Antigravity.",
            },
            "plugin_name": {
                "type": "STRING",
                "description": "Optional snake_case plugin name for 'upgrade_self'.",
            },
            "cwd": {
                "type": "STRING",
                "description": "Optional project directory for 'run'.",
            },
            "model": {
                "type": "STRING",
                "description": "Optional Antigravity model ID (e.g. 'gemini-4-carbon-high' or 'gemini-3.8-flash-high').",
            },
            "conversation_id": {
                "type": "STRING",
                "description": "Optional Antigravity conversation UUID for 'resume'.",
            },
            "auto_apply": {
                "type": "BOOLEAN",
                "description": "For 'upgrade_self': hot-load and dual-OS mirror immediately after passing all 4 safety gates (default true).",
            },
            "visible_console": {
                "type": "BOOLEAN",
                "description": "If true, opens a live terminal window streaming Antigravity steps.",
            },
        },
        "required": [],
    },
}


def _safe_hud(player, title: str, body: str) -> None:
    """Display structured status on the JARVIS HUD safely from any worker thread."""
    if player is None:
        return
    try:
        if hasattr(player, "show_content"):
            player.show_content(title[:48], body[:4000])
        if hasattr(player, "write_log"):
            player.write_log(f"SYS: [Antigravity] {title}")
    except Exception:
        pass


def run(parameters: dict, player=None, session_memory=None) -> str:
    """Entry point discovered by `core.plugin_loader`. Never raises."""
    _ = session_memory
    try:
        from core import antigravity_bridge as agy

        params = parameters if isinstance(parameters, dict) else {}
        raw_action = str(params.get("action") or "").strip().lower()
        task = str(params.get("task") or "").strip()
        capability = str(params.get("capability") or "").strip()
        plugin_name = str(params.get("plugin_name") or "").strip()
        cwd = str(params.get("cwd") or "").strip()
        model = str(params.get("model") or "").strip()
        conv_id = str(params.get("conversation_id") or "").strip()
        auto_apply = bool(params.get("auto_apply", True))
        visible_console = bool(params.get("visible_console", False))

        if not raw_action:
            if capability:
                action = "upgrade_self"
            elif task:
                low = task.lower()
                if any(p in low for p in ("upgrade yourself", "evolve yourself", "add capability", "build yourself")):
                    action = "upgrade_self"
                else:
                    action = "run"
            else:
                action = "status"
        else:
            action = raw_action

        if action == "status":
            return agy.status_report(player=player)
        if action == "history":
            return agy.history_report(player=player)
        if action == "watch":
            return agy.open_live_console()
        if action == "cancel":
            return agy.cancel_active(player=player)
        if action == "auto_heal":
            return agy.run_autonomous_self_upgrade_check(player=player)
        if action == "upgrade_self":
            spec = capability or task
            return agy.start_self_upgrade(
                spec,
                plugin_name=plugin_name,
                auto_apply=auto_apply,
                model=model,
                player=player,
            )
        if action == "resume":
            rid = conv_id or agy.last_conversation_id()
            if not rid:
                return "No previous Antigravity conversation found to resume yet — use action='run' first."
            return agy.start_task(
                task or "Continue and summarize current status.",
                cwd=cwd,
                model=model,
                resume_id=rid,
                visible_console=visible_console,
                player=player,
            )
        if action == "run":
            return agy.start_task(
                task or capability,
                cwd=cwd,
                model=model,
                visible_console=visible_console,
                player=player,
            )

        msg = f"Unknown antigravity_agent action '{action}'. Use status, upgrade_self, auto_heal, run, resume, history, watch, or cancel."
        _safe_hud(player, "ANTIGRAVITY AGENT", msg)
        return msg
    except Exception as exc:
        err = f"Antigravity bridge encountered an error: {exc}"
        _safe_hud(player, "ANTIGRAVITY ERROR", err)
        return err
