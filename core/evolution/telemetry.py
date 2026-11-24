"""core/evolution/telemetry.py — SQLite WAL Telemetry & Failure Immunity Store.

Captures every tool execution and model route attempt (latency, success/failure,
error snippet, full traceback) in git-ignored `memory/telemetry.sqlite`.
Provides real P50/P95 latency, success-rate, recent-failure, recurring-lesson,
and sequential-tool-chain queries for Adaptive Routing, Self-Healing, Habit
Compilation, and Replay Self-Benchmarking.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
import math
from pathlib import Path
import sqlite3
import sys
import threading

if getattr(sys, "frozen", False):
    _BASE = Path(sys.executable).parent
else:
    _BASE = Path(__file__).resolve().parent.parent.parent

DEFAULT_DB_PATH: Path = _BASE / "memory" / "telemetry.sqlite"
_LOCK = threading.Lock()

FAILURE_SIGNALS: tuple[str, ...] = (
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


def _resolve_db(db_path: Path | None = None) -> Path:
    return Path(db_path) if db_path is not None else DEFAULT_DB_PATH


def _connect(db_path: Path | None = None) -> sqlite3.Connection:
    path = _resolve_db(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), timeout=15.0)
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA synchronous = NORMAL")
    _ensure_schema(conn)
    return conn


def _ensure_schema(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS tool_events (
            id             INTEGER PRIMARY KEY AUTOINCREMENT,
            ts_utc         TEXT NOT NULL,
            session_id     TEXT NOT NULL,
            tool_name      TEXT NOT NULL,
            action         TEXT NOT NULL,
            args_json      TEXT NOT NULL,
            success        INTEGER NOT NULL,
            latency_ms     REAL NOT NULL,
            error_type     TEXT NOT NULL,
            error_snippet  TEXT NOT NULL,
            traceback      TEXT NOT NULL,
            result_snippet TEXT NOT NULL
        )
        """
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_tool_events_name_ts ON tool_events(tool_name, ts_utc)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_tool_events_sess_id ON tool_events(session_id, id)")
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS route_events (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            ts_utc        TEXT NOT NULL,
            category      TEXT NOT NULL,
            model         TEXT NOT NULL,
            provider      TEXT NOT NULL,
            success       INTEGER NOT NULL,
            latency_ms    REAL NOT NULL,
            escalated     INTEGER NOT NULL,
            error_snippet TEXT NOT NULL
        )
        """
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_route_events_cat_mod ON route_events(category, model, ts_utc)")


def init_db(db_path: Path | None = None) -> Path:
    path = _resolve_db(db_path)
    with _LOCK:
        conn = _connect(path)
        try:
            conn.commit()
        finally:
            conn.close()
    return path


def _now_utc_iso(ts_utc: str | None = None) -> str:
    if ts_utc:
        return str(ts_utc)
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def _cutoff_iso(window_hours: float) -> str:
    dt = datetime.now(timezone.utc) - timedelta(hours=max(0.01, float(window_hours)))
    return dt.strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def _percentile(values: list[float], q: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    idx = max(0, min(len(ordered) - 1, math.ceil(q * len(ordered)) - 1))
    return round(float(ordered[idx]), 2)


def _infer_success(result: str, error_type: str, tb: str) -> bool:
    if error_type or tb:
        return False
    res = str(result or "")
    if not res:
        return True
    try:
        from core import outcome
        if outcome.is_problem(res):
            return False
    except Exception:
        pass
    low = res.lower()
    if any(sig in low for sig in FAILURE_SIGNALS):
        return False
    return True


def _sanitize_args(args: dict | None) -> str:
    if not isinstance(args, dict):
        return "{}"
    redacted: dict[str, object] = {}
    for k, v in args.items():
        lk = str(k).lower()
        if any(sec in lk for sec in ("password", "secret", "token", "api_key", "key")):
            redacted[str(k)] = "[REDACTED]"
        else:
            redacted[str(k)] = str(v)[:180] if not isinstance(v, (int, float, bool, type(None))) else v
    try:
        return json.dumps(redacted, ensure_ascii=False)[:1000]
    except Exception:
        return "{}"


def record_tool_event(
    tool_name: str,
    args: dict | None,
    result: str,
    latency_ms: float,
    *,
    success: bool | None = None,
    error_type: str = "",
    tb: str = "",
    session_id: str = "default",
    ts_utc: str | None = None,
    db_path: Path | None = None,
) -> None:
    """Record a single tool execution telemetry row. Never raises."""
    try:
        ok = _infer_success(result, error_type, tb) if success is None else bool(success)
        action = ""
        if isinstance(args, dict):
            action = str(args.get("action") or args.get("mode") or "").strip().lower()[:64]
        res_str = str(result or "")
        err_snip = "" if ok else (str(error_type or "") + " " + res_str).strip()[:240]
        row = (
            _now_utc_iso(ts_utc),
            str(session_id or "default")[:64],
            str(tool_name or "unknown")[:80],
            action,
            _sanitize_args(args),
            1 if ok else 0,
            round(max(0.0, float(latency_ms or 0.0)), 2),
            str(error_type or "")[:80],
            err_snip,
            str(tb or "")[:2000],
            res_str[:240],
        )
        with _LOCK:
            conn = _connect(db_path)
            try:
                conn.execute(
                    """
                    INSERT INTO tool_events(
                        ts_utc, session_id, tool_name, action, args_json,
                        success, latency_ms, error_type, error_snippet, traceback, result_snippet
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    row,
                )
                conn.commit()
            finally:
                conn.close()
    except Exception:
        pass


def record_route_event(
    category: str,
    model: str,
    provider: str,
    success: bool,
    latency_ms: float,
    *,
    escalated: bool = False,
    error_snippet: str = "",
    ts_utc: str | None = None,
    db_path: Path | None = None,
) -> None:
    """Record a model routing outcome row. Never raises."""
    try:
        row = (
            _now_utc_iso(ts_utc),
            str(category or "fast")[:32],
            str(model or "unknown")[:120],
            str(provider or "ollama")[:48],
            1 if success else 0,
            round(max(0.0, float(latency_ms or 0.0)), 2),
            1 if escalated else 0,
            str(error_snippet or "")[:240],
        )
        with _LOCK:
            conn = _connect(db_path)
            try:
                conn.execute(
                    """
                    INSERT INTO route_events(
                        ts_utc, category, model, provider, success,
                        latency_ms, escalated, error_snippet
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    row,
                )
                conn.commit()
            finally:
                conn.close()
    except Exception:
        pass


def tool_metrics(
    *,
    tool_name: str | None = None,
    window_hours: float = 168.0,
    db_path: Path | None = None,
) -> dict[str, dict]:
    """Compute per-tool calls, success_rate, consecutive_failures, p50_ms, and p95_ms."""
    cutoff = _cutoff_iso(window_hours)
    with _LOCK:
        conn = _connect(db_path)
        try:
            if tool_name:
                rows = conn.execute(
                    "SELECT tool_name, success, latency_ms, error_snippet, ts_utc "
                    "FROM tool_events WHERE ts_utc >= ? AND tool_name = ? ORDER BY id ASC",
                    (cutoff, str(tool_name)),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT tool_name, success, latency_ms, error_snippet, ts_utc "
                    "FROM tool_events WHERE ts_utc >= ? ORDER BY id ASC",
                    (cutoff,),
                ).fetchall()
        finally:
            conn.close()

    grouped: dict[str, list[tuple[int, float, str, str]]] = {}
    for tname, succ, lat, err, ts in rows:
        grouped.setdefault(tname, []).append((int(succ), float(lat), str(err), str(ts)))

    out: dict[str, dict] = {}
    for tname, items in grouped.items():
        calls = len(items)
        successes = sum(1 for x in items if x[0] == 1)
        failures = calls - successes
        lats = [x[1] for x in items]
        consec = 0
        for succ, _, _, _ in reversed(items):
            if succ == 0:
                consec += 1
            else:
                break
        last_err = ""
        for succ, _, err, _ in reversed(items):
            if succ == 0 and err:
                last_err = err
                break
        out[tname] = {
            "calls": calls,
            "successes": successes,
            "failures": failures,
            "success_rate": round(successes / calls, 4) if calls else 1.0,
            "consecutive_failures": consec,
            "p50_ms": _percentile(lats, 0.50),
            "p95_ms": _percentile(lats, 0.95),
            "last_error": last_err,
            "last_ts": items[-1][3] if items else "",
        }
    return out


def route_metrics(
    *,
    category: str | None = None,
    window_hours: float = 168.0,
    db_path: Path | None = None,
) -> dict[tuple[str, str], dict]:
    """Compute `(category, model)` metrics for Adaptive Hybrid Routing."""
    cutoff = _cutoff_iso(window_hours)
    with _LOCK:
        conn = _connect(db_path)
        try:
            if category:
                rows = conn.execute(
                    "SELECT category, model, success, latency_ms, escalated "
                    "FROM route_events WHERE ts_utc >= ? AND category = ? ORDER BY id ASC",
                    (cutoff, str(category)),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT category, model, success, latency_ms, escalated "
                    "FROM route_events WHERE ts_utc >= ? ORDER BY id ASC",
                    (cutoff,),
                ).fetchall()
        finally:
            conn.close()

    grouped: dict[tuple[str, str], list[tuple[int, float, int]]] = {}
    for cat, mod, succ, lat, esc in rows:
        grouped.setdefault((cat, mod), []).append((int(succ), float(lat), int(esc)))

    out: dict[tuple[str, str], dict] = {}
    for key, items in grouped.items():
        calls = len(items)
        successes = sum(1 for x in items if x[0] == 1)
        escalations = sum(x[2] for x in items)
        lats = [x[1] for x in items]
        out[key] = {
            "calls": calls,
            "successes": successes,
            "failures": calls - successes,
            "success_rate": round(successes / calls, 4) if calls else 1.0,
            "p50_ms": _percentile(lats, 0.50),
            "p95_ms": _percentile(lats, 0.95),
            "escalations": escalations,
        }
    return out


def recent_failures(
    *,
    tool_name: str | None = None,
    limit: int = 5,
    db_path: Path | None = None,
) -> list[dict]:
    """Return latest failed tool executions (used by Self-Healer and Replay Benchmark)."""
    lim = max(1, min(50, int(limit or 5)))
    with _LOCK:
        conn = _connect(db_path)
        try:
            if tool_name:
                rows = conn.execute(
                    "SELECT id, ts_utc, tool_name, action, args_json, error_type, error_snippet, traceback "
                    "FROM tool_events WHERE success = 0 AND tool_name = ? ORDER BY id DESC LIMIT ?",
                    (str(tool_name), lim),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT id, ts_utc, tool_name, action, args_json, error_type, error_snippet, traceback "
                    "FROM tool_events WHERE success = 0 ORDER BY id DESC LIMIT ?",
                    (lim,),
                ).fetchall()
        finally:
            conn.close()

    out: list[dict] = []
    for rid, ts, tname, action, args_json, etype, esnip, tb in rows:
        try:
            args_dict = json.loads(args_json)
            if not isinstance(args_dict, dict):
                args_dict = {}
        except Exception:
            args_dict = {}
        out.append({
            "id": int(rid),
            "ts_utc": ts,
            "tool_name": tname,
            "action": action,
            "args": args_dict,
            "args_json": args_json,
            "error_type": etype,
            "error_snippet": esnip,
            "traceback": tb,
        })
    return out


def recurring_failure_lessons(
    *,
    min_count: int = 2,
    limit: int = 8,
    window_hours: float = 168.0,
    db_path: Path | None = None,
) -> list[dict]:
    """Summarize recurring tool failure patterns for Failure Immunity prompt injection."""
    cutoff = _cutoff_iso(window_hours)
    with _LOCK:
        conn = _connect(db_path)
        try:
            rows = conn.execute(
                """
                SELECT tool_name, action, error_snippet, COUNT(*) AS cnt, MAX(ts_utc) AS last_ts
                FROM tool_events
                WHERE success = 0 AND ts_utc >= ?
                GROUP BY tool_name, action, SUBSTR(error_snippet, 1, 90)
                HAVING COUNT(*) >= ?
                ORDER BY cnt DESC, last_ts DESC
                LIMIT ?
                """,
                (cutoff, max(1, int(min_count)), max(1, min(30, int(limit)))),
            ).fetchall()
        finally:
            conn.close()

    return [
        {
            "tool_name": r[0],
            "action": r[1],
            "error_snippet": r[2],
            "count": int(r[3]),
            "last_ts": r[4],
        }
        for r in rows
    ]


def recent_tool_sequence(
    *,
    limit: int = 300,
    window_hours: float = 168.0,
    db_path: Path | None = None,
) -> list[dict]:
    """Return chronological recent successful tool invocations for Habit Compiler mining."""
    cutoff = _cutoff_iso(window_hours)
    lim = max(1, min(2000, int(limit or 300)))
    with _LOCK:
        conn = _connect(db_path)
        try:
            rows = conn.execute(
                """
                SELECT id, ts_utc, session_id, tool_name, action, args_json, success, latency_ms
                FROM tool_events
                WHERE ts_utc >= ?
                ORDER BY id DESC
                LIMIT ?
                """,
                (cutoff, lim),
            ).fetchall()
        finally:
            conn.close()

    out: list[dict] = []
    for rid, ts, sess, tname, action, args_json, succ, lat in reversed(rows):
        try:
            args_dict = json.loads(args_json)
            if not isinstance(args_dict, dict):
                args_dict = {}
        except Exception:
            args_dict = {}
        out.append({
            "id": int(rid),
            "ts_utc": ts,
            "session_id": sess,
            "tool_name": tname,
            "action": action,
            "args": args_dict,
            "success": bool(succ),
            "latency_ms": float(lat),
        })
    return out
