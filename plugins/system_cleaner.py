"""plugins/system_cleaner.py — Safe Windows temp/cache scanner & CONFIRM-gated space reclaimer."""

from __future__ import annotations

import os
from pathlib import Path
import platform
import subprocess
import time

PLUGIN = {
    "name": "system_cleaner",
    "brief": "Scan and safely reclaim space from Windows temp folders, browser caches, and Recycle Bin",
    "description": (
        "Inspects %TEMP%, browser HTTP/media caches, and Windows Recycle Bin to report reclaimable "
        "disk space, and runs a CONFIRM-gated cleanup skipping active/locked files. "
        "Actions: status, scan, clean."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "action": {
                "type": "STRING",
                "description": "Action: status | scan | clean (default: status)",
            },
            "include_recycle_bin": {
                "type": "BOOLEAN",
                "description": "Whether clean also empties the Windows Recycle Bin (default: False).",
            },
            "targets": {
                "type": "ARRAY",
                "items": {"type": "STRING"},
                "description": "Optional explicit directory paths to scan/clean instead of default system caches.",
            },
            "min_age_hours": {
                "type": "NUMBER",
                "description": "Only delete files older than this many hours (default: 0).",
            },
        },
        "required": [],
    },
}

_PROTECTED_TOKENS = ("/.git", "/mark-lvii", "/system32", "/windows/winsxs")


def _default_targets() -> list[tuple[str, Path]]:
    targets: list[tuple[str, Path]] = []
    temp_dir = os.environ.get("TEMP") or os.environ.get("TMP")
    if temp_dir:
        targets.append(("Windows TEMP", Path(temp_dir)))

    local_app = os.environ.get("LOCALAPPDATA")
    if local_app:
        la = Path(local_app)
        candidates = [
            ("Chrome Cache", la / "Google" / "Chrome" / "User Data" / "Default" / "Cache"),
            ("Chrome Code Cache", la / "Google" / "Chrome" / "User Data" / "Default" / "Code Cache"),
            ("Edge Cache", la / "Microsoft" / "Edge" / "User Data" / "Default" / "Cache"),
            ("Brave Cache", la / "BraveSoftware" / "Brave-Browser" / "User Data" / "Default" / "Cache"),
        ]
        targets.extend(candidates)
        ff_dir = la / "Mozilla" / "Firefox" / "Profiles"
        if ff_dir.is_dir():
            for prof in ff_dir.iterdir():
                c2 = prof / "cache2"
                if c2.is_dir():
                    targets.append((f"Firefox ({prof.name})", c2))
    return targets


def _resolve_targets(custom_targets=None) -> list[tuple[str, Path]]:
    if isinstance(custom_targets, list) and custom_targets:
        out: list[tuple[str, Path]] = []
        for idx, raw in enumerate(custom_targets, 1):
            p = Path(str(raw)).resolve()
            out.append((f"Target {idx} ({p.name})", p))
        return out
    return _default_targets()


def _is_safe_target(path: Path) -> bool:
    low = str(path.resolve()).replace("\\", "/").lower()
    if len(low) <= 4:
        return False
    return not any(tok in low for tok in _PROTECTED_TOKENS)


def _format_size(num_bytes: int) -> str:
    val = float(max(0, num_bytes))
    for unit in ("B", "KB", "MB", "GB"):
        if val < 1024.0 or unit == "GB":
            return f"{val:.1f} {unit}" if unit != "B" else f"{int(val)} B"
        val /= 1024.0
    return f"{val:.1f} GB"


def _scan_dir(root: Path, cutoff_ts: float | None = None) -> tuple[list[Path], list[Path], int]:
    files: list[Path] = []
    dirs: list[Path] = []
    total_bytes = 0
    if not root.is_dir() or not _is_safe_target(root):
        return files, dirs, total_bytes
    for dirpath, dirnames, filenames in os.walk(root, topdown=False, followlinks=False):
        dp = Path(dirpath)
        for fn in filenames:
            fp = dp / fn
            try:
                if fp.is_symlink():
                    continue
                st = fp.stat()
                if cutoff_ts is not None and st.st_mtime > cutoff_ts:
                    continue
                files.append(fp)
                total_bytes += int(st.st_size)
            except OSError:
                continue
        for dn in dirnames:
            sub = dp / dn
            if not sub.is_symlink():
                dirs.append(sub)
    return files, dirs, total_bytes


def scan_report(params: dict | None = None) -> str:
    """Return a read-only reclaimable space summary."""
    p = params or {}
    min_age_h = max(0.0, float(p.get("min_age_hours") or 0.0))
    cutoff = (time.time() - min_age_h * 3600.0) if min_age_h > 0 else None
    targets = _resolve_targets(p.get("targets"))
    lines = ["System Cleaner Scan Report:"]
    grand_files = 0
    grand_bytes = 0
    for label, path in targets:
        if not path.is_dir():
            continue
        files, _, nbytes = _scan_dir(path, cutoff_ts=cutoff)
        grand_files += len(files)
        grand_bytes += nbytes
        lines.append(f"- {label}: {len(files)} files ({_format_size(nbytes)}) [{path}]")
    lines.append(f"Total reclaimable: {grand_files} files, {_format_size(grand_bytes)}.")
    return "\n".join(lines)


def _execute_clean(params: dict) -> str:
    min_age_h = max(0.0, float(params.get("min_age_hours") or 0.0))
    cutoff = (time.time() - min_age_h * 3600.0) if min_age_h > 0 else None
    targets = _resolve_targets(params.get("targets"))

    removed_files = 0
    skipped_files = 0
    reclaimed_bytes = 0
    lines = ["System Cleanup Summary:"]

    for label, path in targets:
        if not path.is_dir() or not _is_safe_target(path):
            continue
        files, dirs, _ = _scan_dir(path, cutoff_ts=cutoff)
        t_removed = 0
        t_skipped = 0
        t_bytes = 0
        for fp in files:
            try:
                sz = int(fp.stat().st_size)
                fp.unlink()
                t_removed += 1
                t_bytes += sz
            except OSError:
                t_skipped += 1
        for dp in dirs:
            try:
                dp.rmdir()
            except OSError:
                pass
        removed_files += t_removed
        skipped_files += t_skipped
        reclaimed_bytes += t_bytes
        lines.append(
            f"- {label}: removed {t_removed} files ({_format_size(t_bytes)}), skipped {t_skipped} in-use"
        )

    recycle_msg = "not requested"
    if params.get("include_recycle_bin"):
        if platform.system() == "Windows":
            try:
                res = subprocess.run(
                    ["powershell", "-NoProfile", "-Command", "Clear-RecycleBin -Force -ErrorAction SilentlyContinue"],
                    capture_output=True,
                    text=True,
                    timeout=30,
                    shell=False,
                )
                recycle_msg = "emptied" if res.returncode == 0 else f"completed (rc={res.returncode})"
            except Exception as exc:
                recycle_msg = f"failed ({exc})"
        else:
            recycle_msg = "skipped (non-Windows host)"

    lines.append(
        f"Reclaimed: {_format_size(reclaimed_bytes)} across {removed_files} files "
        f"(skipped={skipped_files}, recycle_bin={recycle_msg})."
    )
    return "\n".join(lines)


def run(parameters: dict, player=None, session_memory=None) -> str:
    """Plugin entrypoint for safe system cleanup."""
    params = parameters if isinstance(parameters, dict) else {}
    action = str(params.get("action") or "status").strip().lower()

    if action == "status":
        targets = _resolve_targets(params.get("targets"))
        existing = [f"{lbl} ({p})" for lbl, p in targets if p.is_dir()]
        return f"System Cleaner ready. Active targets ({len(existing)}): " + ("; ".join(existing[:6]) or "none")
    if action == "scan":
        return scan_report(params)
    if action == "clean":
        from core.evolution import safety_guard
        detail = scan_report(params)
        return safety_guard.enforce_gate(
            "system_cleaner",
            "clean",
            "Clean System Temp & Caches",
            detail,
            lambda: _execute_clean(params),
            key="system_cleaner_clean",
        )
    return f"Unknown system_cleaner action '{action}'. Use: status, scan, clean."
