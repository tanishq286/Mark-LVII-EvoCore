"""core/evolution/swarm.py — Parallel Multi-Agent Swarm, SQLite Blackboard & Critic Gate.

Spawns specialized sub-agents in parallel waves:
  - `researcher`             : web/RAG/knowledge-graph/system research & citations
  - `coder`                  : code synthesis, refactoring, unit test & static checks
  - `visual_screen_operator` : desktop UIA + Vision inspection & closed-loop GUI control
  - `security_auditor`       : AST safety, risk-tier classification & untrusted boundaries

All sub-agents share discoveries through a thread-safe WAL SQLite blackboard
(`memory/swarm_blackboard.sqlite`). Every sub-agent action and output is audited
by an independent `CriticAgent` before side effects execute (`safety_guard.enforce_gate`)
and before results are accepted or synthesized.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import datetime, timezone
import json
from pathlib import Path
import re
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

DEFAULT_BLACKBOARD_DB: Path = _BASE / "memory" / "swarm_blackboard.sqlite"

ROLE_RESEARCHER = "researcher"
ROLE_CODER = "coder"
ROLE_VISUAL_OPERATOR = "visual_screen_operator"
ROLE_SECURITY_AUDITOR = "security_auditor"

AGENT_ROLES: tuple[str, ...] = (
    ROLE_RESEARCHER,
    ROLE_CODER,
    ROLE_VISUAL_OPERATOR,
    ROLE_SECURITY_AUDITOR,
)

_SCREEN_OPERATOR_LOCK = threading.Lock()
_ACTIVE_CANCEL = threading.Event()

_SWARM_COMPLEXITY_RE = re.compile(
    r"\b(parallel|swarm|multi[- ]?agent|research\s+and\s+(?:build|code|write|audit|implement)|"
    r"compare.+?\band\b.+?(?:implement|audit|summarize|build|create)|"
    r"security\s+audit|full[- ]?stack|architecture\s+and\s+code|"
    r"investigate.+?\band\b.+?(?:fix|patch|verify))\b",
    re.IGNORECASE,
)

_SECRET_LEAK_RE = re.compile(
    r"(AIza[0-9A-Za-z_-]{30,}|sk-[0-9A-Za-z]{24,}|BEGIN (?:RSA |OPENSSH |EC )?PRIVATE KEY)",
)


def _utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def is_complex_swarm_goal(goal: str) -> bool:
    """Heuristic deciding whether `goal` benefits from multi-agent parallel Swarm + Critic."""
    text = str(goal or "").strip()
    if not text:
        return False
    if _SWARM_COMPLEXITY_RE.search(text):
        return True
    conjunctions = len(re.findall(r"\b(and then|also|simultaneously|meanwhile|plus|while)\b", text, re.I))
    domains = sum([
        bool(re.search(r"\b(search|research|find|docs|compare|analyze)\b", text, re.I)),
        bool(re.search(r"\b(code|python|script|implement|function|test|plugin|refactor)\b", text, re.I)),
        bool(re.search(r"\b(screen|window|browser|click|ui)\b", text, re.I)),
        bool(re.search(r"\b(security|audit|safe|risk|vulnerability|verify)\b", text, re.I)),
    ])
    return domains >= 2 and (len(text) >= 70 or conjunctions >= 1)


def request_swarm_cancel() -> None:
    _ACTIVE_CANCEL.set()


def clear_swarm_cancel() -> None:
    _ACTIVE_CANCEL.clear()


@dataclass
class SwarmTask:
    task_id: str
    role: str
    title: str
    instruction: str
    tool_name: str = ""
    tool_args: dict[str, Any] = field(default_factory=dict)
    depends_on: list[str] = field(default_factory=list)
    has_side_effects: bool = False


@dataclass
class CriticVerdict:
    passed: bool
    score: float
    issues: list[str] = field(default_factory=list)
    remediation: str = ""
    gated: bool = False
    gate_message: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "passed": self.passed,
            "score": round(float(self.score), 3),
            "issues": list(self.issues),
            "remediation": self.remediation,
            "gated": self.gated,
            "gate_message": self.gate_message,
        }


class SwarmBlackboard:
    """Thread-safe SQLite WAL shared blackboard for parallel multi-agent runs."""

    def __init__(self, db_path: Path | None = None) -> None:
        self.db_path = Path(db_path) if db_path is not None else DEFAULT_BLACKBOARD_DB
        self._lock = threading.Lock()
        self._init_schema()

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
                    CREATE TABLE IF NOT EXISTS swarm_runs (
                        run_id         TEXT PRIMARY KEY,
                        goal           TEXT NOT NULL,
                        strategy       TEXT NOT NULL,
                        status         TEXT NOT NULL,
                        critic_verdict TEXT NOT NULL,
                        final_report   TEXT NOT NULL,
                        created_at     TEXT NOT NULL,
                        finished_at    TEXT NOT NULL
                    )
                    """
                )
                conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS agent_tasks (
                        task_id          TEXT NOT NULL,
                        run_id           TEXT NOT NULL,
                        role             TEXT NOT NULL,
                        title            TEXT NOT NULL,
                        instruction      TEXT NOT NULL,
                        tool_name        TEXT NOT NULL,
                        tool_args_json   TEXT NOT NULL,
                        depends_on_json  TEXT NOT NULL,
                        has_side_effects INTEGER NOT NULL,
                        status           TEXT NOT NULL,
                        output           TEXT NOT NULL,
                        critic_passed    INTEGER NOT NULL,
                        critic_score     REAL NOT NULL,
                        critic_feedback  TEXT NOT NULL,
                        attempts         INTEGER NOT NULL,
                        latency_ms       REAL NOT NULL,
                        updated_at       TEXT NOT NULL,
                        PRIMARY KEY (run_id, task_id),
                        FOREIGN KEY (run_id) REFERENCES swarm_runs(run_id) ON DELETE CASCADE
                    )
                    """
                )
                conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS blackboard_facts (
                        id          INTEGER PRIMARY KEY AUTOINCREMENT,
                        run_id      TEXT NOT NULL,
                        author_role TEXT NOT NULL,
                        fact_key    TEXT NOT NULL,
                        value_json  TEXT NOT NULL,
                        confidence  REAL NOT NULL,
                        created_at  TEXT NOT NULL,
                        FOREIGN KEY (run_id) REFERENCES swarm_runs(run_id) ON DELETE CASCADE
                    )
                    """
                )
                conn.execute("CREATE INDEX IF NOT EXISTS idx_bb_facts_run ON blackboard_facts(run_id, id)")
                conn.commit()
            finally:
                conn.close()

    def create_run(self, run_id: str, goal: str, strategy: str = "") -> None:
        with self._lock:
            conn = self._connect()
            try:
                conn.execute(
                    """
                    INSERT OR REPLACE INTO swarm_runs(
                        run_id, goal, strategy, status, critic_verdict, final_report, created_at, finished_at
                    ) VALUES (?, ?, ?, 'running', '', '', ?, '')
                    """,
                    (run_id, goal, strategy, _utc_now()),
                )
                conn.commit()
            finally:
                conn.close()

    def finalize_run(self, run_id: str, *, status: str, critic_verdict: dict[str, Any], final_report: str) -> None:
        with self._lock:
            conn = self._connect()
            try:
                conn.execute(
                    """
                    UPDATE swarm_runs
                    SET status = ?, critic_verdict = ?, final_report = ?, finished_at = ?
                    WHERE run_id = ?
                    """,
                    (
                        status,
                        json.dumps(critic_verdict, ensure_ascii=False),
                        str(final_report or "")[:8000],
                        _utc_now(),
                        run_id,
                    ),
                )
                conn.commit()
            finally:
                conn.close()

    def register_task(self, run_id: str, task: SwarmTask) -> None:
        with self._lock:
            conn = self._connect()
            try:
                conn.execute(
                    """
                    INSERT OR REPLACE INTO agent_tasks(
                        task_id, run_id, role, title, instruction, tool_name, tool_args_json,
                        depends_on_json, has_side_effects, status, output, critic_passed,
                        critic_score, critic_feedback, attempts, latency_ms, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending', '', 0, 0.0, '', 0, 0.0, ?)
                    """,
                    (
                        task.task_id,
                        run_id,
                        task.role,
                        task.title,
                        task.instruction,
                        task.tool_name,
                        json.dumps(task.tool_args, ensure_ascii=False),
                        json.dumps(task.depends_on, ensure_ascii=False),
                        1 if task.has_side_effects else 0,
                        _utc_now(),
                    ),
                )
                conn.commit()
            finally:
                conn.close()

    def update_task(
        self,
        run_id: str,
        task_id: str,
        *,
        status: str,
        output: str = "",
        verdict: CriticVerdict | None = None,
        attempts: int = 1,
        latency_ms: float = 0.0,
    ) -> None:
        with self._lock:
            conn = self._connect()
            try:
                conn.execute(
                    """
                    UPDATE agent_tasks
                    SET status = ?, output = ?, critic_passed = ?, critic_score = ?,
                        critic_feedback = ?, attempts = ?, latency_ms = ?, updated_at = ?
                    WHERE run_id = ? AND task_id = ?
                    """,
                    (
                        status,
                        str(output or "")[:4000],
                        1 if (verdict and verdict.passed) else 0,
                        float(verdict.score) if verdict else 0.0,
                        json.dumps(verdict.to_dict(), ensure_ascii=False) if verdict else "",
                        int(attempts),
                        round(float(latency_ms), 2),
                        _utc_now(),
                        run_id,
                        task_id,
                    ),
                )
                conn.commit()
            finally:
                conn.close()

    def post_fact(
        self,
        run_id: str,
        author_role: str,
        fact_key: str,
        value: Any,
        *,
        confidence: float = 0.9,
    ) -> None:
        with self._lock:
            conn = self._connect()
            try:
                conn.execute(
                    """
                    INSERT INTO blackboard_facts(run_id, author_role, fact_key, value_json, confidence, created_at)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        run_id,
                        str(author_role or "agent"),
                        str(fact_key or "note")[:120],
                        json.dumps(value, ensure_ascii=False)[:4000],
                        max(0.0, min(1.0, float(confidence))),
                        _utc_now(),
                    ),
                )
                conn.commit()
            finally:
                conn.close()

    def read_facts(self, run_id: str) -> list[dict[str, Any]]:
        with self._lock:
            conn = self._connect()
            try:
                rows = conn.execute(
                    "SELECT author_role, fact_key, value_json, confidence, created_at "
                    "FROM blackboard_facts WHERE run_id = ? ORDER BY id ASC",
                    (run_id,),
                ).fetchall()
            finally:
                conn.close()
        out: list[dict[str, Any]] = []
        for r in rows:
            try:
                val = json.loads(r["value_json"])
            except Exception:
                val = r["value_json"]
            out.append({
                "author_role": r["author_role"],
                "key": r["fact_key"],
                "value": val,
                "confidence": float(r["confidence"]),
                "created_at": r["created_at"],
            })
        return out

    def get_run_snapshot(self, run_id: str) -> dict[str, Any] | None:
        with self._lock:
            conn = self._connect()
            try:
                run_row = conn.execute(
                    "SELECT * FROM swarm_runs WHERE run_id = ?", (run_id,)
                ).fetchone()
                if run_row is None:
                    return None
                task_rows = conn.execute(
                    "SELECT * FROM agent_tasks WHERE run_id = ? ORDER BY rowid ASC", (run_id,)
                ).fetchall()
            finally:
                conn.close()

        tasks = []
        for t in task_rows:
            try:
                cf = json.loads(t["critic_feedback"]) if t["critic_feedback"] else {}
            except Exception:
                cf = {}
            tasks.append({
                "task_id": t["task_id"],
                "role": t["role"],
                "title": t["title"],
                "tool_name": t["tool_name"],
                "status": t["status"],
                "output": t["output"],
                "critic_passed": bool(t["critic_passed"]),
                "critic_score": float(t["critic_score"]),
                "critic_feedback": cf,
                "attempts": int(t["attempts"]),
                "latency_ms": float(t["latency_ms"]),
            })
        try:
            cv = json.loads(run_row["critic_verdict"]) if run_row["critic_verdict"] else {}
        except Exception:
            cv = {}
        return {
            "run_id": run_row["run_id"],
            "goal": run_row["goal"],
            "strategy": run_row["strategy"],
            "status": run_row["status"],
            "critic_verdict": cv,
            "final_report": run_row["final_report"],
            "created_at": run_row["created_at"],
            "finished_at": run_row["finished_at"],
            "tasks": tasks,
            "facts": self.read_facts(run_id),
        }

    def list_recent_runs(self, limit: int = 10) -> list[dict[str, Any]]:
        with self._lock:
            conn = self._connect()
            try:
                rows = conn.execute(
                    "SELECT run_id, goal, status, strategy, created_at, finished_at "
                    "FROM swarm_runs ORDER BY created_at DESC LIMIT ?",
                    (max(1, min(50, int(limit))),),
                ).fetchall()
            finally:
                conn.close()
        return [dict(r) for r in rows]


class CriticAgent:
    """Independent Critic verifying sub-agent outputs and gating side effects."""

    def __init__(
        self,
        *,
        critic_fn: Callable[[SwarmTask, str, list[dict[str, Any]]], CriticVerdict | dict[str, Any] | bool] | None = None,
    ) -> None:
        self._critic_fn = critic_fn

    def classify_side_effect_risk(self, task: SwarmTask) -> str:
        """Return `SAFE`, `CONFIRM`, or `LOCKED` before a sub-agent executes a tool."""
        if not task.tool_name:
            return safety_guard.SAFE
        action = str(task.tool_args.get("action") or task.tool_args.get("mode") or "").strip().lower()
        tier = safety_guard.classify_risk(task.tool_name, action)
        if tier != safety_guard.SAFE:
            return tier
        if task.has_side_effects:
            return safety_guard.CONFIRM
        return safety_guard.SAFE

    def verify_task_output(
        self,
        task: SwarmTask,
        output: str,
        blackboard_facts: list[dict[str, Any]],
    ) -> CriticVerdict:
        """Audit a sub-agent's output independently before accepting it onto the blackboard."""
        txt = str(output or "").strip()
        if not txt:
            return CriticVerdict(
                passed=False,
                score=0.0,
                issues=["Sub-agent produced empty output."],
                remediation="Re-run with explicit fallback parameters or grounded context.",
            )

        if "[CONFIRMATION_PENDING]" in txt or "[VOICE_LOCKED]" in txt or "[CHOICE_PENDING]" in txt:
            return CriticVerdict(
                passed=False,
                score=0.5,
                issues=["Action parked behind human safety confirmation gate."],
                gated=True,
                gate_message=txt,
            )

        issues: list[str] = []
        low = txt.lower()

        try:
            from core import outcome
            if outcome.is_problem(txt):
                issues.append(f"Tool reported outcome problem: {txt[:140]}")
        except Exception:
            pass

        for sig in telemetry.FAILURE_SIGNALS:
            if sig in low:
                issues.append(f"Output matched failure signal '{sig}'.")
                break

        if _SECRET_LEAK_RE.search(txt):
            issues.append("Output appears to contain a raw API key or private key secret.")

        # If coder role emitted Python code fences or raw module source, run AST safety audit
        if task.role == ROLE_CODER:
            code_blocks = re.findall(r"```python\s*(.*?)```", txt, flags=re.DOTALL | re.IGNORECASE)
            for idx, snippet in enumerate(code_blocks):
                audit = safety_guard.audit_python_source(snippet, filename=f"<coder_block_{idx}>", strict_sandbox=True)
                if not audit["safe"]:
                    issues.extend(audit["violations"])

        # Doubt-Driven cross-agent check: if security_auditor flagged an unresolved BLOCKER, surface it
        if task.role == ROLE_SECURITY_AUDITOR and "CRITICAL_BLOCKER:" in txt:
            issues.append(f"Security Auditor raised blocker: {txt[:160]}")

        if self._critic_fn is not None and not issues:
            custom = self._critic_fn(task, txt, blackboard_facts)
            if isinstance(custom, CriticVerdict):
                return custom
            if isinstance(custom, dict):
                passed = bool(custom.get("passed", True))
                score = float(custom.get("score", 0.9 if passed else 0.2))
                c_issues = [str(i) for i in (custom.get("issues") or [])]
                rem = str(custom.get("remediation") or "")
                return CriticVerdict(passed=passed and not c_issues, score=score, issues=c_issues, remediation=rem)
            if isinstance(custom, bool):
                return CriticVerdict(
                    passed=custom,
                    score=0.92 if custom else 0.2,
                    issues=[] if custom else ["Rejected by custom CriticAgent verifier."],
                    remediation="" if custom else "Address critic objection and provide concrete evidence.",
                )

        if issues:
            return CriticVerdict(
                passed=False,
                score=0.15,
                issues=issues,
                remediation="Resolve reported errors/violations without unsafe primitives or missing arguments.",
            )

        score = 0.92 if len(txt) >= 24 else 0.78
        return CriticVerdict(passed=True, score=score)

    def verify_final_synthesis(
        self,
        goal: str,
        task_results: list[dict[str, Any]],
        synthesis_text: str,
    ) -> CriticVerdict:
        """Final gate before reporting swarm synthesis back to the user."""
        _ = goal
        if not str(synthesis_text or "").strip():
            return CriticVerdict(passed=False, score=0.0, issues=["Empty final synthesis."])
        if _SECRET_LEAK_RE.search(synthesis_text):
            return CriticVerdict(passed=False, score=0.0, issues=["Secret pattern detected in final synthesis."])
        gated = [t for t in task_results if (t.get("verdict") or {}).get("gated")]
        if gated:
            return CriticVerdict(
                passed=False,
                score=0.5,
                issues=["One or more side-effecting steps await human confirmation."],
                gated=True,
                gate_message=gated[0]["verdict"].get("gate_message", ""),
            )
        verified_cnt = sum(1 for t in task_results if t.get("verified"))
        if task_results and verified_cnt == 0:
            return CriticVerdict(
                passed=False,
                score=0.1,
                issues=["Zero sub-agent tasks passed Critic verification."],
            )
        ratio = verified_cnt / max(1, len(task_results))
        return CriticVerdict(passed=True, score=round(0.6 + 0.4 * ratio, 3))


class SwarmCoordinator:
    """Orchestrates parallel specialized sub-agents over `SwarmBlackboard` with `CriticAgent`."""

    def __init__(
        self,
        *,
        db_path: Path | None = None,
        telemetry_db_path: Path | None = None,
        max_workers: int = 4,
        critic: CriticAgent | None = None,
    ) -> None:
        self.blackboard = SwarmBlackboard(db_path=db_path)
        self._telemetry_db = telemetry_db_path
        self._max_workers = max(1, min(8, int(max_workers or 4)))
        self.critic = critic or CriticAgent()

    def decompose_goal(
        self,
        goal: str,
        catalog_text: str = "",
        *,
        max_tasks: int = 4,
        decompose_fn: Callable[[str, str], list[SwarmTask] | dict[str, Any] | None] | None = None,
    ) -> tuple[str, list[SwarmTask]]:
        """Decompose `goal` into specialized parallel & dependent `SwarmTask`s."""
        cap = max(1, min(8, int(max_tasks or 4)))
        if decompose_fn is not None:
            raw = decompose_fn(goal, catalog_text)
            if isinstance(raw, list) and raw and all(isinstance(x, SwarmTask) for x in raw):
                return "Custom multi-agent decomposition", raw[:cap]
            if isinstance(raw, dict):
                return self._parse_plan_dict(goal, raw, cap)

        immunity = reflection_loop.format_failure_immunity_context(db_path=self._telemetry_db)
        prompt = (
            "You are JARVIS's OmniCore Multi-Agent Swarm Architect.\n"
            f"Decompose this goal into 2 to {cap} specialized sub-agent tasks that run in parallel where independent.\n"
            f"Allowed roles: {list(AGENT_ROLES)}.\n"
            "Assign `depends_on` (list of task_id strings) ONLY when a task genuinely needs an earlier task's output.\n"
            "Set `has_side_effects: true` for any task that modifies files, sends messages, or changes system state.\n\n"
            + (f"{immunity}\n\n" if immunity else "")
            + f"Goal: {goal}\n\n"
            f"Available tools:\n{catalog_text or '(standard tools)'}\n\n"
            "Return ONLY valid JSON:\n"
            "{\n"
            '  "strategy": "Concise multi-agent strategy",\n'
            '  "tasks": [\n'
            '    {"task_id": "t1", "role": "researcher", "title": "...", "instruction": "...", '
            '"tool_name": "", "tool_args": {}, "depends_on": [], "has_side_effects": false}\n'
            "  ]\n"
            "}"
        )
        try:
            from core import gemini
            parsed = gemini.as_json(prompt, tier=gemini.SMART)
            if isinstance(parsed, dict) and isinstance(parsed.get("tasks"), list) and parsed["tasks"]:
                return self._parse_plan_dict(goal, parsed, cap)
        except Exception:
            pass

        return self._heuristic_decompose(goal, cap)

    @staticmethod
    def _parse_plan_dict(goal: str, raw: dict[str, Any], cap: int) -> tuple[str, list[SwarmTask]]:
        strategy = str(raw.get("strategy") or f"Parallel multi-agent execution for: {goal[:80]}")
        tasks: list[SwarmTask] = []
        seen_ids: set[str] = set()
        for idx, item in enumerate((raw.get("tasks") or [])[:cap], 1):
            if not isinstance(item, dict):
                continue
            tid = str(item.get("task_id") or f"t{idx}").strip()
            if tid in seen_ids:
                tid = f"{tid}_{idx}"
            seen_ids.add(tid)
            role = str(item.get("role") or ROLE_RESEARCHER).strip().lower()
            if role not in AGENT_ROLES:
                role = ROLE_RESEARCHER
            deps = [str(d).strip() for d in (item.get("depends_on") or []) if str(d).strip() and str(d).strip() != tid]
            t_args = item.get("tool_args") if isinstance(item.get("tool_args"), dict) else {}
            tasks.append(SwarmTask(
                task_id=tid,
                role=role,
                title=str(item.get("title") or f"{role.title()} Task {idx}"),
                instruction=str(item.get("instruction") or goal),
                tool_name=str(item.get("tool_name") or "").strip(),
                tool_args=dict(t_args),
                depends_on=deps,
                has_side_effects=bool(item.get("has_side_effects", False)),
            ))
        return strategy, tasks

    @staticmethod
    def _heuristic_decompose(goal: str, cap: int) -> tuple[str, list[SwarmTask]]:
        low = goal.lower()
        tasks: list[SwarmTask] = [
            SwarmTask(
                task_id="t1_research",
                role=ROLE_RESEARCHER,
                title="Context & Evidence Research",
                instruction=f"Gather facts, relevant files, constraints, and prior context for: {goal}",
            )
        ]
        if re.search(r"\b(code|script|implement|build|plugin|fix|refactor|function|test)\b", low):
            tasks.append(SwarmTask(
                task_id="t2_coder",
                role=ROLE_CODER,
                title="Solution Implementation & Verification Plan",
                instruction=f"Design or produce clean, tested implementation artifacts for: {goal}",
            ))
        if re.search(r"\b(screen|window|click|browser|ui|desktop)\b", low):
            tasks.append(SwarmTask(
                task_id="t3_visual",
                role=ROLE_VISUAL_OPERATOR,
                title="Desktop UI State Inspection",
                instruction=f"Inspect or operate required desktop UI elements safely for: {goal}",
                tool_name="autonomous_screen_agent",
                tool_args={"action": "observe"},
            ))
        tasks.append(SwarmTask(
            task_id="t4_security",
            role=ROLE_SECURITY_AUDITOR,
            title="Security & Risk-Tier Audit",
            instruction=f"Audit safety boundaries, untrusted inputs, and side-effect risks for: {goal}",
            depends_on=[t.task_id for t in tasks if t.role == ROLE_CODER],
        ))
        return "Parallel Researcher + Specialist + Security Auditor + Critic pipeline", tasks[:cap]

    def _run_single_agent(
        self,
        run_id: str,
        task: SwarmTask,
        *,
        run_tool_fn: Callable[[str, dict[str, Any]], str] | None,
        agent_llm_fn: Callable[[SwarmTask, list[dict[str, Any]], str], str] | None,
        on_progress: Callable[[str, str, str, str], None] | None,
    ) -> dict[str, Any]:
        step_key = f"[{task.role}] {task.title}"
        if on_progress:
            on_progress(f"▶ Spawning {step_key}", "executing", step_key, "writing")

        # 1. Pre-execution Critic side-effect risk gate
        risk_tier = self.critic.classify_side_effect_risk(task)
        if risk_tier != safety_guard.SAFE and task.tool_name:
            action_name = str(task.tool_args.get("action") or task.tool_args.get("mode") or "act")
            gate_out = safety_guard.enforce_gate(
                task.tool_name,
                action_name,
                f"Swarm ({task.role}): {task.title}",
                f"Tool: {task.tool_name} | Args: {json.dumps(task.tool_args, ensure_ascii=False)[:180]}",
                lambda: str(run_tool_fn(task.tool_name, dict(task.tool_args)) if callable(run_tool_fn) else "Executed."),
                key=f"swarm_{run_id}_{task.task_id}",
            )
            verdict = self.critic.verify_task_output(task, gate_out, self.blackboard.read_facts(run_id))
            status = "gated" if verdict.gated else ("done" if verdict.passed else "failed")
            self.blackboard.update_task(run_id, task.task_id, status=status, output=gate_out, verdict=verdict, attempts=1)
            if on_progress:
                on_progress(f"⚠ {step_key} -> {status}", "verifying", step_key, "done" if verdict.passed else "fixing")
            return {
                "task_id": task.task_id,
                "role": task.role,
                "title": task.title,
                "output": gate_out,
                "verified": verdict.passed,
                "attempts": 1,
                "verdict": verdict.to_dict(),
            }

        # 2. Execute (up to 2 attempts if Critic requests remediation)
        remediation_hint = ""
        last_output = ""
        last_verdict = CriticVerdict(passed=False, score=0.0)
        total_lat_ms = 0.0

        for attempt in (1, 2):
            if _ACTIVE_CANCEL.is_set():
                aborted = CriticVerdict(passed=False, score=0.0, issues=["Cancelled by user."])
                self.blackboard.update_task(run_id, task.task_id, status="cancelled", output="Cancelled", verdict=aborted, attempts=attempt)
                return {
                    "task_id": task.task_id,
                    "role": task.role,
                    "title": task.title,
                    "output": "Cancelled",
                    "verified": False,
                    "attempts": attempt,
                    "verdict": aborted.to_dict(),
                }

            facts = self.blackboard.read_facts(run_id)
            t0 = time.perf_counter()
            try:
                if task.role == ROLE_VISUAL_OPERATOR and task.tool_name:
                    with _SCREEN_OPERATOR_LOCK:
                        last_output = self._invoke_agent_work(task, facts, remediation_hint, run_tool_fn, agent_llm_fn)
                else:
                    last_output = self._invoke_agent_work(task, facts, remediation_hint, run_tool_fn, agent_llm_fn)
            except Exception as exc:
                last_output = f"failed: {type(exc).__name__}: {exc}"
            lat_ms = (time.perf_counter() - t0) * 1000.0
            total_lat_ms += lat_ms

            last_verdict = self.critic.verify_task_output(task, last_output, facts)
            telemetry.record_tool_event(
                task.tool_name or f"swarm_{task.role}",
                {"task_id": task.task_id, "role": task.role, "attempt": attempt},
                last_output,
                lat_ms,
                success=last_verdict.passed,
                db_path=self._telemetry_db,
            )

            if last_verdict.passed or last_verdict.gated:
                self.blackboard.post_fact(
                    run_id,
                    task.role,
                    f"{task.task_id}:{task.title[:60]}",
                    {"summary": last_output[:800], "critic_score": last_verdict.score},
                    confidence=last_verdict.score,
                )
                self.blackboard.update_task(
                    run_id,
                    task.task_id,
                    status="done" if last_verdict.passed else "gated",
                    output=last_output,
                    verdict=last_verdict,
                    attempts=attempt,
                    latency_ms=total_lat_ms,
                )
                if on_progress:
                    on_progress(
                        f"✓ Critic approved {step_key} (score={last_verdict.score:.2f})",
                        "verifying",
                        step_key,
                        "done",
                    )
                return {
                    "task_id": task.task_id,
                    "role": task.role,
                    "title": task.title,
                    "output": last_output,
                    "verified": last_verdict.passed,
                    "attempts": attempt,
                    "verdict": last_verdict.to_dict(),
                }

            remediation_hint = (
                f"CRITIC REJECTED ATTEMPT {attempt}: {'; '.join(last_verdict.issues)}. "
                f"Remediation: {last_verdict.remediation}"
            )
            self.blackboard.post_fact(
                run_id,
                "critic",
                f"reject:{task.task_id}:attempt{attempt}",
                {"issues": last_verdict.issues, "remediation": last_verdict.remediation},
                confidence=1.0,
            )
            if on_progress and attempt == 1:
                on_progress(f"↻ Critic requested retry on {step_key}: {'; '.join(last_verdict.issues)[:80]}", "verifying", step_key, "fixing")

        self.blackboard.update_task(
            run_id,
            task.task_id,
            status="failed",
            output=last_output,
            verdict=last_verdict,
            attempts=2,
            latency_ms=total_lat_ms,
        )
        if on_progress:
            on_progress(f"✗ Critic rejected {step_key}", "verifying", step_key, "failed")
        return {
            "task_id": task.task_id,
            "role": task.role,
            "title": task.title,
            "output": last_output,
            "verified": False,
            "attempts": 2,
            "verdict": last_verdict.to_dict(),
        }

    @staticmethod
    def _invoke_agent_work(
        task: SwarmTask,
        facts: list[dict[str, Any]],
        remediation_hint: str,
        run_tool_fn: Callable[[str, dict[str, Any]], str] | None,
        agent_llm_fn: Callable[[SwarmTask, list[dict[str, Any]], str], str] | None,
    ) -> str:
        if agent_llm_fn is not None:
            return str(agent_llm_fn(task, facts, remediation_hint) or "")

        if task.tool_name and callable(run_tool_fn):
            args = dict(task.tool_args)
            if remediation_hint:
                args["_critic_remediation"] = remediation_hint
            return str(run_tool_fn(task.tool_name, args) or "")

        facts_txt = "\n".join(
            f"- [{f['author_role']}] {f['key']}: {json.dumps(f['value'], ensure_ascii=False)[:240]}"
            for f in facts[-10:]
        ) or "(none yet)"
        role_persona = {
            ROLE_RESEARCHER: "Specialized Research Analyst: cite concrete facts, paths, and constraints.",
            ROLE_CODER: "Specialized Principal Engineer: write clean, safe, modular code without Qt/eval/shell=True.",
            ROLE_VISUAL_OPERATOR: "Specialized Visual Desktop Operator: reason carefully over UI controls and window states.",
            ROLE_SECURITY_AUDITOR: "Specialized Security Auditor: inspect risks, untrusted inputs, secrets, and confirmation gates.",
        }.get(task.role, "Specialized Agent")

        prompt = (
            f"Role: {role_persona}\n"
            f"Task: {task.title}\n"
            f"Instruction: {task.instruction}\n"
            + (f"\n{remediation_hint}\n" if remediation_hint else "")
            + f"\nShared Blackboard Facts:\n{facts_txt}\n\n"
            "Provide a concise, high-signal specialist deliverable."
        )
        try:
            from core import gemini
            out = gemini.text(prompt, tier=gemini.SMART)
            if out and out.strip():
                return out.strip()
        except Exception:
            pass
        return f"[{task.role}] Completed analysis for '{task.title}': verified against {len(facts)} blackboard fact(s)."

    def execute_swarm(
        self,
        goal: str,
        *,
        catalog_text: str = "",
        max_tasks: int = 4,
        run_tool_fn: Callable[[str, dict[str, Any]], str] | None = None,
        decompose_fn: Callable[[str, str], list[SwarmTask] | dict[str, Any] | None] | None = None,
        agent_llm_fn: Callable[[SwarmTask, list[dict[str, Any]], str], str] | None = None,
        synthesize_fn: Callable[[str, str, list[dict[str, Any]], list[dict[str, Any]]], str] | None = None,
        on_progress: Callable[[str, str, str, str], None] | None = None,
    ) -> dict[str, Any]:
        """Run parallel dependency waves over `SwarmBlackboard` with independent `CriticAgent`."""
        clear_swarm_cancel()
        run_id = f"swarm_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}_{secrets.token_hex(3)}"
        strategy, tasks = self.decompose_goal(
            goal,
            catalog_text,
            max_tasks=max_tasks,
            decompose_fn=decompose_fn,
        )
        self.blackboard.create_run(run_id, goal, strategy)
        try:
            from core.memory import knowledge_graph
            kg_sub = knowledge_graph.query_graph(goal, max_hops=2, top_k=5)
            if kg_sub.get("nodes"):
                self.blackboard.post_fact(
                    run_id,
                    "knowledge_graph",
                    "prior_relational_context",
                    {"entities": len(kg_sub["nodes"]), "summary": kg_sub["formatted"][:1200]},
                    confidence=0.95,
                )
        except Exception:
            pass
        for t in tasks:
            self.blackboard.register_task(run_id, t)

        try:
            from dashboard.server import broadcast_mission_log
            broadcast_mission_log(f"Swarm '{run_id}' spawned {len(tasks)} sub-agents: {[t.role for t in tasks]}", source="swarm", status="executing")
        except Exception:
            pass

        task_map = {t.task_id: t for t in tasks}
        completed_ids: set[str] = set()
        pending_ids: set[str] = set(task_map.keys())
        results_by_id: dict[str, dict[str, Any]] = {}
        waves_executed = 0

        while pending_ids and not _ACTIVE_CANCEL.is_set():
            ready = [
                task_map[tid]
                for tid in sorted(pending_ids)
                if all(dep not in task_map or dep in completed_ids for dep in task_map[tid].depends_on)
            ]
            if not ready:
                # Break any accidental dependency cycle deterministically
                ready = [task_map[sorted(pending_ids)[0]]]

            waves_executed += 1
            with ThreadPoolExecutor(max_workers=min(self._max_workers, len(ready)), thread_name_prefix="jarvis-swarm") as pool:
                fut_map = {
                    pool.submit(
                        self._run_single_agent,
                        run_id,
                        t,
                        run_tool_fn=run_tool_fn,
                        agent_llm_fn=agent_llm_fn,
                        on_progress=on_progress,
                    ): t
                    for t in ready
                }
                for fut in as_completed(fut_map):
                    t = fut_map[fut]
                    res = fut.result()
                    results_by_id[t.task_id] = res
                    pending_ids.discard(t.task_id)
                    completed_ids.add(t.task_id)

        ordered_results = [results_by_id[t.task_id] for t in tasks if t.task_id in results_by_id]
        all_facts = self.blackboard.read_facts(run_id)

        if synthesize_fn is not None:
            final_report = synthesize_fn(goal, strategy, ordered_results, all_facts)
        else:
            final_report = self._default_synthesize(goal, strategy, ordered_results, all_facts)

        final_verdict = self.critic.verify_final_synthesis(goal, ordered_results, final_report)
        status = (
            "cancelled" if _ACTIVE_CANCEL.is_set()
            else ("awaiting_confirmation" if final_verdict.gated else ("done" if final_verdict.passed else "failed"))
        )
        self.blackboard.finalize_run(
            run_id,
            status=status,
            critic_verdict=final_verdict.to_dict(),
            final_report=final_report,
        )

        try:
            from dashboard.server import broadcast_mission_log
            broadcast_mission_log(
                f"Swarm '{run_id}' finished ({status}, critic_score={final_verdict.score:.2f})",
                source="critic",
                status=status,
            )
        except Exception:
            pass

        return {
            "run_id": run_id,
            "goal": goal,
            "strategy": strategy,
            "status": status,
            "waves_executed": waves_executed,
            "tasks": ordered_results,
            "blackboard_facts": all_facts,
            "critic_verdict": final_verdict.to_dict(),
            "final_report": final_report,
        }

    @staticmethod
    def _default_synthesize(
        goal: str,
        strategy: str,
        ordered_results: list[dict[str, Any]],
        facts: list[dict[str, Any]],
    ) -> str:
        verified_blocks = [
            f"[{r['role'].upper()} · {r['title']} · critic={r['verdict'].get('score', 0):.2f}]\n{r['output']}"
            for r in ordered_results
            if r.get("verified")
        ]
        gated_blocks = [
            r["verdict"].get("gate_message", "")
            for r in ordered_results
            if (r.get("verdict") or {}).get("gated")
        ]
        if gated_blocks:
            return "\n".join(gated_blocks)
        if not verified_blocks:
            return "Critic Agent blocked unverified sub-agent outputs: " + "; ".join(
                f"{r['role']}: {r['verdict'].get('issues')}" for r in ordered_results
            )

        synth_prompt = (
            f"Synthesize a concise, verified executive response for the user's goal.\n"
            f"Goal: {goal}\nStrategy: {strategy}\n"
            f"Critic-Verified Specialist Outputs ({len(verified_blocks)}/{len(ordered_results)}, {len(facts)} blackboard facts):\n\n"
            + "\n\n".join(verified_blocks)
        )
        try:
            from core import gemini
            ans = gemini.text(synth_prompt, tier=gemini.SMART)
            if ans and ans.strip():
                return ans.strip()
        except Exception:
            pass
        return "\n\n".join(verified_blocks)
