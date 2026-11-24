"""core/evolution/safety_guard.py — Immutable Security Boundary, AST Gate, Risk Policy & Rollback.

This module is on IMMUTABLE_PATHS and must NEVER be modified by autonomous evolution
or self-healing loops. Every evolved skill, self-healing patch, risky action, and
evolution rollback flows through this guard.
"""
from __future__ import annotations

import ast
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import sys
import threading
from typing import Callable

if getattr(sys, "frozen", False):
    _BASE = Path(sys.executable).parent
else:
    _BASE = Path(__file__).resolve().parent.parent.parent

DEFAULT_COMMITS_PATH: Path = _BASE / "memory" / "evolution_commits.json"

# Immutable security & core self-modification targets — evolution may NEVER write here.
IMMUTABLE_PATHS: frozenset[str] = frozenset({
    "core/evolution/safety_guard.py",
    "core/confirm.py",
    "plugins/_skill_gate.py",
    "plugins/file_vault.py",
    "scripts/validate_plugin.py",
    "core/voice/voice_lock.py",
    "plugins/voice_lock.py",
})

# Protected directories that autonomous evolution cannot target directly.
PROTECTED_DIR_PREFIXES: tuple[str, ...] = (
    "core/",
    "evolution/",
    "config/",
    "scripts/",
    "gui/",
    "memory/",
    "actions/",
    "providers/",
    "dashboard/",
)

# Risk tiers for uniform enforcement across tools.
SAFE = "SAFE"
CONFIRM = "CONFIRM"
LOCKED = "LOCKED"

TOOL_RISK_POLICY: dict[str | tuple[str, str], str] = {
    # LOCKED: security-sensitive decryption / unhide / evolution approval / rollback
    ("file_vault", "decrypt"): LOCKED,
    ("file_vault", "unlock_folder"): LOCKED,
    ("file_vault", "unhide"): LOCKED,
    ("evolution_control", "approve_upgrade"): LOCKED,
    ("evolution_control", "rollback_last"): LOCKED,
    # CONFIRM: external comms, power/network state, destructive deletion, active scans/merges
    "send_message": CONFIRM,
    ("send_message", "send"): CONFIRM,
    ("computer_settings", "restart"): CONFIRM,
    ("computer_settings", "shutdown"): CONFIRM,
    ("computer_settings", "hibernate"): CONFIRM,
    ("computer_settings", "sign_out"): CONFIRM,
    ("computer_settings", "schedule_shutdown"): CONFIRM,
    ("computer_settings", "schedule_restart"): CONFIRM,
    ("computer_settings", "toggle_wifi"): CONFIRM,
    ("file_controller", "delete"): CONFIRM,
    ("file_controller", "delete_file"): CONFIRM,
    ("cybersec_defender", "defender_quick_scan"): CONFIRM,
    ("evolution_control", "self_heal_merge"): CONFIRM,
    ("evolution_control", "self_heal"): CONFIRM,
}

_QT_PREFIXES = ("PyQt5", "PyQt6", "PySide2", "PySide6", "qtpy")
_QT_WIDGET_NAMES = {"QApplication", "QGuiApplication", "QWidget", "QDialog", "QMainWindow", "QThread"}
_FORBIDDEN_DATA_FILES = ("api_keys.json", "long_term.json", "mcp_servers.json")
_FORBIDDEN_BUILTINS = {"eval", "exec", "compile", "__import__", "breakpoint"}
_OS_FORBIDDEN_FUNCS = {
    "system", "popen",
    "spawnl", "spawnle", "spawnlp", "spawnlpe",
    "spawnv", "spawnve", "spawnvp", "spawnvpe",
    "execl", "execle", "execlp", "execlpe",
    "execv", "execve", "execvp", "execvpe",
}
_SUBPROCESS_CALLS = {"run", "Popen", "call", "check_call", "check_output", "getoutput", "getstatusoutput"}
_PROTECTED_WRITE_HINTS = ("core/", "core\\", "evolution/", "evolution\\", "config/", "config\\", "safety_guard")
_ENV_FILE_RE = re.compile(r"(?:^|[\\/])\.env(?:\.[a-z0-9_-]+)?$")

_LEDGER_LOCK = threading.Lock()


def _normalize_rel_path(raw: str | Path) -> tuple[bool, str, str]:
    """Normalize a repo-relative path string and reject absolute/drive/traversal inputs."""
    text = str(raw or "").strip().replace("\\", "/")
    if not text:
        return False, "", "Empty path rejected."
    if text.startswith("/") or (len(text) >= 2 and text[1] == ":"):
        return False, "", f"Absolute or drive-prefixed path rejected: {text!r}"
    parts = [p for p in text.split("/") if p and p != "."]
    if not parts or any(p == ".." for p in parts):
        return False, "", f"Path traversal rejected: {text!r}"
    return True, "/".join(parts), ""


def assert_mutable_path(
    repo_root: Path,
    rel_path: str | Path,
    *,
    allow_tests: bool = False,
) -> tuple[bool, str]:
    """Return `(True, normalized_rel)` iff `rel_path` is safely mutable by Auto-Evolution."""
    ok, norm, err = _normalize_rel_path(rel_path)
    if not ok:
        return False, err

    root_res = Path(repo_root).resolve()
    if norm in IMMUTABLE_PATHS:
        return False, f"IMMUTABLE TARGET BLOCKED: '{norm}' is permanently protected by safety_guard."

    for pref in PROTECTED_DIR_PREFIXES:
        if norm.startswith(pref):
            return False, f"PROTECTED DIRECTORY BLOCKED: '{norm}' lies inside protected '{pref}'."

    in_plugins = norm.startswith("plugins/") and not Path(norm).name.startswith("_") and norm.endswith(".py")
    in_evolved_tests = allow_tests and norm.startswith("tests/evolved/") and norm.endswith(".py")
    if not (in_plugins or in_evolved_tests):
        allowed_desc = "plugins/<name>.py or tests/evolved/<name>.py" if allow_tests else "plugins/<name>.py"
        return False, f"OUT-OF-SCOPE WRITE BLOCKED: '{norm}' must reside under {allowed_desc}."

    # Guard against symlinks along every existing segment inside repo_root
    cur = root_res
    for part in norm.split("/"):
        cur = cur / part
        try:
            if cur.is_symlink():
                return False, f"SYMLINK TARGET BLOCKED: '{norm}' traverses symlink '{cur.name}'."
        except OSError as exc:
            return False, f"Path inspection failed for '{norm}': {exc}"

    try:
        resolved_target = (root_res / norm).resolve()
        if not resolved_target.is_relative_to(root_res):
            return False, f"PATH ESCAPE BLOCKED: '{norm}' resolves outside repo root."
    except OSError as exc:
        return False, f"Cannot resolve '{norm}': {exc}"

    if in_plugins:
        stem = Path(norm).stem
        action_collision = root_res / "actions" / f"{stem}.py"
        if action_collision.is_file():
            return False, f"CORE ACTION COLLISION BLOCKED: '{stem}' exists in actions/{stem}.py."

    return True, norm


def _docstring_expr_ids(tree: ast.AST) -> set[int]:
    ids: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            if (
                node.body
                and isinstance(node.body[0], ast.Expr)
                and isinstance(node.body[0].value, ast.Constant)
                and isinstance(node.body[0].value.value, str)
            ):
                ids.add(id(node.body[0].value))
    return ids


def _has_non_false_shell(call_node: ast.Call) -> bool:
    for kw in call_node.keywords:
        if kw.arg == "shell":
            if isinstance(kw.value, ast.Constant):
                return bool(kw.value.value)
            return True
    return False


def _dotted_name(node: ast.AST) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        parent = _dotted_name(node.value)
        return f"{parent}.{node.attr}" if parent else node.attr
    return ""


def _const_strings_in_node(node: ast.AST) -> list[str]:
    vals: list[str] = []
    for sub in ast.walk(node):
        if isinstance(sub, ast.Constant) and isinstance(sub.value, str):
            vals.append(sub.value)
    return vals


def audit_python_source(
    code: str,
    *,
    filename: str = "<candidate>",
    strict_sandbox: bool = True,
) -> dict:
    """Inspect candidate Python code for safety, evasion, and AGENTS.md violations.

    Returns `{"safe": bool, "violations": list[str], "warnings": list[str]}`.
    """
    violations: list[str] = []
    warnings: list[str] = []
    try:
        tree = ast.parse(code or "", filename=filename)
    except SyntaxError as exc:
        return {"safe": False, "violations": [f"SyntaxError: {exc}"], "warnings": []}

    os_aliases: set[str] = set()
    os_bad_funcs: set[str] = set()
    subproc_aliases: set[str] = set()
    subproc_funcs: set[str] = set()
    importlib_aliases: set[str] = set()
    importlib_funcs: set[str] = set()
    builtins_aliases: set[str] = set()
    ctypes_aliases: set[str] = set()
    shutil_aliases: set[str] = set()
    rmtree_funcs: set[str] = set()

    doc_ids = _docstring_expr_ids(tree)

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                top = alias.name.split(".")[0]
                as_name = alias.asname or alias.name.split(".")[-1]
                if top in _QT_PREFIXES:
                    violations.append(f"Forbidden Qt import: 'import {alias.name}' (line {node.lineno})")
                if alias.name == "os":
                    os_aliases.add(as_name)
                elif alias.name == "subprocess":
                    subproc_aliases.add(as_name)
                elif top == "importlib":
                    importlib_aliases.add(as_name)
                    if strict_sandbox:
                        violations.append(
                            f"Forbidden dynamic import module: 'import {alias.name}' (line {node.lineno})"
                        )
                elif alias.name == "builtins":
                    builtins_aliases.add(as_name)
                elif top == "ctypes" and strict_sandbox:
                    ctypes_aliases.add(as_name)
                    violations.append(f"Forbidden ctypes import in sandboxed plugin (line {node.lineno})")
                elif alias.name == "shutil":
                    shutil_aliases.add(as_name)

        elif isinstance(node, ast.ImportFrom):
            mod = node.module or ""
            top = mod.split(".")[0]
            if top in _QT_PREFIXES:
                violations.append(f"Forbidden Qt import: 'from {mod} import ...' (line {node.lineno})")
            if top == "importlib" and strict_sandbox:
                violations.append(f"Forbidden dynamic import from '{mod}' (line {node.lineno})")
            if top == "ctypes" and strict_sandbox:
                violations.append(f"Forbidden ctypes import from '{mod}' (line {node.lineno})")
            for alias in node.names:
                bound = alias.asname or alias.name
                if alias.name in _QT_WIDGET_NAMES:
                    violations.append(f"Forbidden Qt widget import: '{alias.name}' (line {node.lineno})")
                if mod == "os" and alias.name in _OS_FORBIDDEN_FUNCS:
                    os_bad_funcs.add(bound)
                if mod == "subprocess" and alias.name in _SUBPROCESS_CALLS:
                    subproc_funcs.add(bound)
                if top == "importlib" and alias.name in ("import_module", "__import__"):
                    importlib_funcs.add(bound)
                if mod == "shutil" and alias.name == "rmtree":
                    rmtree_funcs.add(bound)

    for node in ast.walk(tree):
        # String checks (skip docstrings)
        if isinstance(node, ast.Constant) and isinstance(node.value, str) and id(node) not in doc_ids:
            raw_s = node.value.strip()
            low = raw_s.replace("\\", "/").lower()
            for forbidden in _FORBIDDEN_DATA_FILES:
                if forbidden in low:
                    violations.append(
                        f"Direct reference to protected file '{forbidden}' is forbidden "
                        f"(use memory/config_manager.py or memory/memory_manager.py; line {node.lineno})"
                    )
            if strict_sandbox and (low == ".env" or _ENV_FILE_RE.match(low)):
                violations.append(
                    f"Direct reference to protected '.env' file is forbidden (line {node.lineno})"
                )

        # Subscript evasion on __builtins__ / builtins.__dict__
        if isinstance(node, ast.Subscript):
            vname = _dotted_name(node.value)
            if vname == "__builtins__" or vname in {f"{b}.__dict__" for b in builtins_aliases | {"builtins"}}:
                violations.append(f"Obfuscated builtins subscript access '{vname}[...]' (line {node.lineno})")

        if isinstance(node, ast.Call):
            fn = node.func
            dotted = _dotted_name(fn)

            # File write calls targeting core/, evolution/, config/, or safety_guard
            is_write_call = (
                dotted == "open"
                or dotted.endswith(".write_text")
                or dotted.endswith(".write_bytes")
            )
            if is_write_call:
                all_str = " ".join(_const_strings_in_node(node)).lower()
                if any(hint in all_str for hint in _PROTECTED_WRITE_HINTS):
                    violations.append(
                        f"Forbidden write/open call referencing protected repo path near line {node.lineno}"
                    )

            if isinstance(fn, ast.Name):
                if fn.id in _FORBIDDEN_BUILTINS:
                    violations.append(f"Forbidden execution primitive: '{fn.id}()' (line {node.lineno})")
                elif fn.id in os_bad_funcs:
                    violations.append(f"Forbidden shell primitive: 'os.{fn.id}()' (line {node.lineno})")
                elif fn.id in _QT_WIDGET_NAMES:
                    violations.append(f"Forbidden Qt widget instantiation: '{fn.id}()' (line {node.lineno})")
                elif fn.id in subproc_funcs and _has_non_false_shell(node):
                    violations.append(f"Forbidden subprocess call with shell=True: '{fn.id}()' (line {node.lineno})")
                elif strict_sandbox and fn.id in importlib_funcs:
                    violations.append(f"Forbidden dynamic import call: '{fn.id}()' (line {node.lineno})")
                elif strict_sandbox and fn.id in rmtree_funcs:
                    violations.append(f"Forbidden recursive delete primitive: 'rmtree()' (line {node.lineno})")
                elif fn.id == "getattr" and node.args:
                    t0 = _dotted_name(node.args[0])
                    if t0 in ({"__builtins__", "builtins"} | builtins_aliases):
                        violations.append(f"Obfuscated builtins lookup via getattr({t0}, ...) (line {node.lineno})")
                elif dotted == "globals" or fn.id == "vars":
                    pass

            elif isinstance(fn, ast.Attribute):
                parent = _dotted_name(fn.value)
                if parent in os_aliases and fn.attr in _OS_FORBIDDEN_FUNCS:
                    violations.append(f"Forbidden shell primitive: '{parent}.{fn.attr}()' (line {node.lineno})")
                elif parent in subproc_aliases and fn.attr in _SUBPROCESS_CALLS and _has_non_false_shell(node):
                    violations.append(
                        f"Forbidden subprocess call with shell=True: '{parent}.{fn.attr}()' (line {node.lineno})"
                    )
                elif fn.attr in _QT_WIDGET_NAMES:
                    violations.append(f"Forbidden Qt widget call: '.{fn.attr}()' (line {node.lineno})")
                elif strict_sandbox and parent in importlib_aliases and fn.attr in ("import_module", "__import__"):
                    violations.append(f"Forbidden dynamic import: '{parent}.{fn.attr}()' (line {node.lineno})")
                elif strict_sandbox and parent in shutil_aliases and fn.attr == "rmtree":
                    violations.append(f"Forbidden recursive delete: '{parent}.rmtree()' (line {node.lineno})")
                elif strict_sandbox and parent in ctypes_aliases:
                    violations.append(f"Forbidden ctypes call: '{parent}.{fn.attr}()' (line {node.lineno})")
                elif dotted.startswith(("__builtins__.", "builtins.")):
                    violations.append(f"Obfuscated builtins call '{dotted}()' (line {node.lineno})")

    # Check globals()["__builtins__"] pattern
    for node in ast.walk(tree):
        if isinstance(node, ast.Subscript) and isinstance(node.value, ast.Call):
            if _dotted_name(node.value.func) == "globals" and isinstance(node.slice, ast.Constant):
                if node.slice.value == "__builtins__":
                    violations.append(f"Obfuscated globals()['__builtins__'] access (line {node.lineno})")

    unique_violations = list(dict.fromkeys(violations))
    return {
        "safe": len(unique_violations) == 0,
        "violations": unique_violations,
        "warnings": warnings,
    }


def classify_risk(tool_name: str, action: str = "") -> str:
    """Return `SAFE`, `CONFIRM`, or `LOCKED` for `(tool_name, action)`."""
    t = str(tool_name or "").strip().lower()
    a = str(action or "").strip().lower()
    if a and (t, a) in TOOL_RISK_POLICY:
        return TOOL_RISK_POLICY[(t, a)]
    if t in TOOL_RISK_POLICY:
        return TOOL_RISK_POLICY[t]
    return SAFE


def enforce_gate(
    tool_name: str,
    action: str,
    title: str,
    detail: str,
    run_fn: Callable[[], str],
    *,
    key: str | None = None,
) -> str:
    """Execute `run_fn()` immediately if `SAFE`, or gate via `core/confirm.py` for `CONFIRM`/`LOCKED`."""
    tier = classify_risk(tool_name, action)
    if tier == SAFE:
        return run_fn()

    from core import confirm

    if confirm.pending_title():
        return (
            f"[CONFIRMATION_PENDING] Another confirmation ('{confirm.pending_title()}') "
            "is already awaiting user approval on the HUD."
        )

    gate_key = key or f"{tool_name}_{action or 'act'}"
    full_title = f"[{tier}] {title}" if not title.startswith("[") else title
    return confirm.request(
        key=gate_key,
        title=full_title,
        detail=detail,
        run=run_fn,
    )


def _load_commits_unlocked(path: Path) -> list[dict]:
    if not path.is_file():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except Exception:
        return []


def list_evolution_commits(*, ledger_path: Path | None = None) -> list[dict]:
    path = Path(ledger_path) if ledger_path is not None else DEFAULT_COMMITS_PATH
    with _LEDGER_LOCK:
        return _load_commits_unlocked(path)


def record_evolution_commit(
    commit_sha: str,
    branch: str,
    kind: str,
    target: str,
    summary: str,
    *,
    ledger_path: Path | None = None,
) -> dict:
    """Append an evolution merge/commit record so it can be cleanly reverted later."""
    path = Path(ledger_path) if ledger_path is not None else DEFAULT_COMMITS_PATH
    entry = {
        "sha": str(commit_sha).strip(),
        "branch": str(branch).strip(),
        "kind": str(kind).strip(),
        "target": str(target).strip(),
        "summary": str(summary).strip()[:200],
        "recorded_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "reverted": False,
        "revert_sha": "",
    }
    with _LEDGER_LOCK:
        entries = _load_commits_unlocked(path)
        entries.append(entry)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(entries[-100:], indent=2, ensure_ascii=False), encoding="utf-8")
    return entry


def rollback_last_evolution(repo_root: Path, *, ledger_path: Path | None = None) -> dict:
    """Revert the most recent non-reverted auto-evolution commit via `git revert --no-edit`."""
    from core.cross_os import run_git

    root = Path(repo_root).resolve()
    path = Path(ledger_path) if ledger_path is not None else DEFAULT_COMMITS_PATH

    st = run_git(root, "status", "--porcelain")
    if st.returncode != 0:
        return {"ok": False, "error": f"Failed to inspect git status: {st.stderr.strip()}"}
    if st.stdout.strip():
        return {"ok": False, "error": "Working tree is dirty; commit or stash changes before rollback."}

    with _LEDGER_LOCK:
        entries = _load_commits_unlocked(path)
        idx = -1
        for i in range(len(entries) - 1, -1, -1):
            if not entries[i].get("reverted") and entries[i].get("sha"):
                idx = i
                break
        if idx < 0:
            return {"ok": False, "error": "No non-reverted evolution commit found in ledger."}
        target_entry = dict(entries[idx])

    sha = target_entry["sha"]
    parents_res = run_git(root, "rev-list", "--parents", "-n", "1", sha)
    if parents_res.returncode != 0:
        return {"ok": False, "error": f"Commit '{sha}' not found in repository: {parents_res.stderr.strip()}"}

    parent_tokens = parents_res.stdout.strip().split()
    is_merge = len(parent_tokens) > 2
    rev_args = ["revert", "--no-edit", "-m", "1", sha] if is_merge else ["revert", "--no-edit", sha]
    rev_res = run_git(
        root,
        "-c", "user.name=JARVIS Safety Guard",
        "-c", "user.email=jarvis-local@localhost",
        *rev_args,
    )
    if rev_res.returncode != 0:
        run_git(root, "revert", "--abort")
        return {"ok": False, "error": f"git revert failed for {sha[:8]}: {rev_res.stderr.strip()}"}

    new_head = run_git(root, "rev-parse", "--short", "HEAD").stdout.strip()
    with _LEDGER_LOCK:
        entries = _load_commits_unlocked(path)
        if 0 <= idx < len(entries) and entries[idx].get("sha") == sha:
            entries[idx]["reverted"] = True
            entries[idx]["revert_sha"] = new_head
            entries[idx]["reverted_at"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(entries[-100:], indent=2, ensure_ascii=False), encoding="utf-8")

    return {
        "ok": True,
        "reverted_sha": sha,
        "revert_sha": new_head,
        "target": target_entry.get("target", ""),
        "branch": target_entry.get("branch", ""),
        "summary": target_entry.get("summary", ""),
    }
