"""Tests for Milestone 18 — Specialist Expert Router, Auto-RAG, Lifelike Presence, romantic_charm & Persuasion Coach."""
from __future__ import annotations

import asyncio
from datetime import datetime
import json
from pathlib import Path
import subprocess
import sys

import numpy as np
import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent

IMMUTABLE_FILES = [
    "core/evolution/safety_guard.py",
    "plugins/file_vault.py",
    "plugins/voice_lock.py",
    "core/confirm.py",
]


@pytest.fixture(autouse=True)
def _isolate_privacy_and_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    from core import privacy
    from memory import config_manager as cm

    cfg_dir = tmp_path / "cfg"
    cfg_dir.mkdir(parents=True, exist_ok=True)
    cfg_file = cfg_dir / "api_keys.json"
    cfg_file.write_text('{"gemini_api_key": "dummy", "persona_mode": "soulful"}', encoding="utf-8")
    monkeypatch.setattr(cm, "CONFIG_DIR", cfg_dir)
    monkeypatch.setattr(cm, "CONFIG_FILE", cfg_file)
    try:
        privacy.set_active(False, persist=False)
        yield {"cfg_file": cfg_file, "tmp_path": tmp_path}
    finally:
        privacy.set_active(False, persist=False)


def test_expert_router_modes_tools_privacy_and_critic_verify(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    from core import privacy
    from core.brain import expert_router as er
    from core.memory import knowledge_graph
    from plugins import knowledge_rag

    assert len(er.EXPERT_MODES) == 5
    cases = {
        "Design a clean async Python API architecture and debug this SQLite migration bug": "software_architect",
        "Conduct a deep research literature survey comparing benchmarks and citations across papers": "deep_researcher",
        "Analyze unit economics, EBITDA margins, valuation, and contract negotiation strategy": "finance_strategy",
        "Derive the Bayesian posterior probability and prove convergence of gradient descent": "science_math",
        "Write a vivid Hinglish speech and creative story with memorable metaphor": "creative_writer",
    }
    for q, expected in cases.items():
        route = er.classify_expert_intent(q)
        assert route.mode == expected
        assert len(route.tool_set) >= 3

    # Privacy Mode filters cloud/web tools out of tool sets
    privacy.set_active(True, persist=False)
    priv_tools = er.select_tool_set("deep_researcher")
    assert "web_search" not in priv_tools
    assert "web_scraper" not in priv_tools
    assert "search_vault" in priv_tools
    privacy.set_active(False, persist=False)

    # Mock deterministic embeddings for auto-indexing
    monkeypatch.setattr(knowledge_rag, "_embed_model_reachable", lambda *a, **kw: (True, "ok"))
    monkeypatch.setattr(
        knowledge_rag,
        "_embed_text",
        lambda text, **kw: np.array([1.0, 0.25, 0.5, 0.75], dtype=np.float32),
    )

    # Silent Think -> Critic-Verify -> Respond catches bad Python syntax and revises without leaking <think>
    calls: list[str] = []

    def fake_llm(stage: str, sys_p: str, usr_p: str) -> str:
        calls.append(stage)
        if stage == "think":
            return "<think>hidden chain of thought</think>\n```python\ndef add(a, b:\n    return a + b\n```"
        if stage == "critic_verify":
            assert "syntax error" in usr_p.lower()
            return "VERDICT: REVISE Fix missing closing parenthesis in def add."
        return "Here is the verified helper:\n```python\ndef add(a: int, b: int) -> int:\n    return a + b\n```"

    res = er.verify_and_respond(
        "Architect and implement a clean Python add helper and debug signature validation step-by-step",
        llm_fn=fake_llm,
        auto_index=True,
        vault_dir=tmp_path / "knowledge",
        graph_db_path=tmp_path / "kg.sqlite",
    )
    assert calls == ["think", "critic_verify", "respond"]
    assert res["mode"] == "software_architect"
    assert res["verified"] is True
    assert res["critic_passed"] is True
    assert "hidden chain of thought" not in res["text"]
    assert res["auto_indexed"]["ok"] is True
    assert res["auto_indexed"]["rag_indexed"] is True
    assert res["auto_indexed"]["graph_entities"] >= 1
    kg_res = knowledge_graph.query_graph("software_architect", db_path=tmp_path / "kg.sqlite")
    assert len(kg_res.get("nodes") or []) >= 1

    # Heuristic verifier catches arithmetic mismatch too
    math_check = er.heuristic_verify_draft("calculate 25 * 4", "25 * 4 = 90", "science_math")
    assert math_check.passed is False
    assert any("mismatch" in iss.lower() for iss in math_check.issues)


def test_auto_compound_knowledge_from_research_and_tasks(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    from core.memory import knowledge_graph
    from core.tasks.engine import TaskEngine
    from plugins import knowledge_rag

    monkeypatch.setattr(knowledge_rag, "_embed_model_reachable", lambda *a, **kw: (True, "ok"))
    unit = np.array([0.8, 0.4, 0.2, 0.1], dtype=np.float32)
    unit = (unit / np.linalg.norm(unit)).astype(np.float32)
    monkeypatch.setattr(knowledge_rag, "_embed_text", lambda text, **kw: unit)

    vault = tmp_path / "knowledge"
    kg_db = tmp_path / "kg.sqlite"

    first = knowledge_rag.auto_compound_artifact(
        title="SQLite WAL Tuning Study",
        content=(
            "Measured WAL checkpoint tuning for local agent pipelines. "
            "Setting synchronous=NORMAL cuts commit tail latency by 45 percent cleanly."
        ),
        kind="research",
        domain="software_architect",
        vault_dir=vault,
        graph_db_path=kg_db,
    )
    assert first["ok"] is True
    assert first["rag_indexed"] is True
    assert first["chunks"] >= 1
    assert first["graph_entities"] >= 1
    assert Path(first["path"]).is_file()

    # Identical content deduplicates instead of duplicating chunks
    second = knowledge_rag.auto_compound_artifact(
        title="SQLite WAL Tuning Study",
        content=(
            "Measured WAL checkpoint tuning for local agent pipelines. "
            "Setting synchronous=NORMAL cuts commit tail latency by 45 percent cleanly."
        ),
        kind="research",
        domain="software_architect",
        vault_dir=vault,
        graph_db_path=kg_db,
    )
    assert second["deduped"] is True

    hits = knowledge_rag.search_vault("WAL checkpoint latency", top_k=3, vault_dir=vault)
    assert len(hits) >= 1 and "WAL" in hits[0]["text"]

    # TaskEngine._complete automatically compounds solved tasks (uses conftest tmp_path isolation)
    monkeypatch.setattr(knowledge_rag, "KNOWLEDGE_DIR", vault)
    monkeypatch.setattr(knowledge_graph, "DEFAULT_GRAPH_DB", kg_db)
    agents_dir = tmp_path / "agents"
    agents_dir.mkdir()
    engine = TaskEngine(tmp_path / "tasks_state", tmp_path / "reports", [agents_dir])
    task_obj = engine.start("Summarize battery thermals for 2026 laptops", agent="research")
    tid = task_obj["id"] if isinstance(task_obj, dict) else task_obj
    long_report = (
        "## Summary\n\nSustained CPU power curves above 45W require dual vapor-chamber cooling. "
        "Efficiency cores keep idle temperature below 42C while maintaining battery longevity across workloads."
    )
    engine._complete(tid, long_report)
    comp_files = list((vault / "compounded").glob("*.md"))
    assert len(comp_files) >= 1
    assert any("vapor-chamber" in f.read_text(encoding="utf-8") for f in comp_files)


def test_emotion_matched_tts_pacing_and_pipeline_streaming(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    from core import emotions
    from providers.engine_pipeline import PipelineEngine
    from providers.tts_system import SystemTTS

    state_file = tmp_path / "goddess_mind.json"
    emotions.appraise_text("We crushed the launch! Amazing breakthrough!", "user", state_path=state_file)
    fast_pros = emotions.tts_prosody_for_state(state_path=state_file, mood="excited")

    emotions.appraise_text("I am exhausted and quiet tonight, miss you", "user", state_path=state_file)
    soft_pros = emotions.tts_prosody_for_state(state_path=state_file, mood="tender", persona="romantic_charm")

    assert fast_pros["rate"] > soft_pros["rate"]
    assert soft_pros["pause_ms"] >= fast_pros["pause_ms"]
    assert soft_pros["pitch_hint"] == "soft_warm"
    prompt_block = emotions.format_emotion_for_prompt(state_path=state_file)
    assert "Matched voice cadence" in prompt_block

    # SystemTTS streams sentence-by-sentence with rate forwarded & silence pause PCM between sentences
    seen_rates: list[int] = []
    tts = SystemTTS({})

    def fake_synth(sentence: str, *, rate: int = 0) -> bytes:
        seen_rates.append(rate)
        import io
        import wave
        buf = io.BytesIO()
        with wave.open(buf, "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(24000)
            wf.writeframes(b"\x10\x00" * 120)
        return buf.getvalue()

    monkeypatch.setattr(tts, "_synth_blocking", fake_synth)

    async def _collect_sys():
        out = []
        async for chunk in tts.speak("First sentence. Second sentence.", rate=2, pause_ms=200):
            out.append(chunk)
        return out

    chunks = asyncio.run(_collect_sys())
    assert seen_rates == [2, 2]
    assert sum(len(c) for c in chunks) > (240 * 2 + 2000)

    # PipelineEngine forwards emotion prosody kwargs to supporting TTS adapters
    captured: list[dict] = []

    class _ProsodyTTS:
        caps = {}

        async def speak(self, text: str, *, rate: int = 0, pause_ms: int = 0):
            captured.append({"text": text, "rate": rate, "pause_ms": pause_ms})
            yield b"\x00\x00" * 160

    class _DummyLLM:
        caps = {"tools": False, "streaming": True}

    eng = PipelineEngine(_DummyLLM(), tts=_ProsodyTTS())
    asyncio.run(eng._speak("Oh really? Tell me more!"))
    assert len(captured) == 1
    assert isinstance(captured[0]["rate"], int)
    assert 130 <= captured[0]["pause_ms"] <= 450


def test_soul_and_avatar_overlay_breathing_blink_gaze_and_listening(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    from core import goddess_mind, goddess_soul

    monkeypatch.setattr(goddess_mind, "STATE_FILE", tmp_path / "mind.json")
    soul = goddess_soul.Soul()
    soul.step(0.05, "LISTENING", speaking=False, muted=False)
    p0 = soul.snapshot()["breath_phase"]
    soul.note_user_voice(0.72, state="LISTENING")
    soul.step(0.25, "LISTENING", speaking=False, muted=False)
    snap = soul.snapshot()
    assert snap["breath_phase"] != p0
    assert snap["listen_amp"] > 0.10
    assert soul.accents["smile"] > 0.10

    # Qt AvatarOverlayWindow offscreen presence check
    from PyQt6.QtWidgets import QApplication
    from gui.avatar_overlay import AvatarOverlayWindow

    app = QApplication.instance() or QApplication([])
    assert app is not None
    ov = AvatarOverlayWindow(size=180)
    try:
        ov.set_state("THINKING")
        ov._gaze_timer = 0.0
        ov._blink_timer = 0.0
        ov._tick()
        st1 = ov.get_presence_state()
        assert st1["breath_phase"] > 0.0
        assert st1["gaze_target"][1] < 0.0  # contemplative upward gaze while thinking

        ov.set_state("LISTENING")
        ov.set_listening_level(0.75)
        ov._gaze_timer = 0.0
        ov._tick()
        st2 = ov.get_presence_state()
        assert st2["listen_amp"] > 0.5
        assert st2["gesture"] == "attentive"
    finally:
        ov.close()


def test_proactive_checkins_quiet_hours_kg_deadlines_and_mind_drives(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    from actions.proactive import ProactiveEngine
    from core import emotions
    from core.memory import knowledge_graph
    from memory import config_manager as cm

    cm.save_proactive_settings(
        enabled=True,
        quiet_hours_start="23:00",
        quiet_hours_end="07:30",
    )
    eng = ProactiveEngine(min_silence_secs=60, check_cooldown=60)
    # Overnight quiet hours block late night and early morning, allow daytime
    assert eng.is_quiet_hours(datetime(2026, 11, 25, 23, 30)) is True
    assert eng.is_quiet_hours(datetime(2026, 11, 25, 2, 15)) is True
    assert eng.is_quiet_hours(datetime(2026, 11, 25, 11, 0)) is False

    state_file = tmp_path / "mind_state.json"
    emotions.appraise_text("Working on deadline, miss you my dear", "user", state_path=state_file)
    kg_db = tmp_path / "kg.sqlite"
    knowledge_graph.ingest_extracted_facts(
        [{"kind": "deadline", "subject": "Investor Deck", "value": "Friday 5 PM deadline", "fact": "Investor Deck due Friday 5 PM"}],
        db_path=kg_db,
    )

    assert eng.should_trigger(
        last_user_speech=0.0,
        now_mono=1000.0,
        now_dt=datetime(2026, 11, 25, 23, 45),
        emotion_state_path=state_file,
    ) is False
    assert eng.should_trigger(
        last_user_speech=0.0,
        now_mono=1000.0,
        now_dt=datetime(2026, 11, 25, 14, 0),
        emotion_state_path=state_file,
    ) is True

    monkeypatch.setattr("memory.memory_manager.format_memory_for_prompt", lambda m: "User builds Mark-LVII")
    prompt = eng.build_prompt(
        memory={},
        graph_db_path=kg_db,
        emotion_state_path=state_file,
        now_dt=datetime(2026, 11, 25, 14, 0),
    )
    assert "Investor Deck" in prompt or "Friday 5 PM" in prompt
    assert "Mind drives:" in prompt


def test_romantic_charm_persona_callbacks_and_persuasion_coach_ethics(tmp_path: Path):
    from core import persona
    from core.memory import conversation_store as store
    from plugins import persona_control, persuasion_coach

    db = tmp_path / "conversations.sqlite"
    store.append_turn("user", "Let's ship Mark-LVII v18 tonight with chai!", session_id="s1", db_path=db)
    store.add_facts(
        [{"kind": "preference", "subject": "Drink", "value": "Masala chai during late-night coding", "fact": "Drink: Masala chai"}],
        session_id="s1",
        db_path=db,
    )

    assert persona.normalize_persona("flirtist") == "romantic_charm"
    assert persona.normalize_persona("romantic") == "romantic_charm"
    prompt = persona.format_persona_prompt("romantic_charm", db_path=db)
    assert "ROMANTIC CHARM" in prompt
    assert "Hinglish" in prompt
    assert "tender" in prompt and "playful" in prompt and "shy" in prompt
    assert "Masala chai" in prompt

    class _DummyPlayer:
        def __init__(self):
            self.calls = []

        def set_mood(self, mood, intensity=1.0, hold=None):
            self.calls.append(("mood", mood))

        def trigger_avatar_gesture(self, gesture, text=""):
            self.calls.append(("gesture", gesture))

        def write_log(self, msg):
            pass

        def show_content(self, title, body):
            pass

    pl = _DummyPlayer()
    sw = persona_control.run({"action": "set", "persona": "romantic_charm"}, player=pl)
    assert "romantic_charm" in sw.lower()
    assert ("mood", "playful") in pl.calls
    assert ("gesture", "shy") in pl.calls

    # Ethical persuasion coach prepares solid strategy and blocks manipulation
    status = persuasion_coach.run({})
    assert "ethical mode" in status.lower()

    refused = persuasion_coach.run({
        "action": "prepare_pitch",
        "goal": "Trick them into signing with fake scarcity and fabricate numbers",
    })
    assert "refused" in refused.lower()

    brief = persuasion_coach.run({
        "action": "negotiation_brief",
        "goal": "Close an annual pilot contract at $24k/yr",
        "audience": "VP of Engineering worried about rollout risk",
        "context": "Cut manual QA triage by 42% in a 2-week benchmark",
        "batna": "Keep selling standalone Q1 audits",
        "draft_message": "Hey, wanted to chat about working together.",
        "tone": "executive",
    })
    assert "Structured Pitch Script:" in brief
    assert "BATNA / Walk-Away Floor:" in brief
    assert "Draft Audit:" in brief


def test_immutable_files_and_m18_plugin_validation():
    from core.cross_os import run_git

    for rel in IMMUTABLE_FILES:
        r = run_git(REPO_ROOT, "diff", "--name-only", "v17-privacy-tunnel-ready..HEAD", "--", rel)
        assert r.returncode == 0
        assert r.stdout.strip() == "", f"Immutable file changed after v17: {rel}"
        r_wt = run_git(REPO_ROOT, "status", "--porcelain", "--", rel)
        assert r_wt.returncode == 0
        assert r_wt.stdout.strip() == "", f"Immutable file dirty in working tree: {rel}"

    for plug in ("plugins/persuasion_coach.py", "plugins/persona_control.py", "plugins/knowledge_rag.py"):
        res = subprocess.run(
            [sys.executable, "scripts/validate_plugin.py", plug],
            cwd=str(REPO_ROOT),
            capture_output=True,
            text=True,
        )
        out = res.stdout + res.stderr
        assert res.returncode == 0, out
        assert "WARN" not in out, out
