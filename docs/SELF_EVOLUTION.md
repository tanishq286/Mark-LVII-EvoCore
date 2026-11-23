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

---

## 4. Isolated Local QLoRA Fine-Tuning & Promotion Gate (Milestone 5)

Weight fine-tuning stays **strictly isolated** in `/home/tanishq/unsloth_env` (never adding PyTorch/Unsloth to `requirements.txt` or `Mark-LVII/.venv`), requires **>= 200 curated unique corrections** by default, and exports to a separate candidate model (`jarvis-custom`) so `qwen3:8b` is never overwritten.

| Stage | Command |
|---|---|
| Dry-run training plan (check CUDA, VRAM headroom, dataset floor & `Modelfile`) | `/home/tanishq/unsloth_env/bin/python scripts/train_local_lora.py --dry-run` |
| Run 4-bit QLoRA training + GGUF (`q4_k_m`) export (once >= 200 examples collected) | `/home/tanishq/unsloth_env/bin/python scripts/train_local_lora.py` |
| Register candidate model in Ollama (keeps `qwen3:8b` untouched) | `ollama create jarvis-custom -f training_data/exports/<run_tag>/gguf/Modelfile` |
| Dry-run the 20-prompt eval benchmark & promotion gate offline | `.venv/bin/python scripts/eval_local_model.py --dry-run` |
| Side-by-side live promotion gate (`qwen3:8b` vs `jarvis-custom`) | `.venv/bin/python scripts/eval_local_model.py --base qwen3:8b --candidate jarvis-custom` |
| Promote candidate (ONLY if gate outputs `PROMOTE`, exit `0`) | In HUD `⚙ → ENGINE & PROVIDERS`, set Ollama model to `jarvis-custom` |
| Instant model rollback (if any issue appears) | Switch Ollama model back to `qwen3:8b` (or `ollama rm jarvis-custom`) |
| Full repository rollback | `git checkout baseline-before-upgrade` |

---

## 5. Free Local Semantic Knowledge Vault (`knowledge/` — Milestone 7)

Drop private `.md`, `.txt`, `.py`, `.json`, `.csv`, or `.pdf` files into the git-ignored `knowledge/` folder to give JARVIS local semantic RAG with **$0 API cost** using Ollama `nomic-embed-text:latest` + SQLite + NumPy:

1. **Add documents:** Copy notes, project specs, or PDFs into `/home/tanishq/Mark-LVII/knowledge/` (subfolders allowed; symlinks outside `knowledge/` are blocked).
2. **Index incrementally:** Ask JARVIS *"index my knowledge vault"* (`knowledge_rag(mode="index")`) — unchanged files are skipped by SHA-256 hash, modified files are re-embedded, and deleted files are pruned from `knowledge/.rag_index.sqlite`.
3. **Query with citations:** Ask JARVIS *"search my knowledge vault for ..."* (`knowledge_rag(mode="search", query="...", top_k=5)`) — returns exact `knowledge/<rel_path>:L<start>-L<end>` citations wrapped inside `<<<UNTRUSTED source=knowledge_rag>>>` markers.
4. **Check status:** Ask *"check knowledge vault stats"* (`knowledge_rag(mode="stats")`).

---

## 6. Safe Local MCP Tool Bridge (`plugins/mcp_bridge.py` — Milestone 8)

Connect free local stdio or HTTP Model Context Protocol (MCP) servers without weakening Mark-LVII's security boundaries:

1. **Create your private server config (git-ignored):**
   ```bash
   cp config/mcp_servers.example.json config/mcp_servers.json
   ```
2. **Enable & allowlist explicitly:** Edit `config/mcp_servers.json` to set `"enabled": true` on a trusted local server and list ONLY the exact tools JARVIS may call inside `"allowed_tools"`. Any server left disabled or any tool omitted from `"allowed_tools"` is **denied by default**.
3. **Mutating-tool confirmation gate:** Tools listed in `"mutating_tools"` (or starting with mutating verbs such as `write_`, `delete_`, `remove_`, `create_`, `update_`, `exec_`, `run_`, `send_`, `post_`) never execute silently — they pause behind Mark-LVII's on-screen `core/confirm.py` CONFIRM/CANCEL banner (and are blocked when `player=None`).
4. **Untrusted-content isolation & timeouts:** Every MCP tool response is length-capped and wrapped inside `<<<UNTRUSTED source=mcp...>>>`, and stdio child processes are terminated cleanly on timeout with zero leaked processes.
5. **Inspect & invoke:** Use `mcp_bridge(mode="list_servers")`, `mcp_bridge(mode="list_tools", server="<name>")`, `mcp_bridge(mode="call_tool", server="<name>", tool="<tool>", arguments={...})`, or `mcp_bridge(mode="stats")`.

---

## 7. Unified Single-Brain Architecture (Milestone 9 — Windows 11 + WSL2)

`C:\Users\tanis\Downloads\Mark-LVII-main\Mark-LVII-main` is the **single source of truth** (one Git repo with full `baseline-before-upgrade` → `HEAD` history, one `memory/long_term.json`, one `memory/routines.json`, one `knowledge/` vault, one `training_data/`, and one `reports/`). The legacy WSL2 directory (`/home/tanishq/Mark-LVII`) is archived read-only via `.archived_single_brain` so split-brain drift can never recur.

| Task (from Windows PowerShell / CMD) | Command |
|---|---|
| Launch unified J.A.R.V.I.S. (Windows HUD + Voice + WSL2 backend) | `C:\Users\tanis\AppData\Local\Python\pythoncore-3.13-64\pythonw.exe "C:\Users\tanis\Downloads\Mark-LVII-main\Mark-LVII-main\main.py"` |
| Run one-command health check (<10s) | `python scripts\health_check.py` |
| Dry-run local QLoRA training via WSL2 CUDA bridge | `python scripts\wsl_train.py --dry-run` |
| Run real QLoRA training in WSL2 `/home/tanishq/unsloth_env` | `python scripts\wsl_train.py` *(or `python scripts\train_local_lora.py --wsl`)* |
| Run 20-prompt local model promotion gate | `python scripts\eval_local_model.py --base qwen3:8b --candidate jarvis-custom` |
| Full Git rollback (via WSL2 git) | `wsl.exe -d Ubuntu-D --cd /mnt/c/Users/tanis/Downloads/Mark-LVII-main/Mark-LVII-main -- git checkout baseline-before-upgrade` |

## 8. Defensive Cybersecurity & Ethical-Hacking Knowledge Vault ($0 Local)

J.A.R.V.I.S. includes a strictly defensive, local-only cybersecurity stack at zero paid API cost:

- **Defensive Inspector Plugin ([plugins/cybersec_defender.py](../plugins/cybersec_defender.py)):**
  - `audit_ports`: Read-only local listening TCP/UDP socket & process audit (flags unexpected `0.0.0.0` / `::` wildcard exposures).
  - `audit_posture`: Read-only Windows Defender, Firewall (`Domain`/`Private`/`Public`), and UAC (`EnableLUA`) status check.
  - `audit_code`: Local folder/file static SAST + secret scanner (redacts all discovered secrets, skips protected credential/memory files, blocks symlink escapes outside target).
  - `audit_headers`: Loopback-only (`127.0.0.1`, `localhost`, `::1`) HTTP security header inspector; refuses non-loopback targets.
  - `cve_lookup`: Free OSV.dev (`https://api.osv.dev/v1`) advisory & package vulnerability lookup wrapped in `<<<UNTRUSTED source=cybersec_cve>>>`.
- **Threat-Modeling & Research Agent ([agents/cybersec_analyst.md](../agents/cybersec_analyst.md)):**
  - Runs in isolated `mode: research` with `search_web, fetch_page, file_processor` only (strictly omitting `recall_memory` and all acting/write tools) for STRIDE threat modeling, OWASP Top 10/ASVS code reviews, log triage, and authorized CTF/lab study.
  - Outputs evidence-cited Markdown reports with `Severity` (`Critical`/`High`/`Medium`/`Low`), `Root Cause`, and `Remediation` steps for `reports/`.
- **Local Specialist Routing ([providers/local_router.py](../providers/local_router.py)):**
  - Code-audit, CWE, SAST, and vulnerability-remediation prompts automatically route to `qwen2.5-coder:14b` (`coding`).
  - STRIDE threat modeling, OWASP ASVS architecture reviews, MITRE ATT&CK, and log-triage prompts route to `qwen2.5:14b` / `gemma4:12b` (`reasoning`) under the 12 GB VRAM guard.
- **Offline Cybersecurity Reference Vault (`knowledge/cybersec/`):**
  - Store personal Markdown/text study notes (OWASP Cheat Sheets, MITRE ATT&CK defensive mitigations, and authorized HTB/TryHackMe/Juice Shop lab writeups) in git-ignored `knowledge/cybersec/`.
  - Index offline via `knowledge_rag(mode="index")` (`nomic-embed-text:latest` + SQLite cosine vectors) and query anytime with `knowledge_rag(mode="search", query="...")`.

