"""plugins/dev_workspace.py — AST pytest/jest unit test generator, linter, VS Code helper, & project profiles."""

from __future__ import annotations

import ast
import json
from pathlib import Path
import re
import subprocess
import sys

_BASE_DIR = Path(__file__).resolve().parent.parent
DEFAULT_PROFILES_PATH = _BASE_DIR / "memory" / "project_profiles.json"

PLUGIN = {
    "name": "dev_workspace",
    "brief": "Generate pytest/jest unit tests from source files, run linters, and manage project profiles",
    "description": (
        "Inspects Python/JS source files via AST/regex to synthesize runnable pytest or jest unit tests, "
        "runs syntax/lint checks and test suites without shell=True, configures VS Code workspace files, "
        "and persists per-project tool preferences in memory/project_profiles.json. "
        "Actions: status, generate_tests, run_tests, lint, vscode_config, set_profile, get_profile, list_profiles."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "action": {
                "type": "STRING",
                "description": (
                    "Action: status | generate_tests | run_tests | lint | vscode_config | "
                    "set_profile | get_profile | list_profiles"
                ),
            },
            "source_path": {
                "type": "STRING",
                "description": "Path to .py or .js/.ts source file for generate_tests or lint.",
            },
            "out_path": {
                "type": "STRING",
                "description": "Optional output test file path.",
            },
            "project_dir": {
                "type": "STRING",
                "description": "Project workspace root path.",
            },
            "project_name": {
                "type": "STRING",
                "description": "Project profile key name.",
            },
            "profile": {
                "type": "OBJECT",
                "description": "Preferences dict (test_runner, linter, formatter, language, branch_prefix, notes).",
            },
            "run_after": {
                "type": "BOOLEAN",
                "description": "Run generated pytest file immediately when generating Python tests.",
            },
        },
        "required": [],
    },
}


def _load_profiles(store_path: Path | str | None = None) -> dict:
    path = Path(store_path) if store_path else DEFAULT_PROFILES_PATH
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _save_profiles(data: dict, store_path: Path | str | None = None) -> None:
    path = Path(store_path) if store_path else DEFAULT_PROFILES_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")


def generate_tests_for_file(source_path: str | Path, *, out_path: str | Path | None = None) -> dict:
    """Inspect a Python or JS/TS file and synthesize unit test code."""
    src = Path(source_path).resolve()
    if not src.is_file():
        raise FileNotFoundError(f"Source file not found: {src}")
    code = src.read_text(encoding="utf-8", errors="ignore")
    ext = src.suffix.lower()

    if ext == ".py":
        tree = ast.parse(code, filename=str(src))
        funcs = [
            node.name for node in tree.body
            if isinstance(node, ast.FunctionDef) and not node.name.startswith("_")
        ]
        classes = [
            node.name for node in tree.body
            if isinstance(node, ast.ClassDef) and not node.name.startswith("_")
        ]
        target = Path(out_path) if out_path else src.parent / f"test_{src.stem}.py"
        lines = [
            f'"""Auto-generated pytest unit tests for {src.name}."""',
            "import importlib.util",
            "from pathlib import Path",
            "",
            "",
            "def _load_target():",
            f"    p = Path(r'{src}')",
            "    spec = importlib.util.spec_from_file_location(p.stem, str(p))",
            "    mod = importlib.util.module_from_spec(spec)",
            "    spec.loader.exec_module(mod)",
            "    return mod",
            "",
        ]
        if not funcs and not classes:
            lines.extend([
                "def test_module_imports():",
                "    mod = _load_target()",
                "    assert mod is not None",
                "",
            ])
        for fn in funcs:
            lines.extend([
                f"def test_{fn}_exists_and_callable():",
                "    mod = _load_target()",
                f"    assert callable(getattr(mod, '{fn}'))",
                "",
            ])
        for cls in classes:
            lines.extend([
                f"def test_{cls.lower()}_defined():",
                "    mod = _load_target()",
                f"    assert hasattr(mod, '{cls}')",
                "",
            ])
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("\n".join(lines), encoding="utf-8")
        return {"framework": "pytest", "out_path": str(target), "targets": funcs + classes}

    # JavaScript / TypeScript -> Jest stub
    js_funcs = re.findall(r"(?:export\s+)?function\s+([A-Za-z0-9_]+)\s*\(", code)
    js_consts = re.findall(r"(?:export\s+)?const\s+([A-Za-z0-9_]+)\s*=\s*(?:async\s*)?\(", code)
    syms = list(dict.fromkeys(js_funcs + js_consts))
    target = Path(out_path) if out_path else src.parent / f"{src.stem}.test{ext}"
    lines = [
        f"// Auto-generated Jest tests for {src.name}",
        f"const mod = require('./{src.stem}');",
        "",
        f"describe('{src.stem}', () => {{",
    ]
    for s in (syms or ["module"]):
        if s == "module":
            lines.append("  test('module loads', () => { expect(mod).toBeDefined(); });")
        else:
            lines.append(f"  test('{s} is exported', () => {{ expect(typeof mod.{s}).toBe('function'); }});")
    lines.append("});")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return {"framework": "jest", "out_path": str(target), "targets": syms}


def run_test_target(target_path: str | Path) -> str:
    p = Path(target_path)
    if p.suffix.lower() in (".js", ".ts") or (p.is_dir() and (p / "package.json").is_file()):
        cmd = ["npx", "jest", str(p)]
    else:
        cmd = [sys.executable, "-B", "-m", "pytest", "-q", str(p)]
    res = subprocess.run(cmd, capture_output=True, text=True, timeout=60, shell=False)
    out = (res.stdout or "") + (res.stderr or "")
    return f"Test exit={res.returncode}:\n{out.strip()[:1200]}"


def lint_target(target_path: str | Path) -> str:
    p = Path(target_path)
    if not p.exists():
        return f"Target not found: {p}"
    if p.is_file() and p.suffix.lower() == ".py":
        try:
            ast.parse(p.read_text(encoding="utf-8", errors="ignore"), filename=str(p))
            return f"Python AST lint clean: {p.name}"
        except SyntaxError as exc:
            return f"SyntaxError in {p.name} line {exc.lineno}: {exc.msg}"
    return f"Checked {p} (valid path)."


def setup_vscode_workspace(project_dir: str | Path) -> str:
    root = Path(project_dir)
    vs = root / ".vscode"
    vs.mkdir(parents=True, exist_ok=True)
    settings_path = vs / "settings.json"
    settings = {
        "python.testing.pytestEnabled": True,
        "python.testing.unittestEnabled": False,
        "editor.formatOnSave": True,
        "files.exclude": {"**/__pycache__": True, "**/.pytest_cache": True},
    }
    settings_path.write_text(json.dumps(settings, indent=2), encoding="utf-8")
    return f"Configured VS Code workspace settings at {settings_path}"


def run(parameters: dict, player=None, session_memory=None) -> str:
    """Plugin entrypoint for dev workspace utilities."""
    params = parameters if isinstance(parameters, dict) else {}
    action = str(params.get("action") or "status").strip().lower()
    store_path = params.get("store_path")

    if action == "status":
        profs = _load_profiles(store_path)
        return f"Dev Workspace ready (stored_profiles={len(profs)}, store={DEFAULT_PROFILES_PATH.name})."

    if action in ("generate_tests", "create_tests"):
        src = str(params.get("source_path") or "").strip()
        if not src:
            return "Provide source_path='...' to generate unit tests."
        info = generate_tests_for_file(src, out_path=params.get("out_path"))
        msg = f"Generated {info['framework']} tests at {info['out_path']} (symbols={info['targets']})."
        if params.get("run_after") and info["framework"] == "pytest":
            msg += "\n" + run_test_target(info["out_path"])
        return msg

    if action == "run_tests":
        target = str(params.get("source_path") or params.get("project_dir") or "").strip()
        if not target:
            return "Provide source_path or project_dir to run tests."
        return run_test_target(target)

    if action == "lint":
        target = str(params.get("source_path") or params.get("project_dir") or "").strip()
        if not target:
            return "Provide source_path to lint."
        return lint_target(target)

    if action == "vscode_config":
        proj = str(params.get("project_dir") or "").strip()
        if not proj:
            return "Provide project_dir='...' to configure VS Code workspace."
        return setup_vscode_workspace(proj)

    if action == "set_profile":
        name = str(params.get("project_name") or "").strip()
        prof = params.get("profile") or {}
        if not name or not isinstance(prof, dict):
            return "Provide project_name='...' and profile={...}."
        profs = _load_profiles(store_path)
        existing = profs.get(name, {})
        existing.update(prof)
        profs[name] = existing
        _save_profiles(profs, store_path)
        return f"Saved project profile '{name}': {json.dumps(existing)}"

    if action == "get_profile":
        name = str(params.get("project_name") or "").strip()
        profs = _load_profiles(store_path)
        if name not in profs:
            return f"No project profile found for '{name}'."
        return json.dumps({name: profs[name]}, indent=2)

    if action == "list_profiles":
        profs = _load_profiles(store_path)
        if not profs:
            return "No project profiles saved."
        return json.dumps(profs, indent=2)

    return f"Unknown dev_workspace action '{action}'."
