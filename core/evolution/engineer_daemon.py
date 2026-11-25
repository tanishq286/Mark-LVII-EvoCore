"""24/7 Engineer agent (Milestone 15).

Consumes `plugin_fix` specs from the Researcher queue and, for each, stages a
fix on an isolated `evolve/auto-<slug>-<stamp>` Git worktree created from the
committed HEAD. The live checkout (and the user's uncommitted edits in it) is
never written to - so, unlike `meta_optimizer.optimize_plugin_candidate`, no
clean working tree is required.

Gates, in order (any failure -> worktree removed, branch deleted, spec
`discarded`):
  1. target must be a mutable `plugins/<slug>.py` (`assert_optimizable_target`)
     and never one of PROTECTED_PATHS (safety_guard, file_vault, voice_lock,
     core/confirm, the validator...)
  2. AST sandbox audit of the candidate (`safety_guard.audit_python_source`)
  3. `scripts/validate_plugin.py` on the staged file
  4. pytest: the new regression test + every existing tests/test_*<slug>*.py
  5. Replay Self-Benchmark against real telemetry failures + 10D score; the
     candidate must be measurably better than the baseline
  6. the commit may only touch the plugin + its test (no memory/*, no DBs,
     no protected file)

A survivor is queued in `memory/evolution_digest.json` as an `approve_evolve`
proposal and announced on the HUD and phone PWA ("Approve upgrade N"). It is
merged ONLY when the user approves; `snapshot_memory_dbs()` is taken first so
conversations.sqlite and knowledge_graph.sqlite survive every upgrade.
"""
from __future__ import annotations

import shutil
import sqlite3
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from core.evolution import research_daemon as rq

_BASE = rq._BASE

PROTECTED_PATHS: frozenset[str] = frozenset({
    "core/evolution/safety_guard.py",
    "plugins/file_vault.py",
    "plugins/voice_lock.py",
    "core/voice/voice_lock.py",
    "core/confirm.py",
    "plugins/_skill_gate.py",
    "scripts/validate_plugin.py",
    "core/evolution/engineer_daemon.py",
    "core/evolution/research_daemon.py",
    "core/memory/conversation_store.py",
})
PRESERVED_DBS: tuple[str, ...] = ("memory/conversations.sqlite", "memory/knowledge_graph.sqlite")
SNAPSHOT_DIR: Path = _BASE / "memory" / "upgrade_snapshots"
DAILY_ENGINEER_RUNS = 3


def _stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")


def is_protected(rel: str) -> bool:
    r = str(rel or "").replace("\\", "/").strip("/").lower()
    if r in {p.lower() for p in PROTECTED_PATHS}:
        return True
    if r.startswith("memory/") or r.endswith((".sqlite", ".sqlite-wal", ".sqlite-shm", ".db")):
        return True
    try:
        from core.evolution import safety_guard
        return r in {str(p).lower() for p in safety_guard.IMMUTABLE_PATHS}
    except Exception:
        return False


def snapshot_memory_dbs(*, base: Path | None = None, out_dir: Path | None = None) -> list[str]:
    """Consistent online copies (sqlite backup API) of the memory DBs."""
    root = Path(base) if base else _BASE
    dest = Path(out_dir) if out_dir else (root / "memory" / "upgrade_snapshots" / _stamp())
    saved: list[str] = []
    for rel in PRESERVED_DBS:
        src = root / rel
        if not src.is_file():
            continue
        dest.mkdir(parents=True, exist_ok=True)
        target = dest / src.name
        s = sqlite3.connect(str(src), timeout=10.0)
        try:
            d = sqlite3.connect(str(target))
            try:
                s.backup(d)
            finally:
                d.close()
        finally:
            s.close()
        saved.append(str(target))
    return saved


def _llm_candidate(slug: str, code: str, metrics: dict, cases: list[dict], spec: dict,
                   db_path: Path | None) -> str:
    """Ollama (coding tier) first; the cloud generator only inside the daily cap."""
    from core.evolution import meta_optimizer as mo
    if rq.can_spend("local_calls", 1, db_path=db_path):
        rq.charge("local_calls", 1, db_path=db_path)
        try:
            from providers import local_router
            fails = "\n".join(f"- {f.get('error_snippet') or f.get('error')}" for f in cases[:5])
            prompt = (f"Fix Mark-LVII plugin plugins/{slug}.py. Problem: {spec.get('title')}\n"
                      f"Research notes: {spec.get('detail', '')[:800]}\nFailures:\n{fails}\n\n"
                      f"Current source:\n{code}\n\nKeep PLUGIN (name/description/brief/parameters) and "
                      "`def run(parameters: dict, player=None, session_memory=None) -> str`. No Qt, eval, exec, "
                      "__import__, os.system, shell=True. Return ONLY the full Python file.")
            res = local_router.complete_local(prompt, tier="coding", timeout_s=240.0)
            out = mo._strip_fences(str((res or {}).get("text") or ""))
            if "PLUGIN" in out and "def run" in out:
                return out
        except Exception:
            pass
    est = len(code) // 3 + 3000
    if rq.can_spend("cloud_calls", 1, db_path=db_path) and rq.can_spend("cloud_tokens", est, db_path=db_path):
        rq.charge("cloud_calls", 1, db_path=db_path)
        rq.charge("cloud_tokens", est, db_path=db_path)
        return mo._default_optimize_with_llm(slug, code, metrics, cases)
    return ""


def engineer_spec(spec: dict[str, Any], *, repo_root: Path | None = None, db_path: Path | None = None,
                  telemetry_db: Path | None = None,
                  candidate_fn: Callable[[str, dict, list], str] | None = None,
                  test_fn: Callable[[str, str, list], str] | None = None,
                  replay_cases: list[dict] | None = None,
                  digest_path: Path | None = None) -> dict[str, Any]:
    from core.auto_evolution import evaluate_module_10d
    from core.cross_os import run_git
    from core.evolution import meta_optimizer as mo
    from core.evolution import safety_guard, self_healer, telemetry
    from plugins._skill_gate import _git_path_arg, sanitize_plugin_slug

    root = Path(repo_root).resolve() if repo_root else _BASE
    spec_id = int(spec.get("id") or 0)

    def _discard(reason: str, branch: str = "") -> dict[str, Any]:
        if spec_id:
            rq.set_spec_status(spec_id, "discarded", reason, db_path=db_path)
        rq.log_run("engineer", "discarded", target=str(spec.get("target")), branch=branch, detail=reason,
                   db_path=db_path)
        return {"ok": False, "discarded": True, "reason": reason, "branch": ""}

    ok, rel, err = mo.assert_optimizable_target(root, str(spec.get("target") or ""))
    if not ok:
        return _discard(err or "target not optimizable")
    if is_protected(rel):
        return _discard(f"protected path {rel}")
    live = root / rel
    if not live.is_file():
        return _discard(f"{rel} does not exist")
    slug = sanitize_plugin_slug(Path(rel).stem)
    rel_test = f"tests/evolved/test_auto_{slug}.py"
    val_script = root / "scripts" / "validate_plugin.py"
    if not val_script.is_file():
        val_script = _BASE / "scripts" / "validate_plugin.py"

    head = run_git(root, "rev-parse", "HEAD")
    if head.returncode != 0:
        return _discard("not a git repository with commits")
    base_sha = head.stdout.strip()
    # Baseline = the COMMITTED plugin (what the branch is built from), not a dirty working copy.
    shown = run_git(root, "show", f"HEAD:{rel}")
    if shown.returncode != 0:
        return _discard(f"{rel} is not committed")
    current = shown.stdout
    cases = list(replay_cases) if replay_cases is not None else telemetry.recent_failures(
        tool_name=slug, limit=5, db_path=telemetry_db)
    metrics = telemetry.tool_metrics(tool_name=slug, db_path=telemetry_db).get(slug, {})

    branch = f"evolve/auto-{slug}-{_stamp()}"
    wt_parent = Path(tempfile.mkdtemp(prefix="jarvis-engineer-wt-"))
    wt = wt_parent / "repo"
    created = keep = False
    try:
        add = run_git(root, "worktree", "add", "-b", branch, _git_path_arg(wt), base_sha)
        if add.returncode != 0:
            return _discard(f"worktree failed: {add.stderr.strip()[:200]}")
        created = True
        wt_plugin, wt_test = wt / rel, wt / rel_test
        baseline_replay = self_healer.replay_benchmark_plugin(wt_plugin, cases, root)
        baseline_10d = evaluate_module_10d(current, is_plugin=True)

        cand = (mo._strip_fences(candidate_fn(current, metrics, cases) or "") if candidate_fn
                else _llm_candidate(slug, current, metrics, cases, spec, db_path))
        if not cand.strip():
            return _discard("no candidate (LLM unavailable or daily budget exhausted)", branch)
        audit = safety_guard.audit_python_source(cand, filename=rel, strict_sandbox=True)
        if not audit["safe"]:
            return _discard("AST gate: " + "; ".join(audit["violations"])[:300], branch)
        wt_plugin.write_text(cand.rstrip() + "\n", encoding="utf-8")
        wt_test.parent.mkdir(parents=True, exist_ok=True)
        test_src = (mo._strip_fences(test_fn(slug, cand, cases) or "") if test_fn
                    else mo._default_evolve_test(slug, cases))
        wt_test.write_text(test_src.rstrip() + "\n", encoding="utf-8")

        val = self_healer._run_validator(val_script, wt_plugin, wt)
        if not val.get("passed") or val.get("warnings"):
            return _discard("validator: " + "; ".join((val.get("errors") or []) + (val.get("warnings") or []))[:300],
                            branch)
        for t in [rel_test] + sorted(str(p.relative_to(wt)).replace("\\", "/")
                                     for p in (wt / "tests").glob(f"test_*{slug}*.py")):
            ok_py, out = self_healer._run_worktree_pytest(wt, t, extra_repo_root=_BASE)
            if not ok_py:
                return _discard(f"pytest {t}: {out[-300:]}", branch)

        cand_replay = self_healer.replay_benchmark_plugin(wt_plugin, cases, root)
        better, why = mo._is_measurably_better(baseline_replay, cand_replay, baseline_10d,
                                               evaluate_module_10d(cand, is_plugin=True),
                                               has_replay_cases=bool(cases))
        if not better:
            return _discard(why, branch)

        run_git(wt, "add", "--", rel, rel_test)
        changed = [ln.strip() for ln in run_git(wt, "diff", "--cached", "--name-only").stdout.splitlines() if ln.strip()]
        if not changed:
            return _discard("candidate produced no diff", branch)
        bad = [c for c in changed if c not in (rel, rel_test) or is_protected(c)]
        if bad:
            return _discard(f"diff touches forbidden paths: {bad}", branch)
        com = run_git(wt, "-c", "user.name=JARVIS Engineer", "-c", "user.email=jarvis-local@localhost",
                      "commit", "-m", f"fix(auto): {slug} - {str(spec.get('title'))[:60]} ({why[:80]})")
        if com.returncode != 0:
            return _discard(f"commit failed: {com.stderr.strip()[:200]}", branch)
        keep = True
    finally:
        run_git(root, "worktree", "remove", "--force", _git_path_arg(wt))
        shutil.rmtree(wt_parent, ignore_errors=True)
        if created and not keep:
            run_git(root, "branch", "-D", branch)

    upgrade_id = queue_proposal(branch, slug, spec, why, digest_path=digest_path)
    if spec_id:
        rq.set_spec_status(spec_id, "built", f"{branch} -> upgrade {upgrade_id}", db_path=db_path)
    rq.log_run("engineer", "built", target=slug, branch=branch, detail=why, db_path=db_path)
    return {"ok": True, "branch": branch, "upgrade_id": upgrade_id, "reason": why,
            "baseline_replay": baseline_replay, "candidate_replay": cand_replay}


def queue_proposal(branch: str, slug: str, spec: dict[str, Any], why: str, *,
                   digest_path: Path | None = None) -> int:
    from core.evolution import reflection_loop as rl
    digest = rl.load_digest(digest_path=digest_path)
    props = list(digest.get("proposals") or [])
    new_id = max([int(p.get("id", 0)) for p in props] + [0]) + 1
    props.append({
        "id": new_id, "kind": "approve_evolve", "status": "pending",
        "title": f"Engineer fix for {slug}: {str(spec.get('title'))[:80]}",
        "detail": f"{why} Review: `git diff main..{branch}`. Say 'Approve upgrade {new_id}' to merge.",
        "payload": {"branch": branch, "spec_id": spec.get("id"), "source": "engineer_daemon"},
        "created_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    })
    digest["proposals"] = props
    rl._save_digest(digest, digest_path)
    return new_id


def notify(player: Any, title: str, message: str) -> None:
    """HUD log + phone PWA push (best effort, never raises)."""
    try:
        if player is not None and hasattr(player, "write_log"):
            player.write_log(f"SYS: [{title}] {message}")
    except Exception:
        pass
    try:
        import time
        from dashboard import server as srv_mod
        srv_mod.broadcast_mission_log(f"[{title}] {message}", source="engineer_daemon", status="info")
        srv = srv_mod.get_active_server()
        if srv is not None:
            srv.push_ws_event({"type": "mission_alert", "mission_id": "agents_247", "title": title,
                               "message": message[:500], "level": "info", "ts": time.time()})
    except Exception:
        pass


def run_engineer_cycle(*, player: Any = None, force: bool = False, db_path: Path | None = None,
                       repo_root: Path | None = None, gate_overrides: dict | None = None,
                       **kw: Any) -> dict[str, Any]:
    if not force:
        ok, why = rq.check_gates(**(gate_overrides or {}))
        if not ok:
            return {"ran": False, "reason": why}
    done_today = [r for r in rq.runs_today(db_path=db_path) if r["agent"] == "engineer"]
    if len(done_today) >= DAILY_ENGINEER_RUNS and not force:
        return {"ran": False, "reason": f"daily engineer cap reached ({DAILY_ENGINEER_RUNS})"}
    specs = rq.list_specs(status="pending", kind="plugin_fix", limit=1, db_path=db_path)
    if not specs:
        return {"ran": False, "reason": "no pending plugin_fix specs"}
    res = engineer_spec(specs[0], repo_root=repo_root, db_path=db_path, **kw)
    if res.get("ok"):
        notify(player, "Engineer built an upgrade",
               f"{res['reason'][:160]} Say 'Approve upgrade {res['upgrade_id']}' to merge it.")
    return {"ran": True, **res}
