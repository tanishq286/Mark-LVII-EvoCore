"""scripts/validate_plugin.py — Static AST + Isolated Runtime Gate for JARVIS Plugins.

Validates any candidate or existing Mark-LVII plugin against `AGENTS.md` and
`plugins/_template.py` before it is allowed onto a `skill/*` branch or merged:

  1. Syntax: `ast.parse` + `py_compile` (writing bytecode to a temp file, never
     polluting `__pycache__`).
  2. Contract: module-level `PLUGIN` dict (`name`, `description`, `parameters`
     with `"type": "OBJECT"`, plus warning if concise `brief` is absent) and
     callable `run(parameters, player=None, session_memory=None) -> str`.
  3. Collision check: zero tool-name collisions against inline `main.py` tools,
     all bundled `actions/*.py`, and all other active `plugins/*.py`.
  4. Security AST gate: rejects Qt/PyQt/PySide imports, rejects direct file
     references to `config/api_keys.json` or `memory/long_term.json`, and
     rejects dangerous execution primitives (`os.system`, `os.popen`, `eval`,
     `exec`, `__import__`, and `subprocess.*(..., shell=True)`).
  5. Isolated runtime smoke test: runs `run({}, player=None, session_memory=None)`
     in a clean subprocess with a strict timeout, confirming it never raises,
     returns a `str`, and does not dynamically import Qt toolkits.

Exit code is `0` iff all hard checks pass; `1` otherwise.
"""
from __future__ import annotations

import argparse
import ast
import json
from pathlib import Path
import py_compile
import re
import subprocess
import sys
import tempfile

_NAME_RE = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_]{0,63}$")
_QT_PREFIXES = ("PyQt5", "PyQt6", "PySide2", "PySide6", "qtpy")
_QT_WIDGET_NAMES = {"QApplication", "QGuiApplication", "QWidget", "QDialog", "QMainWindow"}
_FORBIDDEN_DATA_FILES = ("api_keys.json", "long_term.json")
_FORBIDDEN_BUILTINS = {"eval", "exec", "__import__"}
_SUBPROCESS_CALLS = {"run", "Popen", "call", "check_call", "check_output", "getoutput", "getstatusoutput"}


def _docstring_expr_ids(tree: ast.AST) -> set[int]:
    """Return AST node ids of pure docstring Constant nodes (module/class/func)."""
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


def _extract_dict_name_from_ast(path: Path, dict_var: str) -> str | None:
    """Safely extract `PLUGIN['name']` or `TOOL['name']` from a file via AST only."""
    try:
        tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
    except (OSError, SyntaxError):
        return None
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
            isinstance(t, ast.Name) and t.id == dict_var for t in node.targets
        ):
            if isinstance(node.value, ast.Dict):
                for k, v in zip(node.value.keys, node.value.values):
                    if (
                        isinstance(k, ast.Constant)
                        and k.value == "name"
                        and isinstance(v, ast.Constant)
                        and isinstance(v.value, str)
                    ):
                        return v.value.strip()
    return None


def _collect_existing_tool_names(repo_root: Path, exclude_file: Path) -> dict[str, str]:
    """Return `{tool_name: source_label}` across main.py, actions/*.py, and plugins/*.py."""
    names: dict[str, str] = {}
    excluded = exclude_file.resolve()

    # 1. Inline tools in main.py
    main_py = repo_root / "main.py"
    if main_py.is_file():
        try:
            tree = ast.parse(main_py.read_text(encoding="utf-8", errors="replace"))
            for node in ast.walk(tree):
                if isinstance(node, ast.Assign) and any(
                    getattr(t, "id", "") == "TOOL_DECLARATIONS" for t in node.targets
                ):
                    decls = ast.literal_eval(node.value)
                    for d in decls:
                        if isinstance(d, dict) and d.get("name"):
                            names[str(d["name"])] = "main.py (inline)"
                    break
        except Exception:
            pass

    # 2. Bundled actions in actions/*.py
    actions_dir = repo_root / "actions"
    if actions_dir.is_dir():
        for p in sorted(actions_dir.glob("*.py")):
            if p.name.startswith("_"):
                continue
            tname = _extract_dict_name_from_ast(p, "TOOL")
            if tname:
                names[tname] = f"actions/{p.name}"

    # 3. Active plugins in plugins/*.py (skipping the file currently being validated)
    plugins_dir = repo_root / "plugins"
    if plugins_dir.is_dir():
        for p in sorted(plugins_dir.glob("*.py")):
            if p.name.startswith("_"):
                continue
            if p.resolve() == excluded:
                continue
            pname = _extract_dict_name_from_ast(p, "PLUGIN")
            if pname:
                names[pname] = f"plugins/{p.name}"

    return names


def _check_ast_security(tree: ast.AST) -> list[str]:
    """Inspect AST for AGENTS.md security violations. Returns list of error messages."""
    errors: list[str] = []
    os_aliases: set[str] = set()
    os_bad_funcs: set[str] = set()
    subproc_aliases: set[str] = set()
    subproc_funcs: set[str] = set()
    doc_ids = _docstring_expr_ids(tree)

    for node in ast.walk(tree):
        # 1. Forbidden Qt imports & alias tracking for os / subprocess
        if isinstance(node, ast.Import):
            for alias in node.names:
                top = alias.name.split(".")[0]
                if top in _QT_PREFIXES:
                    errors.append(f"Forbidden Qt import: 'import {alias.name}' (line {node.lineno})")
                if alias.name == "os":
                    os_aliases.add(alias.asname or "os")
                if alias.name == "subprocess":
                    subproc_aliases.add(alias.asname or "subprocess")

        elif isinstance(node, ast.ImportFrom):
            mod = node.module or ""
            top = mod.split(".")[0]
            if top in _QT_PREFIXES:
                errors.append(f"Forbidden Qt import: 'from {mod} import ...' (line {node.lineno})")
            for alias in node.names:
                if alias.name in _QT_WIDGET_NAMES:
                    errors.append(f"Forbidden Qt widget import: '{alias.name}' (line {node.lineno})")
                if mod == "os" and alias.name in ("system", "popen"):
                    os_bad_funcs.add(alias.asname or alias.name)
                if mod == "subprocess" and alias.name in _SUBPROCESS_CALLS:
                    subproc_funcs.add(alias.asname or alias.name)

    for node in ast.walk(tree):
        # 2. Direct string reference to config/api_keys.json or memory/long_term.json
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            if id(node) not in doc_ids:
                low = node.value.replace("\\", "/").lower()
                for forbidden in _FORBIDDEN_DATA_FILES:
                    if forbidden in low:
                        errors.append(
                            f"Direct reference to protected file '{forbidden}' is forbidden "
                            f"(use memory/config_manager.py or memory/memory_manager.py; line {node.lineno})"
                        )

        # 3. Dangerous execution calls
        if isinstance(node, ast.Call):
            fn = node.func
            if isinstance(fn, ast.Name):
                if fn.id in _FORBIDDEN_BUILTINS:
                    errors.append(f"Forbidden execution primitive: '{fn.id}()' (line {node.lineno})")
                elif fn.id in os_bad_funcs:
                    errors.append(f"Forbidden shell primitive: 'os.{fn.id}()' (line {node.lineno})")
                elif fn.id in _QT_WIDGET_NAMES:
                    errors.append(f"Forbidden Qt widget instantiation: '{fn.id}()' (line {node.lineno})")
                elif fn.id in subproc_funcs:
                    if _has_shell_true(node):
                        errors.append(f"Forbidden subprocess call with shell=True: '{fn.id}()' (line {node.lineno})")
            elif isinstance(fn, ast.Attribute) and isinstance(fn.value, ast.Name):
                if fn.value.id in os_aliases and fn.attr in ("system", "popen"):
                    errors.append(f"Forbidden shell primitive: '{fn.value.id}.{fn.attr}()' (line {node.lineno})")
                elif fn.value.id in subproc_aliases and fn.attr in _SUBPROCESS_CALLS:
                    if _has_shell_true(node):
                        errors.append(
                            f"Forbidden subprocess call with shell=True: "
                            f"'{fn.value.id}.{fn.attr}()' (line {node.lineno})"
                        )
                elif fn.attr in _QT_WIDGET_NAMES:
                    errors.append(f"Forbidden Qt widget call: '.{fn.attr}()' (line {node.lineno})")

    # Deduplicate while preserving order
    return list(dict.fromkeys(errors))


def _has_shell_true(call_node: ast.Call) -> bool:
    """Return True if a subprocess Call sets `shell=True` (or any non-False shell arg)."""
    for kw in call_node.keywords:
        if kw.arg == "shell":
            if isinstance(kw.value, ast.Constant):
                return bool(kw.value.value)
            # Non-literal dynamic `shell=var` is rejected conservatively
            return True
    return False


def _run_isolated_smoke(plugin_path: Path, repo_root: Path, timeout: float) -> dict:
    """Import plugin and call `run({}, player=None, session_memory=None)` in a clean subprocess."""
    harness = r"""
import importlib.util, inspect, json, re, sys
from pathlib import Path

repo_root = Path(sys.argv[1]).resolve()
plugin_path = Path(sys.argv[2]).resolve()
sys.path.insert(0, str(repo_root))

out = {
    "ok": False,
    "plugin_name": None,
    "description_len": 0,
    "brief_len": 0,
    "has_brief": False,
    "qt_loaded": [],
    "errors": [],
    "warnings": [],
    "result_preview": "",
}

def _qt_mods():
    return sorted(
        m for m in sys.modules
        if m.split(".")[0] in ("PyQt5", "PyQt6", "PySide2", "PySide6", "qtpy")
    )

try:
    spec = importlib.util.spec_from_file_location("plugins._validated_candidate", plugin_path)
    if spec is None or spec.loader is None:
        raise ImportError("Could not create import spec")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
except Exception as exc:
    out["errors"].append(f"Import failed: {type(exc).__name__}: {exc}")
    print(json.dumps(out))
    sys.exit(0)

plugin_meta = getattr(mod, "PLUGIN", None)
if not isinstance(plugin_meta, dict):
    out["errors"].append("Missing top-level PLUGIN dict.")
else:
    name = plugin_meta.get("name")
    if not isinstance(name, str) or not re.match(r"^[a-zA-Z_][a-zA-Z0-9_]{0,63}$", name):
        out["errors"].append(f"Invalid PLUGIN['name']: {name!r}")
    else:
        out["plugin_name"] = name

    desc = plugin_meta.get("description")
    if not isinstance(desc, str) or not desc.strip():
        out["errors"].append("PLUGIN['description'] missing or empty.")
    else:
        out["description_len"] = len(desc.strip())

    brief = plugin_meta.get("brief")
    if isinstance(brief, str) and brief.strip():
        out["has_brief"] = True
        out["brief_len"] = len(brief.strip())
        if out["description_len"] and out["brief_len"] >= out["description_len"]:
            out["warnings"].append(
                f"PLUGIN['brief'] ({out['brief_len']} chars) is not shorter than description ({out['description_len']} chars)."
            )
    else:
        out["warnings"].append("PLUGIN['brief'] missing — add a concise brief for local 8B tool budgets.")

    params = plugin_meta.get("parameters")
    if not isinstance(params, dict) or params.get("type") != "OBJECT":
        out["errors"].append("PLUGIN['parameters'] must be a dict with \"type\": \"OBJECT\".")
    elif "properties" in params and not isinstance(params["properties"], dict):
        out["errors"].append("PLUGIN['parameters']['properties'] must be a dict.")

run_fn = getattr(mod, "run", None)
if not callable(run_fn):
    out["errors"].append("Missing callable run(parameters, player=None, session_memory=None) function.")
else:
    sig = inspect.signature(run_fn)
    has_var_kw = any(p.kind == inspect.Parameter.VAR_KEYWORD for p in sig.parameters.values())
    kwargs = {}
    if has_var_kw or "player" in sig.parameters:
        kwargs["player"] = None
    if has_var_kw or "session_memory" in sig.parameters:
        kwargs["session_memory"] = None
    try:
        res = run_fn({}, **kwargs)
    except Exception as exc:
        out["errors"].append(f"run({{}}, player=None) raised {type(exc).__name__}: {exc}")
    else:
        if not isinstance(res, str):
            out["errors"].append(f"run() must return str, got {type(res).__name__}")
        else:
            out["result_preview"] = res[:160]

qt = _qt_mods()
if qt:
    out["qt_loaded"] = qt
    out["errors"].append(f"Qt modules imported dynamically at runtime: {', '.join(qt)}")

out["ok"] = len(out["errors"]) == 0
print(json.dumps(out))
"""
    try:
        proc = subprocess.run(
            [sys.executable, "-c", harness, str(repo_root), str(plugin_path)],
            cwd=str(repo_root),
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return {
            "ok": False,
            "errors": [f"Isolated smoke test timed out after {timeout:.1f}s"],
            "warnings": [],
        }

    lines = [ln.strip() for ln in proc.stdout.splitlines() if ln.strip()]
    if not lines:
        stderr_snip = (proc.stderr or "").strip()[:300]
        return {
            "ok": False,
            "errors": [f"Smoke subprocess produced no output (exit={proc.returncode}): {stderr_snip}"],
            "warnings": [],
        }
    try:
        return json.loads(lines[-1])
    except json.JSONDecodeError:
        return {
            "ok": False,
            "errors": [f"Smoke subprocess emitted invalid JSON: {lines[-1][:200]}"],
            "warnings": [],
        }


def validate_plugin(
    plugin_path: Path,
    repo_root: Path | None = None,
    timeout: float = 10.0,
) -> dict:
    """Validate a plugin file statically and in an isolated runtime subprocess."""
    target = Path(plugin_path).resolve()
    root = Path(repo_root).resolve() if repo_root else Path(__file__).resolve().parent.parent

    report: dict = {
        "path": str(target),
        "repo_root": str(root),
        "plugin_name": None,
        "passed": False,
        "errors": [],
        "warnings": [],
        "checks": {
            "syntax": False,
            "ast_contract": False,
            "security_ast": False,
            "collision_free": False,
            "runtime_smoke": False,
            "has_concise_brief": False,
        },
        "result_preview": "",
    }

    if not target.is_file():
        report["errors"].append(f"Plugin file not found: {target}")
        return report

    try:
        source = target.read_text(encoding="utf-8")
    except OSError as exc:
        report["errors"].append(f"Cannot read plugin file: {exc}")
        return report

    # 1. Syntax check (ast.parse + py_compile to temp file)
    try:
        tree = ast.parse(source, filename=str(target))
        with tempfile.TemporaryDirectory() as td:
            py_compile.compile(str(target), cfile=str(Path(td) / "check.pyc"), doraise=True)
        report["checks"]["syntax"] = True
    except (SyntaxError, py_compile.PyCompileError) as exc:
        report["errors"].append(f"Syntax error: {exc}")
        return report

    # 2. Static AST contract check
    has_plugin = any(
        isinstance(n, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "PLUGIN" for t in n.targets)
        for n in tree.body
    )
    has_run = any(isinstance(n, ast.FunctionDef) and n.name == "run" for n in tree.body)
    if not has_plugin:
        report["errors"].append("Missing top-level PLUGIN dict assignment.")
    if not has_run:
        report["errors"].append("Missing top-level `def run(parameters, player=None, session_memory=None)` function.")
    report["checks"]["ast_contract"] = has_plugin and has_run

    # 3. Static AST security checks (AGENTS.md)
    sec_errors = _check_ast_security(tree)
    if sec_errors:
        report["errors"].extend(sec_errors)
    else:
        report["checks"]["security_ast"] = True

    # Early static collision check if PLUGIN['name'] is statically visible
    static_name = _extract_dict_name_from_ast(target, "PLUGIN")
    existing = _collect_existing_tool_names(root, target)
    if static_name:
        report["plugin_name"] = static_name
        if not _NAME_RE.match(static_name):
            report["errors"].append(f"Invalid PLUGIN['name']: {static_name!r}")
        elif static_name in existing:
            report["errors"].append(
                f"Tool name collision: '{static_name}' is already declared in {existing[static_name]}"
            )
        else:
            report["checks"]["collision_free"] = True

    # Never execute untrusted code if static security checks already failed!
    if sec_errors or not report["checks"]["ast_contract"]:
        report["passed"] = False
        return report

    # 4. Isolated subprocess smoke test
    smoke = _run_isolated_smoke(target, root, timeout=timeout)
    if smoke.get("plugin_name"):
        report["plugin_name"] = smoke["plugin_name"]
    report["warnings"].extend(smoke.get("warnings") or [])
    report["result_preview"] = smoke.get("result_preview") or ""
    report["checks"]["has_concise_brief"] = bool(
        smoke.get("has_brief")
        and smoke.get("description_len", 0) > 0
        and smoke.get("brief_len", 0) < smoke.get("description_len", 0)
    )

    if smoke.get("errors"):
        report["errors"].extend(smoke["errors"])
    else:
        report["checks"]["runtime_smoke"] = True

    # Confirm collision-free using runtime-evaluated plugin_name as well
    final_name = report["plugin_name"]
    if final_name:
        if final_name in existing:
            msg = f"Tool name collision: '{final_name}' is already declared in {existing[final_name]}"
            if msg not in report["errors"]:
                report["errors"].append(msg)
            report["checks"]["collision_free"] = False
        else:
            report["checks"]["collision_free"] = True

    report["passed"] = len(report["errors"]) == 0
    return report


def _print_human(report: dict) -> None:
    verdict = "PASS" if report["passed"] else "FAIL"
    print(f"[{verdict}] {report['path']} (tool={report.get('plugin_name') or '?'})")
    for k, v in report["checks"].items():
        mark = "OK" if v else ("WARN" if k == "has_concise_brief" else "FAIL")
        print(f"  - {k}: {mark}")
    if report.get("result_preview"):
        print(f"  - smoke_preview: {report['result_preview']!r}")
    for w in report.get("warnings") or []:
        print(f"  [WARN] {w}")
    for e in report.get("errors") or []:
        print(f"  [ERR]  {e}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Validate a Mark-LVII plugin statically and in isolation.")
    parser.add_argument("plugin_path", help="Path to the candidate plugin .py file")
    parser.add_argument("--repo-root", default=None, help="Repository root for collision checks (default: auto)")
    parser.add_argument("--timeout", type=float, default=10.0, help="Subprocess smoke-test timeout in seconds")
    parser.add_argument("--json", action="store_true", dest="as_json", help="Emit structured JSON output")
    args = parser.parse_args(argv)

    report = validate_plugin(
        Path(args.plugin_path),
        repo_root=Path(args.repo_root) if args.repo_root else None,
        timeout=args.timeout,
    )
    if args.as_json:
        print(json.dumps(report, indent=2, ensure_ascii=False))
    else:
        _print_human(report)
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
