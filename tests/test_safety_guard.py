"""Tests for Milestone 10 Safety Guard (immutability, AST evasion, risk tiers, rollback)."""
from __future__ import annotations

import os
from pathlib import Path

from core import confirm
from core.cross_os import run_git
from core.evolution import safety_guard


def _git(cwd: Path, *args: str) -> str:
    res = run_git(cwd, *args)
    assert res.returncode == 0, f"git {' '.join(args)} failed: {res.stderr}"
    return res.stdout.strip()


def test_immutable_and_protected_paths_blocked(tmp_path: Path):
    repo = tmp_path / "repo"
    (repo / "core" / "evolution").mkdir(parents=True)
    (repo / "plugins").mkdir()
    (repo / "actions").mkdir()
    (repo / "actions" / "dev_agent.py").write_text("# core\n", encoding="utf-8")

    for imm in (
        "core/evolution/safety_guard.py",
        "core/confirm.py",
        "plugins/_skill_gate.py",
        "plugins/file_vault.py",
        "scripts/validate_plugin.py",
        "core/voice/voice_lock.py",
        "plugins/voice_lock.py",
    ):
        ok, msg = safety_guard.assert_mutable_path(repo, imm)
        assert not ok, f"Expected immutable block for {imm}"
        assert "immutable" in msg.lower() or "protected" in msg.lower()

    for protected in ("core/gemini.py", "config/api_keys.json", "../escape.py", "plugins/_helper.py", "plugins/dev_agent.py"):
        ok, _ = safety_guard.assert_mutable_path(repo, protected)
        assert not ok, f"Expected block for {protected}"

    ok_plug, norm_plug = safety_guard.assert_mutable_path(repo, "plugins/valid_custom.py")
    assert ok_plug and norm_plug == "plugins/valid_custom.py"

    ok_test, norm_test = safety_guard.assert_mutable_path(repo, "tests/evolved/test_custom.py", allow_tests=True)
    assert ok_test and norm_test == "tests/evolved/test_custom.py"


def test_ast_blocks_obfuscated_evasion():
    evasion_samples = [
        "import importlib\nmod = importlib.import_module('os')\n",
        "x = getattr(__builtins__, 'eval')('1+1')\n",
        "import builtins\ny = builtins.__dict__['exec']('pass')\n",
        "z = globals()['__builtins__']\n",
        "import subprocess\nsubprocess.run('whoami', shell=True)\n",
        "import ctypes\n",
        "import shutil\nshutil.rmtree('/tmp/x')\n",
        "open('core/evolution/safety_guard.py', 'w').write('x')\n",
        "open('.env', 'r').read()\n",
        "from PyQt6.QtWidgets import QWidget\n",
    ]
    for code in evasion_samples:
        rep = safety_guard.audit_python_source(code, strict_sandbox=True)
        assert not rep["safe"], f"Expected AST gate to block: {code!r}"
        assert rep["violations"]

    clean = '''"""Clean plugin."""
PLUGIN = {"name": "ok_tool", "description": "Safe tool", "brief": "Safe", "parameters": {"type": "OBJECT", "properties": {}}}
def run(parameters, player=None, session_memory=None):
    return "ok"
'''
    assert safety_guard.audit_python_source(clean, strict_sandbox=True)["safe"] is True


def test_risk_tier_classification_and_confirm_enforcement():
    assert safety_guard.classify_risk("file_vault", "decrypt") == safety_guard.LOCKED
    assert safety_guard.classify_risk("evolution_control", "approve_upgrade") == safety_guard.LOCKED
    assert safety_guard.classify_risk("evolution_control", "rollback_last") == safety_guard.LOCKED
    assert safety_guard.classify_risk("send_message", "send") == safety_guard.CONFIRM
    assert safety_guard.classify_risk("computer_settings", "hibernate") == safety_guard.CONFIRM
    assert safety_guard.classify_risk("cybersec_defender", "defender_quick_scan") == safety_guard.CONFIRM
    assert safety_guard.classify_risk("persona_control", "status") == safety_guard.SAFE

    # SAFE runs directly
    assert safety_guard.enforce_gate("persona_control", "status", "t", "d", lambda: "ran") == "ran"

    # Unbound UI refuses CONFIRM/LOCKED
    orig_callbacks = (confirm._show_cb, confirm._hide_cb, confirm._log_cb)
    confirm.bind(None, None, None)
    try:
        refused = safety_guard.enforce_gate("send_message", "send", "Send", "Detail", lambda: "SHOULD_NOT_RUN")
        assert "cannot confirm" in refused.lower()
    finally:
        confirm.bind(*orig_callbacks)


def test_git_rollback_last_evolution(tmp_path: Path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "README.md").write_text("base\n", encoding="utf-8")
    _git(repo, "init", "-b", "main")
    _git(repo, "config", "user.name", "Test")
    _git(repo, "config", "user.email", "test@localhost")
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "init")

    _git(repo, "checkout", "-b", "skill/demo-1")
    (repo / "plugins").mkdir()
    (repo / "plugins" / "demo.py").write_text("# demo\n", encoding="utf-8")
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "feat: add demo")
    _git(repo, "checkout", "main")
    _git(repo, "merge", "--no-ff", "skill/demo-1", "-m", "merge demo")
    merge_sha = _git(repo, "rev-parse", "HEAD")

    ledger = tmp_path / "evolution_commits.json"
    safety_guard.record_evolution_commit(merge_sha, "skill/demo-1", "skill", "plugins/demo.py", "demo", ledger_path=ledger)

    assert (repo / "plugins" / "demo.py").is_file()
    rb = safety_guard.rollback_last_evolution(repo, ledger_path=ledger)
    assert rb["ok"] is True, rb
    assert rb["reverted_sha"] == merge_sha
    assert not (repo / "plugins" / "demo.py").exists()
    entries = safety_guard.list_evolution_commits(ledger_path=ledger)
    assert entries[-1]["reverted"] is True
