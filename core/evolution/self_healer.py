"""core/evolution/self_healer.py — Telemetry-Driven Self-Healing & Replay Self-Benchmarking.

Detects repeatedly failing plugins in SQLite telemetry, creates an isolated
`autofix/<plugin>-<ts>` Git worktree branch, replays up to 5 historical failed
tasks before and after patching, enforces AST safety + `validate_plugin.py` +
`pytest` regression suite, and auto-reverts if any gate fails or pass rate does
not strictly exceed baseline.
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
from typing import Callable

from core.evolution import safety_guard, telemetry

if getattr(sys, "frozen", False):
    _PROJECT_ROOT = Path(sys.executable).parent
else:
    _PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent


def _strip_code_fences(text: str) -> str:
    cleaned = re.sub(r"(?is)<think(?:ing)?>.*?</think(?:ing)?>", "", text or "").strip()
    cleaned = re.sub(r"^```[a-zA-Z]*\r?\n?", "", cleaned)
    cleaned = re.sub(r"\r?\n?```\s*$", "", cleaned)
    return cleaned.strip()


def replay_benchmark_plugin(
    plugin_path: Path,
    cases: list[dict],
    repo_root: Path,
    *,
    timeout: float = 15.0,
) -> dict:
    """Replay up to 5 historical failed tasks against `plugin_path` in a subprocess.

    Returns `{"total": n, "passed": k, "pass_rate": float, "cases": [...]}`.
    """
    target = Path(plugin_path).resolve()
    root = Path(repo_root).resolve()
    sample = list(cases or [])[:5]
    if not sample:
        return {"total": 0, "passed": 0, "pass_rate": 1.0, "cases": []}
    if not target.is_file():
        return {
            "total": len(sample),
            "passed": 0,
            "pass_rate": 0.0,
            "cases": [{"passed": False, "reason": f"missing file: {target}"} for _ in sample],
        }

    case_args = [
        (c.get("args") if isinstance(c.get("args"), dict) else {})
        for c in sample
    ]

    harness = r"""
import importlib.util, inspect, json, sys
from pathlib import Path

repo_root = Path(sys.argv[1]).resolve()
plugin_path = Path(sys.argv[2]).resolve()
wt_root = plugin_path.parent.parent.resolve()
cases = json.loads(sys.argv[3])

for p in (str(_PROJECT_ROOT_PLACEHOLDER := str(repo_root)), str(wt_root)):
    if p in sys.path:
        sys.path.remove(p)
sys.path.insert(0, str(repo_root))
sys.path.insert(0, str(wt_root))

FAILURE_SIGS = (
    "no file path provided",
    "file not found",
    "path is not a file",
    "unknown action",
    "unknown tool",
    "failed:",
    "crashed during run",
    "access denied",
    "traceback",
    "exception:",
)

def _is_bad(res: str) -> bool:
    low = (res or "").strip().lower()
    if not low:
        return True
    if low.startswith("status=failed ") or low.startswith("status=unverified "):
        return True
    try:
        from core import outcome
        if outcome.is_problem(res):
            return True
    except Exception:
        pass
    return any(sig in low for sig in FAILURE_SIGS)

results = []
try:
    spec = importlib.util.spec_from_file_location("plugins._replay_target", plugin_path)
    if spec is None or spec.loader is None:
        raise ImportError("cannot load plugin spec")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    run_fn = getattr(mod, "run", None)
    if not callable(run_fn):
        raise AttributeError("missing run()")
    sig = inspect.signature(run_fn)
    has_var_kw = any(p.kind == inspect.Parameter.VAR_KEYWORD for p in sig.parameters.values())
    for args in cases:
        kwargs = {}
        if has_var_kw or "player" in sig.parameters:
            kwargs["player"] = None
        if has_var_kw or "session_memory" in sig.parameters:
            kwargs["session_memory"] = None
        try:
            out = run_fn(dict(args), **kwargs)
            if not isinstance(out, str) or _is_bad(out):
                results.append({"passed": False, "output": str(out)[:160]})
            else:
                results.append({"passed": True, "output": out[:160]})
        except Exception as exc:
            results.append({"passed": False, "output": f"{type(exc).__name__}: {exc}"[:160]})
except Exception as exc:
    results = [{"passed": False, "output": f"import/load error: {exc}"[:160]} for _ in cases]

passed = sum(1 for r in results if r["passed"])
print(json.dumps({"total": len(results), "passed": passed, "cases": results}))
"""
    env = os.environ.copy()
    env.pop("PYTHONPATH", None)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    try:
        proc = subprocess.run(
            [sys.executable, "-B", "-c", harness, str(root), str(target), json.dumps(case_args)],
            cwd=str(target.parent.parent),
            env=env,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
        )
        lines = [ln.strip() for ln in (proc.stdout or "").splitlines() if ln.strip()]
        if lines:
            parsed = json.loads(lines[-1])
            tot = int(parsed.get("total", len(sample)))
            ok_cnt = int(parsed.get("passed", 0))
            return {
                "total": tot,
                "passed": ok_cnt,
                "pass_rate": round(ok_cnt / tot, 4) if tot else 1.0,
                "cases": parsed.get("cases") or [],
            }
    except Exception as exc:
        return {
            "total": len(sample),
            "passed": 0,
            "pass_rate": 0.0,
            "cases": [{"passed": False, "output": f"benchmark error: {exc}"} for _ in sample],
        }
    return {
        "total": len(sample),
        "passed": 0,
        "pass_rate": 0.0,
        "cases": [{"passed": False, "output": "benchmark produced no output"} for _ in sample],
    }


def detect_unhealthy_plugins(
    *,
    repo_root: Path | None = None,
    db_path: Path | None = None,
    failure_threshold: int = 3,
    window_hours: float = 168.0,
) -> list[dict]:
    """Return mutable plugins whose recorded failure count >= `failure_threshold`."""
    root = Path(repo_root).resolve() if repo_root else _PROJECT_ROOT
    metrics = telemetry.tool_metrics(window_hours=window_hours, db_path=db_path)
    unhealthy: list[dict] = []
    for tool_name, m in sorted(metrics.items()):
        if int(m.get("failures", 0)) < failure_threshold and int(m.get("consecutive_failures", 0)) < failure_threshold:
            continue
        rel = f"plugins/{tool_name}.py"
        if not (root / rel).is_file():
            continue
        ok_mut, _why = safety_guard.assert_mutable_path(root, rel)
        if not ok_mut:
            continue
        unhealthy.append({
            "plugin_name": tool_name,
            "rel_path": rel,
            "failures": m["failures"],
            "consecutive_failures": m["consecutive_failures"],
            "calls": m["calls"],
            "success_rate": m["success_rate"],
            "p95_ms": m["p95_ms"],
            "last_error": m["last_error"],
        })
    return unhealthy


def _default_patch_with_llm(plugin_name: str, current_code: str, failures: list[dict], errs: list[str] | None = None) -> str:
    from core import gemini
    fail_lines = "\n".join(
        f"- args={f.get('args')} -> err={f.get('error_snippet')} | tb={str(f.get('traceback') or '')[:300]}"
        for f in failures[:5]
    ) or "- static/quality issue"
    err_lines = "\n".join(f"- {e}" for e in (errs or []))
    prompt = (
        f"Repair JARVIS plugin `plugins/{plugin_name}.py` to handle every failing case cleanly without raising.\n\n"
        f"Observed failures:\n{fail_lines}\n"
        + (f"\nValidator/test errors to fix:\n{err_lines}\n" if err_lines else "")
        + f"\nCurrent code:\n{current_code}\n\n"
        "Hard rules:\n"
        "- Preserve PLUGIN dict name, concise `brief`, parameters schema, and `def run(parameters: dict, player=None, session_memory=None) -> str:`.\n"
        "- Never raise exceptions; return a clear non-problem string when safe fallback defaults apply or a helpful specific recovery.\n"
        "- Zero PyQt/PySide imports, zero eval/exec/compile/__import__/importlib/ctypes/os.system/shell=True.\n"
        "- Return ONLY valid Python code (no markdown fences)."
    )
    patched = ""
    try:
        from core import antigravity_bridge
        patched = _strip_code_fences(antigravity_bridge.generate_code_with_antigravity(prompt, timeout_s=120) or "")
    except Exception:
        patched = ""
    if patched and "PLUGIN" in patched and "def run" in patched:
        return patched
    return _strip_code_fences(gemini.text(prompt, tier=gemini.SMART, timeout_ms=90_000) or "")


def _default_regression_test(plugin_name: str, failures: list[dict]) -> str:
    cases_lit = json.dumps([f.get("args") or {} for f in failures[:5]])
    return f'''"""Auto-generated regression test for autofix of plugins.{plugin_name}."""
from __future__ import annotations

import importlib.util
from pathlib import Path

_PLUGIN_PATH = Path(__file__).resolve().parents[2] / "plugins" / "{plugin_name}.py"


def _load_plugin():
    spec = importlib.util.spec_from_file_location("plugins.{plugin_name}", _PLUGIN_PATH)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_{plugin_name}_handles_empty_and_replay_cases():
    mod = _load_plugin()
    assert isinstance(mod.PLUGIN, dict)
    assert mod.PLUGIN.get("name") == "{plugin_name}"
    res_empty = mod.run({{}}, player=None, session_memory=None)
    assert isinstance(res_empty, str) and len(res_empty) > 0
    for args in {cases_lit}:
        res = mod.run(dict(args), player=None, session_memory=None)
        assert isinstance(res, str) and len(res) > 0
        assert not res.lower().startswith("status=failed ")
'''


def _run_validator(val_script: Path, plugin_file: Path, wt_dir: Path) -> dict:
    env = os.environ.copy()
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    proc = subprocess.run(
        [sys.executable, "-B", str(val_script), str(plugin_file), "--repo-root", str(wt_dir), "--json"],
        cwd=str(wt_dir),
        env=env,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=30.0,
    )
    raw = (proc.stdout or "").strip()
    if not raw:
        return {"passed": False, "errors": [f"validator empty output (exit={proc.returncode})"], "warnings": []}
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return {"passed": False, "errors": [f"validator invalid JSON: {raw[:200]}"], "warnings": []}


def _run_worktree_pytest(wt_dir: Path, rel_test_path: str, extra_repo_root: Path | None = None) -> tuple[bool, str]:
    env = os.environ.copy()
    py_paths = [str(wt_dir)]
    if extra_repo_root is not None and extra_repo_root.resolve() != wt_dir.resolve():
        py_paths.append(str(extra_repo_root.resolve()))
    env["PYTHONPATH"] = os.pathsep.join(py_paths)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    proc = subprocess.run(
        [sys.executable, "-B", "-m", "pytest", rel_test_path, "-q", "-p", "no:cacheprovider"],
        cwd=str(wt_dir),
        env=env,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=45.0,
    )
    out = ((proc.stdout or "") + "\n" + (proc.stderr or "")).strip()
    return proc.returncode == 0, out[:1200]


def heal_plugin(
    repo_root: Path,
    plugin_name: str,
    *,
    db_path: Path | None = None,
    patch_fn: Callable[[str, list[dict], list[str]], str] | None = None,
    test_fn: Callable[[str, str, list[dict]], str] | None = None,
    failed_cases: list[dict] | None = None,
    validator_script: Path | None = None,
    player=None,
) -> dict:
    """Repair a failing plugin on an isolated `autofix/*` branch with replay benchmarking.

    Enforces:
      1. `safety_guard.assert_mutable_path` (immutable targets blocked immediately)
      2. Clean repo via `_skill_gate.preflight_clean_repo`
      3. `safety_guard.audit_python_source` (`strict_sandbox=True`)
      4. `scripts/validate_plugin.py`
      5. `pytest` regression suite in worktree
      6. Replay self-benchmark (`candidate.pass_rate > baseline.pass_rate` when failures exist)
    Auto-reverts (removes worktree & deletes branch) if any gate fails.
    """
    _ = player
    from core.cross_os import run_git
    from plugins._skill_gate import _git_path_arg, preflight_clean_repo, sanitize_plugin_slug

    root = Path(repo_root).resolve()
    slug = sanitize_plugin_slug(plugin_name)
    rel_plugin = f"plugins/{slug}.py"
    rel_test = f"tests/evolved/test_autofix_{slug}.py"

    ok_mut, mut_reason = safety_guard.assert_mutable_path(root, rel_plugin)
    if not ok_mut:
        return {"ok": False, "reverted": False, "error": mut_reason, "branch": "", "plugin_name": slug}

    target_live = root / rel_plugin
    if not target_live.is_file():
        return {"ok": False, "reverted": False, "error": f"Plugin '{rel_plugin}' does not exist.", "branch": "", "plugin_name": slug}

    ok_pre, pre_reason = preflight_clean_repo(root, slug)
    if not ok_pre:
        return {"ok": False, "reverted": False, "error": pre_reason, "branch": "", "plugin_name": slug}

    val_script = Path(validator_script).resolve() if validator_script else (root / "scripts" / "validate_plugin.py")
    if not val_script.is_file():
        val_script = _PROJECT_ROOT / "scripts" / "validate_plugin.py"
    if not val_script.is_file():
        return {"ok": False, "reverted": False, "error": f"Missing validator: {val_script}", "branch": "", "plugin_name": slug}

    failures = list(failed_cases) if failed_cases is not None else telemetry.recent_failures(tool_name=slug, limit=5, db_path=db_path)
    baseline = replay_benchmark_plugin(target_live, failures, root)
    current_code = target_live.read_text(encoding="utf-8", errors="replace")

    base_ref = run_git(root, "rev-parse", "--abbrev-ref", "HEAD").stdout.strip() or "main"
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    branch = f"autofix/{slug}-{stamp}"
    if run_git(root, "show-ref", "--verify", "--quiet", f"refs/heads/{branch}").returncode == 0:
        branch = f"{branch}-{secrets.token_hex(2)}"

    wt_parent = Path(tempfile.mkdtemp(prefix="jarvis-autofix-wt-"))
    wt_dir = wt_parent / "repo"
    branch_created = False
    keep_branch = False

    try:
        add_wt = run_git(root, "worktree", "add", "-b", branch, _git_path_arg(wt_dir), "HEAD")
        if add_wt.returncode != 0:
            return {
                "ok": False,
                "reverted": True,
                "error": f"Failed to create autofix worktree: {add_wt.stderr.strip()}",
                "branch": "",
                "plugin_name": slug,
                "baseline": baseline,
            }
        branch_created = True

        candidate_code = (
            _strip_code_fences(patch_fn(current_code, failures, []) or "")
            if patch_fn is not None
            else _default_patch_with_llm(slug, current_code, failures)
        )
        if not candidate_code:
            return {
                "ok": False,
                "reverted": True,
                "error": "Self-healer patch generator returned empty code.",
                "branch": "",
                "plugin_name": slug,
                "baseline": baseline,
            }

        audit = safety_guard.audit_python_source(candidate_code, filename=rel_plugin, strict_sandbox=True)
        if not audit["safe"]:
            return {
                "ok": False,
                "reverted": True,
                "error": "AST safety violation in candidate patch: " + "; ".join(audit["violations"]),
                "branch": "",
                "plugin_name": slug,
                "baseline": baseline,
            }

        wt_plugin = wt_dir / rel_plugin
        wt_plugin.parent.mkdir(parents=True, exist_ok=True)
        wt_plugin.write_text(candidate_code.rstrip() + "\n", encoding="utf-8")

        val = _run_validator(val_script, wt_plugin, wt_dir)
        if not val.get("passed"):
            return {
                "ok": False,
                "reverted": True,
                "error": "Validator failed: " + "; ".join(val.get("errors") or ["unknown"]),
                "branch": "",
                "plugin_name": slug,
                "baseline": baseline,
            }

        test_src = (
            _strip_code_fences(test_fn(slug, candidate_code, failures) or "")
            if test_fn is not None
            else _default_regression_test(slug, failures)
        )
        wt_test = wt_dir / rel_test
        wt_test.parent.mkdir(parents=True, exist_ok=True)
        wt_test.write_text(test_src.rstrip() + "\n", encoding="utf-8")

        ok_py, py_out = _run_worktree_pytest(wt_dir, rel_test, extra_repo_root=_PROJECT_ROOT)
        if not ok_py:
            return {
                "ok": False,
                "reverted": True,
                "error": f"Regression pytest failed: {py_out[:300]}",
                "branch": "",
                "plugin_name": slug,
                "baseline": baseline,
            }

        candidate_bench = replay_benchmark_plugin(wt_plugin, failures, _PROJECT_ROOT)
        if failures and candidate_bench["pass_rate"] <= baseline["pass_rate"]:
            return {
                "ok": False,
                "reverted": True,
                "error": (
                    f"Replay self-benchmark did not improve baseline "
                    f"(baseline={baseline['pass_rate']:.2f}, candidate={candidate_bench['pass_rate']:.2f})."
                ),
                "branch": "",
                "plugin_name": slug,
                "baseline": baseline,
                "candidate": candidate_bench,
            }

        add_res = run_git(wt_dir, "add", "--", rel_plugin, rel_test)
        if add_res.returncode != 0:
            return {
                "ok": False,
                "reverted": True,
                "error": f"git add failed: {add_res.stderr.strip()}",
                "branch": "",
                "plugin_name": slug,
                "baseline": baseline,
                "candidate": candidate_bench,
            }

        if run_git(wt_dir, "diff", "--cached", "--quiet").returncode == 0:
            return {
                "ok": False,
                "reverted": True,
                "error": "Autofix patch produced no effective changes.",
                "branch": "",
                "plugin_name": slug,
                "baseline": baseline,
                "candidate": candidate_bench,
            }

        commit_res = run_git(
            wt_dir,
            "-c", "user.name=JARVIS Self-Healer",
            "-c", "user.email=jarvis-local@localhost",
            "commit",
            "-m", f"fix(autofix): repair {slug} (replay {baseline['passed']}/{baseline['total']} -> {candidate_bench['passed']}/{candidate_bench['total']})",
        )
        if commit_res.returncode != 0:
            return {
                "ok": False,
                "reverted": True,
                "error": f"git commit failed: {commit_res.stderr.strip()}",
                "branch": "",
                "plugin_name": slug,
                "baseline": baseline,
                "candidate": candidate_bench,
            }

        diff_res = run_git(wt_dir, "diff", "HEAD~1..HEAD")
        keep_branch = True
        return {
            "ok": True,
            "reverted": False,
            "branch": branch,
            "base_ref": base_ref,
            "plugin_name": slug,
            "rel_path": rel_plugin,
            "test_path": rel_test,
            "baseline": baseline,
            "candidate": candidate_bench,
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
