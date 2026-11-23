"""Milestone 2 verification script — Continuous Learning & Feedback Logger.

Checks (non-destructive, cleans up all temporary test entries, zero secrets printed):
  1. Static AGENTS.md & 10D quality compliance on `plugins/feedback_logger.py`
     (no Qt imports, no raw api_keys.json / long_term.json access, >=75/100 score).
  2. Full plugin auto-discovery against ALL inline + bundled action tool names
     (8 active, 0 rejected, zero tool-name collisions).
  3. Tool-budget schema compliance (`description` + shorter `brief`, `compact()`
     swap, valid lowercase OpenAI JSON Schema via `providers.schema.to_openai`).
  4. `player=None` safety & malformed/blank input rejection (never raises, writes nothing).
  5. Live JSONL write, `"stats"` readout, and `memory_manager` persistence — using
     a temporary test record & test memory key that are verified and cleanly removed
     in a `finally` block.
  6. Multi-threaded concurrent append stress test (12 threads, zero torn lines).
  7. Git ignore verification (`git check-ignore -v training_data/corrections.jsonl`).
"""
from __future__ import annotations

import ast
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import re
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def _check(label: str, ok: bool, detail: str = "") -> bool:
    status = "PASS" if ok else "FAIL"
    suffix = f" ({detail})" if detail else ""
    print(f"  [{status}] {label}{suffix}")
    return ok


def _collect_core_tool_names() -> set[str]:
    """Gather all inline tool names from main.py + bundled action names from actions/."""
    names: set[str] = set()
    main_src = (ROOT / "main.py").read_text(encoding="utf-8", errors="ignore")
    tree = ast.parse(main_src)
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and any(
            getattr(t, "id", "") == "TOOL_DECLARATIONS" for t in node.targets
        ):
            decls = ast.literal_eval(node.value)
            for d in decls:
                if isinstance(d, dict) and d.get("name"):
                    names.add(str(d["name"]))
            break

    from core.action_loader import discover_actions
    act_reg = discover_actions(ROOT / "actions", logger=lambda _m: None)
    for d in act_reg.get_tool_declarations():
        if isinstance(d, dict) and d.get("name"):
            names.add(str(d["name"]))
    return names


def main() -> int:
    all_ok = True
    print("=== Milestone 2: Feedback Logger & 7-Plugin Audit Verification ===")

    plugin_path = ROOT / "plugins" / "feedback_logger.py"
    code = plugin_path.read_text(encoding="utf-8")

    # 1. Static AGENTS.md & 10D quality checks
    tree = ast.parse(code)
    imports = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imports.append(node.module)
    no_qt = not any(m.startswith("PyQt") or m.startswith("PySide") for m in imports)
    no_raw_files = ("api_keys.json" not in code) and ("long_term.json" not in code)
    all_ok &= _check("Zero Qt imports in plugins/feedback_logger.py", no_qt, f"imports={len(imports)}")
    all_ok &= _check("Zero direct reads/writes to api_keys.json or long_term.json", no_raw_files)

    from core.auto_evolution import evaluate_module_10d
    score = evaluate_module_10d(code, is_plugin=True)
    all_ok &= _check(
        "10D plugin quality gate >= 75/100 (VERIFIED)",
        score["passed"],
        f"combined={score['combined']}, tq={score['tq_score']}, omega={score['omega_score']}",
    )

    # 2. Plugin auto-discovery across all 8 plugins + core tool collision check
    core_names = _collect_core_tool_names()
    from core.plugin_loader import discover_plugins
    logs: list[str] = []
    reg = discover_plugins(ROOT / "plugins", core_names, logger=logs.append)
    ui_list = reg.list_for_ui()
    active = [p for p in ui_list if p["valid"]]
    rejected = [p for p in ui_list if not p["valid"]]
    active_names = sorted(p["name"] for p in active)
    all_ok &= _check(
        "Plugin auto-discovery: >=8 active, 0 rejected, zero collisions",
        len(active) >= 8 and len(rejected) == 0 and "feedback_logger" in active_names,
        f"active={len(active)}, rejected={len(rejected)}, core_checked={len(core_names)}",
    )

    # 3. Schema + compact brief + OpenAI JSON Schema check
    decls = reg.get_tool_declarations()
    fb_decl = next((d for d in decls if d.get("name") == "feedback_logger"), None)
    has_brief = bool(fb_decl and fb_decl.get("brief") and fb_decl.get("description"))
    brief_shorter = bool(
        has_brief and len(fb_decl["brief"]) < len(fb_decl["description"])
    )
    all_ok &= _check(
        "PLUGIN declares both description and concise brief",
        has_brief and brief_shorter,
        f"desc={len(fb_decl['description']) if fb_decl else 0} chars, "
        f"brief={len(fb_decl['brief']) if fb_decl and fb_decl.get('brief') else 0} chars",
    )

    from providers.schema import compact, to_openai
    compacted = compact(fb_decl)
    oai = to_openai([compacted])
    upper_types = re.findall(r'"type":\s*"([A-Z_]+)"', json.dumps(oai))
    all_ok &= _check(
        "providers.schema.compact() + to_openai() produce valid lowercase schema",
        compacted.get("description") == fb_decl["brief"]
        and "brief" not in compacted
        and len(oai) == 1
        and not upper_types,
        f"openai_chars={len(json.dumps(oai))}, upper_types={upper_types or 'none'}",
    )

    # 4. player=None safety + malformed-input handling (snapshot corrections.jsonl first)
    import plugins.feedback_logger as fb
    target_path = fb.CORRECTIONS_PATH
    existed_before = target_path.exists()
    original_bytes = target_path.read_bytes() if existed_before else None

    try:
        bad_inputs = [
            None,
            "not-a-dict",
            {},
            {"mode": "log", "user_prompt": "   ", "ideal_response": "valid"},
            {"mode": "log", "user_prompt": "valid", "ideal_response": ""},
            {"mode": "log", "user_prompt": ["bad"], "ideal_response": {"bad": 1}},
            {"mode": "unknown_action"},
        ]
        bad_ok = True
        for payload in bad_inputs:
            res = fb.run(payload, player=None, session_memory=None)
            if not isinstance(res, str) or not res.strip():
                bad_ok = False
        unchanged_after_bad = (
            (target_path.read_bytes() == original_bytes)
            if existed_before
            else (not target_path.exists())
        )
        all_ok &= _check(
            "Malformed/blank inputs with player=None never raise and write nothing",
            bad_ok and unchanged_after_bad,
            f"cases={len(bad_inputs)}, file_unchanged={unchanged_after_bad}",
        )

        # 5. Live JSONL write + stats readout + memory_manager persistence (with guaranteed cleanup)
        test_prompt = "__m2_verify_prompt__: run tests with pipefail"
        test_ideal = "__m2_verify_ideal__: always run python -u -m providers._tests without hiding exit status."
        test_lesson = "__m2_verify_lesson__: Never pipe test commands into tail without pipefail."
        mem_cat, mem_key = fb.lesson_memory_key(test_lesson, "coding")

        from core import cross_os as _cross_os
        from memory import memory_manager
        _orig_mirror = _cross_os.mirror_memory_file
        _cross_os.mirror_memory_file = lambda _p: False  # never touch Windows memory/ during test
        try:
            log_reply = fb.run(
                {
                    "mode": "log",
                    "user_prompt": test_prompt,
                    "ideal_response": test_ideal,
                    "lesson": test_lesson,
                    "category": "coding",
                },
                player=None,
            )
            stats_reply = fb.run({"mode": "stats"}, player=None)

            # Inspect last written JSONL line
            lines = [
                ln for ln in target_path.read_text(encoding="utf-8").splitlines() if ln.strip()
            ]
            last_rec = json.loads(lines[-1])
            schema_ok = (
                bool(last_rec.get("timestamp"))
                and last_rec.get("category") == "coding"
                and last_rec.get("lesson") == test_lesson
                and last_rec.get("messages") == [
                    {"role": "user", "content": test_prompt},
                    {"role": "assistant", "content": test_ideal},
                ]
            )
            all_ok &= _check(
                "Valid JSONL record written with timestamp/category/messages/lesson",
                schema_ok and "Correction logged" in log_reply,
                f"lines={len(lines)}, key={mem_cat}/{mem_key}",
            )

            stats_ok = (
                f"{len(lines)}/{fb.LORA_TARGET_EXAMPLES}" in stats_reply
                and "coding:" in stats_reply
            )
            all_ok &= _check(
                "mode='stats' reports accurate count and 200-example threshold progress",
                stats_ok,
                stats_reply,
            )

            # Verify memory_manager persistence
            mem_data = memory_manager.load_memory()
            stored_entry = (mem_data.get(mem_cat) or {}).get(mem_key)
            mem_val = stored_entry.get("value") if isinstance(stored_entry, dict) else stored_entry
            all_ok &= _check(
                "Durable lesson persisted through memory_manager (not raw file write)",
                mem_val == test_lesson,
                f"stored={mem_cat}/{mem_key}",
            )
        finally:
            # Always clean up the temporary memory test key before restoring mirror
            try:
                memory_manager.forget(mem_key, category=mem_cat)
                mem_after = memory_manager.load_memory()
                cleaned = mem_key not in (mem_after.get(mem_cat) or {})
                all_ok &= _check(
                    "Temporary test lesson cleaned up from memory_manager",
                    cleaned,
                    f"removed={mem_cat}/{mem_key}",
                )
            finally:
                _cross_os.mirror_memory_file = _orig_mirror
    finally:
        # Restore training_data/corrections.jsonl to exact pre-test state
        if existed_before and original_bytes is not None:
            target_path.write_bytes(original_bytes)
        else:
            if target_path.exists():
                target_path.unlink()
            lock_path = target_path.parent / f".{target_path.name}.lock"
            if lock_path.exists():
                lock_path.unlink()

    # 6. Multi-threaded concurrency safety test (isolated temp file)
    with tempfile.TemporaryDirectory() as tmpdir:
        conc_path = Path(tmpdir) / "corrections.jsonl"

        def _worker(idx: int) -> str:
            return fb._handle_log(
                {
                    "user_prompt": f"Prompt {idx}",
                    "ideal_response": f"Ideal response {idx}",
                    "category": fb.VALID_CATEGORIES[idx % len(fb.VALID_CATEGORIES)],
                },
                player=None,
                path=conc_path,
            )

        with ThreadPoolExecutor(max_workers=12) as pool:
            results = list(pool.map(_worker, range(12)))
        conc_lines = [ln for ln in conc_path.read_text(encoding="utf-8").splitlines() if ln.strip()]
        parsed = [json.loads(ln) for ln in conc_lines]
        all_ok &= _check(
            "Thread-safe concurrent file locking (12 parallel writers, 0 torn writes)",
            len(parsed) == 12 and all("Correction logged" in r for r in results),
            f"valid_lines={len(parsed)}/12",
        )

    # 7. Git ignore check on training_data/corrections.jsonl
    gi = subprocess.run(
        ["git", "check-ignore", "-v", "training_data/corrections.jsonl"],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    all_ok &= _check(
        "git check-ignore -v training_data/corrections.jsonl passes",
        gi.returncode == 0 and "training_data/" in gi.stdout,
        gi.stdout.strip(),
    )

    print("=== RESULT:", "ALL PASSED" if all_ok else "FAILED", "===")
    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main())
