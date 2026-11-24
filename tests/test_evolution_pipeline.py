"""Tests for Auto-Evolution Pipeline (Synthesizer, Self-Healer, Habit Compiler, Reflection, RAG Consolidate)."""
from __future__ import annotations

import sqlite3
from pathlib import Path

import numpy as np

from core.cross_os import run_git
from core.evolution import habit_compiler, reflection_loop, safety_guard, self_healer, skill_synthesizer, telemetry
from plugins import knowledge_rag


def _init_temp_git_repo(repo: Path) -> None:
    repo.mkdir(parents=True, exist_ok=True)
    (repo / "plugins").mkdir(exist_ok=True)
    (repo / "actions").mkdir(exist_ok=True)
    (repo / "scripts").mkdir(exist_ok=True)
    (repo / "actions" / "core_stub.py").write_text("# core\n", encoding="utf-8")
    (repo / "plugins" / "file_vault.py").write_text(
        "PLUGIN={'name':'file_vault','description':'vault','brief':'vault','parameters':{'type':'OBJECT','properties':{}}}\n"
        "def run(parameters, player=None, session_memory=None): return 'ok'\n",
        encoding="utf-8",
    )
    for args in (
        ("init", "-b", "main"),
        ("config", "user.name", "Evo Test"),
        ("config", "user.email", "evo@localhost"),
        ("add", "."),
        ("commit", "-m", "baseline"),
    ):
        r = run_git(repo, *args)
        assert r.returncode == 0, f"git {args} failed: {r.stderr}"


def test_synthesize_sandbox_hotload_and_rollback(tmp_path: Path):
    repo = tmp_path / "repo"
    _init_temp_git_repo(repo)
    ledger = tmp_path / "commits.json"

    class FakeRegistry:
        def __init__(self):
            self.refreshed = 0

        def refresh(self):
            self.refreshed += 1
            return True

    # 1. Unsafe AST candidate rejected cleanly
    bad = skill_synthesizer.synthesize_plugin(
        repo,
        "bad_eval",
        "unsafe skill",
        source_code=(
            "PLUGIN={'name':'bad_eval','description':'bad','brief':'b','parameters':{'type':'OBJECT','properties':{}}}\n"
            "def run(parameters, player=None, session_memory=None): return str(eval('1+1'))\n"
        ),
    )
    assert not bad["ok"]

    # 2. Synthesize valid plugin + pytest test inside worktree, merge, hotload, then rollback
    good_code = '''"""Unit converter sample plugin."""
PLUGIN = {
    "name": "celsius_kelvin",
    "description": "Convert Celsius temperature to Kelvin accurately.",
    "brief": "Convert C to K.",
    "parameters": {"type": "OBJECT", "properties": {"c": {"type": "NUMBER"}}},
}

def run(parameters, player=None, session_memory=None):
    c = float((parameters or {}).get("c", 0.0))
    return f"{c + 273.15:.2f} K"
'''
    good_test = '''"""Unit test for celsius_kelvin."""
import importlib.util
from pathlib import Path

def test_conversion():
    p = Path(__file__).resolve().parents[2] / "plugins" / "celsius_kelvin.py"
    spec = importlib.util.spec_from_file_location("plugins.celsius_kelvin", p)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    assert mod.run({"c": 0}) == "273.15 K"
    assert mod.run({"c": 100}) == "373.15 K"
'''
    res = skill_synthesizer.synthesize_plugin(
        repo,
        "celsius_kelvin",
        "Convert Celsius to Kelvin",
        source_code=good_code,
        test_code=good_test,
    )
    assert res["ok"] is True, res
    assert res["branch"].startswith("skill/celsius_kelvin-")
    assert not (repo / "plugins" / "celsius_kelvin.py").exists()  # main still untouched!

    reg = FakeRegistry()
    merged = skill_synthesizer.approve_and_hotload(
        repo,
        res["branch"],
        target="plugins/celsius_kelvin.py",
        summary="add celsius_kelvin",
        ledger_path=ledger,
        plugin_registry=reg,
    )
    assert merged["ok"] is True, merged
    assert merged["hotloaded"] is True and reg.refreshed == 1
    assert (repo / "plugins" / "celsius_kelvin.py").is_file()

    rb = safety_guard.rollback_last_evolution(repo, ledger_path=ledger)
    assert rb["ok"] is True, rb
    assert not (repo / "plugins" / "celsius_kelvin.py").exists()


def test_self_healer_replay_benchmark_and_immutable_block(tmp_path: Path):
    repo = tmp_path / "repo"
    _init_temp_git_repo(repo)
    db = telemetry.init_db(tmp_path / "telemetry.sqlite")

    # Immutable plugin cannot be self-healed
    imm_res = self_healer.heal_plugin(repo, "file_vault", db_path=db, patch_fn=lambda c, f, e: c)
    assert not imm_res["ok"] and "immutable" in imm_res["error"].lower()

    broken_code = '''"""Broken parser plugin needing repair."""
PLUGIN = {
    "name": "greet_worker",
    "description": "Greet a named user safely.",
    "brief": "Greet user.",
    "parameters": {"type": "OBJECT", "properties": {"mode": {"type": "STRING"}, "name": {"type": "STRING"}}},
}

def run(parameters, player=None, session_memory=None):
    params = parameters or {}
    if params.get("mode") == "legacy":
        return "failed: selector broken"
    return f"Hello {params.get('name') or 'there'}"
'''
    (repo / "plugins" / "greet_worker.py").write_text(broken_code, encoding="utf-8")
    run_git(repo, "add", "plugins/greet_worker.py")
    run_git(repo, "commit", "-m", "add buggy greet_worker")

    for _ in range(4):
        telemetry.record_tool_event("greet_worker", {"mode": "legacy", "name": "Alex"}, "failed: selector broken", 20.0, success=False, db_path=db)

    unhealthy = self_healer.detect_unhealthy_plugins(repo_root=repo, db_path=db, failure_threshold=3)
    assert [u["plugin_name"] for u in unhealthy] == ["greet_worker"]

    # Non-improving patch must fail replay benchmark and auto-revert branch!
    non_improving = broken_code.replace('Hello {', 'Hi {')
    fail_heal = self_healer.heal_plugin(
        repo,
        "greet_worker",
        db_path=db,
        patch_fn=lambda _c, _f, _e: non_improving,
        test_fn=lambda _n, _c, _f: "def test_dummy(): assert True\n",
    )
    assert not fail_heal["ok"] and fail_heal["reverted"] is True
    assert "replay" in fail_heal["error"].lower()
    branches_after_fail = run_git(repo, "branch", "--list", "autofix/*").stdout.strip()
    assert branches_after_fail == ""

    # Fixing patch beats baseline (4/4 > 0/4) and commits autofix/* branch
    fixed_code = '''"""Repaired greeting plugin."""
PLUGIN = {
    "name": "greet_worker",
    "description": "Greet a named user safely.",
    "brief": "Greet user.",
    "parameters": {"type": "OBJECT", "properties": {"mode": {"type": "STRING"}, "name": {"type": "STRING"}}},
}

def run(parameters, player=None, session_memory=None):
    name = str((parameters or {}).get("name") or "there").strip()
    return f"Hello {name}"
'''
    ok_heal = self_healer.heal_plugin(
        repo,
        "greet_worker",
        db_path=db,
        patch_fn=lambda _c, _f, _e: fixed_code,
    )
    assert ok_heal["ok"] is True, ok_heal
    assert ok_heal["branch"].startswith("autofix/greet_worker-")
    assert ok_heal["baseline"]["pass_rate"] == 0.0
    assert ok_heal["candidate"]["pass_rate"] == 1.0


def test_habit_compiler_and_reflection_with_rag_consolidate(tmp_path: Path):
    db = telemetry.init_db(tmp_path / "telemetry.sqlite")
    habits_file = tmp_path / "habits.json"
    digest_file = tmp_path / "evolution_digest.json"
    vault_dir = tmp_path / "knowledge"
    vault_dir.mkdir()

    # Seed duplicate + orphan chunks in SQLite RAG index without calling Ollama
    db_rag = vault_dir / knowledge_rag.INDEX_FILENAME
    conn = sqlite3.connect(str(db_rag))
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("CREATE TABLE files (rel_path TEXT PRIMARY KEY, sha256 TEXT, size_bytes INT, mtime REAL, chunk_count INT, indexed_at TEXT)")
    conn.execute("CREATE TABLE chunks (id INTEGER PRIMARY KEY AUTOINCREMENT, rel_path TEXT, chunk_idx INT, line_start INT, line_end INT, text TEXT, dim INT, embedding BLOB)")
    conn.execute("INSERT INTO files VALUES ('notes.md', 'abc', 100, 1.0, 3, '2026-11-24T00:00:00Z')")
    v1 = np.array([1.0, 0.0, 0.0, 0.0], dtype=np.float32)
    v2 = np.array([0.999, 0.01, 0.0, 0.0], dtype=np.float32)
    v2 /= np.linalg.norm(v2)
    v3 = np.array([0.0, 1.0, 0.0, 0.0], dtype=np.float32)
    conn.execute("INSERT INTO chunks(rel_path, chunk_idx, line_start, line_end, text, dim, embedding) VALUES ('notes.md', 0, 1, 2, 'Deploy on Vercel with apps/web', 4, ?)", (v1.tobytes(),))
    conn.execute("INSERT INTO chunks(rel_path, chunk_idx, line_start, line_end, text, dim, embedding) VALUES ('notes.md', 1, 3, 4, 'Deploy on Vercel with apps/web root', 4, ?)", (v2.tobytes(),))
    conn.execute("INSERT INTO chunks(rel_path, chunk_idx, line_start, line_end, text, dim, embedding) VALUES ('notes.md', 2, 5, 6, 'Use PostgreSQL partitions', 4, ?)", (v3.tobytes(),))
    conn.execute("INSERT INTO chunks(rel_path, chunk_idx, line_start, line_end, text, dim, embedding) VALUES ('deleted.md', 0, 1, 1, 'Orphan chunk', 4, ?)", (v3.tobytes(),))
    conn.commit()
    conn.close()

    # Seed repeated 3-step command sequence (3x)
    for ep in range(3):
        sid = f"s{ep}"
        telemetry.record_tool_event("open_app", {"app_name": "Code"}, "ok", 50.0, session_id=sid, ts_utc=f"2026-11-24T10:0{ep}:01Z", db_path=db)
        telemetry.record_tool_event("system_status", {}, "CPU 12%", 10.0, session_id=sid, ts_utc=f"2026-11-24T10:0{ep}:02Z", db_path=db)
        telemetry.record_tool_event("web_search", {"query": "docs"}, "ok", 90.0, session_id=sid, ts_utc=f"2026-11-24T10:0{ep}:03Z", db_path=db)

    # Seed recurring failure for immunity context
    for _ in range(2):
        telemetry.record_tool_event("web_search", {"query": "x"}, "failed: timeout", 2000.0, success=False, db_path=db)

    repo = tmp_path / "repo"
    _init_temp_git_repo(repo)
    digest = reflection_loop.run_reflection_cycle(
        repo,
        db_path=db,
        vault_dir=vault_dir,
        habits_path=habits_file,
        digest_path=digest_file,
        consolidate_rag=True,
    )
    assert digest["consolidation"]["orphans_removed"] == 1
    assert digest["consolidation"]["duplicates_removed"] == 1
    assert digest["consolidation"]["remaining_chunks"] == 2
    assert "FAILURE IMMUNITY" in digest["failure_immunity"]

    habit_props = [p for p in digest["proposals"] if p["kind"] == "compile_habit"]
    assert habit_props, f"Expected habit proposal in {digest['proposals']}"

    top_id = habit_props[0]["id"]
    msg = reflection_loop.approve_upgrade(
        top_id,
        repo_root=repo,
        digest_path=digest_file,
        habits_path=habits_file,
        require_confirm=False,
    )
    assert "Compiled habit" in msg

    ran_calls: list[tuple[str, dict]] = []
    macro_out = habit_compiler.run_habit(
        habit_compiler.list_habits(habits_path=habits_file)[0]["name"],
        lambda t, p: (ran_calls.append((t, p)), f"{t}:ok")[1],
        habits_path=habits_file,
    )
    assert "executed" in macro_out and len(ran_calls) >= 2
