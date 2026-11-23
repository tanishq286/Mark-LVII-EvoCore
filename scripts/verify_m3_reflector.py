"""Milestone 3 verification script — Read-Only Reflector, Bounded Snapshots & Nightly Routine.

Checks (non-destructive, zero secrets printed, zero mutations to core/prompt.txt or long_term.json):
  1. `core.tasks.agents` parses `agents/reflector.md` cleanly with ONLY `recall_memory` + `file_processor`.
  2. `core.tasks.tools.TaskTools` enforces zero web/acting/write tools and strict task-folder jail.
  3. `core.tasks.snapshots.stage_reflector_inputs` handles populated data:
     - exact count/duplicate/malformed dataset health stats
     - latest 5 non-reflector reports only (excludes older reflector reports by name AND `> reflector` header)
     - enforces per-file byte cap, total byte cap, and refuses symlink escapes to `config/api_keys.json`
  4. `stage_reflector_inputs` handles empty data cleanly (`has_new_data: false`).
  5. End-to-end `TaskEngine` reflector dry-runs (populated + empty) using real `TaskTools`,
     real `file_processor(action='read')`, and real `recall_memory`, verifying cited Markdown report
     generation and unchanged SHA-256 of `core/prompt.txt` and `memory/long_term.json`.
  6. Nightly `01:00` routine seeding (`deliver='silent'`, `quiet=False`, `system=False`),
     quiet-hours safety (`23:00-07:00` fires silently with 0 spoken calls), idempotency,
     non-resurrection after user deletion, and live Linux `memory/routines.json` registration.
"""
from __future__ import annotations

from datetime import datetime, timedelta
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def _check(label: str, ok: bool, detail: str = "") -> bool:
    status = "PASS" if ok else "FAIL"
    suffix = f" ({detail})" if detail else ""
    print(f"  [{status}] {label}{suffix}")
    return ok


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else "MISSING"


def _wait_done(eng, tid: str, timeout: float = 10.0) -> dict | None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        t = eng.get(tid)
        if t and t.get("status") in ("done", "failed", "cancelled"):
            return t
        time.sleep(0.05)
    return eng.get(tid)


def main() -> int:
    all_ok = True
    print("=== Milestone 3: Read-Only Reflector & Nightly Reflection Verification ===")

    prompt_path = ROOT / "core" / "prompt.txt"
    long_term_path = ROOT / "memory" / "long_term.json"
    prompt_hash_before = _sha256(prompt_path)
    memory_hash_before = _sha256(long_term_path)

    # 1. Parse agents/reflector.md via core/tasks/agents.py
    from core.tasks import agents as agent_profiles
    profiles = agent_profiles.load([ROOT / "agents"], log=lambda _m: None)
    prof = profiles.get("reflector")
    all_ok &= _check(
        "agents/reflector.md parsed cleanly by core/tasks/agents.py",
        prof is not None
        and prof.name == "reflector"
        and prof.tools == ["recall_memory", "file_processor"]
        and prof.mode == "",
        f"tools={prof.tools if prof else None}, minutes={prof.minutes if prof else None}, rounds={prof.rounds if prof else None}",
    )

    # 2. Verify TaskTools sandbox isolation (zero web/write/acting tools + strict path jail)
    from core.tasks.tools import TaskTools, _WEB
    with tempfile.TemporaryDirectory() as td:
        workdir = Path(td) / "task_sandbox"
        tools = TaskTools(workdir, prof.tools if prof else [], log=lambda _m: None)
        no_web = (not tools.has_web) and not (_WEB & set(tools.names))
        read_only_tools = set(tools.names) == {"recall_memory", "file_processor"}
        all_ok &= _check(
            "TaskTools grants ONLY recall_memory + file_processor (zero web/write/acting tools)",
            no_web and read_only_tools,
            f"granted={tools.names}, has_web={tools.has_web}",
        )

        # Path traversal refusal & run/test refusal
        traversal_res = tools.execute(
            "file_processor",
            {"file_path": "../../config/api_keys.json", "action": "read"},
        )
        run_refused = tools.execute(
            "file_processor",
            {"file_path": "inputs/snapshot_manifest.md", "action": "run"},
        )
        write_refused = tools.execute("write_file", {"path": "x.txt", "content": "nope"})
        all_ok &= _check(
            "file_processor jail blocks path traversal, code execution, and write_file",
            "outside this task's folder" in traversal_res
            and "not available" in run_refused
            and "not available" in write_refused,
            "traversal=blocked, run=blocked, write_file=blocked",
        )

    # 3. Snapshot attachment: populated case (bounds, recursive exclusion, symlink defense)
    from core.tasks.snapshots import (
        MAX_REPORT_BYTES,
        MAX_REPORT_FILES,
        MAX_TOTAL_SNAPSHOT_BYTES,
        is_reflector_report,
        stage_reflector_inputs,
    )
    with tempfile.TemporaryDirectory() as td:
        base = Path(td)
        tdir = base / "training_data"
        rdir = base / "reports"
        cdir = base / "config"
        tdir.mkdir()
        rdir.mkdir()
        cdir.mkdir()

        # Fake secret file outside reports/
        secret_file = cdir / "api_keys.json"
        secret_file.write_text('{"gemini_api_key": "DO_NOT_STAGE"}', encoding="utf-8")

        # Populated corrections.jsonl (4 valid rows including 1 duplicate prompt + 1 malformed line)
        corr = tdir / "corrections.jsonl"
        corr_rows = [
            json.dumps({
                "timestamp": "2026-11-23T18:00:00Z",
                "category": "coding",
                "messages": [
                    {"role": "user", "content": "Run provider tests"},
                    {"role": "assistant", "content": "Run python -u -m providers._tests without hiding exit status."},
                ],
                "lesson": "Never pipe test commands into tail without pipefail.",
            }),
            json.dumps({
                "timestamp": "2026-11-23T19:00:00Z",
                "category": "coding",
                "messages": [
                    {"role": "user", "content": "Run provider tests"},
                    {"role": "assistant", "content": "Always run full test command and verify exit code 0."},
                ],
                "lesson": "Check exact exit code on every test run.",
            }),
            "not valid json at all",
            json.dumps({
                "timestamp": "2026-11-23T19:30:00Z",
                "category": "tool_use",
                "messages": [
                    {"role": "user", "content": "Check my Bluetooth headset"},
                    {"role": "assistant", "content": "Switch to paired mic profile automatically."},
                ],
                "lesson": "",
            }),
            json.dumps({
                "timestamp": "2026-11-23T19:45:00Z",
                "category": "preference",
                "messages": [
                    {"role": "user", "content": "Keep summaries concise"},
                    {"role": "assistant", "content": "Use crisp GitHub Markdown tables and bullets."},
                ],
                "lesson": "Keep status responses concise with clickable file links.",
            }),
        ]
        corr.write_text("\n".join(corr_rows) + "\n", encoding="utf-8")

        # 7 normal non-reflector reports (1 oversized > 35 KB), plus 2 older reflector reports + 1 secret symlink
        now_ts = time.time()
        for idx in range(7):
            rp = rdir / f"2026-11-{10 + idx:02d}-research-topic-{idx}.md"
            body = f"# Research {idx}\n\n> researcher · 2026-11-{10 + idx:02d} 12:00\n\n"
            if idx == 6:
                body += "X" * 35_000
            else:
                body += f"Findings for topic {idx}.\n"
            rp.write_text(body, encoding="utf-8")
            os.utime(rp, (now_ts - (10 - idx) * 60, now_ts - (10 - idx) * 60))

        # Older reflector report #1 (detected by filename)
        ref1 = rdir / "2026-11-22-nightly-reflection-t111.md"
        ref1.write_text("# Nightly reflection\n\nOld reflection text.\n", encoding="utf-8")
        # Older reflector report #2 (neutral filename, detected by `> reflector ·` header!)
        ref2 = rdir / "2026-11-22-audit-summary-t222.md"
        ref2.write_text("# Audit Summary\n\n> reflector · 2026-11-22 01:00 → 01:02\n\nOld body.\n", encoding="utf-8")

        # Symlink inside reports/ pointing to config/api_keys.json (must be REFUSED!)
        sym = rdir / "2026-11-23-leaked-secret.md"
        try:
            sym.symlink_to(secret_file)
            symlink_created = True
        except OSError:
            symlink_created = False

        dest = base / "inputs_pop"
        staged = stage_reflector_inputs(dest, corrections_path=corr, reports_dir=rdir)
        staged_names = sorted(p.name for p in dest.iterdir())
        staged_reports = [n for n in staged_names if n.endswith(".md") and n != "snapshot_manifest.md"]
        total_bytes = sum(p.stat().st_size for p in dest.iterdir())
        oversized_file = dest / "2026-11-16-research-topic-6.md"
        manifest_text = (dest / "snapshot_manifest.md").read_text(encoding="utf-8")

        all_ok &= _check(
            "Populated snapshot stages corrections.jsonl + manifest + latest 5 non-reflector reports",
            "corrections.jsonl" in staged_names
            and "snapshot_manifest.md" in staged_names
            and len(staged_reports) == MAX_REPORT_FILES
            and "2026-11-16-research-topic-6.md" in staged_reports
            and "2026-11-10-research-topic-0.md" not in staged_reports,
            f"staged={len(staged)}, reports={len(staged_reports)}/{MAX_REPORT_FILES}",
        )
        all_ok &= _check(
            "Older reflector reports excluded (filename + header) and symlink escape blocked",
            ref1.name not in staged_names
            and ref2.name not in staged_names
            and sym.name not in staged_names
            and is_reflector_report(ref1)
            and is_reflector_report(ref2)
            and "excluded_older_reflector_reports: 2" in manifest_text
            and (not symlink_created or "rejected_unsafe_paths: 1" in manifest_text),
            f"excluded_reflector=2, symlink_blocked={symlink_created}",
        )
        all_ok &= _check(
            "Per-file and total byte caps enforced cleanly",
            oversized_file.is_file()
            and oversized_file.stat().st_size <= MAX_REPORT_BYTES
            and "snapshot truncated" in oversized_file.read_text(encoding="utf-8")
            and total_bytes <= MAX_TOTAL_SNAPSHOT_BYTES
            and "total_valid_corrections: 4" in manifest_text
            and "duplicate_user_prompts: 1" in manifest_text
            and "malformed_correction_lines: 1" in manifest_text,
            f"oversized={oversized_file.stat().st_size}B<={MAX_REPORT_BYTES}B, total={total_bytes}B<={MAX_TOTAL_SNAPSHOT_BYTES}B",
        )

    # 4. Snapshot attachment: empty case
    with tempfile.TemporaryDirectory() as td:
        base = Path(td)
        dest_empty = base / "inputs_empty"
        staged_empty = stage_reflector_inputs(
            dest_empty,
            corrections_path=base / "training_data" / "corrections.jsonl",
            reports_dir=base / "reports",
        )
        empty_manifest = (dest_empty / "snapshot_manifest.md").read_text(encoding="utf-8")
        all_ok &= _check(
            "Empty-data snapshot stages clean manifest with has_new_data: false",
            staged_empty == ["inputs/snapshot_manifest.md"]
            and "has_new_data: false" in empty_manifest
            and "total_valid_corrections: 0" in empty_manifest,
            f"staged={staged_empty}",
        )

    # 5. Reflector end-to-end dry-runs (populated + empty) via TaskEngine
    from core.tasks.engine import TaskEngine
    with tempfile.TemporaryDirectory() as td:
        base = Path(td)
        tdir = base / "training_data"
        rdir = base / "reports"
        tdir.mkdir()
        rdir.mkdir()
        (tdir / "corrections.jsonl").write_text(
            json.dumps({
                "timestamp": "2026-11-23T19:50:00Z",
                "category": "coding",
                "messages": [
                    {"role": "user", "content": "Verify exit status"},
                    {"role": "assistant", "content": "Never pipe tests to tail without pipefail."},
                ],
                "lesson": "Always preserve exit code on verification commands.",
            }) + "\n",
            encoding="utf-8",
        )

        tool_results_seen: list[str] = []

        def mock_ask_populated(_system, turns, _decls):
            tool_turns = [t for t in turns if t.get("role") == "tool"]
            if len(tool_turns) == 0:
                return {
                    "text": "Reading snapshot manifest.",
                    "calls": [{
                        "id": "c1",
                        "name": "file_processor",
                        "args": {"file_path": "inputs/snapshot_manifest.md", "action": "read"},
                    }],
                }
            if len(tool_turns) == 1:
                tool_results_seen.append(tool_turns[0]["results"][0]["result"])
                return {
                    "text": "Reading corrections snapshot and checking memory.",
                    "calls": [
                        {
                            "id": "c2",
                            "name": "file_processor",
                            "args": {"file_path": "inputs/corrections.jsonl", "action": "read"},
                        },
                        {
                            "id": "c3",
                            "name": "recall_memory",
                            "args": {"query": "lesson"},
                        },
                    ],
                }
            tool_results_seen.extend(r["result"] for r in tool_turns[1]["results"])
            return {
                "text": (
                    "### 1. Executive Summary\n"
                    "- Reviewed `inputs/snapshot_manifest.md` and `inputs/corrections.jsonl` (1 valid correction).\n\n"
                    "### 2. Recurring Mistake & Friction Patterns\n"
                    "- **Source:** `inputs/corrections.jsonl` (`2026-11-23T19:50:00Z`)\n"
                    "- **Observed friction:** Piping test commands masked non-zero exit codes.\n\n"
                    "### 3. Human-Reviewable Proposals for `core/prompt.txt`\n"
                    "- Status: Proposed (Human Approval Required — Not Auto-Applied)\n"
                    "- Proposal: Require explicit exit-code checks on verification commands.\n\n"
                    "### 4. Candidate Plugins & Skills (M4 Branch-Gated Workflow)\n"
                    "- None warranted from a single correction yet.\n\n"
                    "### 5. LoRA Dataset Health Check\n"
                    "- 1/200 curated examples collected (coding=1, duplicates=0, malformed=0).\n"
                ),
                "calls": [],
            }

        eng_pop = TaskEngine(
            base / "tasks",
            rdir,
            [ROOT / "agents"],
            ask_fn=mock_ask_populated,
            training_dir=tdir,
            tick=0.05,
            log=lambda _m: None,
        )
        try:
            t_pop = eng_pop.start("Nightly reflection dry-run", agent="reflector", deliver="silent")
            done_pop = _wait_done(eng_pop, t_pop["id"])
        finally:
            eng_pop.shutdown(wait=2.0)

        rep_pop = Path(done_pop["report"]) if done_pop and done_pop.get("report") else None
        rep_text = rep_pop.read_text(encoding="utf-8") if rep_pop and rep_pop.is_file() else ""
        all_ok &= _check(
            "Populated reflector dry-run reads inputs/ via real file_processor + writes cited report",
            done_pop is not None
            and done_pop["status"] == "done"
            and rep_pop is not None
            and rep_pop.is_file()
            and "> reflector ·" in rep_text
            and "2026-11-23T19:50:00Z" in rep_text
            and "Human Approval Required" in rep_text
            and "1/200" in rep_text
            and any("<<<UNTRUSTED source=file" in r for r in tool_results_seen)
            and any("2026-11-23T19:50:00Z" in r for r in tool_results_seen),
            f"status={done_pop['status'] if done_pop else None}, report={rep_pop.name if rep_pop else None}",
        )

        # Empty-data dry-run
        empty_base = base / "empty_run"
        empty_rdir = empty_base / "reports"
        empty_rdir.mkdir(parents=True)

        def mock_ask_empty(_system, turns, _decls):
            tool_turns = [t for t in turns if t.get("role") == "tool"]
            if not tool_turns:
                return {
                    "text": "Checking snapshot manifest.",
                    "calls": [{
                        "id": "e1",
                        "name": "file_processor",
                        "args": {"file_path": "inputs/snapshot_manifest.md", "action": "read"},
                    }],
                }
            manifest_out = tool_turns[0]["results"][0]["result"]
            if "has_new_data: false" in manifest_out:
                return {
                    "text": (
                        "## No New Issues to Review\n\n"
                        "- Verified via `inputs/snapshot_manifest.md`: `0/200` corrections, `0` recent reports.\n"
                        "- No action required tonight.\n"
                    ),
                    "calls": [],
                }
            return {"text": "Unexpected data found.", "calls": []}

        eng_empty = TaskEngine(
            empty_base / "tasks",
            empty_rdir,
            [ROOT / "agents"],
            ask_fn=mock_ask_empty,
            training_dir=empty_base / "training_data",
            tick=0.05,
            log=lambda _m: None,
        )
        try:
            t_emp = eng_empty.start("Nightly reflection empty dry-run", agent="reflector", deliver="silent")
            done_emp = _wait_done(eng_empty, t_emp["id"])
        finally:
            eng_empty.shutdown(wait=2.0)

        rep_emp = Path(done_emp["report"]) if done_emp and done_emp.get("report") else None
        rep_emp_text = rep_emp.read_text(encoding="utf-8") if rep_emp and rep_emp.is_file() else ""
        all_ok &= _check(
            "Empty-data reflector dry-run writes honest 'No New Issues to Review' report",
            done_emp is not None
            and done_emp["status"] == "done"
            and "## No New Issues to Review" in rep_emp_text
            and "0/200" in rep_emp_text,
            f"status={done_emp['status'] if done_emp else None}, report={rep_emp.name if rep_emp else None}",
        )

    # Confirm ZERO automatic mutations to real core/prompt.txt or memory/long_term.json
    all_ok &= _check(
        "Zero automatic mutations to core/prompt.txt and memory/long_term.json",
        _sha256(prompt_path) == prompt_hash_before and _sha256(long_term_path) == memory_hash_before,
        "sha256 unchanged for both files",
    )

    # 6. Nightly 01:00 routine seeding & quiet-hours verification
    from core.routines.desktop import NIGHTLY_REFLECTION_TIME, seed_nightly_reflection
    from core.routines.engine import RoutineEngine

    with tempfile.TemporaryDirectory() as td:
        r_file = Path(td) / "routines.json"
        tz = datetime.now().astimezone().tzinfo
        # Start fake clock at 00:59:00 local time
        base_dt = datetime(2026, 11, 24, 0, 59, 0, tzinfo=tz)
        clock_val = [base_dt.timestamp()]
        events: list[tuple] = []

        class FakeHost:
            def ready(self):
                return False  # voice asleep — task routines must still run

            def idle_seconds(self):
                return 3600.0

            def say(self, text):
                events.append(("say", text))

            def prompt(self, text):
                events.append(("prompt", text))

            def builtin(self, name, options):
                events.append(("builtin", name))

            def start_task(self, text, agent, minutes, deliver):
                events.append(("start_task", agent, minutes, deliver))

            def log(self, text):
                events.append(("log", text))

        reng = RoutineEngine(r_file, FakeHost(), clock=lambda: clock_val[0], autostart=False, log=lambda _m: None)
        reng.set_quiet_hours("23:00-07:00")
        r1 = seed_nightly_reflection(reng)
        r2 = seed_nightly_reflection(reng)  # idempotency check
        all_ok &= _check(
            "seed_nightly_reflection() creates editable silent 01:00 daily routine idempotently",
            r1 is not None
            and r2 is not None
            and r1["id"] == r2["id"]
            and len(reng.list()) == 1
            and r1["kind"] == "task"
            and r1["agent"] == "reflector"
            and r1["deliver"] == "silent"
            and r1["quiet"] is False
            and r1["system"] is False
            and r1["trigger"]["rule"]["time"] == NIGHTLY_REFLECTION_TIME
            and r1["trigger"]["rule"]["days"] == [1, 2, 3, 4, 5, 6, 7],
            f"id={r1['id'] if r1 else None}, when={reng.describe(r1) if r1 else None}",
        )

        # Advance clock to 01:00:05 INSIDE 23:00-07:00 quiet hours
        clock_val[0] = (base_dt + timedelta(minutes=1, seconds=5)).timestamp()
        reng.tick()
        spoken_calls = [e for e in events if e[0] in ("say", "prompt")]
        task_calls = [e for e in events if e[0] == "start_task"]
        all_ok &= _check(
            "01:00 reflection fires silently during 23:00-07:00 quiet hours (0 spoken calls)",
            len(task_calls) == 1
            and task_calls[0] == ("start_task", "reflector", 15, "silent")
            and len(spoken_calls) == 0,
            f"task_calls={task_calls}, spoken_calls={len(spoken_calls)}",
        )

        # User can remove routine and re-seeding respects deletion
        removed = reng.remove(r1["id"])
        r3 = seed_nightly_reflection(reng)
        all_ok &= _check(
            "Nightly routine is user-editable/removable and never resurrects after deletion",
            removed and r3 is None and len(reng.list()) == 0,
        )

    # 7. Verify live Linux memory/routines.json registration + git ignore
    live_rpath = ROOT / "memory" / "routines.json"
    live_eng = RoutineEngine(live_rpath, object(), autostart=False, log=lambda _m: None)
    live_ref = next(
        (r for r in live_eng.list() if r.get("kind") == "task" and r.get("agent") == "reflector"),
        None,
    )
    gi = subprocess.run(
        ["git", "check-ignore", "-v", "memory/routines.json"],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    all_ok &= _check(
        "Live Linux memory/routines.json has 01:00 silent reflector routine & is git-ignored",
        live_ref is not None
        and live_ref.get("deliver") == "silent"
        and live_ref.get("enabled") is True
        and live_ref["trigger"]["rule"].get("time") == "01:00"
        and gi.returncode == 0,
        f"id={live_ref['id'] if live_ref else None}, ignore={gi.stdout.strip()}",
    )

    print("=== RESULT:", "ALL PASSED" if all_ok else "FAILED", "===")
    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main())
