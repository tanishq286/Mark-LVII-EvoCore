"""plugins/_skill_gate.py — Shared Branch-Gated Skill Staging & Validation Helper.

Leading underscore keeps `core/plugin_loader.py` from exposing this helper as a
standalone tool. Every self-coding plugin (`skill_drafter`, `self_evolve`,
`forge_skill_factory`) routes through here so that:

  1. No AI-generated plugin is EVER written directly onto `main` unvalidated.
  2. A dirty working tree on `main` is rejected before touching Git or disk.
  3. Core actions in `actions/*.py` can never be overwritten or collided with.
  4. Every draft is written inside an isolated temporary Git worktree on a new
     branch `skill/<slug>-<YYYYMMDD-HHMMSS>`, validated via
     `scripts/validate_plugin.py`, and committed ONLY on that branch.
  5. Failed drafts auto-clean both their worktree and branch; passing drafts
     remove the temp worktree while keeping the unmerged `skill/*` branch and
     returning the exact `git diff` + manual merge / discard commands.
"""
from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import re
import secrets
import shutil
import subprocess
import sys
import tempfile
from typing import Callable

_SLUG_RE = re.compile(r"^[a-z][a-z0-9_]{1,48}$")


def sanitize_plugin_slug(raw: str) -> str:
    """Normalize a requested skill/plugin name to a safe snake_case identifier."""
    slug = re.sub(r"[^a-z0-9_]+", "_", str(raw or "").lower()).strip("_")
    if not slug or not slug[0].isalpha():
        slug = "skill_" + (slug or "custom")
    slug = slug[:44].rstrip("_")
    return slug if _SLUG_RE.match(slug) else "skill_custom"


def _using_wsl_git() -> bool:
    return sys.platform == "win32" and shutil.which("git") is None and shutil.which("wsl.exe") is not None


def _git_path_arg(path: Path) -> str:
    """Format a local path for whichever git executable `_git()` invokes."""
    if _using_wsl_git():
        from core.cross_os import to_wsl_path
        return to_wsl_path(str(path.resolve()))
    return str(path)


def _git(repo_root: Path, *args: str, timeout: float = 30.0) -> subprocess.CompletedProcess:
    from core.cross_os import run_git
    return run_git(repo_root, *args, timeout=timeout)


def _core_action_names(repo_root: Path) -> set[str]:
    names: set[str] = set()
    actions_dir = repo_root / "actions"
    if not actions_dir.is_dir():
        return names
    for p in actions_dir.glob("*.py"):
        if not p.name.startswith("_"):
            names.add(p.stem)
    return names


def preflight_clean_repo(repo_root: Path, plugin_slug: str) -> tuple[bool, str]:
    """Verify Git repo availability, clean working tree, and non-colliding plugin slug."""
    root = Path(repo_root).resolve()
    slug = sanitize_plugin_slug(plugin_slug)

    if slug.startswith("_"):
        return False, f"Plugin slug '{slug}' cannot start with an underscore."
    if slug in _core_action_names(root):
        return False, f"Refused: '{slug}' collides with a core action in actions/. Choose a distinct plugin name."

    from core.cross_os import git_available
    if not git_available():
        return False, "Git executable not found (native or WSL2); branch-gated skill drafting requires Git."

    inside = _git(root, "rev-parse", "--is-inside-work-tree")
    if inside.returncode != 0 or inside.stdout.strip().lower() != "true":
        return (
            False,
            f"'{root}' is not a Git repository. Run branch-gated skill drafting in the primary "
            "Git-tracked repository so `main` stays protected.",
        )

    head_chk = _git(root, "rev-parse", "--verify", "HEAD")
    if head_chk.returncode != 0:
        return False, "Git repository has no commits yet; create an initial baseline commit first."

    status = _git(root, "status", "--porcelain")
    if status.returncode != 0:
        return False, f"Failed to inspect git status: {status.stderr.strip()}"
    if status.stdout.strip():
        dirty_count = len([ln for ln in status.stdout.splitlines() if ln.strip()])
        return (
            False,
            f"Refusing to draft plugin while working tree is dirty ({dirty_count} uncommitted "
            "change(s)). Commit or stash changes on `main` first.",
        )

    return True, "ok"


def _run_validator_cli(validator_script: Path, plugin_file: Path, worktree_root: Path) -> dict:
    """Run `scripts/validate_plugin.py --json` against the staged plugin inside the worktree."""
    proc = subprocess.run(
        [
            sys.executable,
            str(validator_script),
            str(plugin_file),
            "--repo-root",
            str(worktree_root),
            "--json",
        ],
        cwd=str(worktree_root),
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=30.0,
    )
    raw = proc.stdout.strip()
    if not raw:
        return {
            "passed": False,
            "errors": [f"Validator returned no output (exit={proc.returncode}): {proc.stderr.strip()[:240]}"],
            "warnings": [],
        }
    try:
        data = json.loads(raw)
        data["exit_code"] = proc.returncode
        return data
    except json.JSONDecodeError:
        return {
            "passed": False,
            "errors": [f"Validator output was not valid JSON: {raw[:240]}"],
            "warnings": [],
        }


def list_skill_branches(repo_root: Path) -> list[str]:
    """Return unmerged/existing `skill/*` branch names in `repo_root`."""
    root = Path(repo_root).resolve()
    res = _git(root, "branch", "--list", "skill/*", "--format=%(refname:short)")
    if res.returncode != 0:
        return []
    return [ln.strip() for ln in res.stdout.splitlines() if ln.strip()]


def stage_plugin_on_branch(
    repo_root: Path,
    plugin_slug: str,
    code: str,
    summary: str = "",
    *,
    fix_fn: Callable[[str, list[str]], str | None] | None = None,
    max_fix_rounds: int = 2,
    validator_script: Path | None = None,
) -> dict:
    """Write `code` into an isolated `skill/<slug>-<ts>` worktree, validate, and commit on branch.

    Never writes to the live `main` working tree. On validation failure, removes
    both the temporary worktree and the temporary branch cleanly.
    """
    root = Path(repo_root).resolve()
    slug = sanitize_plugin_slug(plugin_slug)

    ok_pre, reason = preflight_clean_repo(root, slug)
    if not ok_pre:
        return {
            "ok": False,
            "error": reason,
            "branch": "",
            "plugin_name": slug,
            "rel_path": f"plugins/{slug}.py",
            "validation": None,
            "diff": "",
        }

    val_script = Path(validator_script).resolve() if validator_script else (root / "scripts" / "validate_plugin.py")
    if not val_script.is_file():
        # Fallback to this repo's validator when running against an isolated test git repo
        fallback_val = Path(__file__).resolve().parent.parent / "scripts" / "validate_plugin.py"
        if fallback_val.is_file():
            val_script = fallback_val
        else:
            return {
                "ok": False,
                "error": f"Validator script missing: {val_script}",
                "branch": "",
                "plugin_name": slug,
                "rel_path": f"plugins/{slug}.py",
                "validation": None,
                "diff": "",
            }

    base_ref = _git(root, "rev-parse", "--abbrev-ref", "HEAD").stdout.strip() or "main"
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    branch = f"skill/{slug}-{stamp}"
    if _git(root, "show-ref", "--verify", "--quiet", f"refs/heads/{branch}").returncode == 0:
        branch = f"{branch}-{secrets.token_hex(2)}"

    wt_parent = Path(tempfile.mkdtemp(prefix="jarvis-skill-wt-"))
    wt_dir = wt_parent / "repo"
    branch_created = False
    keep_branch = False
    rel_path = f"plugins/{slug}.py"

    try:
        add_wt = _git(root, "worktree", "add", "-b", branch, _git_path_arg(wt_dir), "HEAD")
        if add_wt.returncode != 0:
            return {
                "ok": False,
                "error": f"Failed to create isolated Git worktree: {add_wt.stderr.strip()}",
                "branch": "",
                "plugin_name": slug,
                "rel_path": rel_path,
                "validation": None,
                "diff": "",
            }
        branch_created = True

        target_file = wt_dir / rel_path
        target_file.parent.mkdir(parents=True, exist_ok=True)
        current_code = (code or "").rstrip() + "\n"
        target_file.write_text(current_code, encoding="utf-8")

        validation = _run_validator_cli(val_script, target_file, wt_dir)
        attempts = 0
        while not validation.get("passed") and fix_fn is not None and attempts < max_fix_rounds:
            attempts += 1
            errs = list(validation.get("errors") or [])
            revised = fix_fn(current_code, errs)
            if not revised or not revised.strip():
                break
            current_code = revised.rstrip() + "\n"
            target_file.write_text(current_code, encoding="utf-8")
            validation = _run_validator_cli(val_script, target_file, wt_dir)

        if not validation.get("passed"):
            return {
                "ok": False,
                "error": "Validation failed: " + "; ".join(validation.get("errors") or ["unknown error"]),
                "branch": "",
                "plugin_name": slug,
                "rel_path": rel_path,
                "validation": validation,
                "diff": "",
            }

        # Stage and commit ONLY the validated plugin inside the isolated worktree
        add_res = _git(wt_dir, "add", "--", rel_path)
        if add_res.returncode != 0:
            return {
                "ok": False,
                "error": f"git add failed in worktree: {add_res.stderr.strip()}",
                "branch": "",
                "plugin_name": slug,
                "rel_path": rel_path,
                "validation": validation,
                "diff": "",
            }

        # Handle no-op edit cleanly
        diff_cached = _git(wt_dir, "diff", "--cached", "--quiet")
        if diff_cached.returncode == 0:
            return {
                "ok": False,
                "error": f"Draft for '{rel_path}' produced no changes relative to {base_ref}.",
                "branch": "",
                "plugin_name": slug,
                "rel_path": rel_path,
                "validation": validation,
                "diff": "",
            }

        msg_summary = " ".join(str(summary or f"draft {rel_path}").split())[:90]
        commit_res = _git(
            wt_dir,
            "-c", "user.name=JARVIS Skill Gate",
            "-c", "user.email=jarvis-local@localhost",
            "commit",
            "-m", f"feat(skill): {msg_summary} (validated)",
        )
        if commit_res.returncode != 0:
            return {
                "ok": False,
                "error": f"git commit failed in worktree: {commit_res.stderr.strip()}",
                "branch": "",
                "plugin_name": slug,
                "rel_path": rel_path,
                "validation": validation,
                "diff": "",
            }

        diff_res = _git(wt_dir, "diff", "HEAD~1..HEAD")
        diff_text = diff_res.stdout if diff_res.returncode == 0 else ""
        keep_branch = True

        return {
            "ok": True,
            "error": "",
            "branch": branch,
            "base_ref": base_ref,
            "plugin_name": validation.get("plugin_name") or slug,
            "rel_path": rel_path,
            "validation": validation,
            "diff": diff_text,
            "review_cmd": f"git diff {base_ref}..{branch}",
            "merge_cmd": f"git merge --no-ff {branch}",
            "discard_cmd": f"git branch -D {branch}",
            "rollback_cmd": "git checkout baseline-before-upgrade",
        }
    finally:
        # Always detach and remove the temporary worktree directory
        _git(root, "worktree", "remove", "--force", _git_path_arg(wt_dir))
        shutil.rmtree(wt_parent, ignore_errors=True)
        if branch_created and not keep_branch:
            _git(root, "branch", "-D", branch)
