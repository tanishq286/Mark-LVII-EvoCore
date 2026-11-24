"""core/evolution/reflection_loop.py — Idle Reflection Digest, Failure Immunity & Approvals.

Consolidates RAG memory, mines recurring failure root causes for planner Failure Immunity,
surfaces pending self-heal / habit-macro / branch-merge proposals into a numbered
`memory/evolution_digest.json`, and supports 1-command upgrades (`Approve upgrade N`).
"""
from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import sys
import threading

from core.evolution import habit_compiler, safety_guard, self_healer, skill_synthesizer, telemetry

if getattr(sys, "frozen", False):
    _BASE = Path(sys.executable).parent
else:
    _BASE = Path(__file__).resolve().parent.parent.parent

DEFAULT_DIGEST_PATH: Path = _BASE / "memory" / "evolution_digest.json"
_LOCK = threading.Lock()


def _resolve_digest(digest_path: Path | None = None) -> Path:
    return Path(digest_path) if digest_path is not None else DEFAULT_DIGEST_PATH


def load_digest(*, digest_path: Path | None = None) -> dict:
    path = _resolve_digest(digest_path)
    with _LOCK:
        if not path.is_file():
            return {"generated_at": "", "consolidation": {}, "telemetry_summary": {}, "proposals": []}
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {"proposals": []}
        except Exception:
            return {"proposals": []}


def _save_digest(digest: dict, digest_path: Path | None = None) -> None:
    path = _resolve_digest(digest_path)
    with _LOCK:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(digest, indent=2, ensure_ascii=False), encoding="utf-8")


def format_failure_immunity_context(*, db_path: Path | None = None, limit: int = 5) -> str:
    """Build a concise prompt block of recurring telemetry failures and learned heuristics."""
    lines: list[str] = []
    try:
        lessons = telemetry.recurring_failure_lessons(min_count=2, limit=limit, db_path=db_path)
        for item in lessons:
            act = f".{item['action']}" if item.get("action") else ""
            lines.append(
                f"- `{item['tool_name']}{act}` failed {item['count']}x recently: "
                f"{str(item.get('error_snippet') or '')[:140]}"
            )
    except Exception:
        pass

    try:
        from core import auto_evolution
        chain = auto_evolution.load_heuristics_chain()
        for h in chain[-3:]:
            lines.append(f"- Heuristic [{h.get('id', '')}]: {str(h.get('rule') or '')[:140]}")
    except Exception:
        pass

    if not lines:
        return ""
    return "[FAILURE IMMUNITY — AVOID PAST ERRORS]\n" + "\n".join(lines[: limit + 3])


def _pending_evolution_branches(repo_root: Path) -> list[str]:
    from core.cross_os import run_git

    branches: list[str] = []
    try:
        for pattern in ("skill/*", "autofix/*"):
            res = run_git(repo_root, "branch", "--list", pattern, "--format=%(refname:short)")
            if res.returncode == 0:
                for ln in (res.stdout or "").splitlines():
                    b = ln.strip()
                    if b and b not in branches:
                        branches.append(b)
    except Exception:
        pass
    return branches


def run_reflection_cycle(
    repo_root: Path | None = None,
    *,
    db_path: Path | None = None,
    vault_dir: Path | None = None,
    habits_path: Path | None = None,
    digest_path: Path | None = None,
    consolidate_rag: bool = True,
    failure_threshold: int = 3,
    min_habit_support: int = 3,
) -> dict:
    """Run telemetry review, RAG deduplication, habit detection, and emit Evolution Digest."""
    root = Path(repo_root).resolve() if repo_root else _BASE

    consolidation: dict = {"skipped": True}
    if consolidate_rag:
        try:
            from plugins import knowledge_rag
            consolidation = knowledge_rag.consolidate_vault(vault_dir=vault_dir)
        except Exception as exc:
            consolidation = {"error": str(exc)}

    proposals: list[dict] = []
    next_id = 1

    # 1. Pending skill/* or autofix/* branches ready for human approval
    for br in _pending_evolution_branches(root):
        kind = "approve_branch"
        proposals.append({
            "id": next_id,
            "kind": kind,
            "title": f"Merge validated evolution branch '{br}'",
            "detail": f"Run `git diff main..{br}` or approve upgrade {next_id} to merge & hot-load.",
            "status": "pending",
            "payload": {"branch": br},
        })
        next_id += 1

    # 2. Unhealthy plugins in telemetry needing self-healing
    unhealthy = self_healer.detect_unhealthy_plugins(
        repo_root=root,
        db_path=db_path,
        failure_threshold=failure_threshold,
    )
    for item in unhealthy:
        proposals.append({
            "id": next_id,
            "kind": "self_heal",
            "title": f"Self-heal plugin '{item['plugin_name']}' ({item['failures']} failures)",
            "detail": f"Success rate {item['success_rate']:.0%}, P95={item['p95_ms']:.0f}ms. Last error: {item['last_error'][:120]}",
            "status": "pending",
            "payload": {"plugin_name": item["plugin_name"]},
        })
        next_id += 1

    # 3. Repeated command chains ready to compile into 1-voice habits
    habits = habit_compiler.detect_habit_candidates(
        db_path=db_path,
        habits_path=habits_path,
        min_support=min_habit_support,
    )
    for cand in habits:
        seq_str = " -> ".join(pair[0] for pair in cand["signature"])
        proposals.append({
            "id": next_id,
            "kind": "compile_habit",
            "title": f"Compile 1-voice habit '{cand['suggested_name']}' (seen {cand['support_count']}x)",
            "detail": f"Sequence: {seq_str} (confirm_required={cand['requires_confirm']})",
            "status": "pending",
            "payload": {
                "name": cand["suggested_name"],
                "steps": cand["steps"],
                "support_count": cand["support_count"],
            },
        })
        next_id += 1

    # Telemetry overview
    t_metrics = telemetry.tool_metrics(db_path=db_path)
    r_metrics = telemetry.route_metrics(db_path=db_path)
    slowest = sorted(
        ((k, v["p95_ms"], v["success_rate"]) for k, v in t_metrics.items()),
        key=lambda x: -x[1],
    )[:5]
    escalations = sum(v.get("escalations", 0) for v in r_metrics.values())

    digest = {
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "consolidation": consolidation,
        "telemetry_summary": {
            "tracked_tools": len(t_metrics),
            "unhealthy_plugins": len(unhealthy),
            "habit_candidates": len(habits),
            "route_escalations": escalations,
            "slowest_tools": [{"tool": t, "p95_ms": p95, "success_rate": sr} for t, p95, sr in slowest],
        },
        "failure_immunity": format_failure_immunity_context(db_path=db_path),
        "proposals": proposals,
    }
    _save_digest(digest, digest_path=digest_path)
    return digest


def _mark_proposal_status(upgrade_id: int, status: str, note: str, *, digest_path: Path | None = None) -> None:
    digest = load_digest(digest_path=digest_path)
    for prop in digest.get("proposals") or []:
        if int(prop.get("id", -1)) == int(upgrade_id):
            prop["status"] = status
            prop["resolution_note"] = note[:300]
            prop["resolved_at"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
            break
    _save_digest(digest, digest_path=digest_path)


def approve_upgrade(
    upgrade_id: int,
    *,
    repo_root: Path | None = None,
    digest_path: Path | None = None,
    habits_path: Path | None = None,
    ledger_path: Path | None = None,
    db_path: Path | None = None,
    player=None,
    plugin_registry=None,
    require_confirm: bool = True,
) -> str:
    """Execute a numbered proposal from `evolution_digest.json` (e.g., 'Approve upgrade 1')."""
    _ = player
    root = Path(repo_root).resolve() if repo_root else _BASE
    digest = load_digest(digest_path=digest_path)
    target_prop = None
    for prop in digest.get("proposals") or []:
        if int(prop.get("id", -1)) == int(upgrade_id):
            target_prop = prop
            break

    if target_prop is None:
        return f"Upgrade #{upgrade_id} not found in Evolution Digest."
    if target_prop.get("status") == "approved":
        return f"Upgrade #{upgrade_id} ('{target_prop.get('title')}') is already approved."

    kind = str(target_prop.get("kind") or "")
    payload = dict(target_prop.get("payload") or {})

    def _run_upgrade() -> str:
        if kind == "approve_branch":
            res = skill_synthesizer.approve_and_hotload(
                root,
                payload["branch"],
                kind="digest_branch",
                summary=str(target_prop.get("title") or ""),
                ledger_path=ledger_path,
                plugin_registry=plugin_registry,
            )
            if res.get("ok"):
                msg = f"Merged '{res['branch']}' at {res['sha'][:8]} (hotloaded={res['hotloaded']})."
                _mark_proposal_status(upgrade_id, "approved", msg, digest_path=digest_path)
                return msg
            return f"Upgrade #{upgrade_id} failed: {res.get('error')}"

        if kind == "compile_habit":
            rec = habit_compiler.compile_habit(
                payload["name"],
                payload["steps"],
                support_count=int(payload.get("support_count", 0)),
                habits_path=habits_path,
            )
            msg = f"Compiled habit '{rec['name']}' with {len(rec['steps'])} step(s)."
            _mark_proposal_status(upgrade_id, "approved", msg, digest_path=digest_path)
            return msg

        if kind == "self_heal":
            res = self_healer.heal_plugin(root, payload["plugin_name"], db_path=db_path)
            if res.get("ok"):
                msg = f"Staged self-heal branch '{res['branch']}' (review: {res['review_cmd']})."
                _mark_proposal_status(upgrade_id, "staged", msg, digest_path=digest_path)
                return msg
            return f"Self-heal failed (reverted={res.get('reverted')}): {res.get('error')}"

        return f"Unsupported upgrade kind '{kind}'."

    if require_confirm:
        return safety_guard.enforce_gate(
            "evolution_control",
            "approve_upgrade",
            f"Approve Evolution Upgrade #{upgrade_id}",
            f"{target_prop.get('title')}: {target_prop.get('detail')}",
            _run_upgrade,
            key=f"approve_upgrade_{upgrade_id}",
        )
    return _run_upgrade()
