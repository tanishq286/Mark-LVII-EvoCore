"""Turn-end and sleep/session-end memory extraction (Milestone 15).

* Turn end (`process_turn`): cheap deterministic rules over the USER's words
  ("my favorite project is X", "my deadline is Friday", "call me Tony",
  "I'm working on ...") -> durable facts, immediately, no network.
* Session end (`process_session_end`): a summary + any extra facts from a
  local Ollama model first (free), Gemini only as fallback, and a rule-based
  extractive summary when no model answers - so it never fails silently.

Facts land in `conversations.sqlite` (memory_facts), the Knowledge Graph
(`knowledge_graph.ingest_extracted_facts`) and the RAG vault note
(`knowledge_rag.remember_facts`).
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Callable

from core.memory import conversation_store as store

_END = r"(?=\s+(?:and|but|so)\s+(?:my|i|the|our)\b|[.,;!?]|$)"
_VAL = r"([^.,;!?\n]{1,80}?)"

_RULES: list[tuple[str, re.Pattern[str], Callable[[re.Match[str]], tuple[str, str]]]] = [
    ("favorite", re.compile(r"\bmy\s+(?:all[- ]time\s+)?(?:favou?rite|fav)\s+([a-z][a-z \-]{1,30}?)\s+(?:is|are)\s+"
                            + _VAL + _END, re.I),
     lambda m: (f"favorite {m.group(1).strip().lower()}", m.group(2))),
    ("deadline", re.compile(r"\b(?:my|our|the)\s+(?:([a-z][\w \-]{1,30}?)\s+)?deadline\s+(?:is|was|falls on|"
                            r"is now|moved to|got moved to)\s+(?:on\s+|by\s+)?" + _VAL + _END, re.I),
     lambda m: (f"{(m.group(1) or '').strip().lower()} deadline".strip(), m.group(2))),
    ("identity", re.compile(r"\b(?:my name is|call me)\s+([A-Z][\w\-]{1,30}(?:\s+[A-Z][\w\-]{1,30})?)", re.I),
     lambda m: ("user name", m.group(1))),
    ("project", re.compile(r"\bi(?:'m| am)\s+(?:currently\s+)?(?:working on|building|developing|making)\s+"
                           + _VAL + _END, re.I),
     lambda m: ("current project", m.group(1))),
    ("preference", re.compile(r"\bi\s+(?:really\s+)?(prefer|love|like|hate|dislike|can't stand)\s+" + _VAL + _END,
                              re.I),
     lambda m: (f"{m.group(1).lower()}s", m.group(2))),
    ("location", re.compile(r"\bi\s+live\s+in\s+" + _VAL + _END, re.I), lambda m: ("lives in", m.group(1))),
    ("work", re.compile(r"\bi\s+work\s+(?:at|for)\s+" + _VAL + _END, re.I), lambda m: ("works at", m.group(1))),
    ("note", re.compile(r"\b(?:please\s+)?remember\s+(?:that\s+)?([^.!?\n]{4,160})", re.I),
     lambda m: ("remember", m.group(1))),
]
_GENERIC = re.compile(r"\bmy\s+([a-z][a-z \-]{1,24}?)\s+is\s+" + _VAL + _END, re.I)
_GENERIC_SKIP = re.compile(r"\b(favou?rite|fav|deadline|name|question|problem|point|bad|guess|pleasure)\b", re.I)
_FILLER = re.compile(r"^(?:going to be|gonna be|probably|definitely|actually|still|just|now)\s+", re.I)


def _tidy(value: str) -> str:
    v = " ".join(str(value or "").split()).strip(" '\"")
    return _FILLER.sub("", v)[:120]


def extract_rule_facts(user_text: str) -> list[dict[str, Any]]:
    """Deterministic fact extraction from one user utterance."""
    text = " ".join(str(user_text or "").split())
    if len(text) < 6:
        return []
    out: list[dict[str, Any]] = []
    seen_subjects: set[str] = set()
    for kind, rx, fn in _RULES:
        for m in rx.finditer(text):
            subject, value = fn(m)
            value = _tidy(value)
            if not value:
                continue
            seen_subjects.add(subject.lower())
            pretty = subject[:1].upper() + subject[1:]
            out.append({"kind": kind, "subject": subject, "value": value,
                        "fact": f"{pretty}: {value}", "confidence": 0.9, "method": "rules"})
    for m in _GENERIC.finditer(text):
        subject = m.group(1).strip().lower()
        if _GENERIC_SKIP.search(subject) or subject in seen_subjects:
            continue
        value = _tidy(m.group(2))
        if value:
            out.append({"kind": "personal", "subject": f"user's {subject}", "value": value,
                        "fact": f"User's {subject}: {value}", "confidence": 0.75, "method": "rules"})
    return out


def persist_facts(facts: list[dict[str, Any]], *, session_id: str = "", summary: str = "",
                  db_path: Path | None = None, graph_db_path: Path | None = None,
                  vault_dir: Path | None = None, rag: bool = True) -> dict[str, Any]:
    """Write facts to the conversation store, Knowledge Graph and RAG vault."""
    res: dict[str, Any] = {"facts_new": 0, "graph": 0, "rag": 0}
    if facts:
        res["facts_new"] = store.add_facts(facts, session_id=session_id, db_path=db_path)
    if facts or summary:
        try:
            from core.memory import knowledge_graph as kg
            if facts:
                res["graph"] = kg.ingest_extracted_facts(facts, db_path=graph_db_path)["entities_touched"]
            if summary:
                kg.ingest_conversation_turn(summary, db_path=graph_db_path)
        except Exception:
            pass
        if rag:
            try:
                from plugins import knowledge_rag
                res["rag"] = knowledge_rag.remember_facts(facts, summary=summary, vault_dir=vault_dir)["written"]
            except Exception:
                pass
    return res


def process_turn(user_text: str, assistant_text: str = "", *, session_id: str = "",
                 db_path: Path | None = None, graph_db_path: Path | None = None,
                 vault_dir: Path | None = None, rag: bool = False) -> dict[str, Any]:
    """Turn-end hook: rules only, milliseconds, safe to run after every exchange."""
    _ = assistant_text
    facts = extract_rule_facts(user_text)
    if not facts:
        return {"facts_new": 0, "graph": 0, "rag": 0}
    return persist_facts(facts, session_id=session_id, db_path=db_path,
                         graph_db_path=graph_db_path, vault_dir=vault_dir, rag=rag)


_LLM_PROMPT = (
    "Read this conversation between a user and their assistant. Return ONLY JSON:\n"
    '{"summary": "2 sentences: what was discussed, decided and still open",\n'
    ' "facts": [{"kind": "preference|project|task|deadline|identity|person|note", '
    '"subject": "...", "value": "..."}]}\n'
    "Facts must be durable things worth remembering next week (names, preferences, projects, "
    "deadlines, ongoing tasks). No small talk. Max 8 facts.\n\nConversation:\n"
)


def _parse_json(raw: str) -> dict[str, Any] | None:
    txt = str(raw or "").strip()
    m = re.search(r"\{.*\}", txt, re.S)
    if not m:
        return None
    try:
        data = json.loads(m.group(0))
        return data if isinstance(data, dict) else None
    except Exception:
        return None


def _llm_extract(convo: str, *, allow_cloud: bool = True) -> tuple[dict[str, Any] | None, str]:
    prompt = _LLM_PROMPT + convo[-6000:]
    try:
        from providers import local_router
        res = local_router.complete_local(prompt, tier="fast", timeout_s=25.0)
        data = _parse_json((res or {}).get("text", ""))
        if data:
            return data, f"ollama:{(res or {}).get('model', '')}"
    except Exception:
        pass
    if allow_cloud:
        try:
            from core import gemini
            data = gemini.as_json(prompt, tier=gemini.FAST)
            if isinstance(data, dict):
                return data, "gemini"
        except Exception:
            pass
    return None, ""


def _extractive_summary(turns: list[dict[str, Any]], facts: list[dict[str, Any]]) -> str:
    user_bits = [t["text"][:90] for t in turns if t.get("role") == "user"][-4:]
    parts = []
    if user_bits:
        parts.append("User talked about: " + " | ".join(user_bits))
    if facts:
        parts.append("Noted: " + "; ".join(f["fact"] for f in facts[:5]))
    return ". ".join(parts)[:600]


def process_session_end(session_id: str, *, use_llm: bool = True, allow_cloud: bool = True,
                        db_path: Path | None = None, graph_db_path: Path | None = None,
                        vault_dir: Path | None = None, rag: bool = True,
                        llm_fn: Callable[[str], dict[str, Any] | None] | None = None) -> dict[str, Any]:
    """Sleep / shutdown / crash-recovery hook: summary + facts for one session."""
    turns = store.session_turns(session_id, db_path=db_path)
    if not turns:
        return {"ok": False, "reason": "no turns", "session_id": session_id}
    facts: list[dict[str, Any]] = []
    for t in turns:
        if t["role"] == "user":
            facts.extend(extract_rule_facts(t["text"]))
    convo = "\n".join(f"{'User' if t['role'] == 'user' else 'Assistant'}: {t['text']}" for t in turns)
    data, method = (None, "")
    if llm_fn is not None:
        data, method = llm_fn(convo), "injected"
    elif use_llm:
        data, method = _llm_extract(convo, allow_cloud=allow_cloud)
    summary = ""
    if isinstance(data, dict):
        summary = " ".join(str(data.get("summary") or "").split())
        for f in data.get("facts") or []:
            if isinstance(f, dict) and (f.get("value") or f.get("subject")):
                facts.append({"kind": str(f.get("kind") or "note").lower(), "subject": str(f.get("subject") or ""),
                              "value": str(f.get("value") or ""), "confidence": 0.8, "method": method[:24],
                              "fact": f"{str(f.get('subject') or '').strip()}: {str(f.get('value') or '').strip()}"})
    if not summary:
        summary, method = _extractive_summary(turns, facts), "extractive"
    store.add_summary(session_id, summary, method=method, db_path=db_path)
    res = persist_facts(facts, session_id=session_id, summary=summary, db_path=db_path,
                        graph_db_path=graph_db_path, vault_dir=vault_dir, rag=rag)
    return {"ok": True, "session_id": session_id, "summary": summary, "method": method,
            "facts_total": len(facts), **res}


def recover_unsummarized(*, exclude_session: str = "", use_llm: bool = True, allow_cloud: bool = True,
                         db_path: Path | None = None, limit: int = 3, **kw: Any) -> list[dict[str, Any]]:
    """Summarise sessions a crash/power-loss left without a summary."""
    out = []
    for sid in store.sessions_needing_summary(exclude_session=exclude_session, db_path=db_path)[-limit:]:
        try:
            out.append(process_session_end(sid, use_llm=use_llm, allow_cloud=allow_cloud, db_path=db_path, **kw))
        except Exception as exc:
            out.append({"ok": False, "session_id": sid, "reason": str(exc)})
    return out
