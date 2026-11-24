"""core/evolution/skill_synthesizer.py — Sandbox-Tested Skill Synthesis, Hot-Load & Audit.

Co-generates both `plugins/<slug>.py` and `tests/evolved/test_<slug>.py` inside an
isolated `skill/<slug>-<ts>` Git worktree, verifies:
  1. `safety_guard.assert_mutable_path`
  2. `safety_guard.audit_python_source` (`strict_sandbox=True`)
  3. `scripts/validate_plugin.py`
  4. `pytest` execution inside the sandbox worktree
  5. Optional Replay Self-Benchmarking against past failed tasks
Supports explicit approval + `git merge --no-ff` + `PluginRegistry.refresh()`
hot-loading + evolution ledger recording for one-command rollback.
"""
from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import subprocess
import sys
import tempfile

from core.evolution import safety_guard, self_healer, telemetry

if getattr(sys, "frozen", False):
    _PROJECT_ROOT = Path(sys.executable).parent
else:
    _PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent


def _strip_fences(text: str) -> str:
    cleaned = re.sub(r"(?is)<think(?:ing)?>.*?</think(?:ing)?>", "", text or "").strip()
    cleaned = re.sub(r"^```[a-zA-Z]*\r?\n?", "", cleaned)
    cleaned = re.sub(r"\r?\n?```\s*$", "", cleaned)
    return cleaned.strip()


def _default_test_for_skill(slug: str) -> str:
    return f'''"""Auto-generated sandbox unit tests for synthesized plugin plugins.{slug}."""
from __future__ import annotations

import importlib.util
from pathlib import Path

_PLUGIN_PATH = Path(__file__).resolve().parents[2] / "plugins" / "{slug}.py"


def _load():
    spec = importlib.util.spec_from_file_location("plugins.{slug}", _PLUGIN_PATH)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_{slug}_contract_and_smoke():
    mod = _load()
    assert isinstance(mod.PLUGIN, dict)
    assert mod.PLUGIN.get("name") == "{slug}"
    assert isinstance(mod.PLUGIN.get("description"), str) and mod.PLUGIN["description"].strip()
    out = mod.run({{}}, player=None, session_memory=None)
    assert isinstance(out, str) and len(out.strip()) > 0
'''


def _generate_with_llm(slug: str, specification: str) -> tuple[str, str]:
    from core import gemini

    tpl = _PROJECT_ROOT / "plugins" / "_template.py"
    tpl_text = tpl.read_text(encoding="utf-8", errors="replace") if tpl.is_file() else ""
    code_prompt = (
        f"Write a production Mark-LVII plugin `plugins/{slug}.py` implementing:\n{specification}\n\n"
        f"Template:\n{tpl_text}\n\n"
        f"Hard rules:\n"
        f"1. Module docstring + `PLUGIN = {{'name': '{slug}', 'description': '...', 'brief': '...', 'parameters': {{'type': 'OBJECT', 'properties': {{...}}}}}}`\n"
        "2. `def run(parameters: dict, player=None, session_memory=None) -> str:` must never raise and handle `{}` + `player=None`.\n"
        "3. Zero Qt/PyQt/PySide imports, zero eval/exec/compile/__import__/importlib/ctypes/os.system/shell=True, zero raw api_keys/long_term/.env paths.\n"
        "4. Return ONLY raw Python source code."
    )
    code = _strip_fences(gemini.text(code_prompt, tier=gemini.SMART, timeout_ms=90_000) or "")
    test_prompt = (
        f"Write a minimal standalone pytest test file verifying `plugins/{slug}.py` against this spec:\n{specification}\n\n"
        f"Plugin code:\n{code[:4000]}\n\n"
        f"Import via `importlib.util.spec_from_file_location('plugins.{slug}', Path(__file__).resolve().parents[2] / 'plugins' / '{slug}.py')`.\n"
        "Return ONLY valid Python pytest code."
    )
    test_code = _strip_fences(gemini.text(test_prompt, tier=gemini.SMART, timeout_ms=60_000) or "")
    if not test_code or "def test_" not in test_code:
        test_code = _default_test_for_skill(slug)
    return code, test_code


def synthesize_plugin(
    repo_root: Path,
    slug: str,
    specification: str,
    *,
    source_code: str = "",
    test_code: str = "",
    failed_cases: list[dict] | None = None,
    db_path: Path | None = None,
    validator_script: Path | None = None,
    player=None,
) -> dict:
    """Draft, AST-audit, validate, pytest-sandbox, and commit a skill on `skill/<slug>-<ts>`."""
    _ = player
    from core.cross_os import run_git
    from plugins._skill_gate import _git_path_arg, preflight_clean_repo, sanitize_plugin_slug

    root = Path(repo_root).resolve()
    clean_slug = sanitize_plugin_slug(slug)
    rel_plugin = f"plugins/{clean_slug}.py"
    rel_test = f"tests/evolved/test_{clean_slug}.py"

    ok_mut, mut_err = safety_guard.assert_mutable_path(root, rel_plugin)
    if not ok_mut:
        return {"ok": False, "error": mut_err, "branch": "", "plugin_name": clean_slug}

    ok_test_mut, test_err = safety_guard.assert_mutable_path(root, rel_test, allow_tests=True)
    if not ok_test_mut:
        return {"ok": False, "error": test_err, "branch": "", "plugin_name": clean_slug}

    ok_pre, pre_err = preflight_clean_repo(root, clean_slug)
    if not ok_pre:
        return {"ok": False, "error": pre_err, "branch": "", "plugin_name": clean_slug}

    val_script = Path(validator_script).resolve() if validator_script else (root / "scripts" / "validate_plugin.py")
    if not val_script.is_file():
        val_script = _PROJECT_ROOT / "scripts" / "validate_plugin.py"
    if not val_script.is_file():
        return {"ok": False, "error": f"Validator missing: {val_script}", "branch": "", "plugin_name": clean_slug}

    code_text = _strip_fences(source_code)
    test_text = _strip_fences(test_code)
    if not code_text:
        if not specification.strip():
            return {"ok": False, "error": "Provide specification or source_code.", "branch": "", "plugin_name": clean_slug}
        code_text, gen_test = _generate_with_llm(clean_slug, specification)
        if not test_text:
            test_text = gen_test
    elif not test_text:
        test_text = _default_test_for_skill(clean_slug)

    if not code_text or "PLUGIN" not in code_text or "def run" not in code_text:
        return {"ok": False, "error": "Synthesized code missing PLUGIN or def run().", "branch": "", "plugin_name": clean_slug}

    audit = safety_guard.audit_python_source(code_text, filename=rel_plugin, strict_sandbox=True)
    if not audit["safe"]:
        return {
            "ok": False,
            "error": "AST safety gate rejected code: " + "; ".join(audit["violations"]),
            "branch": "",
            "plugin_name": clean_slug,
        }

    replay_cases = (
        list(failed_cases)
        if failed_cases is not None
        else telemetry.recent_failures(tool_name=clean_slug, limit=5, db_path=db_path)
    )
    baseline = None
    if replay_cases:
        existing_live = root / rel_plugin
        if existing_live.is_file():
            baseline = self_healer.replay_benchmark_plugin(existing_live, replay_cases, root)
        else:
            baseline = {"total": len(replay_cases), "passed": 0, "pass_rate": 0.0, "cases": []}

    base_ref = run_git(root, "rev-parse", "--abbrev-ref", "HEAD").stdout.strip() or "main"
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    branch = f"skill/{clean_slug}-{stamp}"
    if run_git(root, "show-ref", "--verify", "--quiet", f"refs/heads/{branch}").returncode == 0:
        branch = f"{branch}-{secrets.token_hex(2)}"

    wt_parent = Path(tempfile.mkdtemp(prefix="jarvis-synth-wt-"))
    wt_dir = wt_parent / "repo"
    branch_created = False
    keep_branch = False

    try:
        add_wt = run_git(root, "worktree", "add", "-b", branch, _git_path_arg(wt_dir), "HEAD")
        if add_wt.returncode != 0:
            return {"ok": False, "error": f"Worktree error: {add_wt.stderr.strip()}", "branch": "", "plugin_name": clean_slug}
        branch_created = True

        wt_plugin = wt_dir / rel_plugin
        wt_test = wt_dir / rel_test
        wt_plugin.parent.mkdir(parents=True, exist_ok=True)
        wt_test.parent.mkdir(parents=True, exist_ok=True)
        wt_plugin.write_text(code_text.rstrip() + "\n", encoding="utf-8")
        wt_test.write_text(test_text.rstrip() + "\n", encoding="utf-8")

        val = self_healer._run_validator(val_script, wt_plugin, wt_dir)
        if not val.get("passed"):
            return {
                "ok": False,
                "error": "Validation failed: " + "; ".join(val.get("errors") or ["unknown"]),
                "branch": "",
                "plugin_name": clean_slug,
            }

        ok_py, py_out = self_healer._run_worktree_pytest(wt_dir, rel_test, extra_repo_root=_PROJECT_ROOT)
        if not ok_py:
            return {
                "ok": False,
                "error": f"Sandbox pytest failed: {py_out[:350]}",
                "branch": "",
                "plugin_name": clean_slug,
            }

        cand_bench = None
        if replay_cases and baseline is not None:
            cand_bench = self_healer.replay_benchmark_plugin(wt_plugin, replay_cases, _PROJECT_ROOT)
            if cand_bench["pass_rate"] <= baseline["pass_rate"]:
                return {
                    "ok": False,
                    "error": (
                        f"Replay benchmark did not improve baseline "
                        f"({baseline['pass_rate']:.2f} -> {cand_bench['pass_rate']:.2f})."
                    ),
                    "branch": "",
                    "plugin_name": clean_slug,
                    "baseline": baseline,
                    "candidate": cand_bench,
                }

        add_res = run_git(wt_dir, "add", "--", rel_plugin, rel_test)
        if add_res.returncode != 0:
            return {"ok": False, "error": f"git add failed: {add_res.stderr.strip()}", "branch": "", "plugin_name": clean_slug}

        if run_git(wt_dir, "diff", "--cached", "--quiet").returncode == 0:
            return {"ok": False, "error": f"No changes produced for {rel_plugin}.", "branch": "", "plugin_name": clean_slug}

        summary = " ".join(specification.split())[:80] or clean_slug
        commit_res = run_git(
            wt_dir,
            "-c", "user.name=JARVIS Skill Synthesizer",
            "-c", "user.email=jarvis-local@localhost",
            "commit",
            "-m", f"feat(synth): {summary} (pytest verified)",
        )
        if commit_res.returncode != 0:
            return {"ok": False, "error": f"git commit failed: {commit_res.stderr.strip()}", "branch": "", "plugin_name": clean_slug}

        diff_res = run_git(wt_dir, "diff", "HEAD~1..HEAD")
        keep_branch = True
        return {
            "ok": True,
            "error": "",
            "branch": branch,
            "base_ref": base_ref,
            "plugin_name": val.get("plugin_name") or clean_slug,
            "rel_path": rel_plugin,
            "test_path": rel_test,
            "validation": val,
            "pytest_output": py_out,
            "baseline": baseline,
            "candidate": cand_bench,
            "diff": diff_res.stdout if diff_res.returncode == 0 else "",
            "review_cmd": f"git diff {base_ref}..{branch}",
            "merge_cmd": f"git merge --no-ff {branch}",
            "discard_cmd": f"git branch -D {branch}",
        }
    finally:
        run_git(root, "worktree", "remove", "--force", _git_path_arg(wt_dir))
        shutil.rmtree(wt_parent, ignore_errors=True)
        if branch_created and not keep_branch:
            run_git(root, "branch", "-D", branch)


def approve_and_hotload(
    repo_root: Path,
    branch: str,
    *,
    kind: str = "skill",
    target: str = "",
    summary: str = "",
    ledger_path: Path | None = None,
    plugin_registry=None,
) -> dict:
    """Merge an approved `skill/*` or `autofix/*` branch after path-mutability check and hot-reload."""
    from core.cross_os import run_git

    root = Path(repo_root).resolve()
    br = str(branch or "").strip()
    if not (br.startswith("skill/") or br.startswith("autofix/")):
        return {"ok": False, "error": f"Refused: only skill/* or autofix/* branches can be auto-merged (got {br!r})."}

    st = run_git(root, "status", "--porcelain")
    if st.returncode != 0:
        return {"ok": False, "error": f"git status failed: {st.stderr.strip()}"}
    if st.stdout.strip():
        return {"ok": False, "error": "Working tree is dirty; refusing merge."}

    chk = run_git(root, "show-ref", "--verify", "--quiet", f"refs/heads/{br}")
    if chk.returncode != 0:
        return {"ok": False, "error": f"Branch '{br}' does not exist."}

    changed_res = run_git(root, "diff", "--name-only", f"HEAD..{br}")
    if changed_res.returncode != 0:
        return {"ok": False, "error": f"Could not inspect branch diff: {changed_res.stderr.strip()}"}

    changed_files = [ln.strip() for ln in (changed_res.stdout or "").splitlines() if ln.strip()]
    if not changed_files:
        return {"ok": False, "error": f"Branch '{br}' contains no file changes."}

    for rel in changed_files:
        ok_mut, why = safety_guard.assert_mutable_path(root, rel, allow_tests=True)
        if not ok_mut:
            return {"ok": False, "error": f"Blocked branch merge: {why}"}

    merge_res = run_git(
        root,
        "-c", "user.name=JARVIS Evolution Gate",
        "-c", "user.email=jarvis-local@localhost",
        "merge", "--no-ff", br,
        "-m", f"feat(evolve): approve {br}",
    )
    if merge_res.returncode != 0:
        run_git(root, "merge", "--abort")
        return {"ok": False, "error": f"git merge failed: {merge_res.stderr.strip()}"}

    sha = run_git(root, "rev-parse", "HEAD").stdout.strip()
    inferred_target = target or next((f for f in changed_files if f.startswith("plugins/")), changed_files[0])
    entry = safety_guard.record_evolution_commit(
        sha,
        br,
        kind,
        inferred_target,
        summary or f"Approved {br}",
        ledger_path=ledger_path,
    )
    run_git(root, "branch", "-D", br)

    hotloaded = False
    if plugin_registry is not None and hasattr(plugin_registry, "refresh"):
        try:
            hotloaded = bool(plugin_registry.refresh())
        except Exception:
            hotloaded = False

    return {
        "ok": True,
        "sha": sha,
        "branch": br,
        "changed_files": changed_files,
        "hotloaded": hotloaded,
        "ledger_entry": entry,
    }
