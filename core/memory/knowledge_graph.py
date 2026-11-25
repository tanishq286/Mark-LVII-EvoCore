"""core/memory/knowledge_graph.py — Local SQLite Entity-Relation Knowledge Graph.

Upgrades flat RAG into a persistent SQLite WAL Entity-Relation Knowledge Graph
tracking people, projects, file paths, preferences, recurring tasks, tools, and
past outcomes. Auto-updates from live conversations and `long_term.json` updates,
supports multi-hop (1-3 hop) relational reasoning across weeks of context, and
consolidates duplicate/alias entities during PC idle reflection cycles.
"""
from __future__ import annotations

from collections import deque
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import re
import sqlite3
import sys
import threading
from typing import Any

if getattr(sys, "frozen", False):
    _BASE = Path(sys.executable).parent
else:
    _BASE = Path(__file__).resolve().parent.parent.parent

DEFAULT_GRAPH_DB: Path = _BASE / "memory" / "knowledge_graph.sqlite"
_LOCK = threading.RLock()

ENTITY_TYPES: tuple[str, ...] = (
    "person",
    "project",
    "file_path",
    "preference",
    "recurring_task",
    "past_outcome",
    "tool",
    "concept",
)

_STOPWORDS: frozenset[str] = frozenset({
    "the", "and", "for", "with", "that", "this", "from", "into", "your", "have",
    "what", "when", "where", "which", "who", "whom", "whose", "why", "how",
    "about", "after", "before", "during", "under", "over", "again", "please",
    "jarvis", "sir", "today", "yesterday", "tomorrow", "just", "make", "made",
    "will", "would", "could", "should", "can", "does", "did", "done", "was",
    "were", "been", "being", "there", "their", "they", "them", "then", "than",
})

_FILE_PATH_RE = re.compile(
    r"(?:[A-Za-z]:\\[A-Za-z0-9_.\-\\]+|"
    r"(?:/home/[A-Za-z0-9_.\-/]+)|"
    r"(?:~\/[A-Za-z0-9_.\-/]+)|"
    r"\b(?:[A-Za-z0-9_\-]+/)+[A-Za-z0-9_.\-]+\.(?:py|kt|kts|ts|tsx|js|jsx|json|md|ps1|sh|sql|yaml|yml|toml|html|css|sqlite)\b|"
    r"\b[A-Za-z0-9_\-]+\.(?:py|kt|ts|tsx|js|ps1|sql|sqlite)\b)"
)

_PERSON_REL_RE = re.compile(
    r"\b(?:[Mm]y\s+(?:sister|brother|mother|mom|father|dad|friend|partner|wife|husband|colleague|teammate|manager|mentor|client|cofounder)|"
    r"[Cc]olleague|[Tt]eammate|[Cc]lient|[Pp]artner)\s+([A-Z][a-z]{1,24}(?:\s+[A-Z][a-z]{1,24})?)\b"
)
_PERSON_WITH_RE = re.compile(
    r"\b(?:[Ww]ith|[Aa]sk|[Tt]ell|[Mm]essage|[Ee]mail|[Cc]all|[Pp]ing|[Aa]ssigned\s+to|[Ff]rom)\s+([A-Z][a-z]{1,22}(?:\s+[A-Z][a-z]{1,22})?)\b"
)
_NON_PERSON_CAPS: frozenset[str] = frozenset({
    "Jarvis", "Sir", "Windows", "Linux", "Ubuntu", "Python", "Android", "Chrome",
    "GitHub", "Google", "Notion", "Vercel", "Supabase", "Docker", "Ollama",
    "Gemini", "Claude", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday",
    "Saturday", "Sunday", "January", "February", "March", "April", "May", "June",
    "July", "August", "September", "October", "November", "December", "Mark",
    "Milestone", "OmniCore", "EvoCore", "Tailscale", "Cloudflare", "PowerShell",
})

_PROJECT_RE = re.compile(
    r"\b(?:project|repo|repository|codebase|app|platform|venture|working\s+on|building|shipping|deploying)\s+"
    r"([A-Za-z0-9][A-Za-z0-9_\-]{1,38})"
)
_KNOWN_REPO_SLUG_RE = re.compile(
    r"\b(Mark-LVII|client-engine|tq-forge|recoveryos-app|Addzify|Finsol|GateGuard|OmniCore)\b",
    re.IGNORECASE,
)

_PREFERENCE_RE = re.compile(
    r"\b(?:i\s+(?:always\s+)?prefer|always\s+use|never\s+use|i\s+like\s+to\s+use|my\s+default\s+is|please\s+always)\s+"
    r"([^.!?\n]{4,110})",
    re.IGNORECASE,
)

_RECURRING_RE = re.compile(
    r"\b((?:every\s+(?:morning|night|evening|day|week|monday|tuesday|wednesday|thursday|friday|saturday|sunday|hour)|"
    r"daily|weekly|nightly)\s+[^.!?\n]{4,110})",
    re.IGNORECASE,
)

_OUTCOME_RE = re.compile(
    r"\b((?:deployed|merged|fixed|resolved|shipped|completed|passed|failed|reverted|rolled\s+back|verified)\s+[^.!?\n]{4,120})",
    re.IGNORECASE,
)


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _resolve_db(db_path: Path | None = None) -> Path:
    return Path(db_path) if db_path is not None else DEFAULT_GRAPH_DB


def _canonical_slug(value: str) -> str:
    text = str(value or "").strip()
    if "/" in text or "\\" in text:
        norm_path = text.replace("\\", "/").strip().lower()
        norm_path = re.sub(r"[^a-z0-9_./:\-]+", "_", norm_path)
        return norm_path[:120].strip("_") or "unknown"
    slug = re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")
    return slug[:96] or "unknown"


def make_entity_id(entity_type: str, name_or_key: str) -> str:
    etype = str(entity_type or "concept").strip().lower()
    if etype not in ENTITY_TYPES:
        etype = "concept"
    return f"{etype}:{_canonical_slug(name_or_key)}"


def _connect(db_path: Path | None = None) -> sqlite3.Connection:
    path = _resolve_db(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), timeout=15.0)
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA synchronous = NORMAL")
    _ensure_schema(conn)
    return conn


def _ensure_schema(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS entities (
            entity_id       TEXT PRIMARY KEY,
            entity_type     TEXT NOT NULL,
            name            TEXT NOT NULL,
            canonical_key   TEXT NOT NULL,
            summary         TEXT NOT NULL DEFAULT '',
            attributes_json TEXT NOT NULL DEFAULT '{}',
            confidence      REAL NOT NULL DEFAULT 0.85,
            mention_count   INTEGER NOT NULL DEFAULT 1,
            first_seen      TEXT NOT NULL,
            last_seen       TEXT NOT NULL
        )
        """
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_entities_type ON entities(entity_type)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_entities_key ON entities(canonical_key)")
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS relations (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            source_id     TEXT NOT NULL REFERENCES entities(entity_id) ON DELETE CASCADE,
            relation_type TEXT NOT NULL,
            target_id     TEXT NOT NULL REFERENCES entities(entity_id) ON DELETE CASCADE,
            weight        REAL NOT NULL DEFAULT 1.0,
            evidence      TEXT NOT NULL DEFAULT '',
            first_seen    TEXT NOT NULL,
            last_seen     TEXT NOT NULL,
            UNIQUE(source_id, relation_type, target_id)
        )
        """
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_rel_source ON relations(source_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_rel_target ON relations(target_id)")
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS observations (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            ts_utc        TEXT NOT NULL,
            source        TEXT NOT NULL,
            snippet       TEXT NOT NULL,
            entities_json TEXT NOT NULL
        )
        """
    )


def init_db(db_path: Path | None = None) -> Path:
    path = _resolve_db(db_path)
    with _LOCK:
        conn = _connect(path)
        try:
            conn.commit()
        finally:
            conn.close()
    return path


def _upsert_entity_conn(
    conn: sqlite3.Connection,
    entity_type: str,
    name: str,
    *,
    summary: str = "",
    attributes: dict[str, Any] | None = None,
    confidence: float = 0.85,
    aliases: list[str] | None = None,
    now_iso: str | None = None,
) -> dict[str, Any]:
    etype = str(entity_type or "concept").strip().lower()
    if etype not in ENTITY_TYPES:
        etype = "concept"
    clean_name = " ".join(str(name or "").split()).strip()[:160] or "unknown"
    ckey = _canonical_slug(clean_name)
    eid = f"{etype}:{ckey}"
    ts = now_iso or _now_iso()
    conf = max(0.05, min(1.0, float(confidence)))
    clean_sum = " ".join(str(summary or "").split()).strip()[:500]

    row = conn.execute(
        "SELECT name, summary, attributes_json, confidence, mention_count, first_seen "
        "FROM entities WHERE entity_id = ?",
        (eid,),
    ).fetchone()

    incoming_attrs = dict(attributes or {})
    incoming_aliases = [str(a).strip() for a in (aliases or []) if str(a).strip()]

    if row is None:
        if incoming_aliases:
            incoming_attrs["aliases"] = sorted(set(incoming_aliases))
        conn.execute(
            """
            INSERT INTO entities(
                entity_id, entity_type, name, canonical_key, summary,
                attributes_json, confidence, mention_count, first_seen, last_seen
            ) VALUES (?, ?, ?, ?, ?, ?, ?, 1, ?, ?)
            """,
            (
                eid,
                etype,
                clean_name,
                ckey,
                clean_sum,
                json.dumps(incoming_attrs, ensure_ascii=False),
                conf,
                ts,
                ts,
            ),
        )
        return {
            "entity_id": eid,
            "entity_type": etype,
            "name": clean_name,
            "summary": clean_sum,
            "mention_count": 1,
        }

    old_name, old_sum, old_attrs_raw, old_conf, old_cnt, first_seen = row
    try:
        merged_attrs = json.loads(old_attrs_raw) if old_attrs_raw else {}
        if not isinstance(merged_attrs, dict):
            merged_attrs = {}
    except Exception:
        merged_attrs = {}

    prev_aliases = set(merged_attrs.get("aliases") or [])
    if old_name and old_name != clean_name:
        prev_aliases.add(str(old_name))
    prev_aliases.update(incoming_aliases)
    for k, v in incoming_attrs.items():
        if k != "aliases" and v is not None:
            merged_attrs[k] = v
    if prev_aliases:
        merged_attrs["aliases"] = sorted(prev_aliases)

    best_sum = clean_sum if len(clean_sum) >= len(old_sum or "") else str(old_sum or "")
    new_cnt = int(old_cnt or 1) + 1
    new_conf = min(1.0, max(float(old_conf or 0.5), conf) + 0.02)
    best_name = old_name if (old_name and len(old_name) >= len(clean_name)) else clean_name

    conn.execute(
        """
        UPDATE entities
        SET name = ?, summary = ?, attributes_json = ?, confidence = ?, mention_count = ?, last_seen = ?
        WHERE entity_id = ?
        """,
        (
            best_name,
            best_sum,
            json.dumps(merged_attrs, ensure_ascii=False),
            round(new_conf, 4),
            new_cnt,
            ts,
            eid,
        ),
    )
    return {
        "entity_id": eid,
        "entity_type": etype,
        "name": best_name,
        "summary": best_sum,
        "mention_count": new_cnt,
        "first_seen": first_seen,
    }


def _add_relation_conn(
    conn: sqlite3.Connection,
    source_id: str,
    relation_type: str,
    target_id: str,
    *,
    weight: float = 1.0,
    evidence: str = "",
    now_iso: str | None = None,
) -> dict[str, Any] | None:
    src = str(source_id or "").strip()
    tgt = str(target_id or "").strip()
    rel = _canonical_slug(relation_type or "related_to")
    if not src or not tgt or src == tgt:
        return None

    ts = now_iso or _now_iso()
    ev = " ".join(str(evidence or "").split()).strip()[:300]
    w = max(0.1, float(weight or 1.0))

    row = conn.execute(
        "SELECT id, weight, evidence, first_seen FROM relations "
        "WHERE source_id = ? AND relation_type = ? AND target_id = ?",
        (src, rel, tgt),
    ).fetchone()
    if row is None:
        conn.execute(
            """
            INSERT INTO relations(source_id, relation_type, target_id, weight, evidence, first_seen, last_seen)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (src, rel, tgt, round(w, 3), ev, ts, ts),
        )
        return {"source_id": src, "relation_type": rel, "target_id": tgt, "weight": round(w, 3)}

    rid, old_w, old_ev, _first = row
    new_w = round(min(50.0, float(old_w or 1.0) + w), 3)
    best_ev = ev if ev else str(old_ev or "")
    conn.execute(
        "UPDATE relations SET weight = ?, evidence = ?, last_seen = ? WHERE id = ?",
        (new_w, best_ev, ts, int(rid)),
    )
    return {"source_id": src, "relation_type": rel, "target_id": tgt, "weight": new_w}


def upsert_entity(
    entity_type: str,
    name: str,
    *,
    summary: str = "",
    attributes: dict[str, Any] | None = None,
    confidence: float = 0.85,
    aliases: list[str] | None = None,
    db_path: Path | None = None,
) -> dict[str, Any]:
    with _LOCK:
        conn = _connect(db_path)
        try:
            ent = _upsert_entity_conn(
                conn,
                entity_type,
                name,
                summary=summary,
                attributes=attributes,
                confidence=confidence,
                aliases=aliases,
            )
            conn.commit()
            return ent
        finally:
            conn.close()


def add_relation(
    source_id: str,
    relation_type: str,
    target_id: str,
    *,
    weight: float = 1.0,
    evidence: str = "",
    db_path: Path | None = None,
) -> dict[str, Any] | None:
    with _LOCK:
        conn = _connect(db_path)
        try:
            rel = _add_relation_conn(
                conn,
                source_id,
                relation_type,
                target_id,
                weight=weight,
                evidence=evidence,
            )
            conn.commit()
            return rel
        finally:
            conn.close()


def ingest_conversation_turn(
    user_text: str,
    assistant_text: str = "",
    *,
    tool_events: list[dict[str, Any]] | None = None,
    db_path: Path | None = None,
) -> dict[str, Any]:
    """Extract entities & relations from a conversation turn and update the SQLite graph."""
    u_txt = str(user_text or "").strip()
    a_txt = str(assistant_text or "").strip()
    combined = f"{u_txt}\n{a_txt}".strip()
    if not combined and not tool_events:
        return {"entities_touched": 0, "relations_touched": 0, "entity_ids": []}

    now_iso = _now_iso()
    ev_snip = " ".join(u_txt.split())[:220] or " ".join(a_txt.split())[:220]

    people: list[tuple[str, str]] = []
    projects: list[str] = []
    files: list[str] = []
    prefs: list[str] = []
    tasks: list[str] = []
    outcomes: list[str] = []
    tools_used: list[tuple[str, str]] = []

    for m in _FILE_PATH_RE.finditer(combined):
        fp = m.group(0).strip().rstrip(".,;:)")
        if len(fp) >= 4 and fp not in files:
            files.append(fp)

    for m in _KNOWN_REPO_SLUG_RE.finditer(combined):
        proj = m.group(1).strip()
        if proj not in projects:
            projects.append(proj)

    for m in _PROJECT_RE.finditer(u_txt):
        cand = m.group(1).strip(".,;:()\"'")
        if len(cand) >= 2 and cand.lower() not in _STOPWORDS and cand not in projects:
            projects.append(cand)

    for m in _PERSON_REL_RE.finditer(u_txt):
        pname = m.group(1).strip()
        role_hint = m.group(0).strip()
        if pname not in _NON_PERSON_CAPS:
            people.append((pname, role_hint))

    for m in _PERSON_WITH_RE.finditer(u_txt):
        pname = m.group(1).strip()
        if pname not in _NON_PERSON_CAPS and all(p[0] != pname for p in people):
            people.append((pname, f"Mentioned in conversation: {ev_snip[:120]}"))

    for m in _PREFERENCE_RE.finditer(u_txt):
        pref = m.group(1).strip(" .,;:")
        if len(pref) >= 4:
            prefs.append(pref)

    for m in _RECURRING_RE.finditer(u_txt):
        rt = m.group(1).strip(" .,;:")
        if len(rt) >= 6:
            tasks.append(rt)

    for m in _OUTCOME_RE.finditer(combined):
        oc = m.group(1).strip(" .,;:")
        if len(oc) >= 6:
            outcomes.append(oc)

    for tev in tool_events or []:
        if not isinstance(tev, dict):
            continue
        tname = str(tev.get("tool") or tev.get("tool_name") or "").strip()
        tsum = str(tev.get("summary") or tev.get("result") or "").strip()[:180]
        if tname:
            tools_used.append((tname, tsum))
            if tsum:
                outcomes.append(f"{tname}: {tsum}")

    touched_entities: list[str] = []
    rel_count = 0

    with _LOCK:
        conn = _connect(db_path)
        try:
            user_ent = _upsert_entity_conn(
                conn,
                "person",
                "User",
                summary="Primary Mark-LVII operator",
                confidence=1.0,
                now_iso=now_iso,
            )
            user_id = user_ent["entity_id"]

            proj_ids: list[str] = []
            for proj in projects[:6]:
                pent = _upsert_entity_conn(
                    conn,
                    "project",
                    proj,
                    summary=f"Project referenced in: {ev_snip}",
                    confidence=0.9,
                    now_iso=now_iso,
                )
                pid = pent["entity_id"]
                proj_ids.append(pid)
                touched_entities.append(pid)
                if _add_relation_conn(conn, user_id, "works_on", pid, evidence=ev_snip, now_iso=now_iso):
                    rel_count += 1

            person_ids: list[str] = []
            for pname, role_desc in people[:6]:
                pe = _upsert_entity_conn(
                    conn,
                    "person",
                    pname,
                    summary=role_desc,
                    confidence=0.88,
                    now_iso=now_iso,
                )
                pe_id = pe["entity_id"]
                person_ids.append(pe_id)
                touched_entities.append(pe_id)
                if _add_relation_conn(conn, user_id, "knows_person", pe_id, evidence=role_desc, now_iso=now_iso):
                    rel_count += 1
                for pid in proj_ids:
                    if _add_relation_conn(conn, pe_id, "collaborates_on", pid, evidence=ev_snip, now_iso=now_iso):
                        rel_count += 1

            file_ids: list[str] = []
            for fp in files[:8]:
                fe = _upsert_entity_conn(
                    conn,
                    "file_path",
                    fp,
                    summary=f"File referenced in: {ev_snip}",
                    confidence=0.92,
                    now_iso=now_iso,
                )
                fid = fe["entity_id"]
                file_ids.append(fid)
                touched_entities.append(fid)
                for pid in proj_ids:
                    if _add_relation_conn(conn, pid, "contains_file", fid, evidence=ev_snip, now_iso=now_iso):
                        rel_count += 1
                for pe_id in person_ids:
                    if _add_relation_conn(conn, pe_id, "touched_file", fid, evidence=ev_snip, now_iso=now_iso):
                        rel_count += 1
                if not proj_ids and not person_ids:
                    if _add_relation_conn(conn, user_id, "touched_file", fid, evidence=ev_snip, now_iso=now_iso):
                        rel_count += 1

            for pref in prefs[:4]:
                pr_e = _upsert_entity_conn(
                    conn,
                    "preference",
                    pref[:72],
                    summary=pref,
                    confidence=0.9,
                    now_iso=now_iso,
                )
                pr_id = pr_e["entity_id"]
                touched_entities.append(pr_id)
                if _add_relation_conn(conn, user_id, "prefers", pr_id, evidence=pref, now_iso=now_iso):
                    rel_count += 1
                for pid in proj_ids:
                    if _add_relation_conn(conn, pid, "governed_by_preference", pr_id, evidence=pref, now_iso=now_iso):
                        rel_count += 1

            for rt in tasks[:4]:
                rt_e = _upsert_entity_conn(
                    conn,
                    "recurring_task",
                    rt[:72],
                    summary=rt,
                    confidence=0.88,
                    now_iso=now_iso,
                )
                rt_id = rt_e["entity_id"]
                touched_entities.append(rt_id)
                if _add_relation_conn(conn, user_id, "schedules_task", rt_id, evidence=rt, now_iso=now_iso):
                    rel_count += 1
                for pid in proj_ids:
                    if _add_relation_conn(conn, rt_id, "targets_project", pid, evidence=rt, now_iso=now_iso):
                        rel_count += 1

            tool_ids: list[str] = []
            for tname, tsum in tools_used[:6]:
                te = _upsert_entity_conn(
                    conn,
                    "tool",
                    tname,
                    summary=tsum or f"Jarvis tool {tname}",
                    confidence=0.95,
                    now_iso=now_iso,
                )
                tid = te["entity_id"]
                tool_ids.append(tid)
                touched_entities.append(tid)

            for oc in outcomes[:5]:
                oc_e = _upsert_entity_conn(
                    conn,
                    "past_outcome",
                    oc[:80],
                    summary=oc,
                    confidence=0.86,
                    now_iso=now_iso,
                )
                oc_id = oc_e["entity_id"]
                touched_entities.append(oc_id)
                anchors = proj_ids or file_ids or tool_ids or [user_id]
                for aid in anchors[:3]:
                    if _add_relation_conn(conn, aid, "produced_outcome", oc_id, evidence=oc, now_iso=now_iso):
                        rel_count += 1

            if touched_entities:
                conn.execute(
                    "INSERT INTO observations(ts_utc, source, snippet, entities_json) VALUES (?, ?, ?, ?)",
                    (now_iso, "conversation", ev_snip[:400], json.dumps(touched_entities)),
                )
            conn.commit()
        finally:
            conn.close()

    return {
        "entities_touched": len(touched_entities),
        "relations_touched": rel_count,
        "entity_ids": touched_entities,
    }


def ingest_memory_dict(memory: dict[str, Any], *, db_path: Path | None = None) -> dict[str, Any]:
    """Sync structured categories from `memory_manager` into the Entity-Relation Graph."""
    if not isinstance(memory, dict):
        return {"entities_synced": 0, "relations_synced": 0}

    now_iso = _now_iso()
    cat_map = {
        "relationships": ("person", "knows_person"),
        "projects": ("project", "works_on"),
        "preferences": ("preference", "prefers"),
        "wishes": ("recurring_task", "plans_task"),
        "notes": ("past_outcome", "recorded_note"),
    }
    ent_cnt = 0
    rel_cnt = 0

    with _LOCK:
        conn = _connect(db_path)
        try:
            ident = memory.get("identity") if isinstance(memory.get("identity"), dict) else {}
            ident_attrs: dict[str, str] = {}
            user_display = "User"
            for k, v in ident.items():
                val = str(v.get("value", "") if isinstance(v, dict) else v).strip()
                if val:
                    ident_attrs[str(k)] = val
                    if str(k) == "name":
                        user_display = val

            user_ent = _upsert_entity_conn(
                conn,
                "person",
                "User",
                summary=f"Primary user ({user_display})",
                attributes=ident_attrs,
                aliases=[user_display] if user_display != "User" else None,
                confidence=1.0,
                now_iso=now_iso,
            )
            user_id = user_ent["entity_id"]

            for cat, (etype, rtype) in cat_map.items():
                items = memory.get(cat)
                if not isinstance(items, dict):
                    continue
                for key, entry in items.items():
                    val = str(entry.get("value", "") if isinstance(entry, dict) else entry).strip()
                    if not val:
                        continue
                    pretty_name = str(key).replace("_", " ").strip()
                    ent = _upsert_entity_conn(
                        conn,
                        etype,
                        pretty_name,
                        summary=val,
                        confidence=0.92,
                        now_iso=now_iso,
                    )
                    ent_cnt += 1
                    if _add_relation_conn(conn, user_id, rtype, ent["entity_id"], evidence=val, now_iso=now_iso):
                        rel_cnt += 1
                    for fp_m in _FILE_PATH_RE.finditer(val):
                        fp = fp_m.group(0).strip().rstrip(".,;:)")
                        if len(fp) >= 4:
                            f_ent = _upsert_entity_conn(conn, "file_path", fp, summary=val[:160], now_iso=now_iso)
                            ent_cnt += 1
                            if _add_relation_conn(conn, ent["entity_id"], "touches_file", f_ent["entity_id"], evidence=val, now_iso=now_iso):
                                rel_cnt += 1

            sessions = memory.get("sessions")
            if isinstance(sessions, list):
                for sess in sessions[-5:]:
                    if isinstance(sess, dict) and sess.get("summary"):
                        s_txt = str(sess["summary"]).strip()
                        oc = _upsert_entity_conn(
                            conn,
                            "past_outcome",
                            f"session_{sess.get('date', 'recent')}",
                            summary=s_txt,
                            confidence=0.85,
                            now_iso=now_iso,
                        )
                        ent_cnt += 1
                        if _add_relation_conn(conn, user_id, "completed_session", oc["entity_id"], evidence=s_txt, now_iso=now_iso):
                            rel_cnt += 1

            conn.commit()
        finally:
            conn.close()

    return {"entities_synced": ent_cnt, "relations_synced": rel_cnt}


def query_graph(
    query: str,
    *,
    max_hops: int = 2,
    top_k: int = 8,
    entity_type: str = "",
    db_path: Path | None = None,
) -> dict[str, Any]:
    """Run multi-hop (1..3 hop) entity-relation subgraph retrieval ranked by relevance & recency."""
    q_raw = str(query or "").strip()
    hops = max(1, min(3, int(max_hops or 2)))
    limit = max(1, min(25, int(top_k or 8)))
    tokens = [
        t for t in re.split(r"[^a-z0-9_./\-]+", q_raw.lower())
        if len(t) >= 2 and t not in _STOPWORDS
    ]
    etype_filter = str(entity_type or "").strip().lower()

    with _LOCK:
        conn = _connect(db_path)
        try:
            ent_rows = conn.execute(
                "SELECT entity_id, entity_type, name, canonical_key, summary, "
                "attributes_json, confidence, mention_count, last_seen FROM entities"
            ).fetchall()
            rel_rows = conn.execute(
                "SELECT source_id, relation_type, target_id, weight, evidence, last_seen FROM relations"
            ).fetchall()
        finally:
            conn.close()

    if not ent_rows:
        return {"seeds": [], "nodes": [], "edges": [], "formatted": ""}

    entities_by_id: dict[str, dict[str, Any]] = {}
    scored_seeds: list[tuple[float, str]] = []

    for eid, etype, name, ckey, summary, attrs_raw, conf, mcnt, last_seen in ent_rows:
        try:
            attrs = json.loads(attrs_raw) if attrs_raw else {}
        except Exception:
            attrs = {}
        aliases = " ".join(attrs.get("aliases") or []) if isinstance(attrs, dict) else ""
        node = {
            "entity_id": eid,
            "entity_type": etype,
            "name": name,
            "canonical_key": ckey,
            "summary": summary,
            "attributes": attrs if isinstance(attrs, dict) else {},
            "confidence": float(conf or 0.5),
            "mention_count": int(mcnt or 1),
            "last_seen": last_seen,
        }
        entities_by_id[eid] = node

        if etype_filter and etype != etype_filter:
            continue

        hay_name = f"{name} {ckey} {aliases}".lower()
        hay_sum = f"{summary} {json.dumps(attrs, ensure_ascii=False)}".lower()

        if not tokens:
            lex = 1.0 if eid != "person:user" else 0.2
        else:
            lex = 0.0
            for tok in tokens:
                if tok == ckey or tok == name.lower():
                    lex += 10.0
                elif tok in hay_name:
                    lex += 5.5
                elif tok in hay_sum:
                    lex += 2.5
                elif tok in etype:
                    lex += 1.0

        if lex > 0.0:
            score = lex * (0.6 + 0.4 * float(conf or 0.5)) * (1.0 + min(0.5, int(mcnt or 1) * 0.05))
            scored_seeds.append((round(score, 4), eid))

    scored_seeds.sort(key=lambda item: (-item[0], item[1]))
    seed_ids = [eid for _sc, eid in scored_seeds[:limit]]
    if not seed_ids:
        return {"seeds": [], "nodes": [], "edges": [], "formatted": ""}

    adj: dict[str, list[dict[str, Any]]] = {}
    for src, rtype, tgt, weight, evidence, last_seen in rel_rows:
        edge = {
            "source_id": src,
            "relation_type": rtype,
            "target_id": tgt,
            "weight": float(weight or 1.0),
            "evidence": evidence,
            "last_seen": last_seen,
        }
        adj.setdefault(src, []).append(edge)
        adj.setdefault(tgt, []).append(edge)

    visited_hops: dict[str, int] = {sid: 0 for sid in seed_ids}
    bfs: deque[tuple[str, int]] = deque((sid, 0) for sid in seed_ids)
    collected_edges: dict[tuple[str, str, str], dict[str, Any]] = {}

    while bfs and len(visited_hops) < limit * 3:
        curr_id, depth = bfs.popleft()
        if depth >= hops:
            continue
        neighbors = sorted(adj.get(curr_id, []), key=lambda e: -e["weight"])
        for edge in neighbors[:8]:
            ekey = (edge["source_id"], edge["relation_type"], edge["target_id"])
            collected_edges[ekey] = edge
            other = edge["target_id"] if edge["source_id"] == curr_id else edge["source_id"]
            if other in entities_by_id and other not in visited_hops:
                visited_hops[other] = depth + 1
                bfs.append((other, depth + 1))

    seed_score_map = dict(scored_seeds)
    ordered_node_ids = sorted(
        visited_hops.keys(),
        key=lambda nid: (visited_hops[nid], -seed_score_map.get(nid, 0.0), nid),
    )[: limit * 2]
    keep_set = set(ordered_node_ids)

    nodes_out = [
        {**entities_by_id[nid], "hop": visited_hops[nid], "score": seed_score_map.get(nid, 0.0)}
        for nid in ordered_node_ids
        if nid in entities_by_id
    ]
    edges_out = [
        e for e in sorted(collected_edges.values(), key=lambda x: -x["weight"])
        if e["source_id"] in keep_set and e["target_id"] in keep_set
    ]

    lines = ["Entities:"]
    for n in nodes_out[:limit]:
        sum_part = f" — {n['summary']}" if n.get("summary") else ""
        lines.append(f"  - [{n['entity_type']}] {n['name']} (hop={n['hop']}, seen={n['mention_count']}x){sum_part}")
    if edges_out:
        lines.append("Relations:")
        for e in edges_out[: limit * 2]:
            s_name = entities_by_id.get(e["source_id"], {}).get("name", e["source_id"])
            t_name = entities_by_id.get(e["target_id"], {}).get("name", e["target_id"])
            ev_part = f" ({e['evidence'][:90]})" if e.get("evidence") else ""
            lines.append(f"  - {s_name} --[{e['relation_type']} w={e['weight']:.1f}]--> {t_name}{ev_part}")

    return {
        "seeds": seed_ids,
        "nodes": nodes_out,
        "edges": edges_out,
        "formatted": "\n".join(lines),
    }


def consolidate_graph(
    *,
    sync_long_term: bool = True,
    prune_min_confidence: float = 0.25,
    stale_days: int = 120,
    db_path: Path | None = None,
) -> dict[str, Any]:
    """Idle-time Knowledge Graph consolidation: syncs memory, merges alias duplicates, and prunes noise."""
    synced = {"entities_synced": 0, "relations_synced": 0}
    if sync_long_term:
        try:
            from memory import memory_manager
            synced = ingest_memory_dict(memory_manager.load_memory(), db_path=db_path)
        except Exception:
            pass

    entities_merged = 0
    edges_merged = 0
    stale_pruned = 0
    cutoff_iso = (datetime.now(timezone.utc) - timedelta(days=max(1, int(stale_days)))).strftime("%Y-%m-%dT%H:%M:%SZ")

    with _LOCK:
        conn = _connect(db_path)
        try:
            rows = conn.execute(
                "SELECT entity_id, entity_type, name, canonical_key, summary, "
                "attributes_json, confidence, mention_count, first_seen, last_seen "
                "FROM entities ORDER BY mention_count DESC, confidence DESC, first_seen ASC"
            ).fetchall()

            # Group alias-overlapping entities within each entity_type
            canonical_owner: dict[tuple[str, str], str] = {}
            merge_pairs: list[tuple[str, str]] = []  # (dup_id, winner_id)

            for row in rows:
                eid, etype, name, ckey, _sum, attrs_raw, _conf, _mcnt, _fs, _ls = row
                try:
                    attrs = json.loads(attrs_raw) if attrs_raw else {}
                except Exception:
                    attrs = {}
                keys_for_row = {ckey, _canonical_slug(name)}
                if isinstance(attrs, dict):
                    for al in attrs.get("aliases") or []:
                        keys_for_row.add(_canonical_slug(str(al)))

                winner = None
                for k in keys_for_row:
                    if (etype, k) in canonical_owner:
                        winner = canonical_owner[(etype, k)]
                        break
                if winner is None:
                    for k in keys_for_row:
                        canonical_owner[(etype, k)] = eid
                elif winner != eid:
                    merge_pairs.append((eid, winner))
                    for k in keys_for_row:
                        canonical_owner.setdefault((etype, k), winner)

            for dup_id, win_id in merge_pairs:
                dup_row = conn.execute(
                    "SELECT name, summary, attributes_json, confidence, mention_count, last_seen "
                    "FROM entities WHERE entity_id = ?",
                    (dup_id,),
                ).fetchone()
                win_row = conn.execute(
                    "SELECT name, summary, attributes_json, confidence, mention_count, last_seen "
                    "FROM entities WHERE entity_id = ?",
                    (win_id,),
                ).fetchone()
                if not dup_row or not win_row:
                    continue

                d_name, d_sum, d_attrs_s, d_conf, d_cnt, d_ls = dup_row
                w_name, w_sum, w_attrs_s, w_conf, w_cnt, w_ls = win_row
                try:
                    w_attrs = json.loads(w_attrs_s) if w_attrs_s else {}
                except Exception:
                    w_attrs = {}
                try:
                    d_attrs = json.loads(d_attrs_s) if d_attrs_s else {}
                except Exception:
                    d_attrs = {}

                aliases = set(w_attrs.get("aliases") or []) | set(d_attrs.get("aliases") or [])
                if d_name and d_name != w_name:
                    aliases.add(d_name)
                merged_attrs = {**d_attrs, **w_attrs}
                if aliases:
                    merged_attrs["aliases"] = sorted(aliases)

                best_sum = w_sum if len(w_sum or "") >= len(d_sum or "") else d_sum
                conn.execute(
                    """
                    UPDATE entities
                    SET summary = ?, attributes_json = ?, confidence = ?, mention_count = ?, last_seen = ?
                    WHERE entity_id = ?
                    """,
                    (
                        best_sum,
                        json.dumps(merged_attrs, ensure_ascii=False),
                        round(min(1.0, max(float(w_conf), float(d_conf))), 4),
                        int(w_cnt) + int(d_cnt),
                        max(str(w_ls), str(d_ls)),
                        win_id,
                    ),
                )

                # Redirect outgoing & incoming edges from dup_id -> win_id
                out_edges = conn.execute(
                    "SELECT id, relation_type, target_id, weight, evidence, last_seen "
                    "FROM relations WHERE source_id = ?",
                    (dup_id,),
                ).fetchall()
                for rid, rtype, tgt, w, ev, ls in out_edges:
                    new_tgt = win_id if tgt == dup_id else tgt
                    conn.execute("DELETE FROM relations WHERE id = ?", (int(rid),))
                    if new_tgt != win_id:
                        _add_relation_conn(conn, win_id, rtype, new_tgt, weight=float(w), evidence=ev, now_iso=ls)
                        edges_merged += 1

                in_edges = conn.execute(
                    "SELECT id, source_id, relation_type, weight, evidence, last_seen "
                    "FROM relations WHERE target_id = ?",
                    (dup_id,),
                ).fetchall()
                for rid, src, rtype, w, ev, ls in in_edges:
                    new_src = win_id if src == dup_id else src
                    conn.execute("DELETE FROM relations WHERE id = ?", (int(rid),))
                    if new_src != win_id:
                        _add_relation_conn(conn, new_src, rtype, win_id, weight=float(w), evidence=ev, now_iso=ls)
                        edges_merged += 1

                conn.execute("DELETE FROM entities WHERE entity_id = ?", (dup_id,))
                entities_merged += 1

            # Remove any accidental self-loops
            conn.execute("DELETE FROM relations WHERE source_id = target_id")

            # Prune stale single-mention ultra-low-confidence noise (never prune person:user)
            cur = conn.execute(
                """
                DELETE FROM entities
                WHERE entity_id != 'person:user'
                  AND mention_count <= 1
                  AND confidence < ?
                  AND last_seen < ?
                """,
                (float(prune_min_confidence), cutoff_iso),
            )
            stale_pruned = max(0, int(cur.rowcount or 0))
            conn.commit()

            tot_ent = int(conn.execute("SELECT COUNT(*) FROM entities").fetchone()[0])
            tot_rel = int(conn.execute("SELECT COUNT(*) FROM relations").fetchone()[0])
        finally:
            conn.close()

    return {
        "synced_from_memory": synced,
        "entities_merged": entities_merged,
        "edges_merged": edges_merged,
        "stale_pruned": stale_pruned,
        "total_entities": tot_ent,
        "total_relations": tot_rel,
    }


def graph_stats(*, db_path: Path | None = None) -> dict[str, Any]:
    path = _resolve_db(db_path)
    if not path.is_file():
        return {
            "db_path": str(path),
            "db_exists": False,
            "total_entities": 0,
            "total_relations": 0,
            "total_observations": 0,
            "by_type": {},
            "db_size_bytes": 0,
        }
    with _LOCK:
        conn = _connect(path)
        try:
            tot_ent = int(conn.execute("SELECT COUNT(*) FROM entities").fetchone()[0])
            tot_rel = int(conn.execute("SELECT COUNT(*) FROM relations").fetchone()[0])
            tot_obs = int(conn.execute("SELECT COUNT(*) FROM observations").fetchone()[0])
            by_type = {
                str(r[0]): int(r[1])
                for r in conn.execute("SELECT entity_type, COUNT(*) FROM entities GROUP BY entity_type").fetchall()
            }
        finally:
            conn.close()
    return {
        "db_path": str(path),
        "db_exists": True,
        "total_entities": tot_ent,
        "total_relations": tot_rel,
        "total_observations": tot_obs,
        "by_type": by_type,
        "db_size_bytes": path.stat().st_size if path.is_file() else 0,
    }
