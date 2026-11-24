# Mark-LVII EvoCore Upgrade Progress

- **Active Milestone:** **Milestone 10 COMPLETE (Gated — Awaiting User Confirmation for Milestone 11)**
- **Branch:** `milestone/10-evocore-safety-evolution` (`2c79c7d` + progress commit)
- **Base `main` Commit (Untouched):** `3a34d38`
- **Test Runner Installed:** `pytest 9.2.0` (`C:\Users\tanis\AppData\Local\Python\pythoncore-3.13-64\python.exe`)

---

## Milestone 10 — Completed Modules & Upgrades

### Auto-Evolution & Safety Core
1. **`core/evolution/safety_guard.py`** — Immutable path protection (`IMMUTABLE_PATHS` including `safety_guard.py`, `core/confirm.py`, `plugins/_skill_gate.py`, `plugins/file_vault.py`, `scripts/validate_plugin.py`, `voice_lock.py`), hardened AST evasion scanner (blocks `importlib.import_module`, `getattr(__builtins__, ...)`, `builtins.__dict__`, `globals()["__builtins__"]`, `ctypes`, `shutil.rmtree`, `eval`/`exec`/`compile`/`__import__`, `shell=True`, protected file paths, Qt imports), declarative `SAFE`/`CONFIRM`/`LOCKED` risk tiers (`classify_risk`, `enforce_gate`), and tracked Git evolution commit ledger + `rollback_last_evolution()`.
2. **`core/evolution/telemetry.py`** — Git-ignored SQLite WAL telemetry (`memory/telemetry.sqlite`) recording tool latency, success/failure, sanitized args, and full exception tracebacks plus model route outcomes (`p50_ms`, `p95_ms`, `success_rate`, `consecutive_failures`, `recent_failures`, `recurring_failure_lessons`, `recent_tool_sequence`), wired into `main.py:_execute_tool`.
3. **`core/evolution/self_healer.py`** — Telemetry-driven unhealthy plugin detector, isolated `autofix/<plugin>-<ts>` Git worktree patcher + regression `pytest` writer, and subprocess Replay Self-Benchmarking (`replay_benchmark_plugin`) requiring strictly higher pass rate over the last 5 failed tasks (auto-reverting branch & worktree on failure).
4. **`core/evolution/habit_compiler.py`** — Episode sequence miner over `telemetry.sqlite` (`detect_habit_candidates`), macro persistence in git-ignored `memory/habits.json` (`compile_habit`), and confirmation-aware `run_habit()`.
5. **`core/evolution/skill_synthesizer.py`** — Co-generates `plugins/<slug>.py` + `tests/evolved/test_<slug>.py` on `skill/<slug>-<ts>`, enforces AST guard + `scripts/validate_plugin.py` + sandbox `pytest` + optional replay benchmark, and provides `approve_and_hotload()` with `PluginRegistry.refresh()` + ledger recording.
6. **`core/evolution/reflection_loop.py`** — Generates numbered `memory/evolution_digest.json` proposals (`approve_branch`, `self_heal`, `compile_habit`), formats `[FAILURE IMMUNITY — AVOID PAST ERRORS]`, triggers RAG chunk consolidation, supports `approve_upgrade()`, and integrates with `core/auto_evolution.py`'s background loop.
7. **`plugins/evolution_control.py`** — Unified voice/HUD control surface (`status`, `telemetry`, `digest`/`reflect`, `approve_upgrade`, `rollback_last`, `synthesize`, `self_heal`, `habits`, `compile_habit`, `run_habit`, `consolidate_memory`).

### 6 EvoCore Upgrades
1. **Adaptive Hybrid Router (`providers/local_router.py`, `core/gemini.py`):** `adaptive_rank_models()` re-orders local RTX 5070 Ti Ollama candidates by live `route_events` success rate & P95 latency and automatically escalates to Gemini when all evaluated local candidates degrade (while keeping `providers/` strictly lazy-loaded at startup).
2. **Failure Immunity & RAG Consolidation (`plugins/knowledge_rag.py`, `core/evolution/reflection_loop.py`):** Added `consolidate_vault()` / `mode="consolidate"` removing duplicate chunks (`cosine >= 0.96`), orphan chunks, and empty chunks; injected recurring failure lessons into planning.
3. **Replay Self-Benchmarking (`core/evolution/self_healer.py`, `core/evolution/skill_synthesizer.py`):** Replays historical failed tasks in isolated `-B` subprocesses before committing `skill/*` or `autofix/*` branches.
4. **Verified Multi-Step Planner + Multi-Option Choice HUD (`core/evolution/planner.py`, `plugins/autonomous_brain.py`, `core/confirm.py`, `gui/overlays.py`, `gui/window.py`, `ui.py`, `main.py`):** Implemented `VerifiedPlanner` (Plan -> Execute -> Verify -> Recover + failure immunity) and Qt-thread-safe `ChoiceBanner` (`request_choice` / `resolve_choice` supporting button click or voice choice).
5. **Uniform Risk-Tier Enforcement (`actions/send_message.py`, `actions/computer_settings.py`):** Routed WhatsApp/messaging dispatch through `safety_guard.enforce_gate()` (`CONFIRM`); added `hibernate`, `sign_out`, `schedule_shutdown`, `schedule_restart` (all `CONFIRM`-gated), plus `cancel_power_timer` (`SAFE`).
6. **Security & 3-Persona Wins (`plugins/file_vault.py`, `plugins/cybersec_defender.py`, `core/persona.py`, `plugins/persona_control.py`, `memory/config_manager.py`, `main.py`):**
   - Immutable AES-256-GCM + PBKDF2-SHA256 (600k iters) `file_vault` with ZipSlip-safe `lock_folder`/`unlock_folder` (`LOCKED` gate) and Windows `attrib` hide/unhide.
   - Windows Defender CLI `defender_scan_file` and `CONFIRM`-gated `defender_quick_scan` via `MpCmdRun.exe`.
   - Instant 3-persona switcher (`precision`, `empathetic`, `expressive`) persisted via `config_manager` and injected into system instructions.

---

## Changed & Added Files (Branch `milestone/10-evocore-safety-evolution`)
- **Created:**
  - `pytest.ini`
  - `EVOLUTION_PROGRESS.md`
  - `core/persona.py`
  - `core/evolution/__init__.py`
  - `core/evolution/safety_guard.py`
  - `core/evolution/telemetry.py`
  - `core/evolution/self_healer.py`
  - `core/evolution/habit_compiler.py`
  - `core/evolution/skill_synthesizer.py`
  - `core/evolution/reflection_loop.py`
  - `core/evolution/planner.py`
  - `plugins/file_vault.py`
  - `plugins/persona_control.py`
  - `plugins/evolution_control.py`
  - `tests/test_safety_guard.py`
  - `tests/test_telemetry_and_router.py`
  - `tests/test_evolution_pipeline.py`
  - `tests/test_planner_and_choice.py`
  - `tests/test_security_and_persona.py`
- **Modified:**
  - `.gitignore`
  - `actions/computer_settings.py`
  - `actions/send_message.py`
  - `core/auto_evolution.py`
  - `core/confirm.py`
  - `core/gemini.py`
  - `gui/overlays.py`
  - `gui/window.py`
  - `main.py`
  - `memory/config_manager.py`
  - `plugins/autonomous_brain.py`
  - `plugins/cybersec_defender.py`
  - `plugins/knowledge_rag.py`
  - `providers/local_router.py`
  - `scripts/validate_plugin.py`
  - `ui.py`

---

## Verification Summary (Windows Python 3.13.17)
- `pytest -v`: **17 passed, 0 failed** (18.22s)
- `scripts/validate_plugin.py`: **6/6 PASS** (0 errors, 0 warnings across `file_vault`, `persona_control`, `evolution_control`, `cybersec_defender`, `knowledge_rag`, `autonomous_brain`)
- `scripts/verify_cybersec.py`: **8/8 checks PASSED** (`ALL CYBERSEC CHECKS PASSED`)
- `scripts/verify_m4_skill_workflow.py`: **18/18 checks PASSED** (`active=15, rejected=0`)
- `scripts/verify_m7_knowledge_rag.py`: **12/12 checks PASSED**
- `scripts/verify_m1_hybrid_brain.py`: **10/10 checks PASSED**
- `scripts/verify_m6_local_router.py`: **10/10 checks PASSED**
- `scripts/verify_wake_activation.py`: **8/8 checks PASSED** (live WASAPI RAW `false_hits=0`)
- `python -m providers._tests`: **108 passed, 0 failed**
- `scripts/health_check.py`: **ALL HEALTHY (`10/10` in `9.51s` on `2c79c7d`, `active=15`, `rejected=0`, `core_checked=28`, `unignored_secrets=none`)**; clean post-handoff check re-run without `--allow-dirty` after committing `EVOLUTION_PROGRESS.md`

---

## Next Steps (Pending User Confirmation)
- **Milestone 11 (`milestone/11-windows-desktop-parity`):** Branch from verified Milestone 10 (after user review/approval) and implement remaining Windows desktop parity modules (Biometric Voice Lock, APScheduler cron Task Scheduler, Network speedtest + IP report, `%TEMP%`/cache/Recycle Bin System Cleaner, Spotify `spotipy`, MediaPipe gestures, Smart Auto-Scroll, Notepad streaming dictation, `.pptx` generator, Playwright+BS4 scraper, AI Website Builder, AI Image Generator, full Excel editing, Email SMTP/Gmail `CONFIRM`, WhatsApp media+calls, batch format converter, Unit Test Generator, Project Preferences Config, Smart Form Filler, Shopping Price Analyzer, Whisper YouTube fallback, `yfinance` Stocks/Forex/Crypto, transparent 2D Avatar overlay + 8 gestures).
- **Milestone 12 (`milestone/12-android-max-companion`):** Generate & run `scripts/setup_android_env.ps1` (OpenJDK 17, Android cmdline-tools, Gradle, ADB) and build `android_companion/` Kotlin MAX app synced with `dashboard/server.py` `/ws` bridge + offline mode.
