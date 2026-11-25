"""24/7 Researcher agent (Milestone 15).

Runs only when the PC is genuinely idle (no keyboard/mouse input, low CPU and
GPU load) and inside a strict per-day budget. Local Ollama is always tried
first; cloud (Gemini) calls are counted against a hard daily cap.

Each cycle mines three sources and writes ranked upgrade specs into
`memory/research_queue.sqlite`:

1. telemetry failures and slow tools (`memory/telemetry.sqlite`)
2. user-correction patterns ("no, I said...", "that's wrong") in the
   append-only conversation history
3. web research (DuckDuckGo) for the top failure signatures, stored as
   untrusted reference data only - never executed, never followed.

The Engineer (`engineer_daemon.py`) consumes `plugin_fix` specs.
"""
from __future__ import annotations

import json
import re
import sqlite3
import subprocess
import sys
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

if getattr(sys, "frozen", False):
    _BASE = Path(sys.executable).parent
else:
    _BASE = Path(__file__).resolve().parent.parent.parent

DEFAULT_QUEUE_DB: Path = _BASE / "memory" / "research_queue.sqlite"
_LOCK = threading.RLock()

DEFAULT_LIMITS: dict[str, float] = {
    "min_idle_s": 300.0,          # 5 min without keyboard/mouse
    "max_cpu_pct": 35.0,
    "max_gpu_pct": 30.0,
    "daily_cloud_tokens": 40_000.0,
    "daily_cloud_calls": 15.0,
    "daily_local_calls": 120.0,
    "daily_web_queries": 12.0,
}

_SCHEMA = """
CREATE TABLE IF NOT EXISTS specs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_utc TEXT NOT NULL, updated_utc TEXT NOT NULL,
    dedupe_key TEXT NOT NULL UNIQUE,
    kind TEXT NOT NULL, target TEXT NOT NULL, title TEXT NOT NULL,
    detail TEXT NOT NULL DEFAULT '', evidence_json TEXT NOT NULL DEFAULT '{}',
    score REAL NOT NULL DEFAULT 0, status TEXT NOT NULL DEFAULT 'pending',
    status_note TEXT NOT NULL DEFAULT '', source TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS ix_specs_status ON specs(status, score);
CREATE TABLE IF NOT EXISTS budget (
    day TEXT NOT NULL, bucket TEXT NOT NULL, amount REAL NOT NULL DEFAULT 0,
    PRIMARY KEY (day, bucket)
);
CREATE TABLE IF NOT EXISTS agent_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT, ts_utc TEXT NOT NULL,
    agent TEXT NOT NULL, outcome TEXT NOT NULL, target TEXT NOT NULL DEFAULT '',
    branch TEXT NOT NULL DEFAULT '', detail TEXT NOT NULL DEFAULT ''
);
"""

_CORRECTION_RX = re.compile(
    r"^(?:no[,.! ]|nope\b|wrong\b|that'?s (?:wrong|not)|not what i|i said\b|i meant\b|"
    r"you (?:didn'?t|did not|misunderstood|got it wrong)|stop doing|why did you)", re.I)


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _today() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _connect(db_path: Path | None = None) -> sqlite3.Connection:
    path = Path(db_path) if db_path is not None else DEFAULT_QUEUE_DB
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), timeout=10.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript(_SCHEMA)
    return conn


def limits() -> dict[str, float]:
    out = dict(DEFAULT_LIMITS)
    try:
        from memory.config_manager import get_plugin_config
        for k, v in (get_plugin_config("agents_247") or {}).items():
            if k in out:
                out[k] = float(v)
    except Exception:
        pass
    return out


# ── budget ──────────────────────────────────────────────────────────────────

def budget_used(bucket: str, *, db_path: Path | None = None) -> float:
    with _LOCK:
        conn = _connect(db_path)
        try:
            row = conn.execute("SELECT amount FROM budget WHERE day=? AND bucket=?", (_today(), bucket)).fetchone()
            return float(row[0]) if row else 0.0
        finally:
            conn.close()


def charge(bucket: str, amount: float, *, db_path: Path | None = None) -> None:
    with _LOCK:
        conn = _connect(db_path)
        try:
            conn.execute("INSERT INTO budget(day, bucket, amount) VALUES (?,?,?) ON CONFLICT(day, bucket) "
                         "DO UPDATE SET amount = amount + excluded.amount", (_today(), bucket, float(amount)))
            conn.commit()
        finally:
            conn.close()


def can_spend(bucket: str, amount: float = 1.0, *, db_path: Path | None = None,
              lim: dict[str, float] | None = None) -> bool:
    lim = lim or limits()
    cap = lim.get(f"daily_{bucket}", 0.0)
    return budget_used(bucket, db_path=db_path) + float(amount) <= cap


def budget_report(*, db_path: Path | None = None) -> dict[str, str]:
    lim = limits()
    return {b: f"{budget_used(b, db_path=db_path):.0f}/{lim[f'daily_{b}']:.0f}"
            for b in ("cloud_tokens", "cloud_calls", "local_calls", "web_queries")}


def ask_llm(prompt: str, *, max_tokens_estimate: int = 800, db_path: Path | None = None,
            allow_cloud: bool = True) -> tuple[str, str]:
    """Ollama first (counted, free); Gemini only inside the strict daily cloud cap."""
    if can_spend("local_calls", 1, db_path=db_path):
        charge("local_calls", 1, db_path=db_path)
        try:
            from providers import local_router
            res = local_router.complete_local(prompt, tier="reasoning", timeout_s=60.0)
            if res and str(res.get("text") or "").strip():
                return str(res["text"]), f"ollama:{res.get('model', '')}"
        except Exception:
            pass
    est = len(prompt) // 4 + max_tokens_estimate
    if allow_cloud and can_spend("cloud_calls", 1, db_path=db_path) and can_spend("cloud_tokens", est, db_path=db_path):
        charge("cloud_calls", 1, db_path=db_path)
        charge("cloud_tokens", est, db_path=db_path)
        try:
            from core import gemini
            return str(gemini.text(prompt, gemini.FAST, None, 45_000) or ""), "gemini"
        except Exception:
            return "", ""
    return "", ""


# ── idle / load gates ───────────────────────────────────────────────────────

def _gpu_util_pct() -> float | None:
    for exe in ("nvidia-smi", r"C:\Windows\System32\nvidia-smi.exe"):
        try:
            out = subprocess.run([exe, "--query-gpu=utilization.gpu", "--format=csv,noheader,nounits"],
                                 capture_output=True, text=True, timeout=4)
            if out.returncode == 0 and out.stdout.strip():
                return max(float(x) for x in out.stdout.split() if x.strip().replace(".", "").isdigit())
        except Exception:
            continue
    return None


def check_gates(*, idle_s: float | None = None, cpu_pct: float | None = None, gpu_pct: float | None = None,
                lim: dict[str, float] | None = None) -> tuple[bool, str]:
    """(ok, reason). Every probe is injectable so tests never depend on the host."""
    lim = lim or limits()
    if idle_s is None:
        try:
            from core.evolution.meta_optimizer import get_system_idle_seconds
            idle_s = get_system_idle_seconds()
        except Exception:
            idle_s = 0.0
    if idle_s < lim["min_idle_s"]:
        return False, f"user active (idle {idle_s:.0f}s < {lim['min_idle_s']:.0f}s)"
    if cpu_pct is None:
        try:
            import psutil
            cpu_pct = float(psutil.cpu_percent(interval=1.0))
        except Exception:
            cpu_pct = 0.0
    if cpu_pct > lim["max_cpu_pct"]:
        return False, f"CPU busy ({cpu_pct:.0f}% > {lim['max_cpu_pct']:.0f}%)"
    if gpu_pct is None:
        gpu_pct = _gpu_util_pct()
    if gpu_pct is not None and gpu_pct > lim["max_gpu_pct"]:
        return False, f"GPU busy ({gpu_pct:.0f}% > {lim['max_gpu_pct']:.0f}%)"
    return True, "idle"


# ── spec queue ──────────────────────────────────────────────────────────────

def upsert_spec(kind: str, target: str, title: str, *, detail: str = "", evidence: dict | None = None,
                score: float = 0.0, source: str = "", db_path: Path | None = None) -> int:
    key = f"{kind}|{target}".lower()
    ev = json.dumps(evidence or {}, ensure_ascii=False, default=str)[:8000]
    with _LOCK:
        conn = _connect(db_path)
        try:
            row = conn.execute("SELECT id, status FROM specs WHERE dedupe_key=?", (key,)).fetchone()
            if row is None:
                cur = conn.execute(
                    "INSERT INTO specs(created_utc, updated_utc, dedupe_key, kind, target, title, detail, "
                    "evidence_json, score, source) VALUES (?,?,?,?,?,?,?,?,?,?)",
                    (_now(), _now(), key, kind, target, title[:200], detail[:2000], ev, float(score), source))
                conn.commit()
                return int(cur.lastrowid or 0)
            if row["status"] in ("pending", "discarded"):
                # A discarded spec whose problem persists is re-opened with fresh evidence.
                conn.execute("UPDATE specs SET updated_utc=?, title=?, detail=CASE WHEN ?='' THEN detail ELSE ? END, "
                             "evidence_json=?, score=?, status='pending' WHERE id=?",
                             (_now(), title[:200], detail, detail[:2000], ev, float(score), row["id"]))
                conn.commit()
            return int(row["id"])
        finally:
            conn.close()


def list_specs(*, status: str | None = "pending", kind: str | None = None, limit: int = 20,
               db_path: Path | None = None) -> list[dict[str, Any]]:
    q, args = "SELECT * FROM specs WHERE 1=1", []
    if status:
        q += " AND status=?"
        args.append(status)
    if kind:
        q += " AND kind=?"
        args.append(kind)
    q += " ORDER BY score DESC, id ASC LIMIT ?"
    args.append(int(limit))
    with _LOCK:
        conn = _connect(db_path)
        try:
            return [dict(r) for r in conn.execute(q, args).fetchall()]
        finally:
            conn.close()


def set_spec_status(spec_id: int, status: str, note: str = "", *, db_path: Path | None = None) -> None:
    with _LOCK:
        conn = _connect(db_path)
        try:
            conn.execute("UPDATE specs SET status=?, status_note=?, updated_utc=? WHERE id=?",
                         (status, note[:500], _now(), int(spec_id)))
            conn.commit()
        finally:
            conn.close()


def log_run(agent: str, outcome: str, *, target: str = "", branch: str = "", detail: str = "",
            db_path: Path | None = None) -> None:
    with _LOCK:
        conn = _connect(db_path)
        try:
            conn.execute("INSERT INTO agent_runs(ts_utc, agent, outcome, target, branch, detail) VALUES (?,?,?,?,?,?)",
                         (_now(), agent, outcome, target, branch, detail[:1000]))
            conn.commit()
        finally:
            conn.close()


def runs_today(*, db_path: Path | None = None) -> list[dict[str, Any]]:
    with _LOCK:
        conn = _connect(db_path)
        try:
            return [dict(r) for r in conn.execute(
                "SELECT * FROM agent_runs WHERE ts_utc >= ? ORDER BY id", (_today(),)).fetchall()]
        finally:
            conn.close()


def specs_today(*, db_path: Path | None = None) -> list[dict[str, Any]]:
    with _LOCK:
        conn = _connect(db_path)
        try:
            return [dict(r) for r in conn.execute(
                "SELECT * FROM specs WHERE updated_utc >= ? ORDER BY score DESC", (_today(),)).fetchall()]
        finally:
            conn.close()


# ── analysis ────────────────────────────────────────────────────────────────

def analyze_telemetry(*, telemetry_db: Path | None = None, repo_root: Path | None = None,
                      window_hours: float = 168.0) -> list[dict[str, Any]]:
    from core.evolution import telemetry
    root = Path(repo_root) if repo_root else _BASE
    out: list[dict[str, Any]] = []
    for tool, m in telemetry.tool_metrics(window_hours=window_hours, db_path=telemetry_db).items():
        calls = int(m.get("calls", 0))
        sr = float(m.get("success_rate", 1.0))
        p95 = float(m.get("p95_ms", 0.0))
        consec = int(m.get("consecutive_failures", 0))
        if calls < 2 or (sr >= 0.9 and p95 < 5000 and consec < 2):
            continue
        fails = telemetry.recent_failures(tool_name=tool, limit=3, db_path=telemetry_db)
        kind = "core_fix"
        try:
            from core.evolution.meta_optimizer import assert_optimizable_target
            ok, rel, _err = assert_optimizable_target(root, tool)
            if ok and (root / rel).is_file():
                kind = "plugin_fix"
        except Exception:
            pass
        score = round((1.0 - sr) * calls * 10 + min(p95 / 1000.0, 20.0) + consec * 3, 2)
        problem = []
        if sr < 0.9:
            problem.append(f"success {sr:.0%} over {calls} calls")
        if p95 >= 5000:
            problem.append(f"slow p95 {p95 / 1000:.1f}s")
        if consec >= 2:
            problem.append(f"{consec} consecutive failures")
        out.append({
            "kind": kind, "target": tool, "score": score,
            "title": f"Fix {tool}: {', '.join(problem)}",
            "evidence": {"metrics": m, "failures": [
                {"error": str(f.get("error_snippet") or "")[:200], "args": str(f.get("args_json") or f.get("args") or "")[:200]}
                for f in fails]},
        })
    try:
        for (cat, model), m in telemetry.route_metrics(window_hours=window_hours, db_path=telemetry_db).items():
            calls, sr = int(m.get("calls", 0)), float(m.get("success_rate", 1.0))
            if calls >= 3 and sr < 0.7:
                out.append({"kind": "routing", "target": f"{cat}:{model}",
                            "score": round((1.0 - sr) * calls * 2, 2),
                            "title": f"Route {cat} via {model} fails {1 - sr:.0%} ({calls} calls)",
                            "evidence": {"metrics": m}})
    except Exception:
        pass
    return out


def analyze_corrections(*, conversations_db: Path | None = None, limit: int = 400) -> list[dict[str, Any]]:
    try:
        from core.memory import conversation_store as cs
        turns = cs.recent_turns(limit, db_path=conversations_db)
    except Exception:
        return []
    examples = []
    for i, t in enumerate(turns):
        if t["role"] == "user" and _CORRECTION_RX.search(t["text"].strip()):
            prev = next((p["text"] for p in reversed(turns[:i]) if p["role"] == "assistant"), "")
            examples.append({"user": t["text"][:200], "after_assistant": prev[:200]})
    if len(examples) < 2:
        return []
    return [{"kind": "behavior", "target": "user_corrections", "score": float(len(examples) * 2),
             "title": f"User corrected Jarvis {len(examples)}x recently",
             "evidence": {"examples": examples[-8:]}}]


def web_research(spec: dict[str, Any], *, db_path: Path | None = None,
                 search_fn: Callable[[str, int], list[dict]] | None = None) -> list[dict[str, str]]:
    fails = (spec.get("evidence") or {}).get("failures") or []
    err = next((f["error"] for f in fails if f.get("error")), "")
    if not err or not can_spend("web_queries", 1, db_path=db_path):
        return []
    charge("web_queries", 1, db_path=db_path)
    query = "python " + re.sub(r"[^\w\s.:-]", " ", err)[:90]
    try:
        if search_fn is None:
            from actions.web_search import _ddg_search as search_fn  # type: ignore[no-redef]
        hits = search_fn(query, 3) or []
    except Exception:
        return []
    return [{"title": str(h.get("title") or "")[:120], "url": str(h.get("url") or "")[:200]} for h in hits[:3]]


def run_research_cycle(*, force: bool = False, use_llm: bool = True, use_web: bool = True,
                       db_path: Path | None = None, telemetry_db: Path | None = None,
                       conversations_db: Path | None = None, repo_root: Path | None = None,
                       gate_overrides: dict[str, float] | None = None,
                       search_fn: Callable[[str, int], list[dict]] | None = None) -> dict[str, Any]:
    if not force:
        ok, why = check_gates(**(gate_overrides or {}))
        if not ok:
            return {"ran": False, "reason": why}
    found = analyze_telemetry(telemetry_db=telemetry_db, repo_root=repo_root)
    found += analyze_corrections(conversations_db=conversations_db)
    found.sort(key=lambda s: -s["score"])
    for spec in found[:2]:
        if use_web:
            refs = web_research(spec, db_path=db_path, search_fn=search_fn)
            if refs:
                spec["evidence"]["web_refs_untrusted"] = refs
    if use_llm and found:
        top = found[0]
        text, via = ask_llm(
            "You are Jarvis's research agent. In <=4 short lines, propose the most likely fix approach "
            f"for this problem. Problem: {top['title']}\nEvidence: "
            f"{json.dumps(top['evidence'], default=str)[:2500]}", db_path=db_path)
        if text:
            top["detail"] = f"[{via}] {text.strip()[:1500]}"
    ids = [upsert_spec(s["kind"], s["target"], s["title"], detail=s.get("detail", ""),
                       evidence=s["evidence"], score=s["score"], source="researcher", db_path=db_path)
           for s in found]
    log_run("researcher", "ok", detail=f"{len(ids)} spec(s): " + "; ".join(s["title"] for s in found[:3]),
            db_path=db_path)
    return {"ran": True, "specs": len(ids), "top": [s["title"] for s in found[:3]],
            "budget": budget_report(db_path=db_path)}
