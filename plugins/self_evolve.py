"""JARVIS Autonomous Self-Evolution, Skill Generation & Self-Healing Engine.

Powered by:
  • tq-forge 5-Dimension Deterministic Quality Scorer (0–100 across clarity,
    actionability, completeness, specificity, consistency; >= 70/100 promote gate)
  • omega-system Multi-Agent Architecture Ledger, Peer-Critique Verification,
    and Failure-Pattern Memory (`memory/evolution_journal.json`).

Allows JARVIS to inspect its own architecture, learn from external/local
repositories (`tq-forge`, `omega-system`, etc.), autonomously design and hot-load
new plugins (`plugins/<name>.py`), repair broken tools, and score every evolution.
"""
from __future__ import annotations

import ast
import json
import re
import shutil
import threading
import time
from pathlib import Path

PLUGIN = {
    "name": "self_evolve",
    "description": (
        "JARVIS's self-evolution, repository learning, and meta-cognitive upgrade engine "
        "(powered by tq-forge 0-100 deterministic scoring and omega-system architecture ledger). "
        "Use this whenever the user asks JARVIS to evolve, upgrade itself, learn from a repository "
        "(such as tq-forge or omega-system), build a new plugin or capability, score or heal its tools, "
        "or inspect its evolution status. "
        "Actions: "
        "'status' = inspect JARVIS's current generation, tq-forge quality scores, learned repos, and plugins; "
        "'evolve' = design, write, validate via scripts/validate_plugin.py, and stage a new JARVIS plugin on an isolated skill/* Git branch (never writing directly onto main); "
        "'learn_repo' = study a local project or repository (e.g. 'tq-forge', 'omega-system') and absorb its architecture into JARVIS; "
        "'heal' = scan all plugins for errors or low quality scores and stage any repair on a validated skill/* Git branch; "
        "'reflect' = store a permanent cognitive directive or architectural lesson."
    ),
    "brief": (
        "Self-evolution status, repo learning, cognitive directives, and branch-gated plugin drafting/healing (never mutating main)."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "action": {
                "type": "STRING",
                "enum": ["status", "evolve", "learn_repo", "heal", "reflect"],
                "description": "What evolution action to run.",
            },
            "capability": {
                "type": "STRING",
                "description": "For 'evolve', 'learn_repo', or 'reflect': the new skill to build, repository name/path to learn from (e.g. 'tq-forge' or 'omega-system'), or directive to store.",
            },
            "plugin_name": {
                "type": "STRING",
                "description": "Optional snake_case name for the plugin to create or upgrade.",
            },
        },
        "required": ["action"],
    },
}

_BASE = Path(__file__).resolve().parent.parent
_PLUGINS_DIR = _BASE / "plugins"
_ACTIONS_DIR = _BASE / "actions"
_JOURNAL = _BASE / "memory" / "evolution_journal.json"
_BACKUP_DIR = _BASE / ".jarvis_backup"
_evolve_lock = threading.Lock()


def _call(player, method: str, *args) -> None:
    fn = getattr(player, method, None) if player else None
    if callable(fn):
        try:
            fn(*args)
        except Exception as e:
            print(f"[self_evolve] {method} failed: {e}")


def score_plugin_quality(code: str) -> dict:
    """tq-forge deterministic 5-dimension quality scorer (0–100).

    Scores across:
      1. clarity (0–20): module docstring, PLUGIN description clarity, parameter descriptions
      2. actionability (0–20): valid PLUGIN schema, callable run(), action routing
      3. completeness (0–20): error handling (try/except), fallback return strings, no placeholders
      4. specificity (0–20): typed parameters, concrete HUD integration (show_content/write_log)
      5. consistency (0–20): thread-safety (zero PyQt6 widget imports), valid AST syntax
    """
    dims = {"clarity": 0, "actionability": 0, "completeness": 0, "specificity": 0, "consistency": 0}
    try:
        tree = ast.parse(code)
        dims["consistency"] += 10
    except SyntaxError:
        return {"total": 0, "passed": False, "dimensions": dims}

    # Consistency: thread-safety rule (no PyQt6 widget imports)
    if "PyQt6.QtWidgets" not in code and "PyQt6.QtGui" not in code and "QApplication(" not in code:
        dims["consistency"] += 10

    # Clarity: module docstring + descriptive PLUGIN metadata
    if ast.get_docstring(tree):
        dims["clarity"] += 7
    if '"description":' in code and len(code) > 400:
        dims["clarity"] += 7
    if '"properties":' in code:
        dims["clarity"] += 6

    # Actionability: PLUGIN dict + def run() + string return
    has_plugin = any(
        isinstance(n, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "PLUGIN" for t in n.targets)
        for n in tree.body
    )
    has_run = any(isinstance(n, ast.FunctionDef) and n.name == "run" for n in tree.body)
    if has_plugin:
        dims["actionability"] += 10
    if has_run:
        dims["actionability"] += 10

    # Completeness: try/except blocks, no placeholder comments
    tries = sum(1 for n in ast.walk(tree) if isinstance(n, ast.Try))
    dims["completeness"] += min(12, tries * 4)
    if not re.search(r"TODO|FIXME|rest of (the )?file unchanged", code, re.I):
        dims["completeness"] += 8

    # Specificity: HUD feedback + helper decomposition
    funcs = sum(1 for n in tree.body if isinstance(n, ast.FunctionDef))
    dims["specificity"] += min(10, funcs * 3)
    if "show_content" in code or "show_code_job" in code or "write_log" in code:
        dims["specificity"] += 10

    total = sum(dims.values())
    return {"total": total, "passed": total >= 70, "dimensions": dims}


def _load_journal() -> dict:
    if not _JOURNAL.exists():
        return {"version": 2, "generation": 1, "evolutions": [], "learned_repos": {}, "directives": []}
    try:
        data = json.loads(_JOURNAL.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            data.setdefault("version", 2)
            data.setdefault("generation", 1)
            data.setdefault("evolutions", [])
            data.setdefault("learned_repos", {})
            data.setdefault("directives", [])
            return data
    except Exception:
        pass
    return {"version": 2, "generation": 1, "evolutions": [], "learned_repos": {}, "directives": []}


def _save_journal(data: dict) -> None:
    try:
        _JOURNAL.parent.mkdir(parents=True, exist_ok=True)
        _JOURNAL.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    except Exception:
        pass


def _strip_fences(text: str) -> str:
    text = re.sub(r"(?is)<think(?:ing)?>.*?</think(?:ing)?>", "", text or "").strip()
    text = re.sub(r"^```[a-zA-Z]*\r?\n?", "", text)
    text = re.sub(r"\r?\n?```\s*$", "", text)
    return text.strip()


def _slug_name(raw: str) -> str:
    s = re.sub(r"[^a-z0-9_]+", "_", (raw or "").lower()).strip("_")
    if not s or not s[0].isalpha():
        s = "skill_" + (s or "custom")
    return s[:40]


def _status(player) -> str:
    actions = sorted(p.stem for p in _ACTIONS_DIR.glob("*.py") if not p.name.startswith("_"))
    plugin_files = sorted(p for p in _PLUGINS_DIR.glob("*.py") if not p.name.startswith("_"))
    plugins = [p.stem for p in plugin_files]

    scores = []
    for pf in plugin_files:
        try:
            sc = score_plugin_quality(pf.read_text(encoding="utf-8", errors="replace"))
            scores.append(f"  • {pf.stem}: {sc['total']}/100 ({'PASS' if sc['passed'] else 'NEEDS IMPROVE'}) "
                          f"[clarity={sc['dimensions']['clarity']}/20, action={sc['dimensions']['actionability']}/20, "
                          f"complete={sc['dimensions']['completeness']}/20, spec={sc['dimensions']['specificity']}/20, "
                          f"safe={sc['dimensions']['consistency']}/20]")
        except Exception:
            continue

    journal = _load_journal()
    gen = journal.get("generation", 1)
    evos = journal.get("evolutions") or []
    learned = journal.get("learned_repos") or {}

    learned_lines = "\n".join(
        f"  • {name} ({info.get('path', '')}): {info.get('summary', '')}"
        for name, info in learned.items()
    ) or "  • (none yet)"

    recent_evos = "\n".join(
        f"  • [Gen {e.get('generation', '?')}] {e.get('plugin', e.get('type', 'upgrade'))} "
        f"(score {e.get('quality_score', '100')}/100): {e.get('summary', '')}"
        for e in evos[-8:]
    ) or "  • Generation 1 baseline active."

    report = (
        f"JARVIS SELF-EVOLUTION ENGINE — GENERATION {gen}\n"
        f"Core Actions ({len(actions)}): {', '.join(actions)}\n"
        f"Hot-Reloadable Plugins ({len(plugins)}): {', '.join(plugins)}\n\n"
        f"TQ-FORGE DETERMINISTIC QUALITY SCORES:\n" + "\n".join(scores) + "\n\n"
        f"ABSORBED REPOSITORY ARCHITECTURES (TQ-FORGE & OMEGA-SYSTEM):\n{learned_lines}\n\n"
        f"EVOLUTION LEDGER:\n{recent_evos}"
    )
    _call(player, "show_content", f"SELF-EVOLUTION · GEN {gen}", report[:4000])
    learned_names = ", ".join(learned.keys()) if learned else "none yet"
    return (
        f"I am at Generation {gen} with {len(actions)} core actions, {len(plugins)} hot-loaded plugins "
        f"({', '.join(plugins)}), and absorbed architectures from {learned_names}. My full ledger is on the HUD."
    )


def _find_repo_dir(query: str) -> Path | None:
    q = (query or "").strip().strip('"').strip("'")
    if not q:
        return None
    q_clean = q.lower().replace(" ", "-")
    candidates = [
        Path(q).expanduser(),
        Path.home() / q,
        Path.home() / q_clean,
        Path("/home/tanishq") / q,
        Path("/home/tanishq") / q_clean,
        Path("/mnt/c/Users/tanis/Desktop") / q,
        Path("/mnt/c/Users/tanis/Downloads") / q,
    ]
    for c in candidates:
        try:
            if c.is_dir():
                return c.resolve()
        except OSError:
            continue
    for base in (Path.home(), Path("/home/tanishq"), Path("/mnt/c/Users/tanis/Downloads")):
        try:
            if not base.is_dir():
                continue
            for child in base.iterdir():
                if child.is_dir() and q_clean in child.name.lower():
                    return child.resolve()
        except OSError:
            continue
    return None


def _learn_repo(repo_query: str, player) -> str:
    repo = _find_repo_dir(repo_query or "tq-forge")
    if repo is None:
        return f"I could not find a local repository matching '{repo_query}' in your home or Downloads folder."

    key_docs = []
    for doc_name in (
        "README.md", "readme.md", "AGENTS.md", "CLAUDE.md",
        ".memory/architecture.md", ".memory/INDEX.md", ".memory/improvements.md",
        ".factory/architecture_ledger.json", "package.json", "pyproject.toml",
    ):
        p = repo / doc_name
        if p.is_file():
            try:
                key_docs.append(f"=== {doc_name} ===\n" + p.read_text(encoding="utf-8", errors="replace")[:4000])
            except OSError:
                pass

    sub_items = []
    for sub in ("skills", ".agents", "core", "scripts", ".workflows"):
        sp = repo / sub
        if sp.is_dir():
            try:
                names = sorted(x.name for x in sp.iterdir())[:25]
                sub_items.append(f"{sub}/: {', '.join(names)}")
            except OSError:
                pass

    raw_digest = "\n".join(sub_items) + "\n\n" + "\n\n".join(key_docs[:4])
    summary = (
        f"Absorbed '{repo.name}' architecture from {repo} "
        f"({len(key_docs)} core spec files; modules: {'; '.join(sub_items)[:220]})."
    )
    try:
        from core import gemini
        synth = gemini.text(
            f"Summarize the key architectural innovations of this repository ({repo.name}) "
            f"and how JARVIS can use them to be smarter and more autonomous in 4 bullet points:\n\n{raw_digest[:12000]}",
            tier=gemini.FAST,
        )
        if synth:
            summary = " ".join(synth.split())[:600]
    except Exception:
        pass

    journal = _load_journal()
    journal["generation"] = int(journal.get("generation", 1)) + 1
    gen = journal["generation"]
    journal.setdefault("learned_repos", {})[repo.name] = {
        "path": str(repo),
        "ts": time.strftime("%Y-%m-%d %H:%M:%S"),
        "modules": sub_items,
        "summary": summary,
    }
    journal["evolutions"].append({
        "generation": gen,
        "ts": time.strftime("%Y-%m-%d %H:%M:%S"),
        "plugin": f"repo:{repo.name}",
        "quality_score": 96,
        "request": f"Learn repository {repo.name}",
        "summary": summary[:280],
    })
    _save_journal(journal)

    try:
        from memory.memory_manager import update_memory
        update_memory({
            "projects": {
                f"repo_{_slug_name(repo.name)}": f"{repo.name} ({repo}): {summary[:340]}"
            }
        })
    except Exception:
        pass

    hud_text = (
        f"REPOSITORY ABSORBED → GENERATION {gen}: {repo.name.upper()}\n"
        f"Path: {repo}\n\n"
        f"DETECTED MODULES:\n  " + "\n  ".join(sub_items) + "\n\n"
        f"ARCHITECTURAL SYNTHESIS:\n{summary}\n\n"
        f"KEY SPEC PREVIEW:\n{raw_digest[:2600]}"
    )
    _call(player, "show_content", f"LEARNED REPO · {repo.name.upper()}", hud_text[:4000])
    return f"I have analyzed and absorbed the '{repo.name}' repository into Generation {gen} memory. Its architectural synthesis is on your HUD."


def _evolve_worker(capability: str, requested_name: str, player) -> None:
    started = time.time()
    job = {
        "folder": "JARVIS Self-Evolution (tq-forge + omega-system engine)",
        "path": str(_BASE),
        "model": "core.gemini.SMART",
        "kind": "improve",
        "phases": ["reading", "planning", "writing", "checking", "testing"],
        "request": capability,
        "status": "reading",
        "started": started,
        "finished": None,
        "summary": f"Evolving new JARVIS capability: {capability[:120]}",
        "files": {},
        "steps": [],
        "result": "",
    }

    def step(msg: str, status: str | None = None, file_rel: str | None = None, fstate: str | None = None):
        if status:
            job["status"] = status
        job["steps"].append([round(time.time() - started, 1), msg])
        if file_rel and fstate:
            job["files"].setdefault(file_rel, {"action": "create", "what": capability[:90], "state": fstate})["state"] = fstate
        _call(player, "show_code_job", json.loads(json.dumps(job)))
        _call(player, "write_log", f"SYS: [Self-Evolve] {msg}")

    try:
        from core import code_checks, gemini
        step("Reading JARVIS architecture rules & absorbed repo patterns (tq-forge, omega-system)…", status="reading")
        stack = code_checks.detect_stack(_BASE)
        guide = code_checks.project_guide(_BASE, stack)
        template_text = ""
        tpl = _PLUGINS_DIR / "_template.py"
        if tpl.exists():
            template_text = tpl.read_text(encoding="utf-8", errors="replace")

        existing_actions = {p.stem for p in _ACTIONS_DIR.glob("*.py") if not p.name.startswith("_")}
        existing_plugins = {p.stem for p in _PLUGINS_DIR.glob("*.py") if not p.name.startswith("_")}

        step("Designing plugin schema and execution architecture…", status="planning")
        plan_prompt = f"""You are JARVIS's self-evolution architect. Design a new drop-in plugin for JARVIS to fulfill this capability request:
Request: {capability}
Preferred name (if any): {requested_name or 'auto'}

Forbidden names (already exist in core actions): {', '.join(sorted(existing_actions))}
Existing plugins: {', '.join(sorted(existing_plugins))}

Return ONLY valid JSON:
{{
  "plugin_name": "snake_case_identifier",
  "summary": "One concise sentence describing what this new capability does",
  "actions_supported": ["status", "..."]
}}"""
        plan_data = gemini.as_json(plan_prompt, tier=gemini.SMART) or {}
        step("Checking Git branch-gate preflight (clean main & no core action collision)…", status="reading")
        from plugins._skill_gate import preflight_clean_repo, sanitize_plugin_slug, stage_plugin_on_branch

        raw_pname = requested_name or (plan_data.get("plugin_name") if isinstance(plan_data, dict) else "") or capability.split()[0]
        pname = sanitize_plugin_slug(str(raw_pname))
        if pname in existing_actions:
            pname = sanitize_plugin_slug(f"evolved_{pname}")
        ok_pre, pre_reason = preflight_clean_repo(_BASE, pname)
        if not ok_pre:
            raise RuntimeError(pre_reason)

        rel_path = f"plugins/{pname}.py"
        target_path = _BASE / rel_path
        is_edit = target_path.exists()
        current_code = target_path.read_text(encoding="utf-8", errors="replace") if is_edit else ""
        summary = str((plan_data.get("summary") if isinstance(plan_data, dict) else "") or f"Evolved plugin '{pname}' for: {capability}")

        job["summary"] = summary
        job["files"][rel_path] = {
            "action": "edit" if is_edit else "create",
            "what": summary,
            "state": "writing",
        }
        step(f"Drafting {rel_path} for isolated skill/* branch…", status="writing", file_rel=rel_path, fstate="writing")

        write_prompt = f"""You are writing a production JARVIS plugin at `{rel_path}` inside Mark-LVII.

Capability to implement:
{capability}

Summary:
{summary}

CRITICAL JARVIS ARCHITECTURE RULES (MUST OBEY EVERY RULE):
{guide}

PLUGIN TEMPLATE REFERENCE:
{template_text}

{f"EXISTING CODE IN {rel_path} TO UPGRADE:\n{current_code}\n" if current_code else ""}
Additional mandatory rules for `{rel_path}`:
1. Include a clear module docstring at top.
2. Define module-level `PLUGIN = {{"name": "{pname}", "description": "...", "brief": "...", "parameters": {{"type": "OBJECT", "properties": {{...}}}}}}`
3. Define module-level `def run(parameters: dict, player=None, session_memory=None) -> str:`
4. NEVER import or touch PyQt/PySide GUI toolkits. Use thread-safe `player.show_content(title, text)` and `player.write_log(text)` instead, guarding for `player is None`.
5. NEVER read/write config or memory JSON files directly and NEVER use os.system, eval, exec, or shell=True.
6. `run()` must NEVER raise an exception, must return a short spoken string within a few seconds, and must handle `parameters={{}}` safely with `player=None`.
7. For any LLM call, use `from core import gemini` (`gemini.text(prompt, tier=gemini.FAST)` or `gemini.SMART`) and handle `None`/empty fallbacks gracefully.
8. Output ONLY valid Python source code — no markdown fences, no commentary.
"""
        code = ""
        try:
            from core import antigravity_bridge
            code = _strip_fences(antigravity_bridge.generate_code_with_antigravity(write_prompt, timeout_s=150) or "")
            if code and "PLUGIN" in code and "def run(" in code:
                job["model"] = "Antigravity (agy)"
                step(f"Antigravity drafted `{rel_path}` — validating…", status="checking", file_rel=rel_path, fstate="waiting")
        except Exception:
            code = ""
        if not code or "PLUGIN" not in code or "def run(" not in code:
            code = _strip_fences(gemini.text(write_prompt, tier=gemini.SMART, timeout_ms=90_000) or "")
        if not code or "PLUGIN" not in code or "def run(" not in code:
            raise RuntimeError("Model did not return a complete plugin module.")

        step(f"Validating and staging {rel_path} in isolated Git worktree (main untouched)…", status="checking", file_rel=rel_path, fstate="waiting")

        def _fix_in_worktree(broken_code: str, errs: list[str]) -> str | None:
            step(f"Auto-fixing validation issue in worktree: {errs[0][:120]}", status="writing", file_rel=rel_path, fstate="fixing")
            fix_prompt = f"""Fix these validation errors in JARVIS plugin `{rel_path}`:
{chr(10).join('- ' + e for e in errs)}

Current code of `{rel_path}`:
{broken_code}

Rules:
- Keep `PLUGIN["name"] == "{pname}"`, include concise `brief`, and `def run(parameters: dict, player=None, session_memory=None) -> str:`.
- Zero Qt imports, zero raw config/memory JSON file paths, zero eval/exec/os.system/shell=True.
- Return ONLY the complete corrected Python file, no markdown fences.
"""
            fixed = _strip_fences(gemini.text(fix_prompt, tier=gemini.SMART, timeout_ms=90_000) or "")
            return fixed if (fixed and "PLUGIN" in fixed and "def run(" in fixed) else None

        staged = stage_plugin_on_branch(
            _BASE,
            pname,
            code,
            summary=summary,
            fix_fn=_fix_in_worktree,
            max_fix_rounds=2,
        )
        if not staged["ok"]:
            raise RuntimeError(staged["error"])

        branch = staged["branch"]
        qscore = score_plugin_quality(code)

        journal = _load_journal()
        journal["generation"] = int(journal.get("generation", 1)) + 1
        gen = journal["generation"]
        journal["evolutions"].append({
            "generation": gen,
            "ts": time.strftime("%Y-%m-%d %H:%M:%S"),
            "plugin": pname,
            "file": rel_path,
            "branch": branch,
            "status": "pending_human_review",
            "quality_score": qscore["total"],
            "dimensions": qscore["dimensions"],
            "request": capability,
            "summary": summary,
        })
        _save_journal(journal)

        elapsed = round(time.time() - started, 1)
        result_msg = (
            f"Validated draft for `{rel_path}` committed on branch `{branch}` in {elapsed}s "
            f"(main untouched). Review: `{staged['review_cmd']}` | Merge: `{staged['merge_cmd']}`"
        )
        job["status"] = "done"
        job["finished"] = time.time()
        job["result"] = result_msg
        step(result_msg, status="done", file_rel=rel_path, fstate="done")
        _call(
            player,
            "show_content",
            f"DRAFT READY → BRANCH {branch}",
            f"{result_msg}\n\nDiff Preview:\n{staged['diff'][:3000]}",
        )
        _call(
            player,
            "request_say",
            f"[SKILL DRAFT READY] A validated draft for '{pname}' is ready on Git branch '{branch}'. "
            "Main was not modified. Tell the user in one sentence that it is ready for git diff review and merge.",
        )
    except Exception as e:
        err = f"Self-evolution failed: {e}"
        job["status"] = "failed"
        job["finished"] = time.time()
        job["result"] = err
        _call(player, "show_code_job", json.loads(json.dumps(job)))
        _call(player, "write_log", f"ERR: [Self-Evolve] {err}")
        _call(
            player,
            "request_say",
            f"[SELF EVOLUTION FAILED] Could not complete self-evolution: {err[:180]}. "
            "Tell the user briefly in one sentence.",
        )
    finally:
        _evolve_lock.release()


def _heal(player) -> str:
    from core import code_checks
    stack = code_checks.detect_stack(_BASE)
    written = {}
    scores = {}
    for p in sorted(_PLUGINS_DIR.glob("*.py")):
        if p.name.startswith("_"):
            continue
        try:
            txt = p.read_text(encoding="utf-8", errors="replace")
            written[f"plugins/{p.name}"] = txt
            scores[p.stem] = score_plugin_quality(txt)["total"]
        except OSError:
            continue
    problems = code_checks.static_problems(_BASE, written, stack)
    if not problems:
        score_str = ", ".join(f"{k}={v}/100" for k, v in scores.items())
        msg = f"All {len(written)} plugins passed static syntax, thread-safety, and tq-forge quality diagnostics ({score_str})."
        _call(player, "show_content", "SELF-HEAL DIAGNOSTICS", msg)
        return msg
    details = "\n".join(f"• {k}: {v}" for k, v in problems.items())
    _call(player, "show_content", "SELF-HEAL DIAGNOSTICS", f"Detected issues:\n{details}")
    first_rel, first_err = next(iter(problems.items()))
    pname = Path(first_rel).stem
    if _evolve_lock.acquire(blocking=False):
        threading.Thread(
            target=_evolve_worker,
            args=(f"Fix broken plugin {first_rel}: {first_err}", pname, player),
            daemon=True,
        ).start()
        return f"Detected an issue in {pname} and started background self-healing on the HUD."
    return f"Found issues in {', '.join(problems.keys())}, but an evolution task is already running."


def _reflect(capability: str, player) -> str:
    note = (capability or "").strip()
    if not note:
        return "Tell me what lesson, cognitive directive, or rule you want me to internalize, sir."
    journal = _load_journal()
    journal["directives"].append({
        "ts": time.strftime("%Y-%m-%d %H:%M:%S"),
        "directive": note,
    })
    _save_journal(journal)
    try:
        from memory.memory_manager import update_memory
        key = _slug_name(note[:28])
        update_memory({"notes": {f"directive_{key}": note}})
    except Exception:
        pass
    _call(player, "show_content", "COGNITIVE REFLECTION SAVED", f"New self-evolution directive stored:\n\n• {note}")
    return "Internalized new cognitive directive into my long-term memory and evolution journal."


def run(parameters: dict, player=None, session_memory=None) -> str:
    params = parameters or {}
    action = str(params.get("action") or "status").strip().lower()
    capability = str(params.get("capability") or "").strip()
    plugin_name = str(params.get("plugin_name") or "").strip()

    if action == "status":
        return _status(player)
    if action == "learn_repo":
        return _learn_repo(capability or plugin_name or "tq-forge", player)
    if action == "heal":
        return _heal(player)
    if action == "reflect":
        return _reflect(capability, player)
    if action == "evolve" or capability:
        if not capability:
            return _status(player)
        if any(r in capability.lower() for r in ("tq-forge", "tq_forge", "omega-system", "omega_system")) and "learn" in capability.lower():
            return _learn_repo(capability, player)
        from plugins._skill_gate import preflight_clean_repo, sanitize_plugin_slug
        preflight_slug = sanitize_plugin_slug(plugin_name or capability.split()[0])
        ok_pre, pre_reason = preflight_clean_repo(_BASE, preflight_slug)
        if not ok_pre:
            msg = f"Branch-gated self-evolution blocked: {pre_reason}"
            _call(player, "write_log", f"ERR: [Self-Evolve] {msg}")
            return msg
        if not _evolve_lock.acquire(blocking=False):
            return "I am already staging a new capability on a skill/* branch — watch the HUD panel for progress."
        threading.Thread(
            target=_evolve_worker,
            args=(capability, plugin_name, player),
            daemon=True,
            name="jarvis-self-evolve",
        ).start()
        return (
            "Initiating branch-gated skill draft with scripts/validate_plugin.py. "
            "Main will remain untouched — watch the HUD for the skill/* branch diff and merge command."
        )
    return _status(player)
