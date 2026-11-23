"""
Branch-Gated Skill Drafter Plugin for J.A.R.V.I.S (Mark-LVII).

Drafts, validates, and lists candidate JARVIS plugins strictly on dedicated
`skill/<slug>-<timestamp>` Git branches via `plugins._skill_gate` and
`scripts/validate_plugin.py`, leaving `main` completely untouched until the user
explicitly reviews `git diff` and merges.
"""
from __future__ import annotations

from pathlib import Path
import re
import sys

PLUGIN = {
    "name": "skill_drafter",
    "description": (
        "Safely draft, validate, or inspect branch-gated JARVIS plugin skills without "
        "ever writing unvalidated AI code onto main. "
        "Actions: "
        "'draft' = generate (or accept supplied source_code) for a new plugin skill, "
        "stage it inside an isolated Git worktree on branch skill/<slug>-<timestamp>, "
        "validate it via scripts/validate_plugin.py, and return the git diff + manual "
        "merge command for explicit human approval; "
        "'list_branches' = list pending unmerged skill/* draft branches waiting for review; "
        "'validate' = run scripts/validate_plugin.py against an existing plugin file. "
        "Do NOT use dev_agent for JARVIS self-plugin drafting — dev_agent is for external "
        "user projects."
    ),
    "brief": (
        "Draft a validated JARVIS plugin on an isolated skill/* Git branch (never touching main), "
        "list pending skill/* branches, or validate an existing plugin."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "action": {
                "type": "STRING",
                "enum": ["list_branches", "draft", "validate"],
                "description": "'list_branches' (default safe readout), 'draft', or 'validate'.",
            },
            "skill_name": {
                "type": "STRING",
                "description": "Snake_case name of the plugin to draft or validate (e.g. 'pomodoro_timer').",
            },
            "specification": {
                "type": "STRING",
                "description": "For 'draft': concise description of what the new plugin should do.",
            },
            "source_code": {
                "type": "STRING",
                "description": "Optional pre-written Python plugin code to stage and validate directly.",
            },
        },
        "required": [],
    },
}


def _get_base_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent.parent


BASE_DIR: Path = _get_base_dir()


def _notify_hud(player, log_line: str, title: str = "", body: str = "") -> None:
    if player is None:
        return
    try:
        if hasattr(player, "write_log"):
            player.write_log(f"JARVIS: {log_line}")
    except Exception:
        pass
    if title and body:
        try:
            if hasattr(player, "show_content"):
                player.show_content(title, body[:4000])
        except Exception:
            pass


def _strip_code_fences(text: str) -> str:
    cleaned = re.sub(r"(?is)<think(?:ing)?>.*?</think(?:ing)?>", "", text or "").strip()
    cleaned = re.sub(r"^```[a-zA-Z]*\r?\n?", "", cleaned)
    cleaned = re.sub(r"\r?\n?```\s*$", "", cleaned)
    return cleaned.strip()


def _generate_candidate_code(slug: str, specification: str) -> str:
    """Draft a candidate plugin module through `core.gemini` following AGENTS.md rules."""
    from core import gemini

    tpl_path = BASE_DIR / "plugins" / "_template.py"
    tpl_text = tpl_path.read_text(encoding="utf-8", errors="replace") if tpl_path.is_file() else ""
    prompt = (
        f"Write a production-ready Mark-LVII drop-in plugin module `plugins/{slug}.py`.\n\n"
        f"Specification:\n{specification}\n\n"
        f"Template:\n{tpl_text}\n\n"
        "Hard rules (enforced automatically by scripts/validate_plugin.py):\n"
        f"1. Define module-level `PLUGIN` dict with `name='{slug}'`, a clear `description`, "
        "a shorter concise `brief`, and `parameters` (`type: 'OBJECT'`, `properties: {...}`).\n"
        "2. Define `def run(parameters: dict, player=None, session_memory=None) -> str:` "
        "that handles `parameters={}` and `player=None` cleanly, never raises, and returns `str`.\n"
        "3. Zero Qt/GUI widget toolkit imports; use `player.write_log` / `player.show_content` guarded by `if player`.\n"
        "4. Never read or write raw config or memory JSON paths directly — use `memory.config_manager` "
        "and `memory.memory_manager`.\n"
        "5. Never use shell execution primitives (`shell=True`, dynamic evaluation, or system shell calls).\n"
        "6. Output ONLY raw Python code with no markdown fences."
    )
    return _strip_code_fences(gemini.text(prompt, tier=gemini.SMART, timeout_ms=90_000) or "")


def _handle_list_branches(player=None, repo_root: Path | None = None) -> str:
    from plugins._skill_gate import list_skill_branches

    root = repo_root or BASE_DIR
    branches = list_skill_branches(root)
    if not branches:
        msg = "Sir, there are no pending skill/* draft branches awaiting review."
        _notify_hud(player, msg)
        return msg

    lines = [
        f"Pending skill draft branches ({len(branches)}):",
        *[f"  - {b}  (review: git diff main..{b} | merge: git merge --no-ff {b})" for b in branches],
    ]
    body = "\n".join(lines)
    reply = (
        f"Sir, there are {len(branches)} pending skill draft branch(es): "
        f"{', '.join(branches[:5])}. Review with git diff before merging."
    )
    _notify_hud(player, reply, "PENDING SKILL BRANCHES", body)
    return reply


def _handle_validate(skill_name: str, player=None, repo_root: Path | None = None) -> str:
    from plugins._skill_gate import sanitize_plugin_slug
    from scripts.validate_plugin import validate_plugin

    root = repo_root or BASE_DIR
    raw = str(skill_name or "").strip()
    if not raw:
        msg = "Sir, specify skill_name to validate (for example 'feedback_logger')."
        _notify_hud(player, msg)
        return msg

    slug = sanitize_plugin_slug(raw.removesuffix(".py"))
    target = root / "plugins" / f"{slug}.py"
    if not target.is_file():
        msg = f"Sir, plugin file 'plugins/{slug}.py' was not found."
        _notify_hud(player, msg)
        return msg

    report = validate_plugin(target, repo_root=root)
    verdict = "PASSED" if report["passed"] else "FAILED"
    detail_lines = [
        f"Plugin: plugins/{slug}.py ({verdict})",
        *[f"  - {k}: {'OK' if v else 'FAIL'}" for k, v in (report.get("checks") or {}).items()],
    ]
    if report.get("warnings"):
        detail_lines.append("\nWarnings:\n" + "\n".join(f"  - {w}" for w in report["warnings"]))
    if report.get("errors"):
        detail_lines.append("\nErrors:\n" + "\n".join(f"  - {e}" for e in report["errors"]))
    reply = (
        f"Validation {verdict} for plugins/{slug}.py "
        f"({len(report.get('errors') or [])} error(s), {len(report.get('warnings') or [])} warning(s))."
    )
    _notify_hud(player, reply, f"VALIDATE PLUGIN · {slug.upper()}", "\n".join(detail_lines))
    return reply


def _handle_draft(
    skill_name: str,
    specification: str,
    source_code: str = "",
    player=None,
    repo_root: Path | None = None,
) -> str:
    from plugins._skill_gate import preflight_clean_repo, sanitize_plugin_slug, stage_plugin_on_branch

    root = repo_root or BASE_DIR
    spec_text = str(specification or "").strip()
    code_text = str(source_code or "").strip()
    if not skill_name and not spec_text:
        msg = "Sir, provide skill_name and specification (or source_code) to draft a plugin on a skill/ branch."
        _notify_hud(player, msg)
        return msg

    slug = sanitize_plugin_slug(skill_name or (spec_text.split()[0] if spec_text else "custom"))
    ok_pre, pre_reason = preflight_clean_repo(root, slug)
    if not ok_pre:
        msg = f"Sir, cannot draft plugins/{slug}.py: {pre_reason}"
        _notify_hud(player, msg)
        return msg

    if not code_text:
        if not spec_text:
            msg = f"Sir, provide a specification or source_code for plugins/{slug}.py."
            _notify_hud(player, msg)
            return msg
        code_text = _generate_candidate_code(slug, spec_text)
        if not code_text:
            msg = f"Sir, model generation returned empty code for plugins/{slug}.py. Nothing was written."
            _notify_hud(player, msg)
            return msg

    def _auto_fix(broken_code: str, errs: list[str]) -> str | None:
        if source_code:
            return None  # Caller supplied explicit code; report exact errors instead of mutating it
        try:
            from core import gemini
            fix_prompt = (
                f"Fix these validation errors in Mark-LVII plugin `plugins/{slug}.py`:\n"
                + "\n".join(f"- {e}" for e in errs)
                + f"\n\nCurrent code:\n{broken_code}\n\n"
                "Return ONLY the corrected raw Python file with no markdown fences."
            )
            return _strip_code_fences(gemini.text(fix_prompt, tier=gemini.SMART, timeout_ms=90_000) or "")
        except Exception:
            return None

    staged = stage_plugin_on_branch(
        root,
        slug,
        code_text,
        summary=spec_text[:80] or f"draft {slug}",
        fix_fn=_auto_fix,
    )
    if not staged["ok"]:
        msg = f"Sir, branch-gated draft for '{slug}' was rejected: {staged['error']}"
        _notify_hud(player, msg, "SKILL DRAFT REJECTED", staged["error"])
        return msg

    branch = staged["branch"]
    body = (
        f"Branch: {branch} (unmerged — main untouched)\n"
        f"File: {staged['rel_path']}\n"
        f"Validator: PASSED\n\n"
        f"Review command:\n  {staged['review_cmd']}\n"
        f"Merge command (after review):\n  {staged['merge_cmd']}\n"
        f"Discard command:\n  {staged['discard_cmd']}\n\n"
        f"Diff preview:\n{staged['diff'][:2400]}"
    )
    reply = (
        f"Validated draft for '{staged['plugin_name']}' committed on branch '{branch}'. "
        f"Main is untouched — review with `{staged['review_cmd']}` and merge with `{staged['merge_cmd']}`."
    )
    _notify_hud(player, reply, f"SKILL DRAFT READY · {slug.upper()}", body)
    return reply


def run(parameters: dict, player=None, session_memory=None) -> str:
    """Entry point discovered by `core.plugin_loader`. Never raises."""
    _ = session_memory
    try:
        params = parameters if isinstance(parameters, dict) else {}
        action = str(params.get("action") or "list_branches").strip().lower()
        skill_name = str(params.get("skill_name") or "").strip()
        specification = str(params.get("specification") or "").strip()
        source_code = str(params.get("source_code") or "")

        if action == "list_branches":
            return _handle_list_branches(player=player)
        if action == "validate":
            return _handle_validate(skill_name, player=player)
        if action == "draft":
            return _handle_draft(skill_name, specification, source_code=source_code, player=player)

        msg = f"Sir, unknown skill_drafter action '{action}'. Use 'list_branches', 'draft', or 'validate'."
        _notify_hud(player, msg)
        return msg
    except Exception as exc:
        err = f"Sir, skill_drafter encountered an error: {exc}"
        _notify_hud(player, err)
        return err
