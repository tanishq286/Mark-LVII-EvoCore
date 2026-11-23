"""Milestone 4 verification script — Plugin Validator, Branch-Gated Skill Workflow & Docs.

Checks (non-destructive, isolated temp Git repo for branch tests, zero secrets printed):
  1. `scripts/validate_plugin.py` CLI (`--json` & human output) passes with exit 0 on
     `plugins/feedback_logger.py` and `plugins/skill_drafter.py`.
  2. `scripts/validate_plugin.py` cleanly rejects (exit 1) crafted bad plugins:
     - Qt import (`PyQt6.QtWidgets`)
     - direct `config/api_keys.json` read/write
     - `subprocess.run(..., shell=True)` + `os.system` + `eval`
     - raising `run()`
     - missing `PLUGIN` keys
     - duplicate tool name (`feedback_logger` and core action `dev_agent`)
  3. Isolated temporary Git repo branch-gated workflow (`plugins/_skill_gate.py`):
     - valid draft creates `skill/<slug>-<ts>` branch, runs validator, removes temp
       worktree, leaves `main` HEAD and working tree untouched, returns diff + merge cmd
     - invalid draft auto-cleans worktree AND branch (zero broken branches left)
     - core action collision (`dev_agent`) refused at preflight
     - dirty `main` working tree blocks `stage_plugin_on_branch`, `self_evolve`,
       and `forge_skill_factory` without modifying `main`
  4. Live plugin auto-discovery: `_skill_gate.py` skipped, 9 active, 0 rejected,
     zero tool-name collisions across 27 core tools + 9 plugins, `skill_drafter`
     has concise `brief` and >= 75/100 10D score.
  5. `docs/SELF_EVOLUTION.md` covers the complete loop and baseline rollback command.
"""
from __future__ import annotations

import ast
import json
from pathlib import Path
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parent.parent
VALIDATOR = ROOT / "scripts" / "validate_plugin.py"
sys.path.insert(0, str(ROOT))


def _check(label: str, ok: bool, detail: str = "") -> bool:
    status = "PASS" if ok else "FAIL"
    suffix = f" ({detail})" if detail else ""
    print(f"  [{status}] {label}{suffix}")
    return ok


def _run_validator(path: Path, repo_root: Path | None = None) -> tuple[int, dict]:
    cmd = [sys.executable, str(VALIDATOR), str(path), "--json"]
    if repo_root is not None:
        cmd.extend(["--repo-root", str(repo_root)])
    proc = subprocess.run(
        cmd,
        cwd=str(ROOT),
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=25.0,
    )
    data = json.loads(proc.stdout.strip())
    return proc.returncode, data


def _git(cwd: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *args],
        cwd=str(cwd),
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=True,
    )


def _collect_core_names() -> set[str]:
    names: set[str] = set()
    main_src = (ROOT / "main.py").read_text(encoding="utf-8", errors="ignore")
    tree = ast.parse(main_src)
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and any(
            getattr(t, "id", "") == "TOOL_DECLARATIONS" for t in node.targets
        ):
            for d in ast.literal_eval(node.value):
                if isinstance(d, dict) and d.get("name"):
                    names.add(str(d["name"]))
            break
    from core.action_loader import discover_actions
    act_reg = discover_actions(ROOT / "actions", logger=lambda _m: None)
    for d in act_reg.get_tool_declarations():
        if isinstance(d, dict) and d.get("name"):
            names.add(str(d["name"]))
    return names


def main() -> int:
    all_ok = True
    print("=== Milestone 4: Plugin Validator, Branch-Gated Skill Workflow & Docs Verification ===")

    # 1. Validator passes cleanly on feedback_logger.py and skill_drafter.py
    rc_fb, rep_fb = _run_validator(ROOT / "plugins" / "feedback_logger.py", repo_root=ROOT)
    all_ok &= _check(
        "scripts/validate_plugin.py passes on plugins/feedback_logger.py",
        rc_fb == 0 and rep_fb["passed"] and not rep_fb["errors"] and rep_fb["checks"]["has_concise_brief"],
        f"exit={rc_fb}, warnings={len(rep_fb['warnings'])}, tool={rep_fb['plugin_name']}",
    )

    rc_sd, rep_sd = _run_validator(ROOT / "plugins" / "skill_drafter.py", repo_root=ROOT)
    all_ok &= _check(
        "scripts/validate_plugin.py passes on plugins/skill_drafter.py",
        rc_sd == 0 and rep_sd["passed"] and not rep_sd["errors"] and rep_sd["checks"]["has_concise_brief"],
        f"exit={rc_sd}, warnings={len(rep_sd['warnings'])}, tool={rep_sd['plugin_name']}",
    )

    # Human-readable CLI output sanity check
    human = subprocess.run(
        [sys.executable, str(VALIDATOR), str(ROOT / "plugins" / "feedback_logger.py")],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        timeout=25.0,
    )
    all_ok &= _check(
        "scripts/validate_plugin.py human output mode succeeds (exit 0)",
        human.returncode == 0 and "[PASS]" in human.stdout,
    )

    # 2. Validator cleanly rejects all 6 crafted bad plugins
    bad_cases: list[tuple[str, str, str]] = [
        (
            "bad_qt.py",
            (
                "from PyQt6.QtWidgets import QWidget\n"
                "PLUGIN = {'name': 'bad_qt', 'description': 'bad', 'brief': 'b', "
                "'parameters': {'type': 'OBJECT', 'properties': {}}}\n"
                "def run(parameters, player=None, session_memory=None): return 'ok'\n"
            ),
            "Qt import",
        ),
        (
            "bad_secret_read.py",
            (
                "PLUGIN = {'name': 'bad_secret_read', 'description': 'bad', 'brief': 'b', "
                "'parameters': {'type': 'OBJECT', 'properties': {}}}\n"
                "def run(parameters, player=None, session_memory=None):\n"
                "    return open('config/api_keys.json').read()\n"
            ),
            "api_keys.json",
        ),
        (
            "bad_shell.py",
            (
                "import subprocess, os\n"
                "PLUGIN = {'name': 'bad_shell', 'description': 'bad', 'brief': 'b', "
                "'parameters': {'type': 'OBJECT', 'properties': {}}}\n"
                "def run(parameters, player=None, session_memory=None):\n"
                "    subprocess.run('echo hi', shell=True)\n"
                "    os.system('echo hi')\n"
                "    eval('1+1')\n"
                "    return 'ok'\n"
            ),
            "shell=True",
        ),
        (
            "bad_raises.py",
            (
                "PLUGIN = {'name': 'bad_raises', 'description': 'bad', 'brief': 'b', "
                "'parameters': {'type': 'OBJECT', 'properties': {}}}\n"
                "def run(parameters, player=None, session_memory=None):\n"
                "    raise RuntimeError('kaboom')\n"
            ),
            "raised RuntimeError",
        ),
        (
            "bad_missing_keys.py",
            (
                "PLUGIN = {'name': 'bad_missing_keys'}\n"
                "def run(parameters, player=None, session_memory=None): return 'ok'\n"
            ),
            "PLUGIN['description']",
        ),
        (
            "bad_duplicate_name.py",
            (
                "PLUGIN = {'name': 'feedback_logger', 'description': 'dup tool name', 'brief': 'dup', "
                "'parameters': {'type': 'OBJECT', 'properties': {}}}\n"
                "def run(parameters, player=None, session_memory=None): return 'ok'\n"
            ),
            "collision",
        ),
        (
            "bad_core_collision.py",
            (
                "PLUGIN = {'name': 'dev_agent', 'description': 'core collision', 'brief': 'dup', "
                "'parameters': {'type': 'OBJECT', 'properties': {}}}\n"
                "def run(parameters, player=None, session_memory=None): return 'ok'\n"
            ),
            "collision",
        ),
    ]

    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        for fname, code, expected_hint in bad_cases:
            p = tmp / fname
            p.write_text(code, encoding="utf-8")
            rc, rep = _run_validator(p, repo_root=ROOT)
            joined_errs = " | ".join(rep.get("errors") or [])
            ok = (rc == 1) and (not rep["passed"]) and (expected_hint.lower() in joined_errs.lower())
            all_ok &= _check(
                f"Validator rejects {fname} ({expected_hint})",
                ok,
                f"exit={rc}, errors={len(rep.get('errors') or [])}",
            )

    # 3. Isolated temporary Git repo branch-gated workflow tests
    from plugins._skill_gate import list_skill_branches, preflight_clean_repo, stage_plugin_on_branch
    import plugins.self_evolve as self_evolve_mod
    import plugins.forge_skill_factory as forge_mod

    valid_skill_code = '''"""Sample validated skill plugin for Milestone 4 verification."""
from __future__ import annotations

PLUGIN = {
    "name": "sample_echo_skill",
    "description": "Echoes a short safe diagnostic status message for Milestone 4 branch-gate verification.",
    "brief": "Return a safe diagnostic echo string.",
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "text": {"type": "STRING", "description": "Optional text to echo."}
        },
        "required": [],
    },
}


def run(parameters: dict, player=None, session_memory=None) -> str:
    """Safe non-raising entry point."""
    _ = session_memory
    text = str((parameters or {}).get("text") or "ready").strip()
    reply = f"sample_echo_skill: {text}"
    if player is not None:
        try:
            player.write_log(f"JARVIS: {reply}")
        except Exception:
            pass
    return reply
'''

    bad_skill_code = (
        "import subprocess\n"
        "PLUGIN = {'name': 'bad_branch_skill', 'description': 'bad', 'brief': 'b', "
        "'parameters': {'type': 'OBJECT', 'properties': {}}}\n"
        "def run(parameters, player=None, session_memory=None):\n"
        "    subprocess.run('whoami', shell=True)\n"
        "    return 'nope'\n"
    )

    with tempfile.TemporaryDirectory() as td:
        repo = Path(td) / "temp_repo"
        repo.mkdir()
        (repo / "actions").mkdir()
        (repo / "plugins").mkdir()
        (repo / "scripts").mkdir()
        (repo / "actions" / "dev_agent.py").write_text(
            "TOOL = {'name': 'dev_agent', 'description': 'core', 'parameters': {'type': 'OBJECT', 'properties': {}}}\n",
            encoding="utf-8",
        )
        (repo / "plugins" / "feedback_logger.py").write_text(
            (ROOT / "plugins" / "feedback_logger.py").read_text(encoding="utf-8"),
            encoding="utf-8",
        )
        (repo / "scripts" / "validate_plugin.py").write_text(
            VALIDATOR.read_text(encoding="utf-8"),
            encoding="utf-8",
        )

        _git(repo, "init", "-b", "main")
        _git(repo, "config", "user.name", "Verify M4")
        _git(repo, "config", "user.email", "verify-m4@localhost")
        _git(repo, "add", ".")
        _git(repo, "commit", "-m", "chore: baseline temp repo")
        main_head_before = _git(repo, "rev-parse", "HEAD").stdout.strip()

        # 3a. Valid draft creates skill/* branch, validates, leaves main untouched
        staged = stage_plugin_on_branch(
            repo,
            "sample_echo_skill",
            valid_skill_code,
            summary="add sample_echo_skill",
        )
        main_head_after = _git(repo, "rev-parse", "HEAD").stdout.strip()
        branches = list_skill_branches(repo)
        wt_list = _git(repo, "worktree", "list").stdout.strip().splitlines()
        main_status = _git(repo, "status", "--porcelain").stdout.strip()
        all_ok &= _check(
            "Valid branch-gated draft commits on skill/* branch and leaves main untouched",
            staged["ok"]
            and staged["branch"].startswith("skill/sample_echo_skill-")
            and staged["branch"] in branches
            and main_head_after == main_head_before
            and not (repo / "plugins" / "sample_echo_skill.py").exists()
            and not main_status
            and len(wt_list) == 1
            and "sample_echo_skill" in staged["diff"]
            and f"git merge --no-ff {staged['branch']}" == staged["merge_cmd"],
            f"branch={staged['branch']}, main_untouched=True, worktrees={len(wt_list)}",
        )

        # 3b. Invalid draft fails validator and leaves ZERO stray branches or files
        branches_before_bad = list(branches)
        bad_res = stage_plugin_on_branch(repo, "bad_branch_skill", bad_skill_code)
        branches_after_bad = list_skill_branches(repo)
        all_ok &= _check(
            "Invalid draft rejected by validator and auto-cleans worktree & branch",
            (not bad_res["ok"])
            and "Validation failed" in bad_res["error"]
            and branches_after_bad == branches_before_bad
            and not (repo / "plugins" / "bad_branch_skill.py").exists(),
            f"error={bad_res['error'][:90]}",
        )

        # 3c. Core action collision blocked at preflight
        ok_core, msg_core = preflight_clean_repo(repo, "dev_agent")
        all_ok &= _check(
            "Core action overwrite/collision ('dev_agent') blocked at preflight",
            (not ok_core) and "core action" in msg_core.lower(),
            msg_core,
        )

        # 3d. Dirty main working tree blocks _skill_gate, self_evolve, and forge_skill_factory
        (repo / "plugins" / "feedback_logger.py").write_text("# dirty\n", encoding="utf-8")
        dirty_res = stage_plugin_on_branch(repo, "another_skill", valid_skill_code)

        orig_se_base = self_evolve_mod._BASE
        orig_fg_base = forge_mod._BASE
        try:
            self_evolve_mod._BASE = repo
            forge_mod._BASE = repo
            se_reply = self_evolve_mod.run(
                {"action": "evolve", "capability": "add a dirty test plugin", "plugin_name": "dirty_test"},
                player=None,
            )
            # Also seed minimal ecosystem_index.json in temp repo to test forge_skill_factory compile_skill
            (repo / "memory").mkdir(exist_ok=True)
            (repo / "memory" / "ecosystem_index.json").write_text(
                json.dumps({"skills": [{"name": "demo_skill", "description": "demo", "path": "/tmp/demo"}]}),
                encoding="utf-8",
            )
            fg_reply = forge_mod.run(
                {"action": "compile_skill", "target": "demo_skill"},
                player=None,
            )
        finally:
            self_evolve_mod._BASE = orig_se_base
            forge_mod._BASE = orig_fg_base

        all_ok &= _check(
            "Dirty main working tree blocks _skill_gate, self_evolve, and forge_skill_factory",
            (not dirty_res["ok"])
            and "dirty" in dirty_res["error"].lower()
            and "blocked" in se_reply.lower()
            and "dirty" in se_reply.lower()
            and "blocked" in fg_reply.lower()
            and "dirty" in fg_reply.lower()
            and not (repo / "plugins" / "dirty_test.py").exists()
            and not (repo / "plugins" / "skill_demo_skill.py").exists(),
            "all 3 self-coding paths refused cleanly on dirty main",
        )

    # Confirm self_evolve.py no longer writes directly to target_path on main
    se_src = (ROOT / "plugins" / "self_evolve.py").read_text(encoding="utf-8")
    all_ok &= _check(
        "plugins/self_evolve.py routes through plugins._skill_gate with zero direct main writes",
        "stage_plugin_on_branch" in se_src and "target_path.write_text" not in se_src,
    )

    # 4. Full plugin auto-discovery & 10D score check on live repo
    core_names = _collect_core_names()
    from core.plugin_loader import discover_plugins
    reg = discover_plugins(ROOT / "plugins", core_names, logger=lambda _m: None)
    ui_list = reg.list_for_ui()
    active = [p for p in ui_list if p["valid"]]
    rejected = [p for p in ui_list if not p["valid"]]
    active_names = sorted(p["name"] for p in active)
    all_ok &= _check(
        "Live plugin auto-discovery: 9 active, 0 rejected, _skill_gate skipped, 0 collisions",
        len(active) == 9
        and len(rejected) == 0
        and "skill_drafter" in active_names
        and "_skill_gate" not in active_names,
        f"active={len(active)}, rejected={len(rejected)}, core_checked={len(core_names)}",
    )

    from core.auto_evolution import evaluate_module_10d
    sd_score = evaluate_module_10d((ROOT / "plugins" / "skill_drafter.py").read_text(encoding="utf-8"), is_plugin=True)
    all_ok &= _check(
        "plugins/skill_drafter.py passes 10D quality gate (>= 75/100)",
        sd_score["passed"],
        f"combined={sd_score['combined']}, tq={sd_score['tq_score']}, omega={sd_score['omega_score']}",
    )

    # 5. docs/SELF_EVOLUTION.md completeness check
    doc_path = ROOT / "docs" / "SELF_EVOLUTION.md"
    doc_text = doc_path.read_text(encoding="utf-8") if doc_path.is_file() else ""
    required_terms = (
        "feedback_logger",
        "reflector",
        "validate_plugin.py",
        "_skill_gate.py",
        "git diff",
        "git merge --no-ff",
        "git checkout baseline-before-upgrade",
    )
    missing_terms = [t for t in required_terms if t not in doc_text]
    all_ok &= _check(
        "docs/SELF_EVOLUTION.md documents complete loop and rollback command",
        doc_path.is_file() and not missing_terms,
        f"bytes={len(doc_text)}, missing={missing_terms or 'none'}",
    )

    print("=== RESULT:", "ALL PASSED" if all_ok else "FAILED", "===")
    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main())
