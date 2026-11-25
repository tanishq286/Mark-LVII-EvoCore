"""core/evolution/planner.py — Verified Plan -> Execute -> Verify -> Recover Loop.

Injects Failure Immunity lessons from `telemetry.sqlite` / `reflection_loop` into
multi-step planning, executes each step sequentially, verifies every tool output,
triggers a single structured recovery attempt on failure (fallback tool or adaptive
recovery callback), and records full telemetry.
"""
from __future__ import annotations

import re
import threading
from typing import Any, Callable

from core.evolution import reflection_loop, telemetry

_GUI_SCREEN_CUE_RE = re.compile(
    r"\b(click|double[\s-]?click|right[\s-]?click|type\s+into|press\s+|hotkey|"
    r"on\s+screen|search\s+bar|address\s+bar|dialog|button|checkbox|drag|scroll|"
    r"go\s+to\s+.+?\s+and\s+(?:search|click|play|type|fill|submit)|"
    r"open\s+(?:chrome|edge|firefox|notepad|word|excel|calculator|settings|paint|"
    r"vscode|vs\s*code|spotify|explorer|browser|youtube).+?\b(?:and|then|go\s+to|write|type|search|click|save))\b",
    re.IGNORECASE,
)
_NON_GUI_CODE_RE = re.compile(
    r"\b(git\s+status|pytest|unit\s+test|security\s+scan|weather\s+report|"
    r"market\s+quote|research\s+report|deck_generator)\b",
    re.IGNORECASE,
)


def is_gui_screen_goal(goal: str) -> bool:
    """Return True when `goal` asks for multi-step interactive desktop GUI control."""
    text = str(goal or "").strip()
    if not text:
        return False
    if _NON_GUI_CODE_RE.search(text) and not re.search(r"\b(click|type\s+into|hotkey|button|open\s+chrome|open\s+notepad)\b", text, re.IGNORECASE):
        return False
    return bool(_GUI_SCREEN_CUE_RE.search(text))


class VerifiedPlanner:
    """Plan -> Execute -> Verify -> Recover orchestrator for multi-step commands."""

    def __init__(self, *, db_path=None):
        self._db_path = db_path

    def plan(
        self,
        goal: str,
        catalog_text: str,
        *,
        max_steps: int = 6,
        llm_json_fn: Callable[[str], dict | None] | None = None,
    ) -> dict[str, Any]:
        immunity = reflection_loop.format_failure_immunity_context(db_path=self._db_path)
        kg_context = ""
        try:
            from core.memory import knowledge_graph
            g = knowledge_graph.query_graph(goal, max_hops=2, top_k=5)
            if g.get("nodes"):
                kg_context = f"[KNOWLEDGE GRAPH CONTEXT]\n{g['formatted']}"
        except Exception:
            kg_context = ""
        requires_screen_loop = is_gui_screen_goal(goal)
        prompt = (
            f"You are JARVIS's Verified Multi-Step Planner. Break the goal into at most "
            f"{max(1, min(12, int(max_steps or 6)))} verifiable steps.\n"
            "If the request requires interactive desktop GUI chains (opening desktop apps, clicking UI controls, "
            "typing into windows, or verifying live screen changes), prefer `autonomous_screen_agent` with `action='run'` and `goal`.\n"
            "If the request is genuinely ambiguous with mutually exclusive choices that require "
            "the user to pick an option first, set `ambiguous: true` and provide 2-4 concise `options`.\n"
            f"For risky/uncertain steps, specify `fallback_tool` and `fallback_parameters`.\n\n"
            + (f"{immunity}\n\n" if immunity else "")
            + (f"{kg_context}\n\n" if kg_context else "")
            + f"Goal: {goal}\n\n"
            f"Available tools:\n{catalog_text or '(standard tools)'}\n\n"
            "Return ONLY valid JSON:\n"
            "{\n"
            '  "strategy": "One-sentence summary",\n'
            '  "ambiguous": false,\n'
            '  "options": [],\n'
            '  "steps": [\n'
            '    {"id": "step_1", "title": "...", "tool": "tool_name", "parameters": {}, '
            '"expected": "...", "fallback_tool": "", "fallback_parameters": {}}\n'
            "  ]\n"
            "}"
        )
        raw = None
        if llm_json_fn is not None:
            raw = llm_json_fn(prompt)
        else:
            from core import gemini
            raw = gemini.as_json(prompt, tier=gemini.SMART)

        if not isinstance(raw, dict):
            raw = {}
        steps = raw.get("steps") if isinstance(raw.get("steps"), list) else []
        options = [str(o).strip() for o in (raw.get("options") or []) if str(o).strip()]
        return {
            "goal": goal,
            "strategy": str(raw.get("strategy") or "Direct verified execution."),
            "ambiguous": bool(raw.get("ambiguous")) and len(options) >= 2,
            "options": options[:5],
            "steps": [s for s in steps[:max_steps] if isinstance(s, dict)],
            "requires_screen_loop": requires_screen_loop,
            "immunity_injected": bool(immunity),
            "immunity_context": immunity,
        }

    @staticmethod
    def verify_step(
        step: dict,
        output: str,
        *,
        verify_fn: Callable[[dict, str], tuple[bool, str] | bool] | None = None,
    ) -> tuple[bool, str]:
        text = str(output or "").strip()
        if verify_fn is not None:
            res = verify_fn(step, text)
            if isinstance(res, tuple):
                return bool(res[0]), str(res[1])
            return bool(res), ("verified by custom verifier" if res else "custom verifier rejected output")

        if not text:
            return False, "empty output"
        low = text.lower()
        try:
            from core import outcome
            if outcome.is_problem(text):
                return False, f"outcome problem: {text[:120]}"
        except Exception:
            pass
        for sig in telemetry.FAILURE_SIGNALS:
            if sig in low:
                return False, f"matched failure signal '{sig}'"
        if low.startswith("error:") or "timed out" in low:
            return False, f"error prefix/timeout: {text[:120]}"
        return True, "verified"

    def execute_plan(
        self,
        plan: dict[str, Any],
        run_tool_fn: Callable[[str, dict], str] | None,
        *,
        recover_fn: Callable[[dict, str, str], tuple[str, dict] | str | None] | None = None,
        verify_fn: Callable[[dict, str], tuple[bool, str] | bool] | None = None,
        on_step: Callable[[str, str, str | None, str | None], None] | None = None,
        cancel_event: threading.Event | None = None,
    ) -> dict[str, Any]:
        steps = list(plan.get("steps") or [])
        immunity = str(plan.get("immunity_context") or "")
        trace: list[dict[str, Any]] = []
        verified_cnt = 0
        recovered_cnt = 0
        failed_cnt = 0

        for idx, step in enumerate(steps, 1):
            if cancel_event is not None and cancel_event.is_set():
                return {
                    "status": "cancelled",
                    "steps_verified": verified_cnt,
                    "steps_recovered": recovered_cnt,
                    "steps_failed": failed_cnt,
                    "trace": trace,
                }

            title = str(step.get("title") or f"Step {idx}")
            tool = str(step.get("tool") or "").strip()
            params = dict(step.get("parameters") or {}) if isinstance(step.get("parameters"), dict) else {}
            step_key = f"Step {idx}: {title}"

            if on_step:
                on_step(f"▶ {step_key} ({tool or 'reasoning'})", "executing", step_key, "writing")

            t0 = __import__("time").perf_counter()
            if tool and tool.lower() != "none" and callable(run_tool_fn):
                try:
                    out = str(run_tool_fn(tool, params) or "Done.")
                except Exception as exc:
                    out = f"failed: {type(exc).__name__}: {exc}"
            else:
                from core import gemini
                prompt = f"Execute step '{title}': {step.get('expected', plan.get('goal', ''))}"
                out = gemini.text(prompt, tier=gemini.SMART) or "Completed reasoning step."
            lat_ms = (__import__("time").perf_counter() - t0) * 1000.0

            ok, note = self.verify_step(step, out, verify_fn=verify_fn)
            telemetry.record_tool_event(
                tool or "reasoning_step",
                params,
                out,
                lat_ms,
                success=ok,
                db_path=self._db_path,
            )

            recovered = False
            recovery_note = ""
            if not ok:
                if on_step:
                    on_step(f"↻ Recovering {step_key}: {note[:80]}", "verifying", step_key, "fixing")
                fb_tool = str(step.get("fallback_tool") or "").strip()
                fb_params = (
                    dict(step.get("fallback_parameters") or {})
                    if isinstance(step.get("fallback_parameters"), dict)
                    else {}
                )
                rec_out = None
                used_tool = fb_tool or tool or "recovery"
                if fb_tool and callable(run_tool_fn):
                    try:
                        rec_out = str(run_tool_fn(fb_tool, fb_params) or "Done.")
                    except Exception as exc:
                        rec_out = f"failed: {type(exc).__name__}: {exc}"
                elif recover_fn is not None:
                    alt = recover_fn(step, note, immunity)
                    if isinstance(alt, tuple) and len(alt) == 2 and callable(run_tool_fn):
                        used_tool, alt_params = str(alt[0]), dict(alt[1] or {})
                        try:
                            rec_out = str(run_tool_fn(used_tool, alt_params) or "Done.")
                        except Exception as exc:
                            rec_out = f"failed: {type(exc).__name__}: {exc}"
                    elif isinstance(alt, str) and alt:
                        rec_out = alt

                if rec_out is not None:
                    ok2, note2 = self.verify_step(step, rec_out, verify_fn=verify_fn)
                    telemetry.record_tool_event(
                        used_tool,
                        fb_params,
                        rec_out,
                        0.0,
                        success=ok2,
                        db_path=self._db_path,
                    )
                    if ok2:
                        ok = True
                        recovered = True
                        out = rec_out
                        recovery_note = f"Recovered via {used_tool}: {note2}"
                    else:
                        recovery_note = f"Recovery attempt failed: {note2}"

            if ok:
                verified_cnt += 1
                if recovered:
                    recovered_cnt += 1
                if on_step:
                    on_step(f"✓ {step_key}", "verifying", step_key, "done")
            else:
                failed_cnt += 1
                if on_step:
                    on_step(f"✗ {step_key} -> {note[:90]}", "verifying", step_key, "failed")

            trace.append({
                "index": idx,
                "title": title,
                "tool": tool,
                "verified": ok,
                "recovered": recovered,
                "note": recovery_note or note,
                "output": out[:1200],
            })

        status = "done" if failed_cnt == 0 else ("partial" if verified_cnt > 0 else "failed")
        return {
            "status": status,
            "steps_verified": verified_cnt,
            "steps_recovered": recovered_cnt,
            "steps_failed": failed_cnt,
            "trace": trace,
        }
