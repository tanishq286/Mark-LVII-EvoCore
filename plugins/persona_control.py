"""plugins/persona_control.py — Instant 3-Persona Profile Switcher Plugin.

Switches JARVIS's live conversational persona between:
  - precision  (Nova-inspired analytical operator)
  - empathetic (MJ-inspired conversational / Hinglish-friendly)
  - expressive (ELEVEN-inspired vivid avatar-synced presence)
"""
from __future__ import annotations

PLUGIN = {
    "name": "persona_control",
    "description": (
        "Switch or inspect JARVIS's active communication persona profile across four "
        "built-in modes: 'precision' (Nova analytical/developer mode), 'empathetic' "
        "(MJ warm English/Hindi/Hinglish mode), 'expressive' (ELEVEN lively avatar mode), "
        "and 'soulful' (Goddess Heart deeply emotional & devoted companion mode). "
        "Actions: 'status' (default), 'set' (requires persona), or 'list'."
    ),
    "brief": "Switch JARVIS persona between precision, empathetic, expressive, and soulful.",
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "action": {
                "type": "STRING",
                "enum": ["status", "set", "list"],
                "description": "'status' (default), 'set', or 'list'.",
            },
            "persona": {
                "type": "STRING",
                "enum": ["precision", "empathetic", "expressive", "soulful", "romantic_charm"],
                "description": "Target persona mode when action='set'.",
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


def run(parameters: dict, player=None, session_memory=None) -> str:
    """Entry point discovered by `core.plugin_loader`. Never raises."""
    _ = session_memory
    try:
        from core.persona import (
            PERSONAS,
            avatar_mood_for_persona,
            format_persona_prompt,
            normalize_persona,
        )
        from memory.config_manager import get_persona_mode, save_persona_mode

        params = parameters if isinstance(parameters, dict) else {}
        action = str(params.get("action") or "").strip().lower()
        target = str(params.get("persona") or "").strip()
        if not action:
            action = "set" if target else "status"

        if action == "list":
            current = get_persona_mode()
            lines = [
                f"{'*' if k == current else ' '} {k} ({v['title']} — {v['inspired_by']}): {v['summary']}"
                for k, v in PERSONAS.items()
            ]
            body = "\n".join(lines)
            _notify(player, f"Active persona: {current}.", "JARVIS PERSONA PROFILES", body)
            return f"Available personas (active={current}): {', '.join(PERSONAS.keys())}.\n{body}"

        if action == "set":
            if not target:
                return f"Specify persona ({', '.join(PERSONAS.keys())})."
            canonical = save_persona_mode(normalize_persona(target))
            meta = PERSONAS[canonical]
            if player is not None and hasattr(player, "set_mood"):
                try:
                    player.set_mood(avatar_mood_for_persona(canonical), 0.90, 12.0)
                    if canonical == "romantic_charm" and hasattr(player, "trigger_avatar_gesture"):
                        player.trigger_avatar_gesture("shy", meta["title"])
                except Exception:
                    pass
            msg = f"Persona switched to {canonical} ({meta['title']}, mood={meta['avatar_mood']})."
            _notify(player, msg, f"PERSONA · {canonical.upper()}", format_persona_prompt(canonical))
            return msg

        current = get_persona_mode()
        meta = PERSONAS[current]
        msg = f"Current persona is {current} ({meta['title']}): {meta['summary']}"
        _notify(player, msg)
        return msg
    except Exception as exc:
        err = f"persona_control error: {exc}"
        _notify(player, err)
        return err
