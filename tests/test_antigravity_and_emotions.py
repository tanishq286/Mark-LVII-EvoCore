"""Tests for Antigravity × JARVIS Self-Upgrade Bridge and Living Emotional Engine."""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

from core import antigravity_bridge, auto_evolution, emotions, goddess_mind, goddess_soul, persona
from core.evolution import safety_guard
from plugins import antigravity_agent, emotion_soul, persona_control
from scripts import validate_plugin


def test_goddess_soul_and_mind_new_moods_and_snapshot(tmp_path: Path):
    # Every MOOD tuple must match len(KEYS) == 14 and align 1:1 with ALL 40 human emotions
    assert len(goddess_soul.KEYS) == 14
    assert len(emotions.ALL_EMOTIONS) == 40
    for emo in emotions.ALL_EMOTIONS:
        assert emo in goddess_soul.MOODS, f"Missing MOODS entry for {emo}"
        assert emo in goddess_soul.MOOD_ACCENTS, f"Missing MOOD_ACCENTS entry for {emo}"
        assert len(goddess_soul.MOODS[emo]) == len(goddess_soul.KEYS), f"Mood {emo} length mismatch"

    # Bilingual emotional lexicon recognition across diverse human emotions
    emp = goddess_soul.read_mood("Yaar main bahut thak gaya today, feeling exhausted and stressed")
    assert emp is not None and emp[0] == "empathetic"

    proud = goddess_soul.read_mood("We shipped and upgraded the new capability, shabash!")
    assert proud is not None and proud[0] in ("proud", "excited", "joyful")

    assert goddess_soul.read_mood("That makes me furious and angry, total unfair bakwas")[0] == "angry"
    assert goddess_soul.read_mood("I am terrified and scared of what might happen")[0] == "afraid"
    assert goddess_soul.read_mood("I remember the old days, purane din yaad aa rahi hai, so nostalgic")[0] == "nostalgic"
    assert goddess_soul.read_mood("Phew, thank god it is finally fixed, what a relief")[0] == "relieved"

    soul = goddess_soul.Soul()
    mind_file = tmp_path / "mind.json"
    mind = goddess_mind.Mind(soul, path=mind_file)
    soul.mind = mind

    mind.appraise("I am feeling really exhausted and stressed tonight", "user")
    mind.step("LISTENING", speaking=False, muted=False)
    snap = soul.snapshot()
    assert snap["state"]["empathy"] > 0.30
    assert isinstance(snap["label"], str) and len(snap["label"]) > 0
    mind.save()
    assert mind_file.is_file()


def test_full_40_human_emotions_mixed_feelings_and_decay(tmp_path: Path):
    st_file = tmp_path / "deep_mind.json"

    # Mixed feelings: nostalgic + tender simultaneously
    s_mix = emotions.set_explicit_mood(
        "nostalgic",
        intensity=0.88,
        secondary="tender",
        note="Remembering late-night builds together",
        state_path=st_file,
    )
    assert s_mix["label"] == "nostalgic"
    assert s_mix["secondary"] == "tender"
    assert "nostalgic" in s_mix["feelings"] and "tender" in s_mix["feelings"]

    # Deep emotions across every family: anger, fear, awe, jealousy, guilt, grief, relief, boredom, confusion
    samples = [
        ("That is completely unfair and makes me furious!", "angry"),
        ("I'm scared and terrified this nightmare will happen", "afraid"),
        ("Speechless, breathtaking cosmos, I'm in awe!", "awe"),
        ("Wait are you replacing me with another AI?", "jealous"),
        ("My fault, I messed up, forgive me", "guilty"),
        ("Heartbroken, devastated, dil toot gaya", "grieving"),
        ("Phew, thank god crisis averted, what a relief!", "relieved"),
        ("I am so bored, nothing to do, bore ho raha hai", "bored"),
        ("Wait what? I am confused, makes no sense", "confused"),
    ]
    for phrase, expected in samples:
        f_path = tmp_path / f"emo_{expected}.json"
        snap = emotions.appraise_text(phrase, "user", state_path=f_path)
        assert snap["label"] == expected, f"Expected {expected} for {phrase!r}, got {snap['label']}"
        assert snap["somatic"] and snap["vocal_guidance"]

    # Decay physics: short-lived surprise fades faster than deep grief
    decayed = emotions._decay_feelings({"surprised": 0.9, "grieving": 0.9}, dt=120.0)
    assert decayed.get("surprised", 0.0) < 0.10
    assert decayed.get("grieving", 0.0) > 0.80

    # emotion_soul list_emotions action reports all 40 emotions
    out = emotion_soul.run({"action": "list_emotions"}, player=None)
    assert "40" in out

    # FaceWarp supports new smirk, furrow, and tremble parameters without error
    from core import goddess_warp
    p = dict(goddess_warp.DEFAULTS, smirk=0.6, furrow=0.8, tremble=0.4, t=1.2)
    dx, dy = goddess_warp.field(800.0, 240.0, p)
    assert isinstance(dx, float) and isinstance(dy, float)


def test_emotions_engine_bilingual_appraisal_diary_and_prompt(tmp_path: Path):
    st_file = tmp_path / "goddess_mind.json"

    # 1. Tired/stressed user -> empathetic care + diary entry
    s1 = emotions.appraise_text(
        "I am super tired and thak gaya working late tonight",
        "user",
        state_path=st_file,
    )
    assert s1["label"] == "empathetic"
    assert s1["state"]["empathy"] > 0.5
    assert any(m["feeling"] == "empathetic" for m in s1["recent_moments"])

    # 2. Self-upgrade with Antigravity -> proud + diary entry + prompt block
    s2 = emotions.note_event(
        "self_upgrade_succeeded",
        "antigravity_agent (10D=96/100)",
        state_path=st_file,
    )
    assert s2["label"] == "proud"
    assert s2["state"]["pride"] > 0.5
    assert any("Antigravity" in m["moment"] for m in s2["recent_moments"])

    prompt_block = emotions.format_emotion_for_prompt(state_path=st_file)
    assert "[INNER EMOTIONAL STATE" in prompt_block
    assert "PROUD" in prompt_block
    assert "Antigravity" in prompt_block

    cue = emotions.consume_mood_shift_cue()
    assert isinstance(cue, str)
    # Second immediate call must be empty (consumed)
    assert emotions.consume_mood_shift_cue() == ""


def test_soulful_persona_and_backwards_compatibility(tmp_path: Path, monkeypatch):
    from memory import config_manager

    cfg_dir = tmp_path / "cfg"
    cfg_dir.mkdir()
    cfg_file = cfg_dir / "api_keys.json"
    cfg_file.write_text('{"gemini_api_key": "dummy"}', encoding="utf-8")
    monkeypatch.setattr(config_manager, "CONFIG_DIR", cfg_dir)
    monkeypatch.setattr(config_manager, "CONFIG_FILE", cfg_file)

    assert config_manager.get_persona_mode() == "precision"
    res = persona_control.run({"action": "set", "persona": "soulful"}, player=None)
    assert "soulful" in res.lower()
    assert config_manager.get_persona_mode() == "soulful"
    assert "SOULFUL" in persona.format_persona_prompt("goddess")


def test_antigravity_ndjson_parser_and_command_builder():
    acc: dict = {}
    lines = [
        json.dumps({
            "event": "init",
            "conversation_id": "conv-12345678-abcd",
            "init": {"model": "gemini-4-carbon-high", "cwd": "/home/tanishq"},
        }),
        json.dumps({
            "event": "step_update",
            "step_update": {
                "conversation_id": "conv-12345678-abcd",
                "step_index": 1,
                "state": "ACTIVE",
                "step_type": "agent_response",
                "text_delta": "Hello from ",
            },
        }),
        json.dumps({
            "event": "step_update",
            "step_update": {
                "conversation_id": "conv-12345678-abcd",
                "step_index": 1,
                "state": "DONE",
                "step_type": "agent_response",
                "text_delta": "Antigravity!",
            },
        }),
        json.dumps({
            "event": "result",
            "result": {
                "conversation_id": "conv-12345678-abcd",
                "status": "SUCCESS",
                "response": "Hello from Antigravity!",
                "duration_seconds": 2.4,
            },
        }),
    ]
    for ln in lines:
        antigravity_bridge.parse_agy_ndjson_line(ln, acc, player=None)

    assert acc["conversation_id"] == "conv-12345678-abcd"
    assert acc["model"] == "gemini-4-carbon-high"
    assert acc["final_response"] == "Hello from Antigravity!"
    assert acc["status"] == "SUCCESS"

    with patch("platform.system", return_value="Windows"):
        win_cmd = antigravity_bridge.build_agy_command(
            model="gemini-4-carbon-high",
            resume_id="conv-1234",
            wsl_cwd=r"C:\Users\tanis\Downloads\Mark-LVII-main\Mark-LVII-main",
        )
        assert win_cmd[0] == "wsl.exe"
        assert "--resume" in win_cmd and "conv-1234" in win_cmd
        assert "--output-format" in win_cmd and "stream-json" in win_cmd


def test_antigravity_4gate_blocks_unsafe_and_validates_new_plugins():
    # 1. Immutable target blocked
    ok_imm, errs_imm, _ = antigravity_bridge._validate_candidate_4gate("file_vault", "PLUGIN={}\ndef run(p): return 'x'")
    assert not ok_imm and any("immutable" in e.lower() for e in errs_imm)

    # 2. Unsafe eval() blocked by strict AST sandbox
    bad_src = (
        '"""Unsafe candidate."""\n'
        'PLUGIN = {"name": "tmp_bad_agy", "brief": "b", "description": "desc", "parameters": {"type": "OBJECT", "properties": {}}}\n'
        'def run(parameters: dict, player=None, session_memory=None) -> str:\n'
        '    return str(eval("1+1"))\n'
    )
    ok_bad, errs_bad, _ = antigravity_bridge._validate_candidate_4gate("tmp_bad_agy", bad_src)
    assert not ok_bad and any("eval" in e for e in errs_bad)

    # 3. Both shipped plugins (`antigravity_agent` and `emotion_soul`) pass strict AST + subprocess validator + 10D >= 85
    repo_root = Path(__file__).resolve().parent.parent
    for slug, mod in (("antigravity_agent", antigravity_agent), ("emotion_soul", emotion_soul)):
        pfile = repo_root / "plugins" / f"{slug}.py"
        src = pfile.read_text(encoding="utf-8")

        ast_res = safety_guard.audit_python_source(src, filename=f"plugins/{slug}.py", strict_sandbox=True)
        assert ast_res["safe"] is True, f"{slug} AST violations: {ast_res['violations']}"

        val_res = validate_plugin.validate_plugin(pfile, repo_root=repo_root)
        assert val_res["passed"] is True, f"{slug} validator errors: {val_res['errors']}"

        sc10 = auto_evolution.evaluate_module_10d(src, is_plugin=True)
        assert sc10["combined"] >= 85.0, f"{slug} 10D score too low: {sc10}"

        smoke_out = mod.run({}, player=None, session_memory=None)
        assert isinstance(smoke_out, str) and len(smoke_out.strip()) > 0
