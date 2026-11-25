"""Append-only cross-session conversation memory (`memory/conversations.sqlite`).

Every user and assistant turn is committed the moment it is transcribed
(WAL + synchronous=FULL), so nothing is lost on sleep, crash, restart or an
auto-upgrade. UPDATE/DELETE on history, summaries and facts are refused by
triggers: the store only ever grows (the engineer daemon never touches it,
and the file is gitignored, so merges cannot overwrite it).

`build_bootstrap_context()` is what a NEW Live session (or a wake from
SLEEPING) is primed with: last session summary + recent turns + top durable
facts + related Knowledge-Graph entities.
"""
from __future__ import annotations

import os
import re
import sqlite3
import sys
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

if getattr(sys, "frozen", False):
    _BASE = Path(sys.executable).parent
else:
    _BASE = Path(__file__).resolve().parent.parent.parent

DEFAULT_DB: Path = _BASE / "memory" / "conversations.sqlite"
_LOCK = threading.RLock()
_CURRENT: dict[str, str] = {}
ROLES = ("user", "assistant", "system")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS conversation_history (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL,
    ts_utc     TEXT NOT NULL,
    role       TEXT NOT NULL CHECK (role IN ('user','assistant','system')),
    text       TEXT NOT NULL,
    source     TEXT NOT NULL DEFAULT 'voice'
);
CREATE INDEX IF NOT EXISTS ix_hist_session ON conversation_history(session_id, id);
CREATE TABLE IF NOT EXISTS sessions (
    session_id  TEXT PRIMARY KEY,
    started_utc TEXT NOT NULL,
    ended_utc   TEXT NOT NULL DEFAULT '',
    end_reason  TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS session_summaries (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL,
    ts_utc     TEXT NOT NULL,
    summary    TEXT NOT NULL,
    method     TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS memory_facts (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    ts_utc     TEXT NOT NULL,
    session_id TEXT NOT NULL,
    kind       TEXT NOT NULL,
    subject    TEXT NOT NULL,
    value      TEXT NOT NULL,
    fact       TEXT NOT NULL,
    fact_key   TEXT NOT NULL UNIQUE,
    confidence REAL NOT NULL DEFAULT 0.8,
    method     TEXT NOT NULL DEFAULT 'rules'
);
CREATE INDEX IF NOT EXISTS ix_facts_subject ON memory_facts(kind, subject, id);
"""
_APPEND_ONLY = ("conversation_history", "session_summaries", "memory_facts")


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")[:-4] + "Z"


def resolve_db(db_path: Path | None = None) -> Path:
    if db_path is not None:
        return Path(db_path)
    env = os.environ.get("JARVIS_CONVERSATIONS_DB", "").strip()
    return Path(env) if env else DEFAULT_DB


def _connect(db_path: Path | None = None) -> sqlite3.Connection:
    path = resolve_db(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), timeout=10.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=FULL")
    conn.executescript(_SCHEMA)
    for tbl in _APPEND_ONLY:
        for op in ("UPDATE", "DELETE"):
            conn.execute(
                f"CREATE TRIGGER IF NOT EXISTS trg_{tbl}_no_{op.lower()} BEFORE {op} ON {tbl} "
                f"BEGIN SELECT RAISE(ABORT, '{tbl} is append-only'); END"
            )
    return conn


def _clean(text: str, limit: int = 4000) -> str:
    return " ".join(str(text or "").split())[:limit]


# ── sessions ────────────────────────────────────────────────────────────────

def start_session(reason: str = "startup", *, db_path: Path | None = None) -> str:
    sid = f"s{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}-{uuid.uuid4().hex[:6]}"
    with _LOCK:
        conn = _connect(db_path)
        try:
            conn.execute("INSERT INTO sessions(session_id, started_utc, end_reason) VALUES (?,?,?)",
                         (sid, _now(), ""))
            conn.commit()
        finally:
            conn.close()
        _CURRENT[str(resolve_db(db_path))] = sid
    _ = reason
    return sid


def current_session(*, db_path: Path | None = None, create: bool = True) -> str:
    key = str(resolve_db(db_path))
    with _LOCK:
        sid = _CURRENT.get(key, "")
    if not sid and create:
        sid = start_session("implicit", db_path=db_path)
    return sid


def end_session(session_id: str = "", reason: str = "", *, db_path: Path | None = None) -> None:
    sid = session_id or current_session(db_path=db_path, create=False)
    if not sid:
        return
    with _LOCK:
        conn = _connect(db_path)
        try:
            conn.execute("UPDATE sessions SET ended_utc=?, end_reason=? WHERE session_id=?",
                         (_now(), str(reason)[:80], sid))
            conn.commit()
        finally:
            conn.close()
        key = str(resolve_db(db_path))
        if _CURRENT.get(key) == sid:
            _CURRENT.pop(key, None)


# ── turns ───────────────────────────────────────────────────────────────────

def append_turn(role: str, text: str, *, session_id: str = "", source: str = "voice",
                db_path: Path | None = None) -> int:
    """Persist one turn immediately. Returns its row id (0 when skipped)."""
    role = str(role or "").strip().lower()
    role = {"jarvis": "assistant", "model": "assistant", "you": "user"}.get(role, role)
    body = _clean(text)
    if role not in ROLES or not body:
        return 0
    sid = session_id or current_session(db_path=db_path)
    with _LOCK:
        conn = _connect(db_path)
        try:
            cur = conn.execute(
                "INSERT INTO conversation_history(session_id, ts_utc, role, text, source) VALUES (?,?,?,?,?)",
                (sid, _now(), role, body, str(source)[:24]),
            )
            conn.commit()
            return int(cur.lastrowid or 0)
        finally:
            conn.close()


def session_turns(session_id: str, *, db_path: Path | None = None) -> list[dict[str, Any]]:
    with _LOCK:
        conn = _connect(db_path)
        try:
            rows = conn.execute(
                "SELECT id, session_id, ts_utc, role, text FROM conversation_history "
                "WHERE session_id=? ORDER BY id", (session_id,)).fetchall()
        finally:
            conn.close()
    return [dict(r) for r in rows]


def recent_turns(limit: int = 12, *, exclude_session: str = "", db_path: Path | None = None) -> list[dict[str, Any]]:
    with _LOCK:
        conn = _connect(db_path)
        try:
            rows = conn.execute(
                "SELECT id, session_id, ts_utc, role, text FROM conversation_history "
                "WHERE session_id != ? ORDER BY id DESC LIMIT ?",
                (exclude_session or "", max(1, int(limit)))).fetchall()
        finally:
            conn.close()
    return [dict(r) for r in reversed(rows)]


def sessions_needing_summary(*, exclude_session: str = "", db_path: Path | None = None) -> list[str]:
    """Sessions with turns but no summary - e.g. the one a crash interrupted."""
    with _LOCK:
        conn = _connect(db_path)
        try:
            rows = conn.execute(
                "SELECT DISTINCT h.session_id FROM conversation_history h "
                "LEFT JOIN session_summaries s ON s.session_id = h.session_id "
                "WHERE s.id IS NULL AND h.session_id != ? ORDER BY h.id", (exclude_session or "",)).fetchall()
        finally:
            conn.close()
    return [r[0] for r in rows]


# ── summaries & facts ───────────────────────────────────────────────────────

def add_summary(session_id: str, summary: str, *, method: str = "", db_path: Path | None = None) -> int:
    body = _clean(summary, 1200)
    if not body:
        return 0
    with _LOCK:
        conn = _connect(db_path)
        try:
            cur = conn.execute(
                "INSERT INTO session_summaries(session_id, ts_utc, summary, method) VALUES (?,?,?,?)",
                (session_id, _now(), body, method[:24]))
            conn.commit()
            return int(cur.lastrowid or 0)
        finally:
            conn.close()


def last_session_summary(*, exclude_session: str = "", db_path: Path | None = None) -> dict[str, Any] | None:
    with _LOCK:
        conn = _connect(db_path)
        try:
            row = conn.execute(
                "SELECT session_id, ts_utc, summary FROM session_summaries WHERE session_id != ? "
                "ORDER BY id DESC LIMIT 1", (exclude_session or "",)).fetchone()
        finally:
            conn.close()
    return dict(row) if row else None


def _fact_key(kind: str, subject: str, value: str) -> str:
    norm = lambda s: re.sub(r"[^a-z0-9]+", " ", str(s).lower()).strip()  # noqa: E731
    return f"{norm(kind)}|{norm(subject)}|{norm(value)}"


def add_facts(facts: list[dict[str, Any]], *, session_id: str = "", method: str = "rules",
              db_path: Path | None = None) -> int:
    """Insert new durable facts (duplicates ignored). Returns how many were new."""
    sid = session_id or current_session(db_path=db_path)
    added = 0
    with _LOCK:
        conn = _connect(db_path)
        try:
            for f in facts or []:
                kind = _clean(f.get("kind") or "note", 40).lower()
                subject = _clean(f.get("subject") or "", 120)
                value = _clean(f.get("value") or "", 300)
                fact = _clean(f.get("fact") or f"{subject}: {value}", 400)
                if not (subject or value):
                    continue
                cur = conn.execute(
                    "INSERT OR IGNORE INTO memory_facts(ts_utc, session_id, kind, subject, value, fact, "
                    "fact_key, confidence, method) VALUES (?,?,?,?,?,?,?,?,?)",
                    (_now(), sid, kind, subject, value, fact, _fact_key(kind, subject, value),
                     float(f.get("confidence") or 0.8), str(f.get("method") or method)[:24]))
                added += int(cur.rowcount or 0)
            conn.commit()
        finally:
            conn.close()
    return added


def latest_facts(limit: int = 30, *, db_path: Path | None = None) -> list[dict[str, Any]]:
    """Newest fact per (kind, subject): a later 'deadline is Monday' supersedes 'Friday'."""
    with _LOCK:
        conn = _connect(db_path)
        try:
            rows = conn.execute(
                "SELECT f.* FROM memory_facts f JOIN (SELECT kind, lower(subject) AS s, MAX(id) AS mid "
                "FROM memory_facts GROUP BY kind, lower(subject)) g ON f.id = g.mid "
                "ORDER BY f.id DESC LIMIT ?", (max(1, int(limit)),)).fetchall()
        finally:
            conn.close()
    return [dict(r) for r in rows]


def search_facts(query: str, k: int = 8, *, db_path: Path | None = None) -> list[dict[str, Any]]:
    toks = {t for t in re.split(r"[^a-z0-9]+", str(query or "").lower()) if len(t) >= 3}
    facts = latest_facts(200, db_path=db_path)
    if not toks:
        return facts[:k]
    scored = []
    for f in facts:
        hay = f"{f['kind']} {f['subject']} {f['value']} {f['fact']}".lower()
        score = sum(1 for t in toks if t in hay)
        if score:
            scored.append((score, f["id"], f))
    scored.sort(key=lambda x: (-x[0], -x[1]))
    return [f for _s, _i, f in scored[:k]]


def stats(*, db_path: Path | None = None) -> dict[str, int]:
    with _LOCK:
        conn = _connect(db_path)
        try:
            return {t: int(conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0])
                    for t in ("conversation_history", "sessions", "session_summaries", "memory_facts")}
        finally:
            conn.close()


# ── bootstrap ───────────────────────────────────────────────────────────────

def build_bootstrap_context(*, query_hint: str = "", exclude_session: str = "", max_turns: int = 10,
                            max_facts: int = 15, max_chars: int = 3500, include_graph: bool = True,
                            db_path: Path | None = None, graph_db_path: Path | None = None) -> str:
    """The memory block injected into the system prompt of every new session."""
    try:
        last = last_session_summary(exclude_session=exclude_session, db_path=db_path)
        turns = recent_turns(max_turns, exclude_session=exclude_session, db_path=db_path)
        facts = latest_facts(max_facts, db_path=db_path)
        if query_hint:
            seen = {f["id"] for f in facts}
            facts += [f for f in search_facts(query_hint, 5, db_path=db_path) if f["id"] not in seen]
    except Exception:
        return ""
    if not (last or turns or facts):
        return ""
    lines = ["[CROSS-SESSION MEMORY - you remember this from earlier conversations with the user; "
             "use it naturally, never read it out as a list, never say you lack memory of it]"]
    if facts:
        lines.append("Durable facts about the user:")
        lines += [f"- {f['fact']}" for f in facts]
    if last:
        lines.append(f"Last session summary ({last['ts_utc'][:10]}): {last['summary']}")
    if turns:
        lines.append("Most recent turns before this session (oldest first):")
        for t in turns:
            who = "User" if t["role"] == "user" else "You"
            lines.append(f"  {who}: {t['text'][:220]}")
    if include_graph:
        hint = query_hint or " ".join(f["subject"] + " " + f["value"] for f in facts[:6])
        try:
            from core.memory import knowledge_graph as kg
            sub = kg.query_graph(hint, max_hops=1, top_k=6, db_path=graph_db_path)
            ents = [e for e in (sub.get("nodes") or []) if isinstance(e, dict)][:6]
            if ents:
                lines.append("Related knowledge-graph entities: " + "; ".join(
                    f"{e.get('entity_type', '')}:{e.get('name', '')}"
                    + (f" ({str(e.get('summary'))[:60]})" if e.get("summary") else "")
                    for e in ents))
        except Exception:
            pass
    block = "\n".join(lines)
    return block[:max_chars] + "\n"
