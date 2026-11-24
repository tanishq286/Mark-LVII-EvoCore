"""core/evolution/meta_optimizer.py — Idle-Time Meta-Evolution & Self-Optimizer.

During PC idle time, analyzes `memory/telemetry.sqlite` for the lowest-performing
mutable plugins, model routing rules, and prompt/tool descriptions; stages candidate
plugin upgrades on isolated `evolve/*` Git worktrees; runs AST safety checks,
`scripts/validate_plugin.py`, `pytest`, and historical replay benchmarks; and queues
ONLY measurably better upgrades into `memory/evolution_digest.json` (`"Approve upgrade 1"`).

Strictly immutable targets (`safety_guard.py`, `file_vault.py`, `voice_lock.py`,
`core/confirm.py`, `plugins/_skill_gate.py`, `scripts/validate_plugin.py`) are
blocked at multiple independent layers and can NEVER be modified.
"""
from __future__ import annotations

import ctypes
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import sqlite3
import sys
import tempfile
import threading
from typing import Any, Callable

from core.auto_evolution import evaluate_module_10d
from core.evolution import safety_guard, self_healer, telemetry

if getattr(sys, "frozen", False):
    _PROJECT_ROOT = Path(sys.executable).parent
else:
    _PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent

DEFAULT_META_DB: Path = _PROJECT_ROOT / "memory" / "meta_optimizer.sqlite"
_LOCK = threading.RLock()

# Explicit hard immutability list enforced BEFORE safety_guard.assert_mutable_path.
IMMUTABLE_OPTIMIZER_PATHS: frozenset[str] = frozenset({
    "core/evolution/safety_guard.py",
    "plugins/file_vault.py",
    "plugins/voice_lock.py",
    "core/voice/voice_lock.py",
    "core/confirm.py",
    "plugins/_skill_gate.py",
    "scripts/validate_plugin.py",
})

IMMUTABLE_OPTIMIZER_SLUGS: frozenset[str] = frozenset({
    "safety_guard",
    "file_vault",
    "voice_lock",
    "confirm",
    "_skill_gate",
    "validate_plugin",
})


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _strip_fences(text: str) -> str:
    cleaned = re.sub(r"(?is)<think(?:ing)?>.*?</think(?:ing)?>", "", text or "").strip()
    cleaned = re.sub(r"^```[a-zA-Z]*\r?\n?", "", cleaned)
    cleaned = re.sub(r"\r?\n?```\s*$", "", cleaned)
    return cleaned.strip()


def _connect_meta_db(meta_db_path: Path | None = None) -> sqlite3.Connection:
    path = Path(meta_db_path) if meta_db_path is not None else DEFAULT_META_DB
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), timeout=15.0)
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS tuning_overrides (
            override_key TEXT PRIMARY KEY,
            kind         TEXT NOT NULL,
            target       TEXT NOT NULL,
            payload_json TEXT NOT NULL,
            applied_at   TEXT NOT NULL
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS optimization_runs (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            ts_utc          TEXT NOT NULL,
            target          TEXT NOT NULL,
            kind            TEXT NOT NULL,
            accepted        INTEGER NOT NULL,
            baseline_score  REAL NOT NULL,
            candidate_score REAL NOT NULL,
            branch          TEXT NOT NULL,
            detail          TEXT NOT NULL
        )
        """
    )
    return conn


def get_system_idle_seconds() -> float:
    """Return seconds since last keyboard/mouse input on Windows; 0.0 if unavailable."""
    if os.name != "nt":
        return 0.0
    try:
        class LASTINPUTINFO(ctypes.Structure):
            _fields_ = [("cbSize", ctypes.c_uint), ("dwTime", ctypes.c_uint)]

        lii = LASTINPUTINFO()
        lii.cbSize = ctypes.sizeof(LASTINPUTINFO)
        if ctypes.windll.user32.GetLastInputInfo(ctypes.byref(lii)):
            millis = ctypes.windll.kernel32.GetTickCount() - lii.dwTime
            return max(0.0, float(millis) / 1000.0)
    except Exception:
        pass
    return 0.0


def is_pc_idle(min_idle_seconds: float = 180.0, *, idle_seconds_override: float | None = None) -> bool:
    idle_s = float(idle_seconds_override) if idle_seconds_override is not None else get_system_idle_seconds()
    return idle_s >= max(0.0, float(min_idle_seconds))


def assert_optimizable_target(repo_root: Path, target_or_slug: str) -> tuple[bool, str, str]:
    """Verify `target_or_slug` is NOT immutable and resolves to a mutable `plugins/<slug>.py`."""
    raw = str(target_or_slug or "").strip().replace("\\", "/")
    if not raw:
        return False, "", "IMMUTABLE/INVALID TARGET BLOCKED: empty target."

    low = raw.lower().strip("/")
    stem = Path(low).stem
    if (
        low in {p.lower() for p in IMMUTABLE_OPTIMIZER_PATHS}
        or low in {p.lower() for p in safety_guard.IMMUTABLE_PATHS}
        or stem in IMMUTABLE_OPTIMIZER_SLUGS
    ):
        return (
            False,
            "",
            f"IMMUTABLE TARGET BLOCKED: '{raw}' is permanently protected and cannot be modified by meta_optimizer.",
        )

    if "/" in raw or raw.endswith(".py"):
        rel_path = raw
    else:
        rel_path = f"plugins/{raw}.py"

    norm_rel = rel_path.strip("/")
    if norm_rel in IMMUTABLE_OPTIMIZER_PATHS or norm_rel in safety_guard.IMMUTABLE_PATHS:
        return (
            False,
            "",
            f"IMMUTABLE TARGET BLOCKED: '{norm_rel}' is permanently protected.",
        )

    ok_mut, why_or_norm = safety_guard.assert_mutable_path(Path(repo_root).resolve(), norm_rel)
    if not ok_mut:
        return False, "", why_or_norm
    return True, why_or_norm, ""


def analyze_telemetry_bottlenecks(
    *,
    repo_root: Path | None = None,
    db_path: Path | None = None,
    window_hours: float = 168.0,
    min_calls: int = 2,
) -> dict[str, Any]:
    """Analyze `memory/telemetry.sqlite` for lowest-performing plugins, routes, and prompts."""
    root = Path(repo_root).resolve() if repo_root else _PROJECT_ROOT
    t_metrics = telemetry.tool_metrics(window_hours=window_hours, db_path=db_path)
    r_metrics = telemetry.route_metrics(window_hours=window_hours, db_path=db_path)
    lessons = telemetry.recurring_failure_lessons(min_count=2, limit=10, window_hours=window_hours, db_path=db_path)

    plugin_candidates: list[dict[str, Any]] = []
    skipped_immutable: list[str] = []

    for tool_name, m in sorted(t_metrics.items()):
        calls = int(m.get("calls", 0))
        if calls < max(1, int(min_calls)):
            continue
        ok_opt, rel_path, _err = assert_optimizable_target(root, tool_name)
        if not ok_opt:
            if Path(str(tool_name)).stem.lower() in IMMUTABLE_OPTIMIZER_SLUGS:
                skipped_immutable.append(tool_name)
            continue
        if not (root / rel_path).is_file():
            continue

        sr = float(m.get("success_rate", 1.0))
        p95 = float(m.get("p95_ms", 0.0))
        consec = int(m.get("consecutive_failures", 0))
        if sr >= 0.98 and p95 < 2500.0 and consec == 0:
            continue

        bottleneck_score = round((1.0 - sr) * 100.0 + min(30.0, p95 / 250.0) + consec * 8.0, 2)
        plugin_candidates.append({
            "plugin_name": tool_name,
            "rel_path": rel_path,
            "calls": calls,
            "failures": int(m.get("failures", 0)),
            "success_rate": sr,
            "p95_ms": p95,
            "consecutive_failures": consec,
            "last_error": str(m.get("last_error") or ""),
            "bottleneck_score": bottleneck_score,
        })

    plugin_candidates.sort(key=lambda item: (-item["bottleneck_score"], item["plugin_name"]))

    routing_candidates: list[dict[str, Any]] = []
    by_category: dict[str, list[tuple[str, dict[str, Any]]]] = {}
    for (cat, model), rm in r_metrics.items():
        by_category.setdefault(cat, []).append((model, rm))

    for cat, entries in sorted(by_category.items()):
        best_peer = None
        for mod, rm in entries:
            if int(rm.get("calls", 0)) >= 1 and float(rm.get("success_rate", 0.0)) >= 0.9:
                if best_peer is None or float(rm["p95_ms"]) < float(best_peer[1]["p95_ms"]):
                    best_peer = (mod, rm)
        for mod, rm in entries:
            calls = int(rm.get("calls", 0))
            sr = float(rm.get("success_rate", 1.0))
            esc = int(rm.get("escalations", 0))
            p95 = float(rm.get("p95_ms", 0.0))
            if calls >= max(1, int(min_calls)) and (sr < 0.80 or esc >= 2 or p95 > 8000.0):
                routing_candidates.append({
                    "category": cat,
                    "degraded_model": mod,
                    "calls": calls,
                    "success_rate": sr,
                    "p95_ms": p95,
                    "escalations": esc,
                    "recommended_primary": best_peer[0] if best_peer and best_peer[0] != mod else "gemini_cloud_fallback",
                })

    prompt_candidates: list[dict[str, Any]] = []
    for les in lessons:
        tname = str(les.get("tool_name") or "")
        err_snip = str(les.get("error_snippet") or "")
        cnt = int(les.get("count", 0))
        prompt_candidates.append({
            "tool_name": tname,
            "action": str(les.get("action") or ""),
            "failure_count": cnt,
            "error_snippet": err_snip,
            "prompt_guardrail": (
                f"When invoking `{tname}`, validate required arguments upfront to avoid `{err_snip[:90]}`."
            ),
        })

    return {
        "plugin_candidates": plugin_candidates,
        "routing_candidates": routing_candidates,
        "prompt_candidates": prompt_candidates,
        "skipped_immutable": sorted(set(skipped_immutable)),
    }


def _is_measurably_better(
    baseline_replay: dict[str, Any],
    candidate_replay: dict[str, Any],
    baseline_10d: dict[str, Any],
    candidate_10d: dict[str, Any],
    *,
    has_replay_cases: bool,
) -> tuple[bool, str]:
    b_pr = float(baseline_replay.get("pass_rate", 0.0))
    c_pr = float(candidate_replay.get("pass_rate", 0.0))
    b_q = float(baseline_10d.get("combined", 0.0))
    c_q = float(candidate_10d.get("combined", 0.0))

    if not candidate_10d.get("passed", False) and c_q < 75.0:
        return False, f"Candidate failed 10D quality floor ({c_q:.1f} < 75.0)."

    if has_replay_cases:
        if c_pr > b_pr and c_q >= (b_q - 2.0):
            return True, f"Replay pass_rate improved {b_pr:.2f} -> {c_pr:.2f} (10D {b_q:.1f} -> {c_q:.1f})."
        if c_pr == b_pr == 1.0 and c_q > b_q:
            return True, f"Replay pass_rate maintained at 1.00 and 10D quality improved {b_q:.1f} -> {c_q:.1f}."
        return (
            False,
            f"Candidate not measurably better (replay {b_pr:.2f} -> {c_pr:.2f}, 10D {b_q:.1f} -> {c_q:.1f}).",
        )

    if c_q > b_q:
        return True, f"10D static/engineering score improved {b_q:.1f} -> {c_q:.1f}."
    return False, f"10D score did not improve ({b_q:.1f} -> {c_q:.1f})."


def _default_optimize_with_llm(
    plugin_name: str,
    current_code: str,
    bottleneck: dict[str, Any],
    failures: list[dict[str, Any]],
) -> str:
    from core import gemini

    fail_lines = "\n".join(
        f"- args={f.get('args')} -> err={f.get('error_snippet')}"
        for f in failures[:5]
    ) or "- latency / prompt clarity optimization"
    prompt = (
        f"Optimize Mark-LVII plugin `plugins/{plugin_name}.py` to measurably improve reliability, "
        f"argument fallback handling, and tool prompt/brief clarity.\n\n"
        f"Telemetry bottleneck: {json.dumps(bottleneck, ensure_ascii=False)}\n"
        f"Historical failures to resolve:\n{fail_lines}\n\n"
        f"Current source:\n{current_code}\n\n"
        "Hard rules:\n"
        "1. Preserve `PLUGIN['name']`, concise `brief` (< `description` length), and `def run(parameters: dict, player=None, session_memory=None) -> str:`.\n"
        "2. Handle missing/malformed parameters gracefully without raising or returning problem prefixes.\n"
        "3. Zero Qt/PyQt/PySide imports, zero eval/exec/compile/__import__/importlib/ctypes/os.system/shell=True, zero raw api_keys/long_term/mcp_servers/.env strings.\n"
        "4. Return ONLY valid Python source code."
    )
    return _strip_fences(gemini.text(prompt, tier=gemini.SMART, timeout_ms=90_000) or "")


def _default_evolve_test(plugin_name: str, failures: list[dict[str, Any]]) -> str:
    cases_json = json.dumps([f.get("args") or {} for f in failures[:5]])
    return f'''"""Auto-generated meta-optimizer regression suite for plugins.{plugin_name}."""
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


def test_evolve_{plugin_name}_smoke_and_historical_replay():
    mod = _load_plugin()
    assert isinstance(mod.PLUGIN, dict)
    assert mod.PLUGIN.get("name") == "{plugin_name}"
    assert isinstance(mod.PLUGIN.get("brief"), str) and len(mod.PLUGIN["brief"].strip()) > 0
    out_default = mod.run({{}}, player=None, session_memory=None)
    assert isinstance(out_default, str) and len(out_default.strip()) > 0
    for case_args in {cases_json}:
        out = mod.run(dict(case_args), player=None, session_memory=None)
        assert isinstance(out, str) and len(out.strip()) > 0
        assert not out.lower().startswith("status=failed ")
'''


def _record_optimization_run(
    target: str,
    kind: str,
    accepted: bool,
    b_score: float,
    c_score: float,
    branch: str,
    detail: str,
    *,
    meta_db_path: Path | None = None,
) -> None:
    try:
        with _LOCK:
            conn = _connect_meta_db(meta_db_path)
            try:
                conn.execute(
                    """
                    INSERT INTO optimization_runs(
                        ts_utc, target, kind, accepted, baseline_score, candidate_score, branch, detail
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (_now_iso(), target, kind, 1 if accepted else 0, float(b_score), float(c_score), branch, detail[:300]),
                )
                conn.commit()
            finally:
                conn.close()
    except Exception:
        pass


def optimize_plugin_candidate(
    repo_root: Path,
    plugin_name: str,
    *,
    db_path: Path | None = None,
    meta_db_path: Path | None = None,
    candidate_fn: Callable[[str, dict[str, Any], list[dict[str, Any]]], str] | None = None,
    test_fn: Callable[[str, str, list[dict[str, Any]]], str] | None = None,
    replay_cases: list[dict[str, Any]] | None = None,
    validator_script: Path | None = None,
) -> dict[str, Any]:
    """Stage a candidate plugin optimization on an isolated `evolve/*` Git worktree.

    Runs AST safety audit, `validate_plugin.py`, `pytest`, and historical replay
    benchmarking. Auto-reverts the worktree & branch unless measurably better.
    """
    from core.cross_os import run_git
    from plugins._skill_gate import _git_path_arg, preflight_clean_repo, sanitize_plugin_slug

    root = Path(repo_root).resolve()
    ok_opt, rel_plugin, opt_err = assert_optimizable_target(root, plugin_name)
    if not ok_opt:
        return {
            "ok": False,
            "reverted": False,
            "measurably_better": False,
            "error": opt_err,
            "branch": "",
            "plugin_name": str(plugin_name),
        }

    slug = sanitize_plugin_slug(Path(rel_plugin).stem)
    rel_test = f"tests/evolved/test_evolve_{slug}.py"
    ok_test, test_why = safety_guard.assert_mutable_path(root, rel_test, allow_tests=True)
    if not ok_test:
        return {
            "ok": False,
            "reverted": False,
            "measurably_better": False,
            "error": test_why,
            "branch": "",
            "plugin_name": slug,
        }

    target_live = root / rel_plugin
    if not target_live.is_file():
        return {
            "ok": False,
            "reverted": False,
            "measurably_better": False,
            "error": f"Target plugin '{rel_plugin}' does not exist.",
            "branch": "",
            "plugin_name": slug,
        }

    ok_pre, pre_err = preflight_clean_repo(root, slug)
    if not ok_pre:
        return {
            "ok": False,
            "reverted": False,
            "measurably_better": False,
            "error": pre_err,
            "branch": "",
            "plugin_name": slug,
        }

    val_script = Path(validator_script).resolve() if validator_script else (root / "scripts" / "validate_plugin.py")
    if not val_script.is_file():
        val_script = _PROJECT_ROOT / "scripts" / "validate_plugin.py"
    if not val_script.is_file():
        return {
            "ok": False,
            "reverted": False,
            "measurably_better": False,
            "error": f"Validator missing: {val_script}",
            "branch": "",
            "plugin_name": slug,
        }

    cases = (
        list(replay_cases)
        if replay_cases is not None
        else telemetry.recent_failures(tool_name=slug, limit=5, db_path=db_path)
    )
    current_code = target_live.read_text(encoding="utf-8", errors="replace")
    baseline_replay = self_healer.replay_benchmark_plugin(target_live, cases, root)
    baseline_10d = evaluate_module_10d(current_code, is_plugin=True)
    t_metrics = telemetry.tool_metrics(tool_name=slug, db_path=db_path).get(slug, {})

    base_ref = run_git(root, "rev-parse", "--abbrev-ref", "HEAD").stdout.strip() or "main"
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    branch = f"evolve/{slug}-{stamp}"
    if run_git(root, "show-ref", "--verify", "--quiet", f"refs/heads/{branch}").returncode == 0:
        branch = f"{branch}-{secrets.token_hex(2)}"

    wt_parent = Path(tempfile.mkdtemp(prefix="jarvis-evolve-wt-"))
    wt_dir = wt_parent / "repo"
    branch_created = False
    keep_branch = False

    try:
        add_wt = run_git(root, "worktree", "add", "-b", branch, _git_path_arg(wt_dir), "HEAD")
        if add_wt.returncode != 0:
            return {
                "ok": False,
                "reverted": True,
                "measurably_better": False,
                "error": f"Failed to create evolve/* worktree: {add_wt.stderr.strip()}",
                "branch": "",
                "plugin_name": slug,
            }
        branch_created = True

        cand_code = (
            _strip_fences(candidate_fn(current_code, t_metrics, cases) or "")
            if candidate_fn is not None
            else _default_optimize_with_llm(slug, current_code, t_metrics, cases)
        )
        if not cand_code:
            return {
                "ok": False,
                "reverted": True,
                "measurably_better": False,
                "error": "Candidate generator returned empty code.",
                "branch": "",
                "plugin_name": slug,
            }

        audit = safety_guard.audit_python_source(cand_code, filename=rel_plugin, strict_sandbox=True)
        if not audit["safe"]:
            err = "AST safety gate rejected candidate: " + "; ".join(audit["violations"])
            _record_optimization_run(slug, "plugin", False, baseline_replay["pass_rate"], 0.0, "", err, meta_db_path=meta_db_path)
            return {
                "ok": False,
                "reverted": True,
                "measurably_better": False,
                "error": err,
                "branch": "",
                "plugin_name": slug,
            }

        wt_plugin = wt_dir / rel_plugin
        wt_test = wt_dir / rel_test
        wt_plugin.parent.mkdir(parents=True, exist_ok=True)
        wt_test.parent.mkdir(parents=True, exist_ok=True)
        wt_plugin.write_text(cand_code.rstrip() + "\n", encoding="utf-8")

        test_src = (
            _strip_fences(test_fn(slug, cand_code, cases) or "")
            if test_fn is not None
            else _default_evolve_test(slug, cases)
        )
        wt_test.write_text(test_src.rstrip() + "\n", encoding="utf-8")

        val = self_healer._run_validator(val_script, wt_plugin, wt_dir)
        if not val.get("passed"):
            err = "Plugin validator rejected candidate: " + "; ".join(val.get("errors") or ["unknown"])
            _record_optimization_run(slug, "plugin", False, baseline_replay["pass_rate"], 0.0, "", err, meta_db_path=meta_db_path)
            return {
                "ok": False,
                "reverted": True,
                "measurably_better": False,
                "error": err,
                "branch": "",
                "plugin_name": slug,
            }

        ok_py, py_out = self_healer._run_worktree_pytest(wt_dir, rel_test, extra_repo_root=_PROJECT_ROOT)
        if not ok_py:
            err = f"Worktree pytest failed: {py_out[:320]}"
            _record_optimization_run(slug, "plugin", False, baseline_replay["pass_rate"], 0.0, "", err, meta_db_path=meta_db_path)
            return {
                "ok": False,
                "reverted": True,
                "measurably_better": False,
                "error": err,
                "branch": "",
                "plugin_name": slug,
            }

        candidate_replay = self_healer.replay_benchmark_plugin(wt_plugin, cases, _PROJECT_ROOT)
        candidate_10d = evaluate_module_10d(cand_code, is_plugin=True)
        better, reason = _is_measurably_better(
            baseline_replay,
            candidate_replay,
            baseline_10d,
            candidate_10d,
            has_replay_cases=bool(cases),
        )
        if not better:
            _record_optimization_run(
                slug, "plugin", False, baseline_replay["pass_rate"], candidate_replay["pass_rate"], "", reason, meta_db_path=meta_db_path
            )
            return {
                "ok": False,
                "reverted": True,
                "measurably_better": False,
                "error": reason,
                "branch": "",
                "plugin_name": slug,
                "baseline_replay": baseline_replay,
                "candidate_replay": candidate_replay,
                "baseline_10d": baseline_10d,
                "candidate_10d": candidate_10d,
            }

        add_res = run_git(wt_dir, "add", "--", rel_plugin, rel_test)
        if add_res.returncode != 0:
            return {
                "ok": False,
                "reverted": True,
                "measurably_better": False,
                "error": f"git add failed: {add_res.stderr.strip()}",
                "branch": "",
                "plugin_name": slug,
            }

        if run_git(wt_dir, "diff", "--cached", "--quiet").returncode == 0:
            return {
                "ok": False,
                "reverted": True,
                "measurably_better": False,
                "error": "Candidate produced no diff.",
                "branch": "",
                "plugin_name": slug,
            }

        commit_res = run_git(
            wt_dir,
            "-c", "user.name=JARVIS Meta-Optimizer",
            "-c", "user.email=jarvis-local@localhost",
            "commit",
            "-m", f"perf(evolve): optimize {slug} ({reason})",
        )
        if commit_res.returncode != 0:
            return {
                "ok": False,
                "reverted": True,
                "measurably_better": False,
                "error": f"git commit failed: {commit_res.stderr.strip()}",
                "branch": "",
                "plugin_name": slug,
            }

        diff_res = run_git(wt_dir, "diff", "HEAD~1..HEAD")
        keep_branch = True
        _record_optimization_run(
            slug, "plugin", True, baseline_replay["pass_rate"], candidate_replay["pass_rate"], branch, reason, meta_db_path=meta_db_path
        )
        return {
            "ok": True,
            "reverted": False,
            "measurably_better": True,
            "reason": reason,
            "branch": branch,
            "base_ref": base_ref,
            "plugin_name": slug,
            "rel_path": rel_plugin,
            "test_path": rel_test,
            "baseline_replay": baseline_replay,
            "candidate_replay": candidate_replay,
            "baseline_10d": baseline_10d,
            "candidate_10d": candidate_10d,
            "diff": diff_res.stdout if diff_res.returncode == 0 else "",
            "review_cmd": f"git diff {base_ref}..{branch}",
        }
    finally:
        run_git(root, "worktree", "remove", "--force", _git_path_arg(wt_dir))
        shutil.rmtree(wt_parent, ignore_errors=True)
        if branch_created and not keep_branch:
            run_git(root, "branch", "-D", branch)


def apply_tuning_proposal(payload: dict[str, Any], *, meta_db_path: Path | None = None) -> dict[str, Any]:
    """Persist an approved routing-rule or prompt guardrail override in `meta_optimizer.sqlite`."""
    kind = str(payload.get("tuning_kind") or "routing").strip()
    target = str(payload.get("target") or "default").strip()
    key = f"{kind}:{target}"
    now = _now_iso()
    with _LOCK:
        conn = _connect_meta_db(meta_db_path)
        try:
            conn.execute(
                """
                INSERT INTO tuning_overrides(override_key, kind, target, payload_json, applied_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(override_key) DO UPDATE SET
                    payload_json = excluded.payload_json,
                    applied_at   = excluded.applied_at
                """,
                (key, kind, target, json.dumps(payload, ensure_ascii=False), now),
            )
            conn.commit()
        finally:
            conn.close()
    return {"ok": True, "override_key": key, "applied_at": now}


def list_tuning_overrides(*, meta_db_path: Path | None = None) -> list[dict[str, Any]]:
    path = Path(meta_db_path) if meta_db_path is not None else DEFAULT_META_DB
    if not path.is_file():
        return []
    with _LOCK:
        conn = _connect_meta_db(path)
        try:
            rows = conn.execute(
                "SELECT override_key, kind, target, payload_json, applied_at FROM tuning_overrides ORDER BY applied_at DESC"
            ).fetchall()
        finally:
            conn.close()
    out = []
    for key, kind, target, pjson, applied_at in rows:
        try:
            pdata = json.loads(pjson)
        except Exception:
            pdata = {}
        out.append({
            "override_key": key,
            "kind": kind,
            "target": target,
            "payload": pdata,
            "applied_at": applied_at,
        })
    return out


def run_idle_optimization(
    repo_root: Path | None = None,
    *,
    db_path: Path | None = None,
    digest_path: Path | None = None,
    meta_db_path: Path | None = None,
    min_idle_seconds: float = 180.0,
    idle_seconds_override: float | None = None,
    force: bool = False,
    max_plugin_candidates: int = 2,
    candidate_fn: Callable[[str, dict[str, Any], list[dict[str, Any]]], str] | None = None,
    validator_script: Path | None = None,
) -> dict[str, Any]:
    """Run idle-time telemetry analysis, stage measurably better `evolve/*` candidates,
    and queue verified upgrades into `memory/evolution_digest.json`.
    """
    if not force and not is_pc_idle(min_idle_seconds, idle_seconds_override=idle_seconds_override):
        return {
            "ok": True,
            "skipped_not_idle": True,
            "idle_seconds": idle_seconds_override if idle_seconds_override is not None else get_system_idle_seconds(),
            "staged_branches": [],
            "queued_proposals": [],
        }

    root = Path(repo_root).resolve() if repo_root else _PROJECT_ROOT
    analysis = analyze_telemetry_bottlenecks(repo_root=root, db_path=db_path)

    staged_branches: list[dict[str, Any]] = []
    rejected_candidates: list[dict[str, Any]] = []

    for pc in analysis["plugin_candidates"][: max(1, int(max_plugin_candidates))]:
        res = optimize_plugin_candidate(
            root,
            pc["plugin_name"],
            db_path=db_path,
            meta_db_path=meta_db_path,
            candidate_fn=candidate_fn,
            validator_script=validator_script,
        )
        if res.get("ok") and res.get("measurably_better"):
            staged_branches.append(res)
        else:
            rejected_candidates.append({
                "plugin_name": pc["plugin_name"],
                "error": res.get("error", ""),
                "reverted": bool(res.get("reverted", False)),
            })

    # Refresh the Evolution Digest so staged `evolve/*` branches + verified routing/prompt tunings
    # appear as numbered upgrades ("Approve upgrade 1")
    from core.evolution import reflection_loop

    digest = reflection_loop.load_digest(digest_path=digest_path)
    proposals: list[dict[str, Any]] = list(digest.get("proposals") or [])
    existing_branches = {
        str((p.get("payload") or {}).get("branch") or "")
        for p in proposals
    }
    existing_targets = {
        (str(p.get("kind") or ""), str((p.get("payload") or {}).get("target") or ""))
        for p in proposals
    }
    next_id = max([int(p.get("id", 0)) for p in proposals] or [0]) + 1
    queued_now: list[dict[str, Any]] = []

    for st in staged_branches:
        br = st["branch"]
        if br in existing_branches:
            continue
        prop = {
            "id": next_id,
            "kind": "approve_branch",
            "title": f"Merge measurably better meta-evolved plugin '{st['plugin_name']}' ({br})",
            "detail": f"{st['reason']} | Review: `{st['review_cmd']}`",
            "status": "pending",
            "payload": {
                "branch": br,
                "plugin_name": st["plugin_name"],
                "baseline_replay": st["baseline_replay"],
                "candidate_replay": st["candidate_replay"],
            },
        }
        proposals.append(prop)
        queued_now.append(prop)
        existing_branches.add(br)
        next_id += 1

    for rc in analysis["routing_candidates"][:2]:
        tkey = f"{rc['category']}:{rc['degraded_model']}"
        if ("apply_meta_tuning", tkey) in existing_targets:
            continue
        prop = {
            "id": next_id,
            "kind": "apply_meta_tuning",
            "title": f"Optimize '{rc['category']}' route away from degraded '{rc['degraded_model']}'",
            "detail": (
                f"Observed success_rate={rc['success_rate']:.0%}, P95={rc['p95_ms']:.0f}ms, "
                f"escalations={rc['escalations']} -> prefer '{rc['recommended_primary']}'."
            ),
            "status": "pending",
            "payload": {
                "tuning_kind": "routing",
                "target": tkey,
                "category": rc["category"],
                "demote_model": rc["degraded_model"],
                "prefer_model": rc["recommended_primary"],
            },
        }
        proposals.append(prop)
        queued_now.append(prop)
        existing_targets.add(("apply_meta_tuning", tkey))
        next_id += 1

    for prm in analysis["prompt_candidates"][:2]:
        tkey = f"prompt:{prm['tool_name']}:{prm['action'] or 'default'}"
        if ("apply_meta_tuning", tkey) in existing_targets:
            continue
        prop = {
            "id": next_id,
            "kind": "apply_meta_tuning",
            "title": f"Add prompt guardrail for '{prm['tool_name']}' ({prm['failure_count']}x failures)",
            "detail": prm["prompt_guardrail"],
            "status": "pending",
            "payload": {
                "tuning_kind": "prompt",
                "target": tkey,
                "tool_name": prm["tool_name"],
                "guardrail": prm["prompt_guardrail"],
            },
        }
        proposals.append(prop)
        queued_now.append(prop)
        existing_targets.add(("apply_meta_tuning", tkey))
        next_id += 1

    digest["generated_at"] = _now_iso()
    digest["proposals"] = proposals
    reflection_loop._save_digest(digest, digest_path=digest_path)

    return {
        "ok": True,
        "skipped_not_idle": False,
        "analysis": analysis,
        "staged_branches": staged_branches,
        "rejected_candidates": rejected_candidates,
        "queued_proposals": queued_now,
        "digest": digest,
    }
