"""
Continuous Learning & Correction Logger Plugin for J.A.R.V.I.S (Mark-LVII).

Captures structured user corrections into `training_data/corrections.jsonl`
(ChatML `messages` format ready for local QLoRA fine-tuning) using cross-platform
thread- and process-safe file locking, and optionally persists a durable rule via
`memory.memory_manager` so JARVIS applies the lesson immediately on the next turn.
"""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import sys
import threading
from typing import Iterator

try:
    import fcntl
except ImportError:  # Windows
    fcntl = None  # type: ignore[assignment]

try:
    import msvcrt
except ImportError:  # POSIX / Linux
    msvcrt = None  # type: ignore[assignment]


def _get_base_dir() -> Path:
    """Resolve the Mark-LVII installation root for both source and frozen runs."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent.parent


BASE_DIR: Path = _get_base_dir()
CORRECTIONS_PATH: Path = BASE_DIR / "training_data" / "corrections.jsonl"
LORA_TARGET_EXAMPLES: int = 200
VALID_CATEGORIES: tuple[str, ...] = (
    "coding",
    "voice",
    "tool_use",
    "preference",
    "general",
)

_THREAD_LOCK = threading.Lock()

PLUGIN = {
    "name": "feedback_logger",
    "description": (
        "Log a structured user correction pair into training_data/corrections.jsonl "
        "for future local QLoRA fine-tuning, and immediately save any durable lesson "
        "through memory_manager so JARVIS behaves better on the very next turn. "
        "Call this with mode='log' when the user corrects a mistake ('that was wrong', "
        "'next time do X instead', 'you should have answered...', 'log that correction'). "
        "Call this with mode='stats' when the user asks how many corrections or training "
        "examples have been collected toward the 200-example LoRA threshold. "
        "Do NOT use self_evolve for routine mistake corrections (self_evolve is for "
        "architectural directives and plugin evolution), and do NOT use save_memory "
        "alone when the user is correcting an output that should also be logged as a "
        "training pair."
    ),
    "brief": (
        "Log a user correction (user_prompt, ideal_response, optional lesson, category) "
        "to training_data/corrections.jsonl + durable memory, or check collection progress "
        "toward 200 examples (mode='stats')."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "mode": {
                "type": "STRING",
                "enum": ["log", "stats"],
                "description": (
                    "'log' (default) appends a validated correction pair to "
                    "training_data/corrections.jsonl (and stores lesson in memory if given). "
                    "'stats' reports total curated corrections and progress toward 200 examples."
                ),
            },
            "user_prompt": {
                "type": "STRING",
                "description": "What the user asked or intended (required for mode='log').",
            },
            "ideal_response": {
                "type": "STRING",
                "description": (
                    "The corrected, ideal response or workflow JARVIS should have produced "
                    "(required for mode='log')."
                ),
            },
            "lesson": {
                "type": "STRING",
                "description": (
                    "Optional concise durable rule to remember immediately via memory_manager "
                    "so future turns avoid repeating the mistake."
                ),
            },
            "category": {
                "type": "STRING",
                "enum": ["coding", "voice", "tool_use", "preference", "general"],
                "description": "Domain category for this correction. Defaults to 'general'.",
            },
        },
        "required": [],
    },
}


def _clean_text(value: object) -> str:
    """Safely normalize an incoming tool argument to a stripped string."""
    if value is None or isinstance(value, (dict, list, tuple, set, bool)):
        return ""
    return str(value).strip()


def _normalize_category(raw: object) -> str:
    """Normalize category into VALID_CATEGORIES, defaulting to 'general'."""
    cleaned = _clean_text(raw).lower().replace("-", "_").replace(" ", "_")
    return cleaned if cleaned in VALID_CATEGORIES else "general"


def lesson_memory_key(lesson: str, category: str = "general") -> tuple[str, str]:
    """Compute a deterministic, collision-free (memory_category, memory_key) pair.

    Uses the `lesson_` prefix so entries never collide with `self_evolve`'s
    `directive_*` or `evolved_*` keys.
    """
    mem_cat = "preferences" if _normalize_category(category) == "preference" else "notes"
    normalized = " ".join(lesson.lower().split())
    digest = hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:6]
    slug = re.sub(r"[^a-z0-9]+", "_", normalized).strip("_")[:28].strip("_") or "rule"
    return mem_cat, f"lesson_{slug}_{digest}"


@contextmanager
def _locked_path(path: Path) -> Iterator[None]:
    """Acquire an in-process thread lock plus a cross-platform OS file lock."""
    path.parent.mkdir(parents=True, exist_ok=True)
    lock_file = path.parent / f".{path.name}.lock"
    with _THREAD_LOCK:
        fh = None
        locked_windows = False
        try:
            fh = open(lock_file, "a+b")
            if fcntl is not None:
                fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
            elif msvcrt is not None:
                fh.seek(0, os.SEEK_END)
                if fh.tell() == 0:
                    fh.write(b"0")
                    fh.flush()
                fh.seek(0)
                msvcrt.locking(fh.fileno(), msvcrt.LK_LOCK, 1)
                locked_windows = True
            yield
        finally:
            if fh is not None:
                try:
                    if fcntl is not None:
                        fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
                    elif msvcrt is not None and locked_windows:
                        fh.seek(0)
                        msvcrt.locking(fh.fileno(), msvcrt.LK_UNLCK, 1)
                except OSError:
                    pass
                try:
                    fh.close()
                except OSError:
                    pass


def _append_record(record: dict, path: Path | None = None) -> int:
    """Append a single JSONL correction record atomically under file locking.

    Returns the total number of valid JSONL records now stored in the file.
    """
    target = path or CORRECTIONS_PATH
    line = json.dumps(record, ensure_ascii=False) + "\n"
    with _locked_path(target):
        with open(target, "a", encoding="utf-8") as fh:
            fh.write(line)
            fh.flush()
            try:
                os.fsync(fh.fileno())
            except OSError:
                pass
        return _count_valid_records_unlocked(target)


def _count_valid_records_unlocked(target: Path) -> int:
    """Count valid JSON object lines in `target` (caller must hold `_locked_path`)."""
    if not target.is_file():
        return 0
    count = 0
    with open(target, "r", encoding="utf-8", errors="replace") as fh:
        for raw in fh:
            stripped = raw.strip()
            if not stripped:
                continue
            try:
                obj = json.loads(stripped)
                if isinstance(obj, dict) and isinstance(obj.get("messages"), list):
                    count += 1
            except json.JSONDecodeError:
                continue
    return count


def _collect_stats(path: Path | None = None) -> dict:
    """Read curated correction dataset statistics safely under file locking."""
    target = path or CORRECTIONS_PATH
    counts: dict[str, int] = {cat: 0 for cat in VALID_CATEGORIES}
    total = 0
    with_lessons = 0
    malformed = 0
    latest_ts = ""

    with _locked_path(target):
        if target.is_file():
            with open(target, "r", encoding="utf-8", errors="replace") as fh:
                for raw in fh:
                    stripped = raw.strip()
                    if not stripped:
                        continue
                    try:
                        obj = json.loads(stripped)
                    except json.JSONDecodeError:
                        malformed += 1
                        continue
                    if not isinstance(obj, dict) or not isinstance(obj.get("messages"), list):
                        malformed += 1
                        continue
                    total += 1
                    cat = _normalize_category(obj.get("category"))
                    counts[cat] = counts.get(cat, 0) + 1
                    if _clean_text(obj.get("lesson")):
                        with_lessons += 1
                    ts = _clean_text(obj.get("timestamp"))
                    if ts and ts > latest_ts:
                        latest_ts = ts

    remaining = max(0, LORA_TARGET_EXAMPLES - total)
    pct = min(100.0, round((total / LORA_TARGET_EXAMPLES) * 100.0, 1))
    return {
        "total": total,
        "target": LORA_TARGET_EXAMPLES,
        "remaining": remaining,
        "progress_pct": pct,
        "by_category": counts,
        "with_lessons": with_lessons,
        "malformed_lines": malformed,
        "latest_timestamp": latest_ts,
        "path": str(target),
    }


def _persist_lesson(lesson: str, category: str) -> tuple[str, str, bool]:
    """Store a durable behavioral lesson exclusively through `memory.memory_manager`."""
    mem_cat, mem_key = lesson_memory_key(lesson, category)
    try:
        from memory import memory_manager
        memory_manager.remember(mem_key, lesson, category=mem_cat)
        return mem_cat, mem_key, True
    except Exception:
        return mem_cat, mem_key, False


def _notify_hud(player, log_line: str, panel_title: str = "", panel_body: str = "") -> None:
    """Safely send HUD output when `player` is available; no-op when `player` is None."""
    if player is None:
        return
    try:
        if hasattr(player, "write_log"):
            player.write_log(f"JARVIS: {log_line}")
    except Exception:
        pass
    if panel_title and panel_body:
        try:
            if hasattr(player, "show_content"):
                player.show_content(panel_title, panel_body)
        except Exception:
            pass


def _handle_log(params: dict, player=None, path: Path | None = None) -> str:
    """Validate and persist a single correction pair plus optional durable lesson."""
    user_prompt = _clean_text(params.get("user_prompt"))
    ideal_response = _clean_text(params.get("ideal_response"))
    lesson = _clean_text(params.get("lesson"))
    category = _normalize_category(params.get("category"))

    if not user_prompt or not ideal_response:
        missing = []
        if not user_prompt:
            missing.append("user_prompt")
        if not ideal_response:
            missing.append("ideal_response")
        msg = (
            f"Sir, I need a non-empty {' and '.join(missing)} to log a correction. "
            "Nothing was saved."
        )
        _notify_hud(player, msg)
        return msg

    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    record = {
        "timestamp": timestamp,
        "category": category,
        "messages": [
            {"role": "user", "content": user_prompt},
            {"role": "assistant", "content": ideal_response},
        ],
        "lesson": lesson,
    }

    total = _append_record(record, path=path)
    remaining = max(0, LORA_TARGET_EXAMPLES - total)

    lesson_note = ""
    if lesson:
        mem_cat, mem_key, saved = _persist_lesson(lesson, category)
        if saved:
            lesson_note = f" Durable rule saved to memory ({mem_cat}/{mem_key})."
        else:
            lesson_note = " Note: the JSONL example saved, but persisting the durable rule to memory failed."

    progress_note = (
        f"{total}/{LORA_TARGET_EXAMPLES} examples logged "
        f"({remaining} left for local QLoRA)."
        if remaining > 0
        else f"{total}/{LORA_TARGET_EXAMPLES} examples logged — ready for local QLoRA fine-tuning."
    )
    reply = f"Correction logged under '{category}'. {progress_note}{lesson_note}"
    panel_body = (
        f"Category: {category}\n"
        f"Logged At: {timestamp}\n"
        f"Progress: {total}/{LORA_TARGET_EXAMPLES} examples\n"
        f"Lesson: {lesson or '(none)'}"
    )
    _notify_hud(player, reply, "CORRECTION LOGGED", panel_body)
    return reply


def _handle_stats(player=None, path: Path | None = None) -> str:
    """Summarize collected corrections and progress toward the 200-example LoRA threshold."""
    stats = _collect_stats(path=path)
    total = stats["total"]
    target = stats["target"]
    remaining = stats["remaining"]
    pct = stats["progress_pct"]

    non_zero = [
        f"{cat}: {cnt}"
        for cat, cnt in stats["by_category"].items()
        if cnt > 0
    ]
    breakdown = ", ".join(non_zero) if non_zero else "none yet"

    if total == 0:
        reply = (
            f"Sir, we have 0/{target} curated corrections logged so far "
            "toward the local QLoRA fine-tuning threshold."
        )
    elif remaining > 0:
        reply = (
            f"Sir, we have {total}/{target} curated corrections logged ({pct}% — {breakdown}). "
            f"{remaining} more examples recommended before local QLoRA fine-tuning."
        )
    else:
        reply = (
            f"Sir, we have {total}/{target} curated corrections logged ({breakdown}) — "
            "we have reached the 200-example threshold for local QLoRA fine-tuning."
        )

    panel_lines = [
        f"Total curated corrections: {total} / {target} ({pct}%)",
        f"With durable memory lessons: {stats['with_lessons']}",
        f"Remaining to LoRA threshold: {remaining}",
        "",
        "Breakdown by category:",
    ]
    for cat in VALID_CATEGORIES:
        panel_lines.append(f"  - {cat}: {stats['by_category'].get(cat, 0)}")
    if stats["latest_timestamp"]:
        panel_lines.append(f"\nLatest entry: {stats['latest_timestamp']}")
    if stats["malformed_lines"]:
        panel_lines.append(f"Skipped malformed lines: {stats['malformed_lines']}")

    _notify_hud(player, reply, "TRAINING FEEDBACK STATS", "\n".join(panel_lines))
    return reply


def run(parameters: dict, player=None, session_memory=None) -> str:
    """Entry point discovered by `core.plugin_loader`. Never raises."""
    _ = session_memory
    try:
        if parameters is None:
            params: dict = {}
        elif isinstance(parameters, dict):
            params = parameters
        else:
            msg = "Sir, feedback_logger received invalid parameters. Nothing was saved."
            _notify_hud(player, msg)
            return msg

        mode = _clean_text(params.get("mode")).lower() or "log"
        if mode == "log":
            return _handle_log(params, player=player)
        if mode == "stats":
            return _handle_stats(player=player)

        msg = f"Sir, unknown feedback_logger mode '{mode}'. Use 'log' or 'stats'."
        _notify_hud(player, msg)
        return msg
    except Exception as exc:
        err = f"Sir, feedback_logger encountered an error: {exc}"
        _notify_hud(player, err)
        return err
