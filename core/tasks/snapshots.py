"""core/tasks/snapshots.py — bounded, read-only snapshot staging for background agents.

Why this exists:
    `core/tasks/tools.py` deliberately jails `file_processor` inside each task's
    own folder (`memory/tasks/<id>/`). Rather than weakening that jail for the
    nightly `reflector` agent, this module copies strictly bounded, read-only
    snapshots from two whitelisted sources into `memory/tasks/<id>/inputs/`
    before the agent starts:
      1. `training_data/corrections.jsonl` (tail-bounded on whole JSONL lines)
      2. The latest few non-reflector `reports/*.md` files (excluding older
         reflector reports to prevent recursive self-citation loops)

Security boundaries:
    - Whitelist-only sources; symlinks resolving outside their whitelisted root
      are refused so `config/api_keys.json`, `memory/long_term.json`, or `.env`
      can never be staged.
    - Hard caps on file count, per-file byte size, and total staged bytes.
    - Destination filenames are stripped of any directory component and verified
      to stay strictly inside `dest_inputs_dir`.
    - Source files are never modified.
"""
from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import re

MAX_REPORT_FILES: int = 5
MAX_REPORT_BYTES: int = 24_000
MAX_CORRECTIONS_BYTES: int = 64_000
MAX_TOTAL_SNAPSHOT_BYTES: int = 160_000
LORA_TARGET_EXAMPLES: int = 200

_SAFE_NAME_RE = re.compile(r"[^A-Za-z0-9._-]+")
_REFLECTOR_HEADER_RE = re.compile(r"(?im)^>\s*reflector\b")
_VALID_CATEGORIES = ("coding", "voice", "tool_use", "preference", "general")


def _is_within(candidate: Path, root: Path) -> bool:
    """Return True iff `candidate.resolve()` stays strictly inside `root.resolve()`."""
    try:
        resolved_root = root.resolve(strict=True)
        resolved_cand = candidate.resolve(strict=True)
    except OSError:
        return False
    return resolved_cand != resolved_root and resolved_root in resolved_cand.parents


def _safe_dest(dest_dir: Path, raw_name: str) -> Path:
    """Resolve a flat filename strictly inside `dest_dir`; raises ValueError on traversal."""
    base = Path(str(raw_name or "")).name
    cleaned = _SAFE_NAME_RE.sub("_", base).lstrip(".")
    if not cleaned or cleaned in (".", ".."):
        raise ValueError(f"unsafe snapshot filename: {raw_name!r}")
    dest_root = dest_dir.resolve()
    target = (dest_root / cleaned).resolve()
    if target.parent != dest_root:
        raise ValueError(f"snapshot path escapes inputs/: {raw_name!r}")
    return target


def is_reflector_report(path: Path) -> bool:
    """Detect older reflector reports by filename OR `> reflector · ...` header line.

    Prevents recursive self-citation loops where night N's reflection analyzes
    night N-1's reflection output.
    """
    stem_low = path.stem.lower()
    if "reflector" in stem_low or "reflection" in stem_low:
        return True
    try:
        with open(path, "rb") as fh:
            head = fh.read(512).decode("utf-8", errors="ignore")
        return bool(_REFLECTOR_HEADER_RE.search(head))
    except OSError:
        return True


def _truncate_utf8(raw: bytes, limit: int, marker: str = "") -> tuple[bytes, bool]:
    """Truncate UTF-8 bytes cleanly at a character boundary within `limit` bytes."""
    if limit <= 0:
        return b"", bool(raw)
    if len(raw) <= limit:
        return raw, False
    marker_bytes = marker.encode("utf-8") if marker else b""
    keep = max(0, limit - len(marker_bytes))
    sliced = raw[:keep].decode("utf-8", errors="ignore").encode("utf-8")
    return sliced + marker_bytes, True


def _analyze_and_bound_corrections(
    corrections_path: Path,
    byte_cap: int,
) -> tuple[dict, bytes]:
    """Scan `corrections.jsonl` for dataset health metrics and return tail-bounded valid JSONL."""
    stats = {
        "present": False,
        "total_valid": 0,
        "malformed_lines": 0,
        "duplicate_prompts": 0,
        "with_lessons": 0,
        "categories": {c: 0 for c in _VALID_CATEGORIES},
        "latest_timestamp": "",
        "staged_lines": 0,
        "staged_bytes": 0,
        "truncated": False,
    }
    if not corrections_path.is_file():
        return stats, b""

    stats["present"] = True
    valid_lines: list[bytes] = []
    seen_prompts: set[str] = set()

    with open(corrections_path, "r", encoding="utf-8", errors="replace") as fh:
        for raw_line in fh:
            stripped = raw_line.strip()
            if not stripped:
                continue
            try:
                obj = json.loads(stripped)
            except json.JSONDecodeError:
                stats["malformed_lines"] += 1
                continue
            if not isinstance(obj, dict) or not isinstance(obj.get("messages"), list):
                stats["malformed_lines"] += 1
                continue

            stats["total_valid"] += 1
            cat = str(obj.get("category") or "general").strip().lower()
            if cat not in stats["categories"]:
                cat = "general"
            stats["categories"][cat] += 1

            if str(obj.get("lesson") or "").strip():
                stats["with_lessons"] += 1

            ts = str(obj.get("timestamp") or "").strip()
            if ts and ts > stats["latest_timestamp"]:
                stats["latest_timestamp"] = ts

            # Track normalized user-prompt duplicates for LoRA quality auditing
            user_msg = next(
                (
                    str(m.get("content") or "").strip()
                    for m in obj["messages"]
                    if isinstance(m, dict) and m.get("role") == "user"
                ),
                "",
            )
            norm_prompt = " ".join(user_msg.lower().split())
            if norm_prompt:
                if norm_prompt in seen_prompts:
                    stats["duplicate_prompts"] += 1
                else:
                    seen_prompts.add(norm_prompt)

            valid_lines.append((stripped + "\n").encode("utf-8"))

    # Keep the most recent whole JSONL lines that fit inside `byte_cap`
    kept_rev: list[bytes] = []
    used = 0
    for line_bytes in reversed(valid_lines):
        if used + len(line_bytes) > byte_cap:
            stats["truncated"] = True
            break
        kept_rev.append(line_bytes)
        used += len(line_bytes)

    kept = list(reversed(kept_rev))
    payload = b"".join(kept)
    stats["staged_lines"] = len(kept)
    stats["staged_bytes"] = len(payload)
    return stats, payload


def stage_reflector_inputs(
    dest_inputs_dir: Path,
    corrections_path: Path | None = None,
    reports_dir: Path | None = None,
    *,
    max_reports: int = MAX_REPORT_FILES,
    max_report_bytes: int = MAX_REPORT_BYTES,
    max_corrections_bytes: int = MAX_CORRECTIONS_BYTES,
    max_total_bytes: int = MAX_TOTAL_SNAPSHOT_BYTES,
) -> list[str]:
    """Copy bounded, read-only snapshots into `dest_inputs_dir`.

    Returns a list of task-relative paths (e.g. `['inputs/snapshot_manifest.md', ...]`).
    """
    dest = Path(dest_inputs_dir)
    dest.mkdir(parents=True, exist_ok=True)

    # Reserve up to 4 KB for snapshot_manifest.md so the manifest always fits within max_total_bytes.
    manifest_reserve = min(4_096, max(512, max_total_bytes // 10))
    remaining_budget = max(0, max_total_bytes - manifest_reserve)

    staged_files: list[str] = []
    staged_report_rows: list[str] = []
    excluded_reflector = 0
    rejected_unsafe = 0
    skipped_by_cap = 0

    # 1. Stage bounded corrections.jsonl (whitelist: exact file inside its parent training_data/)
    corr_stats = {
        "present": False,
        "total_valid": 0,
        "malformed_lines": 0,
        "duplicate_prompts": 0,
        "with_lessons": 0,
        "categories": {c: 0 for c in _VALID_CATEGORIES},
        "latest_timestamp": "",
        "staged_lines": 0,
        "staged_bytes": 0,
        "truncated": False,
    }
    if corrections_path is not None:
        cp = Path(corrections_path)
        if cp.name == "corrections.jsonl" and _is_within(cp, cp.parent) and cp.is_file():
            cap = min(max_corrections_bytes, remaining_budget)
            corr_stats, payload = _analyze_and_bound_corrections(cp, cap)
            if payload:
                target = _safe_dest(dest, "corrections.jsonl")
                target.write_bytes(payload)
                remaining_budget = max(0, remaining_budget - len(payload))
                staged_files.append(f"inputs/{target.name}")
        elif cp.exists() or cp.is_symlink():
            rejected_unsafe += 1

    # 2. Stage latest N non-reflector reports/*.md (excluding older reflector reports & symlinks out)
    if reports_dir is not None and max_reports > 0:
        rdir = Path(reports_dir)
        if rdir.is_dir():
            candidates: list[tuple[float, str, Path]] = []
            for entry in rdir.iterdir():
                if entry.name.startswith(".") or entry.suffix.lower() != ".md":
                    continue
                if not _is_within(entry, rdir) or not entry.is_file():
                    rejected_unsafe += 1
                    continue
                if is_reflector_report(entry):
                    excluded_reflector += 1
                    continue
                try:
                    mtime = entry.stat().st_mtime
                except OSError:
                    continue
                candidates.append((mtime, entry.name, entry))

            # Newest first, deterministic tie-break on filename
            candidates.sort(key=lambda item: (item[0], item[1]), reverse=True)

            taken = 0
            for _mtime, _name, src in candidates:
                if taken >= max_reports or remaining_budget <= 0:
                    skipped_by_cap += 1
                    continue
                per_cap = min(max_report_bytes, remaining_budget)
                if per_cap <= 0:
                    skipped_by_cap += 1
                    continue
                try:
                    raw = src.read_bytes()
                except OSError:
                    continue
                marker = f"\n\n[... snapshot truncated at {per_cap} bytes ...]\n"
                bounded, was_cut = _truncate_utf8(raw, per_cap, marker=marker)
                if not bounded:
                    skipped_by_cap += 1
                    continue
                # Avoid filename collisions with corrections.jsonl or snapshot_manifest.md
                out_name = src.name
                if out_name.lower() in ("snapshot_manifest.md", "corrections.jsonl"):
                    out_name = f"report_{out_name}"
                target = _safe_dest(dest, out_name)
                target.write_bytes(bounded)
                remaining_budget = max(0, remaining_budget - len(bounded))
                taken += 1
                staged_files.append(f"inputs/{target.name}")
                staged_report_rows.append(
                    f"- `inputs/{target.name}` ({len(bounded)} bytes"
                    + (", truncated" if was_cut else "")
                    + ")"
                )

    # 3. Always write snapshot_manifest.md so the reflector has verified counts even on empty data
    has_new_data = bool(corr_stats["total_valid"] > 0 or staged_report_rows)
    remaining_lora = max(0, LORA_TARGET_EXAMPLES - corr_stats["total_valid"])
    cat_summary = ", ".join(
        f"{cat}={corr_stats['categories'].get(cat, 0)}" for cat in _VALID_CATEGORIES
    )
    now_utc = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    manifest_lines = [
        "# Reflector Read-Only Snapshot Manifest",
        "",
        f"- snapshot_utc: {now_utc}",
        f"- has_new_data: {'true' if has_new_data else 'false'}",
        f"- corrections_present: {'true' if corr_stats['present'] else 'false'}",
        f"- total_valid_corrections: {corr_stats['total_valid']}",
        f"- lora_target_examples: {LORA_TARGET_EXAMPLES}",
        f"- remaining_to_lora_target: {remaining_lora}",
        f"- corrections_with_lessons: {corr_stats['with_lessons']}",
        f"- duplicate_user_prompts: {corr_stats['duplicate_prompts']}",
        f"- malformed_correction_lines: {corr_stats['malformed_lines']}",
        f"- category_counts: {cat_summary}",
        f"- latest_correction_timestamp: {corr_stats['latest_timestamp'] or 'none'}",
        f"- staged_correction_lines: {corr_stats['staged_lines']} ({corr_stats['staged_bytes']} bytes, truncated={'true' if corr_stats['truncated'] else 'false'})",
        f"- staged_reports_count: {len(staged_report_rows)}",
        f"- excluded_older_reflector_reports: {excluded_reflector}",
        f"- skipped_reports_by_cap: {skipped_by_cap}",
        f"- rejected_unsafe_paths: {rejected_unsafe}",
        "",
        "## Staged Report Snapshots",
    ]
    if staged_report_rows:
        manifest_lines.extend(staged_report_rows)
    else:
        manifest_lines.append("- (none)")
    manifest_text = "\n".join(manifest_lines) + "\n"
    manifest_bytes, _ = _truncate_utf8(manifest_text.encode("utf-8"), manifest_reserve)
    manifest_target = _safe_dest(dest, "snapshot_manifest.md")
    manifest_target.write_bytes(manifest_bytes)

    return [f"inputs/{manifest_target.name}", *staged_files]
