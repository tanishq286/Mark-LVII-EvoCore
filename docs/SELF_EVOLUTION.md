# J.A.R.V.I.S Safe Self-Evolution Workflow (Mark-LVII)

This document defines the closed-loop, human-in-the-loop self-evolution pipeline for Mark-LVII. No AI-generated plugin, prompt edit, or weight rewrite is ever applied silently to `main`.

---

## 1. The Safe Self-Evolution Loop

```text
User Correction / Friction
        │
        ▼
[1] feedback_logger (plugins/feedback_logger.py)
    • Appends structured ChatML pair → training_data/corrections.jsonl (git-ignored)
    • Saves durable rule immediately via memory.memory_manager (lesson_<slug>_<hash>)
        │
        ▼
[2] Nightly 01:00 Silent Reflection (agents/reflector.md)
    • core/tasks/snapshots.py stages bounded read-only inputs/ snapshots
      (corrections.jsonl + latest 5 non-reflector reports/*.md; excludes older reflections)
    • Read-only sandbox: ONLY recall_memory + file_processor (zero web/write/acting tools)
    • Writes cited Markdown report → reports/YYYY-MM-DD-nightly-reflection-<id>.md
    • Proposes human-reviewable core/prompt.txt edits & M4 skill candidates (never auto-applied)
        │
        ▼
[3] Branch-Gated Skill Drafting (plugins/skill_drafter.py, self_evolve, forge_skill_factory)
    • Shared gate: plugins/_skill_gate.py
    • Preflight refuses dirty working trees, non-Git dirs, and core action collisions
    • Writes candidate plugin ONLY inside a temp Git worktree on branch skill/<slug>-<ts>
        │
        ▼
[4] Automated Static + Isolated Runtime Gate (scripts/validate_plugin.py)
    • Syntax (ast.parse + py_compile), PLUGIN contract + concise brief check
    • Collision check across main.py inline tools, actions/*.py, and plugins/*.py
    • AST security gate: blocks Qt imports, raw api_keys.json / long_term.json access,
      and eval / exec / __import__ / os.system / os.popen / subprocess(..., shell=True)
    • Clean subprocess smoke test: run({}, player=None, session_memory=None) → str
    • Failed drafts auto-delete their temp worktree AND branch
        │
        ▼
[5] Explicit Human Review, Merge, or Rollback
    • Inspect exact diff on main:      git diff main..skill/<slug>-<ts>
    • Approve & merge explicitly:      git merge --no-ff skill/<slug>-<ts>
    • Or discard without trace:        git branch -D skill/<slug>-<ts>
    • Emergency full rollback:         git checkout baseline-before-upgrade
```

---

## 2. Hard Safety Invariants

1. **Zero unvalidated writes to `main`:** `plugins/skill_drafter.py`, `plugins/self_evolve.py` (`action="evolve"` / `"heal"`), and `plugins/forge_skill_factory.py` (`action="compile_skill"`) all enforce `plugins/_skill_gate.py`.
2. **Never touch a dirty `main` tree:** If `git status --porcelain` is non-empty, drafting aborts before creating any worktree or branch.
3. **Never overwrite core actions:** Any candidate whose slug matches a file in `actions/*.py` is rejected.
4. **Never leak secrets or mutate runtime configs:** `config/api_keys.json`, `memory/long_term.json`, `memory/routines.json`, and `training_data/` remain git-ignored and off-limits to generated plugins.
5. **Windows code mirroring stays code-only:** After merging a reviewed skill on WSL2 (`/home/tanishq/Mark-LVII`), mirror **only** verified code files (`.py`, `.md`, `.gitignore`, `scripts/`) to `C:\Users\tanis\Downloads\Mark-LVII-main\Mark-LVII-main` — never overwrite Windows `config/api_keys.json`, `memory/`, `training_data/`, or `.venv`.

---

## 3. Quick Operator Commands

| Task | Command |
|---|---|
| Validate a plugin (human view) | `.venv/bin/python scripts/validate_plugin.py plugins/feedback_logger.py` |
| Validate a plugin (JSON output) | `.venv/bin/python scripts/validate_plugin.py plugins/feedback_logger.py --json` |
| List pending unmerged skill branches | `git branch --list 'skill/*'` |
| Review a drafted skill diff | `git diff main..skill/<slug>-<timestamp>` |
| Merge an approved skill branch | `git merge --no-ff skill/<slug>-<timestamp>` |
| Discard a rejected skill branch | `git branch -D skill/<slug>-<timestamp>` |
| Run full provider + upgrade checks | `.venv/bin/python -u -m providers._tests && .venv/bin/python -u scripts/verify_m4_skill_workflow.py` |
| Roll back to pre-upgrade baseline | `git checkout baseline-before-upgrade` |
