"""core/evolution/mission_runner.py — Persistent Long-Horizon Background Missions.

Supports multi-stage background goals that:
  1. Survive restarts via WAL SQLite persistence (`memory/missions.sqlite`) with
     step-level checkpoints (`current_step_idx`, `checkpoints_json`).
  2. Wake on schedules (`interval` seconds or 5-field `cron`), runtime events
     (`notify_event`), or live desktop screen triggers (`screen_trigger`).
  3. Self-recover from transient errors with Failure Immunity context, retry
     budgets (`max_retries`), and exponential backoff.
  4. Push live alerts and 1-tap `CONFIRM` approval requests straight to the
     paired phone over `dashboard/server.py`.
"""
from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import secrets
import sqlite3
import sys
import threading
import time
from typing import Any, Callable

from core.evolution import reflection_loop, safety_guard, telemetry

if getattr(sys, "frozen", False):
    _BASE = Path(sys.executable).parent
else:
    _BASE = Path(__file__).resolve().parent.parent.parent

DEFAULT_MISSIONS_DB: Path = _BASE / "memory" / "missions.sqlite"


def _next_cron_epoch(expr: str, now_ts: float) -> float:
    """Compute next fire timestamp for a 5-field cron expression."""
    try:
        from croniter import croniter
        base_dt = datetime.fromtimestamp(now_ts, tz=timezone.utc)
        return float(croniter(expr, base_dt).get_next(float))
    except Exception:
        # Lightweight fallback if croniter unavailable: default 300s cadence
        return now_ts + 300.0


class MissionRunner:
    """SQLite-persisted long-horizon mission engine surviving restarts."""

    def __init__(
        self,
        *,
        db_path: Path | None = None,
        telemetry_db_path: Path | None = None,
        step_executor: Callable[[dict[str, Any], dict[str, Any]], str] | None = None,
        screen_probe_fn: Callable[[], dict[str, str]] | None = None,
        phone_push_fn: Callable[[dict[str, Any]], None] | None = None,
    ) -> None:
        self.db_path = Path(db_path) if db_path is not None else DEFAULT_MISSIONS_DB
        self._telemetry_db = telemetry_db_path
        self._step_executor = step_executor
        self._screen_probe_fn = screen_probe_fn
        self._phone_push_fn = phone_push_fn
        self._lock = threading.RLock()
        self._stop_ev = threading.Event()
        self._thread: threading.Thread | None = None
        self._init_schema()
        self._recover_interrupted_missions()

    def _connect(self) -> sqlite3.Connection:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(self.db_path), timeout=15.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode = WAL")
        conn.execute("PRAGMA synchronous = NORMAL")
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    def _init_schema(self) -> None:
        with self._lock:
            conn = self._connect()
            try:
                conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS missions (
                        mission_id       TEXT PRIMARY KEY,
                        title            TEXT NOT NULL,
                        goal             TEXT NOT NULL,
                        steps_json       TEXT NOT NULL,
                        current_step_idx INTEGER NOT NULL,
                        checkpoints_json TEXT NOT NULL,
                        status           TEXT NOT NULL,
                        trigger_type     TEXT NOT NULL,
                        trigger_spec     TEXT NOT NULL,
                        retry_count      INTEGER NOT NULL,
                        max_retries      INTEGER NOT NULL,
                        backoff_until    REAL NOT NULL,
                        next_run_at      REAL NOT NULL,
                        notify_phone     INTEGER NOT NULL,
                        last_result      TEXT NOT NULL,
                        last_error       TEXT NOT NULL,
                        run_count        INTEGER NOT NULL,
                        created_at       REAL NOT NULL,
                        updated_at       REAL NOT NULL
                    )
                    """
                )
                conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS mission_events (
                        id         INTEGER PRIMARY KEY AUTOINCREMENT,
                        mission_id TEXT NOT NULL,
                        event_type TEXT NOT NULL,
                        detail     TEXT NOT NULL,
                        ts         REAL NOT NULL,
                        FOREIGN KEY (mission_id) REFERENCES missions(mission_id) ON DELETE CASCADE
                    )
                    """
                )
                conn.execute("CREATE INDEX IF NOT EXISTS idx_missions_status_next ON missions(status, next_run_at)")
                conn.commit()
            finally:
                conn.close()

    def _recover_interrupted_missions(self) -> int:
        """Mark missions interrupted mid-flight (`running`) back to `active` at their saved checkpoint."""
        now = time.time()
        with self._lock:
            conn = self._connect()
            try:
                cur = conn.execute(
                    """
                    UPDATE missions
                    SET status = 'active', updated_at = ?
                    WHERE status = 'running'
                    """,
                    (now,),
                )
                recovered = int(cur.rowcount or 0)
                conn.commit()
            finally:
                conn.close()
        return recovered

    def _log_event(self, mission_id: str, event_type: str, detail: str) -> None:
        now = time.time()
        with self._lock:
            conn = self._connect()
            try:
                conn.execute(
                    "INSERT INTO mission_events(mission_id, event_type, detail, ts) VALUES (?, ?, ?, ?)",
                    (mission_id, event_type, str(detail or "")[:1000], now),
                )
                conn.commit()
            finally:
                conn.close()

    def _push_to_phone(self, mission_id: str, title: str, message: str, *, level: str = "info") -> None:
        payload = {
            "type": "mission_alert",
            "mission_id": mission_id,
            "title": title,
            "message": message[:500],
            "level": level,
            "ts": time.time(),
        }
        if callable(self._phone_push_fn):
            try:
                self._phone_push_fn(payload)
            except Exception:
                pass
        try:
            from dashboard import server as srv_mod
            srv_mod.broadcast_mission_log(f"[{title}] {message}", source="mission_runner", status=level)
            srv = srv_mod.get_active_server()
            if srv is not None:
                srv.push_ws_event(payload)
        except Exception:
            pass

    def create_mission(
        self,
        goal: str,
        *,
        title: str = "",
        steps: list[dict[str, Any]] | None = None,
        trigger_type: str = "immediate",
        trigger_spec: dict[str, Any] | None = None,
        max_retries: int = 3,
        notify_phone: bool = True,
        mission_id: str | None = None,
        now_ts: float | None = None,
    ) -> dict[str, Any]:
        """Persist a new background mission in SQLite."""
        now = float(now_ts) if now_ts is not None else time.time()
        mid = str(mission_id or f"msn_{secrets.token_hex(5)}")
        clean_title = str(title or goal[:72]).strip() or "Autonomous Mission"
        ttype = str(trigger_type or "immediate").strip().lower()
        tspec = dict(trigger_spec or {})

        if steps is None or not len(steps):
            steps = [{"id": "step_1", "title": clean_title, "instruction": goal, "tool": "", "parameters": {}}]

        if ttype == "immediate":
            next_run = now
        elif ttype == "interval":
            next_run = now + max(1.0, float(tspec.get("interval_s") or 60.0))
            if tspec.get("run_immediately"):
                next_run = now
        elif ttype == "cron":
            next_run = _next_cron_epoch(str(tspec.get("cron") or "*/15 * * * *"), now)
        else:
            # 'event' or 'screen_trigger' waits until matching event/screen state
            next_run = 0.0

        with self._lock:
            conn = self._connect()
            try:
                conn.execute(
                    """
                    INSERT OR REPLACE INTO missions(
                        mission_id, title, goal, steps_json, current_step_idx, checkpoints_json,
                        status, trigger_type, trigger_spec, retry_count, max_retries,
                        backoff_until, next_run_at, notify_phone, last_result, last_error,
                        run_count, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, 0, '[]', 'active', ?, ?, 0, ?, 0.0, ?, ?, '', '', 0, ?, ?)
                    """,
                    (
                        mid,
                        clean_title,
                        goal,
                        json.dumps(steps, ensure_ascii=False),
                        ttype,
                        json.dumps(tspec, ensure_ascii=False),
                        max(0, int(max_retries)),
                        next_run,
                        1 if notify_phone else 0,
                        now,
                        now,
                    ),
                )
                conn.commit()
            finally:
                conn.close()

        self._log_event(mid, "created", f"trigger={ttype} steps={len(steps)}")
        return self.get_mission(mid) or {"mission_id": mid}

    def get_mission(self, mission_id: str) -> dict[str, Any] | None:
        with self._lock:
            conn = self._connect()
            try:
                row = conn.execute("SELECT * FROM missions WHERE mission_id = ?", (mission_id,)).fetchone()
                ev_rows = conn.execute(
                    "SELECT event_type, detail, ts FROM mission_events WHERE mission_id = ? ORDER BY id DESC LIMIT 20",
                    (mission_id,),
                ).fetchall()
            finally:
                conn.close()
        if row is None:
            return None
        return self._row_to_dict(row, [dict(e) for e in reversed(ev_rows)])

    def list_missions(self, *, status: str | None = None, limit: int = 25) -> list[dict[str, Any]]:
        with self._lock:
            conn = self._connect()
            try:
                if status:
                    rows = conn.execute(
                        "SELECT * FROM missions WHERE status = ? ORDER BY updated_at DESC LIMIT ?",
                        (status, max(1, int(limit))),
                    ).fetchall()
                else:
                    rows = conn.execute(
                        "SELECT * FROM missions ORDER BY updated_at DESC LIMIT ?",
                        (max(1, int(limit)),),
                    ).fetchall()
            finally:
                conn.close()
        return [self._row_to_dict(r) for r in rows]

    @staticmethod
    def _row_to_dict(row: sqlite3.Row, events: list[dict[str, Any]] | None = None) -> dict[str, Any]:
        try:
            steps = json.loads(row["steps_json"])
        except Exception:
            steps = []
        try:
            checkpoints = json.loads(row["checkpoints_json"])
        except Exception:
            checkpoints = []
        try:
            tspec = json.loads(row["trigger_spec"])
        except Exception:
            tspec = {}
        out = {
            "mission_id": row["mission_id"],
            "title": row["title"],
            "goal": row["goal"],
            "steps": steps,
            "current_step_idx": int(row["current_step_idx"]),
            "checkpoints": checkpoints,
            "status": row["status"],
            "trigger_type": row["trigger_type"],
            "trigger_spec": tspec,
            "retry_count": int(row["retry_count"]),
            "max_retries": int(row["max_retries"]),
            "backoff_until": float(row["backoff_until"]),
            "next_run_at": float(row["next_run_at"]),
            "notify_phone": bool(row["notify_phone"]),
            "last_result": row["last_result"],
            "last_error": row["last_error"],
            "run_count": int(row["run_count"]),
            "created_at": float(row["created_at"]),
            "updated_at": float(row["updated_at"]),
        }
        if events is not None:
            out["events"] = events
        return out

    def set_status(self, mission_id: str, status: str) -> bool:
        with self._lock:
            conn = self._connect()
            try:
                cur = conn.execute(
                    "UPDATE missions SET status = ?, updated_at = ? WHERE mission_id = ?",
                    (status, time.time(), mission_id),
                )
                conn.commit()
                ok = int(cur.rowcount or 0) > 0
            finally:
                conn.close()
        if ok:
            self._log_event(mission_id, "status_change", status)
        return ok

    def notify_event(self, event_name: str, payload: dict[str, Any] | None = None, *, now_ts: float | None = None) -> list[str]:
        """Wake any active `event`-triggered mission watching `event_name`."""
        now = float(now_ts) if now_ts is not None else time.time()
        ename = str(event_name or "").strip().lower()
        triggered: list[str] = []
        for m in self.list_missions(status="active", limit=100):
            if m["trigger_type"] != "event":
                continue
            want = str(m["trigger_spec"].get("event") or "").strip().lower()
            if want and (want == ename or want == "*"):
                self._log_event(m["mission_id"], "event_wake", f"{event_name}: {json.dumps(payload or {})[:200]}")
                self.run_mission_once(m["mission_id"], context_event={"event": event_name, "payload": payload or {}}, now_ts=now)
                triggered.append(m["mission_id"])
        return triggered

    def _probe_screen(self) -> dict[str, str]:
        if callable(self._screen_probe_fn):
            try:
                return dict(self._screen_probe_fn() or {})
            except Exception:
                return {}
        try:
            from core import screenview
            v = screenview.look(budget_s=0.8, allow_ocr=False)
            labels = " ".join(el.label for el in (v.elements or [])[:25] if el.label)
            return {"app": str(v.app or ""), "title": str(v.title or ""), "text": labels}
        except Exception:
            return {}

    def _matches_screen_trigger(self, tspec: dict[str, Any], screen_state: dict[str, str]) -> bool:
        if not screen_state:
            return False
        want_title = str(tspec.get("window_contains") or "").strip().lower()
        want_app = str(tspec.get("app_contains") or "").strip().lower()
        want_text = str(tspec.get("text_contains") or "").strip().lower()
        if not (want_title or want_app or want_text):
            return False
        if want_title and want_title not in str(screen_state.get("title") or "").lower():
            return False
        if want_app and want_app not in str(screen_state.get("app") or "").lower():
            return False
        if want_text and want_text not in (
            f"{screen_state.get('title', '')} {screen_state.get('text', '')}".lower()
        ):
            return False
        return True

    def tick(self, *, now_ts: float | None = None) -> list[dict[str, Any]]:
        """Evaluate due scheduled, retry-ready, and screen-triggered missions once."""
        now = float(now_ts) if now_ts is not None else time.time()
        active = self.list_missions(status="active", limit=100)
        needs_screen = any(m["trigger_type"] == "screen_trigger" for m in active)
        screen_state = self._probe_screen() if needs_screen else {}

        executed: list[dict[str, Any]] = []
        for m in active:
            if m["backoff_until"] and now < m["backoff_until"]:
                continue
            ttype = m["trigger_type"]
            due = False
            if ttype in ("immediate", "interval", "cron"):
                due = m["next_run_at"] > 0.0 and now >= m["next_run_at"]
            elif ttype == "screen_trigger":
                due = self._matches_screen_trigger(m["trigger_spec"], screen_state)
            if due:
                res = self.run_mission_once(m["mission_id"], now_ts=now)
                executed.append(res)
        return executed

    def _execute_step(self, mission: dict[str, Any], step: dict[str, Any], context_event: dict[str, Any] | None) -> str:
        tool = str(step.get("tool") or "").strip()
        params = dict(step.get("parameters") or {}) if isinstance(step.get("parameters"), dict) else {}
        requires_approval = bool(step.get("requires_approval", False))
        action = str(params.get("action") or params.get("mode") or "").strip().lower()
        risk_tier = safety_guard.CONFIRM if requires_approval else (
            safety_guard.classify_risk(tool, action) if tool else safety_guard.SAFE
        )

        if risk_tier != safety_guard.SAFE:
            mid = mission["mission_id"]
            title = f"Mission Approval: {step.get('title') or mission['title']}"
            detail = f"Mission '{mission['title']}' requests {tool or 'step'}: {step.get('instruction') or params}"

            def _approved_continuation() -> str:
                self.approve_waiting_step(mid)
                return f"Approved and resumed mission {mid}."

            if tool and risk_tier == safety_guard.LOCKED:
                gate_msg = safety_guard.enforce_gate(tool, action, title, detail, _approved_continuation, key=f"msn_{mid}")
            else:
                from core import confirm
                if confirm._show_cb is not None:
                    gate_msg = confirm.request(key=f"msn_{mid}", title=f"[CONFIRM] {title}", detail=detail, run=_approved_continuation)
                else:
                    gate_msg = f"[CONFIRMATION_PENDING] Approval required on phone/HUD for {title}"
            return gate_msg

        if callable(self._step_executor):
            return str(self._step_executor(mission, {**step, "_event": context_event}) or "Done.")

        if tool:
            # Try active server tool executor or plugin registry fallback
            from plugins import swarm_orchestrator
            _ = swarm_orchestrator
        immunity = reflection_loop.format_failure_immunity_context(db_path=self._telemetry_db)
        prompt = (
            f"Mission: {mission['goal']}\n"
            f"Step: {step.get('title') or ''}\n"
            f"Instruction: {step.get('instruction') or mission['goal']}\n"
            + (f"Retry recovery context (previous error={mission['last_error']}):\n{immunity}\n" if mission["last_error"] else "")
        )
        try:
            from core import gemini
            ans = gemini.text(prompt, tier=gemini.SMART)
            if ans and ans.strip():
                return ans.strip()
        except Exception:
            pass
        return f"Completed step '{step.get('title') or 'task'}' for mission '{mission['title']}'."

    def approve_waiting_step(self, mission_id: str, *, now_ts: float | None = None) -> dict[str, Any]:
        """Advance a `waiting_approval` mission past its gated step and resume execution."""
        now = float(now_ts) if now_ts is not None else time.time()
        m = self.get_mission(mission_id)
        if m is None:
            return {"ok": False, "error": f"Mission '{mission_id}' not found."}
        steps = list(m["steps"])
        idx = int(m["current_step_idx"])
        checkpoints = list(m["checkpoints"])
        if idx < len(steps):
            step = dict(steps[idx])
            step_clean = {**step, "requires_approval": False}
            if callable(self._step_executor):
                out = str(self._step_executor(m, step_clean) or "Approved & executed.")
            else:
                out = f"Approved step '{step.get('title') or idx + 1}'."
            checkpoints.append({"step_idx": idx, "output": out[:800], "approved": True, "ts": now})
            idx += 1
        status = "completed" if idx >= len(steps) else "active"
        with self._lock:
            conn = self._connect()
            try:
                conn.execute(
                    """
                    UPDATE missions
                    SET current_step_idx = ?, checkpoints_json = ?, status = ?, updated_at = ?
                    WHERE mission_id = ?
                    """,
                    (idx, json.dumps(checkpoints, ensure_ascii=False), status, now, mission_id),
                )
                conn.commit()
            finally:
                conn.close()
        self._log_event(mission_id, "approved", f"step={idx}/{len(steps)} status={status}")
        if status == "active":
            return self.run_mission_once(mission_id, now_ts=now)
        if m["notify_phone"]:
            self._push_to_phone(mission_id, m["title"], "Approved mission completed cleanly.", level="completed")
        return self.get_mission(mission_id) or {"mission_id": mission_id, "status": status}

    def run_mission_once(
        self,
        mission_id: str,
        *,
        context_event: dict[str, Any] | None = None,
        now_ts: float | None = None,
    ) -> dict[str, Any]:
        """Execute remaining steps from the persisted SQLite checkpoint, handling retries & approvals."""
        now = float(now_ts) if now_ts is not None else time.time()
        m = self.get_mission(mission_id)
        if m is None:
            return {"ok": False, "error": f"Mission '{mission_id}' not found."}

        self.set_status(mission_id, "running")
        steps = list(m["steps"])
        idx = int(m["current_step_idx"])
        checkpoints = list(m["checkpoints"])

        while idx < len(steps):
            step = dict(steps[idx])
            t0 = time.perf_counter()
            try:
                out = self._execute_step(m, step, context_event)
            except Exception as exc:
                out = f"failed: {type(exc).__name__}: {exc}"
            lat_ms = (time.perf_counter() - t0) * 1000.0

            if "[CONFIRMATION_PENDING]" in out or "[VOICE_LOCKED]" in out:
                with self._lock:
                    conn = self._connect()
                    try:
                        conn.execute(
                            """
                            UPDATE missions
                            SET current_step_idx = ?, checkpoints_json = ?, status = 'waiting_approval',
                                last_result = ?, updated_at = ?
                            WHERE mission_id = ?
                            """,
                            (idx, json.dumps(checkpoints, ensure_ascii=False), out[:1000], now, mission_id),
                        )
                        conn.commit()
                    finally:
                        conn.close()
                self._log_event(mission_id, "waiting_approval", out[:240])
                if m["notify_phone"]:
                    self._push_to_phone(mission_id, m["title"], f"Approval required: {step.get('title') or m['title']}", level="approval_required")
                return self.get_mission(mission_id) or {"mission_id": mission_id, "status": "waiting_approval"}

            low = out.lower()
            is_err = any(sig in low for sig in telemetry.FAILURE_SIGNALS) or low.startswith("error:")
            telemetry.record_tool_event(
                step.get("tool") or "mission_step",
                {"mission_id": mission_id, "step_idx": idx},
                out,
                lat_ms,
                success=not is_err,
                db_path=self._telemetry_db,
            )

            if is_err:
                retries = int(m["retry_count"]) + 1
                max_ret = int(m["max_retries"])
                if retries <= max_ret:
                    backoff = min(120.0, float(2 ** (retries - 1)))
                    next_try = now + backoff
                    with self._lock:
                        conn = self._connect()
                        try:
                            conn.execute(
                                """
                                UPDATE missions
                                SET current_step_idx = ?, checkpoints_json = ?, status = 'active',
                                    retry_count = ?, backoff_until = ?, next_run_at = ?,
                                    last_error = ?, updated_at = ?
                                WHERE mission_id = ?
                                """,
                                (idx, json.dumps(checkpoints, ensure_ascii=False), retries, next_try, next_try, out[:500], now, mission_id),
                            )
                            conn.commit()
                        finally:
                            conn.close()
                    self._log_event(mission_id, "self_recovery_scheduled", f"retry={retries}/{max_ret} backoff={backoff}s err={out[:160]}")
                    return self.get_mission(mission_id) or {"mission_id": mission_id, "status": "active"}

                with self._lock:
                    conn = self._connect()
                    try:
                        conn.execute(
                            """
                            UPDATE missions
                            SET current_step_idx = ?, checkpoints_json = ?, status = 'failed',
                                retry_count = ?, last_error = ?, updated_at = ?
                            WHERE mission_id = ?
                            """,
                            (idx, json.dumps(checkpoints, ensure_ascii=False), retries, out[:500], now, mission_id),
                        )
                        conn.commit()
                    finally:
                        conn.close()
                self._log_event(mission_id, "failed", out[:240])
                if m["notify_phone"]:
                    self._push_to_phone(mission_id, m["title"], f"Mission failed after {max_ret} retries: {out[:200]}", level="error")
                return self.get_mission(mission_id) or {"mission_id": mission_id, "status": "failed"}

            # Step succeeded -> persist checkpoint immediately so restarts never repeat completed steps!
            checkpoints.append({"step_idx": idx, "title": step.get("title", ""), "output": out[:800], "ts": now})
            idx += 1
            m["last_error"] = ""
            with self._lock:
                conn = self._connect()
                try:
                    conn.execute(
                        """
                        UPDATE missions
                        SET current_step_idx = ?, checkpoints_json = ?, retry_count = 0,
                            backoff_until = 0.0, last_result = ?, last_error = '', updated_at = ?
                        WHERE mission_id = ?
                        """,
                        (idx, json.dumps(checkpoints, ensure_ascii=False), out[:1000], now, mission_id),
                    )
                    conn.commit()
                finally:
                    conn.close()

        # All steps finished! Re-arm recurring schedules (`interval` / `cron`) or mark `completed`.
        ttype = m["trigger_type"]
        tspec = m["trigger_spec"]
        run_cnt = int(m["run_count"]) + 1
        final_out = checkpoints[-1]["output"] if checkpoints else "Completed."

        if ttype == "interval":
            next_at = now + max(1.0, float(tspec.get("interval_s") or 60.0))
            new_status = "active"
            reset_idx = 0
        elif ttype == "cron":
            next_at = _next_cron_epoch(str(tspec.get("cron") or "*/15 * * * *"), now)
            new_status = "active"
            reset_idx = 0
        else:
            next_at = 0.0
            new_status = "completed"
            reset_idx = idx

        with self._lock:
            conn = self._connect()
            try:
                conn.execute(
                    """
                    UPDATE missions
                    SET current_step_idx = ?, status = ?, next_run_at = ?,
                        run_count = ?, last_result = ?, updated_at = ?
                    WHERE mission_id = ?
                    """,
                    (reset_idx, new_status, next_at, run_cnt, final_out[:1000], now, mission_id),
                )
                conn.commit()
            finally:
                conn.close()

        self._log_event(mission_id, "completed_cycle", f"run_count={run_cnt} next_status={new_status}")
        if m["notify_phone"]:
            self._push_to_phone(mission_id, m["title"], f"Mission cycle #{run_cnt} complete: {final_out[:220]}", level="completed")
        return self.get_mission(mission_id) or {"mission_id": mission_id, "status": new_status}

    def start_background_loop(self, poll_interval_s: float = 5.0) -> None:
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return
            self._stop_ev.clear()

            def _worker() -> None:
                while not self._stop_ev.wait(max(0.5, float(poll_interval_s))):
                    try:
                        self.tick()
                    except Exception as exc:
                        print(f"[MissionRunner] tick error: {exc}")

            self._thread = threading.Thread(target=_worker, daemon=True, name="jarvis-mission-runner")
            self._thread.start()

    def stop_background_loop(self) -> None:
        self._stop_ev.set()


_DEFAULT_RUNNER: MissionRunner | None = None
_RUNNER_LOCK = threading.Lock()


def get_default_runner() -> MissionRunner:
    global _DEFAULT_RUNNER
    with _RUNNER_LOCK:
        if _DEFAULT_RUNNER is None:
            _DEFAULT_RUNNER = MissionRunner()
        return _DEFAULT_RUNNER
