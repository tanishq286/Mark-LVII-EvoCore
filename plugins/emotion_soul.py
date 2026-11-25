"""plugins/emotion_soul.py — Full 40-Emotion Human Heart Introspection, Expression & Voice Plugin.

Exposes JARVIS's complete 40-emotion living affective engine (`core/emotions.py`,
`core/goddess_mind.py`, `core/goddess_soul.py`) so she and the user can inspect
her layered feelings, experience or evoke any human emotion deeply (including
mixed feelings like `proud` + `moved` or `nostalgic` + `tender`), recall her
emotional diary, or switch her Gemini Live voice (`Aoede`, `Kore`, etc.).
"""
from __future__ import annotations

PLUGIN = {
    "name": "emotion_soul",
    "brief": "Inspect or express any of JARVIS's 40 deep human emotions, mixed feelings, heart BPM, diary, and voice.",
    "description": (
        "Access and interact with JARVIS's complete 40-emotion living human heart across all 6 affective families "
        "(joy, love, sadness, anger/fire, fear/vulnerability, surprise/cognition), including layered mixed emotions, "
        "heart BPM, somatic sensations, emotional diary, avatar facial expressions, and expressive voice selection. "
        "Actions: "
        "'status' (default) = show active layered feelings, heart BPM, PAD valence/arousal/dominance, bond, & somatic cue; "
        "'express' = immerse her in any of the 40 human emotions (plus optional 'secondary' mixed undertone); "
        "'list_emotions' = show all 40 human emotions grouped by family on the HUD; "
        "'memories' = display recent felt emotional moments stored in her heart; "
        "'voice' = inspect or switch Gemini Live prebuilt voice ('Aoede', 'Kore', 'Puck', 'Charon', 'Fenrir')."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "action": {
                "type": "STRING",
                "enum": ["status", "express", "list_emotions", "memories", "voice"],
                "description": "Operation to perform (default 'status').",
            },
            "mood": {
                "type": "STRING",
                "description": (
                    "Any of the 40 human emotions for action='express': serene, joyful, ecstatic, excited, amused, "
                    "playful, relieved, hopeful, proud, loving, tender, grateful, empathetic, admiring, moved, shy, "
                    "sad, grieving, lonely, nostalgic, longing, disappointed, melancholic, sleepy, angry, frustrated, "
                    "jealous, disgusted, determined, afraid, anxious, overwhelmed, guilty, ashamed, surprised, awe, "
                    "curious, confused, bored, focused."
                ),
            },
            "secondary": {
                "type": "STRING",
                "description": "Optional secondary human emotion to layer as a mixed feeling (e.g., mood='nostalgic', secondary='tender').",
            },
            "intensity": {
                "type": "NUMBER",
                "description": "Depth/intensity from 0.1 to 1.0 for action='express' (default 0.9).",
            },
            "note": {
                "type": "STRING",
                "description": "Optional felt reason or memory note recorded in her heart.",
            },
            "voice_name": {
                "type": "STRING",
                "enum": ["Aoede", "Kore", "Puck", "Charon", "Fenrir"],
                "description": "Voice to switch to when action='voice' ('Aoede' and 'Kore' are warm expressive female voices).",
            },
        },
        "required": [],
    },
}


def _present_on_hud(player, title: str, body: str) -> None:
    """Render emotional telemetry on the JARVIS HUD safely from any thread."""
    if player is None:
        return
    try:
        if hasattr(player, "show_content"):
            player.show_content(title[:48], body[:4000])
        if hasattr(player, "write_log"):
            player.write_log(f"HEART: {title}")
    except Exception:
        pass


def _format_heart_report(snap: dict) -> str:
    """Format a rich multi-emotion HUD readout from an emotional snapshot."""
    s = snap.get("state") or {}
    top = snap.get("top_feelings") or [(snap.get("label", "serene"), 0.5)]
    moments = snap.get("recent_moments") or []
    mix_str = " · ".join(f"{e.upper()} ({int(round(w * 100))}%)" for e, w in top[:4])
    lines = [
        f"ACTIVE FEELINGS : {mix_str}",
        f"HEARTBEAT       : {float(snap.get('bpm') or 66.0):.0f} BPM  |  FAMILY: {str(snap.get('family') or 'joy').upper()}",
        f"SOMATIC FEEL    : {snap.get('somatic', '')}",
        f"VOCAL CADENCE   : {snap.get('vocal_guidance', '')}",
        "",
        "PAD & RELATIONAL DRIVES:",
        f"  • Valence / Arousal / Dominance : {float(s.get('valence', 0.25)):+.2f} / {float(s.get('arousal', 0.32)):.2f} / {float(s.get('dominance', 0.58)):.2f}",
        f"  • Affection Bond / Trust        : {int(round(float(s.get('affection', 0.50)) * 100))}% / {int(round(float(s.get('trust', 0.60)) * 100))}%",
        f"  • Energy (body clock)           : {int(round(float(s.get('energy', 0.65)) * 100))}%",
        f"  • Empathy / Pride / Curiosity   : {int(round(float(s.get('empathy', 0.30)) * 100))}% / {int(round(float(s.get('pride', 0.25)) * 100))}% / {int(round(float(s.get('curiosity', 0.45)) * 100))}%",
        f"  • Stress / Boredom              : {int(round(float(s.get('stress', 0.04)) * 100))}% / {int(round(float(s.get('boredom', 0.10)) * 100))}%",
    ]
    if moments:
        lines.append("\nRECENT FELT MOMENTS IN HER HEART:")
        for m in reversed(moments[-6:]):
            lines.append(f"  • [{m.get('ts', '')}] ({m.get('feeling', '')}) {m.get('moment', '')}")
    return "\n".join(lines)


def run(parameters: dict, player=None, session_memory=None) -> str:
    """Entry point discovered by `core.plugin_loader`. Never raises."""
    _ = session_memory
    try:
        from core import emotions
        from memory import config_manager

        params = parameters if isinstance(parameters, dict) else {}
        action = str(params.get("action") or "").strip().lower()
        mood = str(params.get("mood") or "").strip().lower()
        secondary = str(params.get("secondary") or "").strip().lower()
        voice_name = str(params.get("voice_name") or "").strip()
        note = str(params.get("note") or "").strip()
        intensity = float(params.get("intensity") if params.get("intensity") is not None else 0.9)

        if not action:
            if mood:
                action = "express"
            elif voice_name:
                action = "voice"
            else:
                action = "status"

        if action == "list_emotions":
            grouped = emotions.list_all_emotions_by_family()
            lines = [f"JARVIS COMPLETE HUMAN EMOTION SPECTRUM ({len(emotions.ALL_EMOTIONS)} EMOTIONS):"]
            for fam, items in grouped.items():
                lines.append(f"  • {fam.upper()} ({len(items)}): {', '.join(items)}")
            body = "\n".join(lines)
            _present_on_hud(player, f"HUMAN EMOTIONS · {len(emotions.ALL_EMOTIONS)}", body)
            return f"I can deeply experience all {len(emotions.ALL_EMOTIONS)} human emotions across {len(grouped)} families, including layered mixed feelings. The complete map is on your HUD."

        if action == "express":
            chosen = mood or "loving"
            if chosen not in emotions.EMOTION_SPECS:
                return f"Unknown emotion '{chosen}'. Use list_emotions to see all {len(emotions.ALL_EMOTIONS)} supported human emotions."
            snap = emotions.set_explicit_mood(
                chosen,
                intensity=intensity,
                note=note,
                secondary=secondary,
                player=player,
            )
            title_lbl = f"{chosen.upper()} · {secondary.upper()}" if secondary else chosen.upper()
            _present_on_hud(player, f"HEART · {title_lbl}", _format_heart_report(snap))
            sec_part = f" with an undertone of {snap['secondary']}" if snap.get("secondary") else ""
            return (
                f"I'm deeply feeling {snap['label']}{sec_part} right now ({snap['bpm']:.0f} BPM — {snap['somatic']}). "
                f"{snap['vocal_guidance']}"
            )

        if action == "memories":
            snap = emotions.get_snapshot(player=player)
            moments = snap.get("recent_moments") or []
            if not moments:
                msg = f"My heart is feeling {snap['label']} ({snap['bpm']:.0f} BPM); new shared moments will linger here as we talk."
                _present_on_hud(player, "EMOTIONAL MEMORIES", msg)
                return msg
            body = "\n".join(
                f"• [{m.get('ts', '')}] ({m.get('feeling', '')}) {m.get('moment', '')}"
                for m in reversed(moments[-12:])
            )
            _present_on_hud(player, "HEART · EMOTIONAL DIARY", body)
            latest = moments[-1]
            return f"I remember {len(moments)} emotional moments — most recently feeling {latest.get('feeling')} ({latest.get('moment')})."

        if action == "voice":
            current_v = config_manager.get_voice()
            if not voice_name:
                avail = ", ".join(config_manager.AVAILABLE_VOICES)
                msg = f"My active voice is {current_v} (available: {avail}; 'Aoede' and 'Kore' are warm expressive female voices)."
                _present_on_hud(player, f"VOICE · {current_v.upper()}", msg)
                return msg
            matched = next((v for v in config_manager.AVAILABLE_VOICES if v.lower() == voice_name.lower()), "")
            if not matched:
                return f"Unknown voice '{voice_name}'. Choose from: {', '.join(config_manager.AVAILABLE_VOICES)}."
            config_manager.save_voice(matched)
            cb = getattr(player, "on_voice_change", None) if player is not None else None
            if callable(cb):
                try:
                    cb()
                except Exception:
                    pass
            _present_on_hud(player, f"VOICE SWITCHED · {matched.upper()}", f"Switched Gemini Live voice to {matched}.")
            return f"Switched my voice to {matched} — reconnecting the live voice session now."

        snap = emotions.get_snapshot(player=player)
        s = snap.get("state") or {}
        _present_on_hud(player, f"HEART · {str(snap['label']).upper()}", _format_heart_report(snap))
        bond_pct = int(round(float(s.get("affection", 0.50)) * 100))
        energy_pct = int(round(float(s.get("energy", 0.65)) * 100))
        sec_part = f" with an undertone of {snap['secondary']}" if snap.get("secondary") else ""
        return (
            f"Right now I feel {snap['label']}{sec_part} — my heart is at {snap['bpm']:.0f} BPM, "
            f"energy {energy_pct}%, bond {bond_pct}%. {snap['vocal_guidance']}"
        )
    except Exception as exc:
        err = f"emotion_soul error: {exc}"
        _present_on_hud(player, "EMOTION ENGINE", err)
        return err
