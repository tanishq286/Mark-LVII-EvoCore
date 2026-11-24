"""core/evolution/habit_compiler.py — Procedural Habit Compiler & 1-Voice Macro Runner.

Mines chronological tool invocations in `memory/telemetry.sqlite` for repeated
multi-step chains, compiles approved chains into named macros in git-ignored
`memory/habits.json`, and enforces `safety_guard` confirmation gates whenever a
macro contains `CONFIRM`/`LOCKED` steps.
"""
from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import sys
import threading
from typing import Callable

from core.evolution import safety_guard, telemetry

if getattr(sys, "frozen", False):
    _BASE = Path(sys.executable).parent
else:
    _BASE = Path(__file__).resolve().parent.parent.parent

DEFAULT_HABITS_PATH: Path = _BASE / "memory" / "habits.json"
_LOCK = threading.Lock()


def _resolve_path(habits_path: Path | None = None) -> Path:
    return Path(habits_path) if habits_path is not None else DEFAULT_HABITS_PATH


def _parse_iso(ts: str) -> float:
    try:
        dt = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
        return dt.timestamp()
    except Exception:
        return 0.0


def _slug(raw: str) -> str:
    s = re.sub(r"[^a-z0-9_]+", "_", str(raw or "").lower()).strip("_")
    return s[:48] or "habit_macro"


def _load_habits_unlocked(path: Path) -> list[dict]:
    if not path.is_file():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except Exception:
        return []


def list_habits(*, habits_path: Path | None = None) -> list[dict]:
    path = _resolve_path(habits_path)
    with _LOCK:
        return _load_habits_unlocked(path)


def remove_habit(name: str, *, habits_path: Path | None = None) -> bool:
    path = _resolve_path(habits_path)
    target = _slug(name)
    with _LOCK:
        habits = _load_habits_unlocked(path)
        kept = [h for h in habits if _slug(h.get("name", "")) != target]
        if len(kept) == len(habits):
            return False
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(kept, indent=2, ensure_ascii=False), encoding="utf-8")
        return True


def _sequence_signature(steps: list[dict]) -> tuple[tuple[str, str], ...]:
    sig: list[tuple[str, str]] = []
    for st in steps:
        t = str(st.get("tool") or st.get("tool_name") or "").strip().lower()
        params = st.get("parameters") or st.get("args") or {}
        a = str(st.get("action") or (params.get("action") or params.get("mode") if isinstance(params, dict) else "") or "").strip().lower()
        sig.append((t, a))
    return tuple(sig)


def detect_habit_candidates(
    *,
    db_path: Path | None = None,
    habits_path: Path | None = None,
    min_support: int = 3,
    min_len: int = 2,
    max_len: int = 5,
    max_gap_s: float = 180.0,
) -> list[dict]:
    """Mine repeated contiguous multi-step tool chains from `telemetry.sqlite`."""
    events = telemetry.recent_tool_sequence(limit=400, db_path=db_path)
    successful = [e for e in events if e.get("success")]
    if len(successful) < min_len:
        return []

    # Segment into chronological episodes by session_id + max_gap_s
    episodes: list[list[dict]] = []
    cur_ep: list[dict] = []
    prev_ts = 0.0
    prev_sess = ""
    for ev in successful:
        ts_val = _parse_iso(ev.get("ts_utc", ""))
        sess = str(ev.get("session_id") or "")
        if cur_ep and (sess != prev_sess or (ts_val > 0 and prev_ts > 0 and (ts_val - prev_ts) > max_gap_s)):
            episodes.append(cur_ep)
            cur_ep = []
        cur_ep.append(ev)
        prev_ts = ts_val
        prev_sess = sess
    if cur_ep:
        episodes.append(cur_ep)

    counts: Counter[tuple[tuple[str, str], ...]] = Counter()
    exemplars: dict[tuple[tuple[str, str], ...], list[dict]] = {}

    for ep in episodes:
        n_ep = len(ep)
        for length in range(max(2, min_len), min(max_len, n_ep) + 1):
            seen_in_ep: set[tuple[tuple[str, str], ...]] = set()
            for start in range(0, n_ep - length + 1):
                win = ep[start : start + length]
                sig = tuple((str(x["tool_name"]).lower(), str(x.get("action") or "").lower()) for x in win)
                # Ignore homogeneous single-tool-and-action repeats (e.g. volume_up x3)
                if len(set(sig)) < 2:
                    continue
                if sig in seen_in_ep:
                    continue
                seen_in_ep.add(sig)
                counts[sig] += 1
                exemplars[sig] = [
                    {
                        "tool": str(x["tool_name"]),
                        "parameters": dict(x.get("args") or {}),
                    }
                    for x in win
                ]

    existing_sigs = {_sequence_signature(h.get("steps") or []) for h in list_habits(habits_path=habits_path)}

    candidates: list[dict] = []
    for sig, support in counts.most_common():
        if support < min_support or sig in existing_sigs:
            continue
        steps = exemplars[sig]
        suggested_name = "_".join(t for t, _a in sig[:3])
        suggested_name = _slug(f"routine_{suggested_name}")
        requires_confirm = any(
            safety_guard.classify_risk(t, a) != safety_guard.SAFE
            for t, a in sig
        )
        candidates.append({
            "suggested_name": suggested_name,
            "signature": [list(pair) for pair in sig],
            "support_count": int(support),
            "length": len(sig),
            "requires_confirm": requires_confirm,
            "steps": steps,
        })

    # Prefer longer, higher-support habits
    candidates.sort(key=lambda c: (-c["support_count"], -c["length"], c["suggested_name"]))
    return candidates[:10]


def compile_habit(
    name: str,
    steps: list[dict],
    *,
    trigger_phrases: list[str] | None = None,
    support_count: int = 0,
    habits_path: Path | None = None,
) -> dict:
    """Persist a named procedural macro in `memory/habits.json`."""
    if not isinstance(steps, list) or len(steps) < 1:
        raise ValueError("Habit requires at least one step dict.")
    clean_steps: list[dict] = []
    requires_confirm = False
    for st in steps:
        if not isinstance(st, dict) or not st.get("tool"):
            continue
        tool = str(st["tool"]).strip()
        params = dict(st.get("parameters") or {})
        action = str(params.get("action") or params.get("mode") or "")
        if safety_guard.classify_risk(tool, action) != safety_guard.SAFE:
            requires_confirm = True
        clean_steps.append({"tool": tool, "parameters": params})

    if not clean_steps:
        raise ValueError("No valid steps supplied to compile_habit.")

    path = _resolve_path(habits_path)
    slug = _slug(name)
    record = {
        "name": slug,
        "trigger_phrases": [str(p).strip() for p in (trigger_phrases or [slug.replace("_", " ")]) if str(p).strip()],
        "steps": clean_steps,
        "support_count": int(support_count),
        "requires_confirm": requires_confirm,
        "created_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }

    with _LOCK:
        habits = [h for h in _load_habits_unlocked(path) if _slug(h.get("name", "")) != slug]
        habits.append(record)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(habits, indent=2, ensure_ascii=False), encoding="utf-8")

    return record


def find_habit(name_or_phrase: str, *, habits_path: Path | None = None) -> dict | None:
    q = str(name_or_phrase or "").strip().lower()
    if not q:
        return None
    q_slug = _slug(q)
    for h in list_habits(habits_path=habits_path):
        if _slug(h.get("name", "")) == q_slug:
            return h
        for ph in h.get("trigger_phrases") or []:
            if ph.lower() == q or q in ph.lower():
                return h
    return None


def _execute_steps(habit: dict, run_tool_fn: Callable[[str, dict], str]) -> str:
    results: list[str] = []
    for idx, st in enumerate(habit.get("steps") or [], 1):
        tool = str(st["tool"])
        params = dict(st.get("parameters") or {})
        try:
            out = str(run_tool_fn(tool, params) or "Done.")
        except Exception as exc:
            out = f"failed: {exc}"
        results.append(f"{idx}. {tool}: {out[:120]}")
    return f"Habit '{habit['name']}' executed {len(results)} step(s): " + " | ".join(results)


def run_habit(
    name_or_phrase: str,
    run_tool_fn: Callable[[str, dict], str],
    *,
    player=None,
    habits_path: Path | None = None,
) -> str:
    """Execute a compiled habit macro by name or phrase, enforcing confirm gates if needed."""
    _ = player
    habit = find_habit(name_or_phrase, habits_path=habits_path)
    if habit is None:
        return f"No compiled habit matching '{name_or_phrase}' was found."

    if habit.get("requires_confirm"):
        from core import confirm
        if confirm.pending_title():
            return (
                f"[CONFIRMATION_PENDING] Another confirmation ('{confirm.pending_title()}') "
                "is already awaiting approval on the HUD."
            )
        step_desc = " -> ".join(str(s.get("tool")) for s in habit.get("steps") or [])
        return confirm.request(
            key=f"habit_{habit['name']}",
            title=f"Run habit macro '{habit['name']}'",
            detail=f"Includes protected/confirm step(s): {step_desc}",
            run=lambda h=habit, fn=run_tool_fn: _execute_steps(h, fn),
        )

    return _execute_steps(habit, run_tool_fn)
