"""Milestone 15: cross-session memory (E2E across two real processes) and the
24/7 Researcher / Engineer agents.

Live Gemini recall runs only with JARVIS_LIVE_GEMINI=1 (it costs API quota).
"""
from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

from core.cross_os import run_git
from core.evolution import agents_247, engineer_daemon, research_daemon as rq, telemetry
from core.memory import conversation_store as cs
from core.memory import knowledge_graph as kg
from core.memory import memory_extractor as mx
from core.memory.live_memory import LiveMemoryBridge

REPO_ROOT = Path(__file__).resolve().parent.parent


# ── conversation store ──────────────────────────────────────────────────────

def test_history_is_append_only_and_persists_each_turn(tmp_path: Path):
    db = tmp_path / "c.sqlite"
    sid = cs.start_session(db_path=db)
    rid = cs.append_turn("user", "hello there", session_id=sid, db_path=db)
    assert rid > 0
    # Visible to a brand-new connection immediately (committed, not buffered).
    rows = sqlite3.connect(db).execute("SELECT role, text FROM conversation_history").fetchall()
    assert rows == [("user", "hello there")]
    cs.add_summary(sid, "a summary", db_path=db)
    cs.add_facts([{"kind": "note", "subject": "s", "value": "v"}], session_id=sid, db_path=db)
    for sql in ("DELETE FROM conversation_history", "UPDATE conversation_history SET text='x'",
                "DELETE FROM memory_facts", "DELETE FROM session_summaries"):
        conn = sqlite3.connect(db, timeout=2)
        try:
            with pytest.raises(sqlite3.DatabaseError, match="append-only"):
                conn.execute(sql)
        finally:
            conn.rollback()
            conn.close()
    assert cs.stats(db_path=db)["conversation_history"] == 1
    assert cs.append_turn("bogus_role", "x", db_path=db) == 0
    assert cs.append_turn("user", "   ", db_path=db) == 0


def test_rule_extraction_facts_names_preferences_and_supersede(tmp_path: Path):
    facts = mx.extract_rule_facts(
        "My favorite project is Project Nova and my deadline is Friday. Call me Tony. I prefer dark mode.")
    got = {(f["kind"], f["value"]) for f in facts}
    assert ("favorite", "Project Nova") in got
    assert ("deadline", "Friday") in got
    assert ("identity", "Tony") in got
    assert ("preference", "dark mode") in got

    db = tmp_path / "c.sqlite"
    mx.process_turn("my deadline is Friday", db_path=db, graph_db_path=tmp_path / "kg.sqlite")
    mx.process_turn("my deadline moved to Monday", db_path=db, graph_db_path=tmp_path / "kg.sqlite")
    latest = {f["subject"]: f["value"] for f in cs.latest_facts(db_path=db)}
    assert latest["deadline"] == "Monday"


def test_session_end_feeds_knowledge_graph_and_rag_note(tmp_path: Path):
    db, g, vault = tmp_path / "c.sqlite", tmp_path / "kg.sqlite", tmp_path / "vault"
    sid = cs.start_session(db_path=db)
    cs.append_turn("user", "My favorite project is Project Nova", session_id=sid, db_path=db)
    cs.append_turn("assistant", "Great choice.", session_id=sid, db_path=db)
    res = mx.process_session_end(
        sid, db_path=db, graph_db_path=g, vault_dir=vault,
        llm_fn=lambda _c: {"summary": "Discussed Project Nova.",
                           "facts": [{"kind": "task", "subject": "next step", "value": "ship the demo"}]})
    assert res["ok"] and res["summary"] == "Discussed Project Nova."
    sub = kg.query_graph("Project Nova", max_hops=1, top_k=5, db_path=g)
    assert any("Nova" in n["name"] for n in sub["nodes"])
    note = (vault / "jarvis_memory" / "remembered_facts.md").read_text(encoding="utf-8")
    assert "Project Nova" in note and "ship the demo" in note
    assert cs.last_session_summary(db_path=db)["summary"] == "Discussed Project Nova."


def test_wake_primer_only_once_after_sleep(tmp_path: Path):
    br = LiveMemoryBridge(db_path=tmp_path / "c.sqlite", use_llm=False)
    br.start(recover=False).result(5)
    assert br.wake_primer() == ""                      # never slept
    br.record("user", "my favorite color is teal")
    br.flush()
    br.on_sleep("test").result(10)
    primer = br.wake_primer()
    assert "teal" in primer and "MEMORY REFRESH" in primer
    assert br.wake_primer() == ""                      # consumed
    br.on_shutdown()


_SESSION_SCRIPT = textwrap.dedent("""
    import sys, json
    from pathlib import Path
    sys.path.insert(0, sys.argv[1])
    tmp = Path(sys.argv[2])
    from core.memory import knowledge_graph, conversation_store
    from plugins import knowledge_rag
    knowledge_graph.DEFAULT_GRAPH_DB = tmp / "knowledge_graph.sqlite"
    knowledge_rag.KNOWLEDGE_DIR = tmp / "knowledge"
    from core.memory.live_memory import LiveMemoryBridge
    br = LiveMemoryBridge(use_llm=False)
    if sys.argv[3] == "one":
        br.start().result(10)
        br.record("user", "My favorite project is Project Nova and my deadline is Friday")
        br.record("assistant", "Got it - Project Nova, due Friday.")
        br.flush()
        br.on_shutdown()
        print(json.dumps({"sid": br.session_id}))
    else:
        recovered = br.start().result(30)          # summarises session one (crash-safe path)
        print(json.dumps({"bootstrap": br.bootstrap_text(), "recovered": recovered}))
        br.on_shutdown()
""")


def _run_session(tmp_path: Path, which: str) -> dict:
    script = tmp_path / "session.py"
    script.write_text(_SESSION_SCRIPT, encoding="utf-8")
    env = {**os.environ, "JARVIS_CONVERSATIONS_DB": str(tmp_path / "conversations.sqlite"),
           "PYTHONIOENCODING": "utf-8"}
    proc = subprocess.run([sys.executable, "-B", str(script), str(REPO_ROOT), str(tmp_path), which],
                          capture_output=True, text=True, encoding="utf-8", env=env, timeout=120)
    assert proc.returncode == 0, proc.stderr[-1500:]
    return json.loads(proc.stdout.strip().splitlines()[-1])


def test_e2e_session_two_recalls_session_one_after_shutdown(tmp_path: Path):
    """Session 1 states facts and shuts down; a NEW process boots and is primed with them."""
    _run_session(tmp_path, "one")
    out = _run_session(tmp_path, "two")
    boot = out["bootstrap"]
    assert "CROSS-SESSION MEMORY" in boot
    assert "Favorite project: Project Nova" in boot
    assert "Deadline: Friday" in boot
    assert "Last session summary" in boot          # crash-recovery summary of session one
    assert any(r.get("ok") for r in out["recovered"])
    # Knowledge graph written by the first process, read back by the second.
    sub = kg.query_graph("Project Nova", top_k=5, db_path=tmp_path / "knowledge_graph.sqlite")
    assert any("Nova" in n["name"] for n in sub["nodes"])


@pytest.mark.skipif(os.environ.get("JARVIS_LIVE_GEMINI") != "1", reason="set JARVIS_LIVE_GEMINI=1 for live recall")
def test_live_gemini_recalls_facts_from_bootstrap(tmp_path: Path):
    _run_session(tmp_path, "one")
    boot = _run_session(tmp_path, "two")["bootstrap"]
    from core import gemini
    answer = gemini.text(boot + "\n\nUser: What is my favorite project and when is my deadline? "
                         "Answer in one short sentence.", gemini.FAST, None, 30_000)
    assert "nova" in answer.lower() and "friday" in answer.lower()


def test_main_injects_bootstrap_and_records_turns_off_loop():
    src = (REPO_ROOT / "main.py").read_text(encoding="utf-8")
    assert "self._live_mem.bootstrap_text()" in src            # every new Live session
    assert "self._live_mem.wake_primer()" in src               # wake from SLEEPING
    assert "self._live_mem.on_sleep(reason)" in src
    assert 'self._live_mem.record("user", full_in)' in src
    assert "asyncio.to_thread(_post_tool_bookkeeping)" in src  # telemetry off the audio loop
    assert src.index("_ensure_dpi()") < src.index("import sounddevice")


# ── 24/7 agents: gates & budget ─────────────────────────────────────────────

def test_gates_pause_for_active_user_cpu_and_gpu():
    lim = dict(rq.DEFAULT_LIMITS)
    assert rq.check_gates(idle_s=10, cpu_pct=1, gpu_pct=1, lim=lim)[0] is False
    assert "CPU" in rq.check_gates(idle_s=900, cpu_pct=90, gpu_pct=1, lim=lim)[1]
    assert "GPU" in rq.check_gates(idle_s=900, cpu_pct=1, gpu_pct=95, lim=lim)[1]
    assert rq.check_gates(idle_s=900, cpu_pct=1, gpu_pct=None, lim=lim) == (True, "idle")


def test_daily_budget_is_strict_and_ollama_first(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    db = tmp_path / "q.sqlite"
    calls: list[str] = []
    from core import gemini
    from providers import local_router
    monkeypatch.setattr(local_router, "complete_local", lambda *a, **k: calls.append("ollama") or {"text": "local ok", "model": "qwen"})
    monkeypatch.setattr(gemini, "text", lambda *a, **k: calls.append("gemini") or "cloud ok")
    assert rq.ask_llm("hi", db_path=db) == ("local ok", "ollama:qwen")
    assert calls == ["ollama"]

    monkeypatch.setattr(local_router, "complete_local", lambda *a, **k: None)
    monkeypatch.setattr(rq, "limits", lambda: {**rq.DEFAULT_LIMITS, "daily_cloud_calls": 1.0})
    assert rq.ask_llm("hi", db_path=db)[1] == "gemini"
    assert rq.ask_llm("hi", db_path=db) == ("", "")       # cap reached -> no cloud call
    assert calls.count("gemini") == 1


def test_pause_resume_persist_and_block_runs():
    assert "paused" in agents_247.pause("test")
    assert agents_247.is_paused()
    assert agents_247.run_once() == {"ran": False, "reason": "paused"}
    assert "resumed" in agents_247.resume()
    assert not agents_247.is_paused()
    assert agents_247.start() is False                    # JARVIS_DISABLE_247_AGENTS=1 in tests


# ── researcher ──────────────────────────────────────────────────────────────

def test_researcher_ranks_telemetry_and_correction_specs(tmp_path: Path):
    tel, q, conv = tmp_path / "t.sqlite", tmp_path / "q.sqlite", tmp_path / "c.sqlite"
    for _ in range(4):
        telemetry.record_tool_event("knowledge_rag", {"action": "search"}, "failed: Ollama embed timeout",
                                    900.0, success=False, db_path=tel)
    telemetry.record_tool_event("file_vault", {"action": "x"}, "failed: nope", 10.0, success=False, db_path=tel)
    telemetry.record_tool_event("file_vault", {"action": "x"}, "failed: nope", 10.0, success=False, db_path=tel)
    sid = cs.start_session(db_path=conv)
    for txt in ("open chrome", "no, I said Chrome not Edge", "play music", "that's wrong, stop"):
        cs.append_turn("assistant", "Opening Edge.", session_id=sid, db_path=conv)
        cs.append_turn("user", txt, session_id=sid, db_path=conv)
    refs = []
    res = rq.run_research_cycle(force=True, use_llm=False, db_path=q, telemetry_db=tel, conversations_db=conv,
                                search_fn=lambda query, n: refs.append(query) or [{"title": "Fix", "url": "https://x"}])
    assert res["ran"] and res["specs"] >= 2
    specs = rq.list_specs(db_path=q)
    kinds = {(s["kind"], s["target"]) for s in specs}
    assert ("plugin_fix", "knowledge_rag") in kinds
    assert ("behavior", "user_corrections") in kinds
    assert not any(s["target"] == "file_vault" and s["kind"] == "plugin_fix" for s in specs)
    top = next(s for s in specs if s["target"] == "knowledge_rag")
    assert "web_refs_untrusted" in json.loads(top["evidence_json"]) and refs
    assert "researcher ran 1 cycle" in agents_247.built_today_report(db_path=q)


def test_research_cycle_respects_idle_gate(tmp_path: Path):
    res = rq.run_research_cycle(db_path=tmp_path / "q.sqlite", gate_overrides={"idle_s": 0, "cpu_pct": 0, "gpu_pct": 0})
    assert res == {"ran": False, "reason": res["reason"]} and "user active" in res["reason"]


# ── engineer ────────────────────────────────────────────────────────────────

_DEMO = '''"""Demo plugin."""
from __future__ import annotations

PLUGIN = {
    "name": "demo_eng",
    "brief": "Demo plugin for engineer daemon replay benchmark testing.",
    "description": (
        "Demo plugin for engineer daemon replay benchmark testing across historical "
        "failed telemetry invocations and 10-dimension quality scoring."
    ),
    "parameters": {"type": "OBJECT", "properties": {"query": {"type": "STRING"}}, "required": []},
}


def run(parameters: dict, player=None, session_memory=None) -> str:
    _ = player, session_memory
    try:
        params = parameters if isinstance(parameters, dict) else {}
        if params.get("mode") == "strict" and not params.get("query"):
            return "failed: missing query in strict mode"
        return "demo_eng baseline ready"
    except Exception as exc:
        return f"error: {exc}"
'''


def _demo_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    (repo / "plugins").mkdir(parents=True)
    (repo / "memory").mkdir()
    (repo / "plugins" / "demo_eng.py").write_text(_DEMO, encoding="utf-8")
    (repo / "plugins" / "file_vault.py").write_text('"""Immutable stub."""\n', encoding="utf-8")
    for args in (("init", "-b", "main"), ("config", "user.name", "T"), ("config", "user.email", "t@localhost"),
                 ("add", "."), ("commit", "-m", "init")):
        assert run_git(repo, *args).returncode == 0
    return repo


def test_engineer_builds_on_isolated_worktree_and_queues_approval(tmp_path: Path):
    repo = _demo_repo(tmp_path)
    # The user's uncommitted edit in the live checkout must survive untouched.
    (repo / "plugins" / "demo_eng.py").write_text(_DEMO + "\n# user's local edit\n", encoding="utf-8")
    tel, q, digest = tmp_path / "t.sqlite", tmp_path / "q.sqlite", tmp_path / "digest.json"
    for _ in range(3):
        telemetry.record_tool_event("demo_eng", {"mode": "strict"}, "failed: missing query in strict mode",
                                    300.0, success=False, db_path=tel)
    sid = rq.upsert_spec("plugin_fix", "demo_eng", "Fix demo_eng strict mode", score=9, db_path=q)
    spec = rq.list_specs(db_path=q)[0]

    bad = engineer_daemon.engineer_spec(spec, repo_root=repo, db_path=q, telemetry_db=tel, digest_path=digest,
                                        candidate_fn=lambda c, m, k: c.replace("baseline ready", "still buggy"))
    assert bad["discarded"] and "not measurably better" in bad["reason"].lower()
    assert run_git(repo, "branch", "--list", "evolve/*").stdout.strip() == ""

    rq.upsert_spec("plugin_fix", "demo_eng", "Fix demo_eng strict mode", score=9, db_path=q)   # re-open
    good_code = _DEMO.replace('return "failed: missing query in strict mode"',
                              'return "strict fallback: handled empty query cleanly"')
    ok = engineer_daemon.engineer_spec(rq.list_specs(db_path=q)[0], repo_root=repo, db_path=q, telemetry_db=tel,
                                       digest_path=digest, candidate_fn=lambda c, m, k: good_code)
    assert ok["ok"], ok
    assert ok["branch"].startswith("evolve/auto-demo_eng-")
    assert run_git(repo, "rev-parse", "--abbrev-ref", "HEAD").stdout.strip() == "main"
    assert "# user's local edit" in (repo / "plugins" / "demo_eng.py").read_text(encoding="utf-8")
    changed = run_git(repo, "diff", "--name-only", f"main..{ok['branch']}").stdout.split()
    assert sorted(changed) == ["plugins/demo_eng.py", "tests/evolved/test_auto_demo_eng.py"]
    props = json.loads(digest.read_text(encoding="utf-8"))["proposals"]
    assert props[-1]["kind"] == "approve_evolve" and props[-1]["payload"]["branch"] == ok["branch"]
    assert rq.list_specs(status="built", db_path=q)[0]["id"] == sid


def test_engineer_refuses_protected_targets_and_forbidden_paths(tmp_path: Path):
    repo = _demo_repo(tmp_path)
    q = tmp_path / "q.sqlite"
    for target in ("file_vault", "safety_guard", "voice_lock", "confirm"):
        rq.upsert_spec("plugin_fix", target, f"Fix {target}", score=1, db_path=q)
        spec = next(s for s in rq.list_specs(db_path=q) if s["target"] == target)
        res = engineer_daemon.engineer_spec(spec, repo_root=repo, db_path=q, telemetry_db=tmp_path / "t.sqlite",
                                            candidate_fn=lambda c, m, k: "x = 1\n")
        assert res["discarded"] and ("IMMUTABLE" in res["reason"] or "protected" in res["reason"])
    for rel in ("core/confirm.py", "core/evolution/safety_guard.py", "plugins/voice_lock.py",
                "plugins/file_vault.py", "memory/conversations.sqlite", "memory/knowledge_graph.sqlite"):
        assert engineer_daemon.is_protected(rel)
    assert not engineer_daemon.is_protected("plugins/demo_eng.py")
    assert run_git(repo, "branch", "--list", "evolve/*").stdout.strip() == ""


def test_memory_dbs_snapshotted_before_upgrade(tmp_path: Path):
    base = tmp_path / "base"
    cs.append_turn("user", "remember me", db_path=base / "memory" / "conversations.sqlite")
    kg.upsert_entity("project", "Nova", db_path=base / "memory" / "knowledge_graph.sqlite")
    saved = engineer_daemon.snapshot_memory_dbs(base=base, out_dir=tmp_path / "snap")
    assert sorted(Path(p).name for p in saved) == ["conversations.sqlite", "knowledge_graph.sqlite"]
    rows = sqlite3.connect(tmp_path / "snap" / "conversations.sqlite").execute(
        "SELECT text FROM conversation_history").fetchall()
    assert rows == [("remember me",)]


def test_evolution_control_voice_actions():
    from plugins import evolution_control
    assert "paused" in evolution_control.run({"action": "pause_agents"})
    assert agents_247.is_paused()
    assert "resumed" in evolution_control.run({"action": "resume"})
    report = evolution_control.run({"action": "agents_today"})
    assert "researcher" in report and "engineer" in report
