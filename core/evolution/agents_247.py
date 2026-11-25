"""Scheduler for the 24/7 Researcher + Engineer agents (Milestone 15).

One daemon thread wakes every `interval_s` (default 20 min; never a busy
loop), checks the idle/CPU/GPU gates and the daily budget, then runs one
research cycle and at most one engineer cycle. Pause/resume is persisted in
`memory/agents_state.json`, so "pause the agents" survives a restart.

Disable entirely with env `JARVIS_DISABLE_247_AGENTS=1` (tests set nothing:
`start()` is only ever called by main.py).
"""
from __future__ import annotations

import json
import os
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from core.evolution import research_daemon as rq

DEFAULT_STATE_PATH: Path = rq._BASE / "memory" / "agents_state.json"
DEFAULT_INTERVAL_S = 20 * 60
FIRST_DELAY_S = 10 * 60

_LOCK = threading.Lock()
_STOP = threading.Event()
_THREAD: list[threading.Thread] = []
_LAST: dict[str, Any] = {}


def _load_state() -> dict[str, Any]:
    try:
        data = json.loads(Path(DEFAULT_STATE_PATH).read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _save_state(state: dict[str, Any]) -> None:
    p = Path(DEFAULT_STATE_PATH)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(state, indent=2), encoding="utf-8")


def is_paused() -> bool:
    from core import privacy
    if privacy.is_active():
        return True
    return bool(_load_state().get("paused", False))


def pause(reason: str = "user") -> str:
    st = _load_state()
    st.update({"paused": True, "paused_utc": datetime.now(timezone.utc).isoformat(), "reason": reason})
    _save_state(st)
    return "24/7 research and engineering agents paused. Nothing will run until you resume them."


def resume() -> str:
    st = _load_state()
    st.update({"paused": False, "resumed_utc": datetime.now(timezone.utc).isoformat()})
    _save_state(st)
    return ("24/7 research and engineering agents resumed. They work only while the PC is idle "
            "and within today's budget.")


def run_once(*, player: Any = None, force: bool = False, **kw: Any) -> dict[str, Any]:
    """One researcher + engineer pass (used by the loop and by 'run now')."""
    if is_paused() and not force:
        return {"ran": False, "reason": "paused"}
    research = rq.run_research_cycle(force=force, **{k: v for k, v in kw.items()
                                                     if k in ("db_path", "gate_overrides", "use_llm", "use_web")})
    engineer: dict[str, Any] = {"ran": False, "reason": "researcher did not run"}
    if research.get("ran"):
        from core.evolution import engineer_daemon
        engineer = engineer_daemon.run_engineer_cycle(player=player, force=force, **{
            k: v for k, v in kw.items() if k in ("db_path", "gate_overrides", "repo_root")})
    out = {"ran": bool(research.get("ran")), "research": research, "engineer": engineer,
           "ts": datetime.now(timezone.utc).isoformat()}
    _LAST.clear()
    _LAST.update(out)
    return out


def _loop(player: Any, interval_s: float) -> None:
    if _STOP.wait(FIRST_DELAY_S):
        return
    while not _STOP.is_set():
        try:
            run_once(player=player)
        except Exception as exc:
            _LAST.update({"error": f"{type(exc).__name__}: {exc}"})
        if _STOP.wait(max(60.0, float(interval_s))):
            return


def start(*, player: Any = None, interval_s: float = DEFAULT_INTERVAL_S) -> bool:
    if os.environ.get("JARVIS_DISABLE_247_AGENTS", "").strip().lower() in ("1", "true", "yes"):
        return False
    with _LOCK:
        if _THREAD and _THREAD[0].is_alive():
            return False
        _STOP.clear()
        t = threading.Thread(target=_loop, args=(player, interval_s), daemon=True, name="jarvis-agents-247")
        _THREAD[:] = [t]
        t.start()
    return True


def stop() -> None:
    _STOP.set()


def status() -> dict[str, Any]:
    running = bool(_THREAD and _THREAD[0].is_alive())
    return {"thread_running": running, "paused": is_paused(), "last": dict(_LAST),
            "budget": rq.budget_report(), "pending_specs": len(rq.list_specs(limit=200))}


def built_today_report(*, db_path: Path | None = None) -> str:
    specs = rq.specs_today(db_path=db_path)
    runs = rq.runs_today(db_path=db_path)
    built = [r for r in runs if r["agent"] == "engineer" and r["outcome"] == "built"]
    discarded = [r for r in runs if r["agent"] == "engineer" and r["outcome"] == "discarded"]
    lines = [f"Today the researcher ran {sum(1 for r in runs if r['agent'] == 'researcher')} cycle(s) "
             f"and has {len(specs)} ranked spec(s) updated today."]
    for s in specs[:5]:
        lines.append(f"  - [{s['status']}] {s['title']} (score {s['score']:.1f})")
    if built:
        lines.append(f"The engineer built {len(built)} verified upgrade(s):")
        lines += [f"  - {r['branch']}: {r['detail'][:120]}" for r in built]
    else:
        lines.append("The engineer has no verified upgrade today.")
    if discarded:
        lines.append(f"{len(discarded)} attempt(s) were auto-discarded (failed tests or not better than baseline).")
    lines.append("State: " + ("PAUSED" if is_paused() else "active (idle-only)")
                 + f"; budget {rq.budget_report(db_path=db_path)}")
    return "\n".join(lines)
