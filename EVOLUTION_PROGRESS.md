# Mark-LVII EvoCore Upgrade Progress

- **Active Milestone:** **Milestone 13 — Closed-Loop Autonomous Screen Agent (`Observe -> Verify -> Act -> Re-Observe`) Verified & Ready for Review**
- **Current Branch:** `milestone/13-autonomous-screen-loop` (`53aa8e3`, branched off `main` `54d60bc`, tagged `v12-evocore-10of10`)
- **Base `main` Commit (Untouched):** `54d60bc` (`v12-evocore-10of10`)
- **Test Runners Installed:**
  - `pytest 9.2.0` (`C:\Users\tanis\AppData\Local\Python\pythoncore-3.13-64\python.exe`)
  - OpenJDK 17 (`EclipseAdoptium.Temurin.17.JDK`), Gradle `8.10.2`, Android SDK `android-35` + `build-tools 34.0.0/35.0.0` + `adb`

---

## Final Modules + 5/5 Evolution Engines Completion Scorecard

| Track | Target | Verified Implementation | Status |
| :--- | :--- | :--- | :--- |
| **EvoCore Auto-Evolution Engines** | 5 / 5 | Telemetry SQLite WAL, Safety Guard + Rollback, Self-Healer + Replay Benchmark, Habit Compiler, Skill Synthesizer + Reflection Loop | **5 / 5 COMPLETE** |
| **Milestone 10: Security, Hybrid Router & Planner Core** | 6 / 6 | Adaptive Router (`local_router.py`), RAG Consolidation & Failure Immunity, Replay Self-Benchmarking, Verified Planner + Choice HUD (`confirm.py`), Uniform `SAFE`/`CONFIRM`/`LOCKED` gates, AES-256-GCM `file_vault` + Defender + 3-Persona | **6 / 6 COMPLETE** |
| **Milestone 11: Windows Desktop Parity** | 41 / 41 | Biometric Voice Lock, Cron SQLite Routines, Network Speed/IP, `system_cleaner`, Spotify, MediaPipe Gestures, Auto-Scroll & Notepad Dictation, `.pptx` Decks, Scraper + Price Comparator, Website & Image Generators, `office_suite` (Excel/PDF/Convert), Email SMTP/IMAP, WhatsApp Media/Calls, Dev Workspace + Profiles, AES Form Filler, YouTube Whisper Fallback, Market Finance, 2D Avatar Overlay (8 Gestures) | **41 / 41 COMPLETE** |
| **Milestone 12: Android MAX Companion & PC Bridge** | 7 / 7 | Setup Pre-Flight (`setup_android_env.ps1`), Bidirectional TLS `/ws` Bridge + Instant PWA (`/pwa`), `android_bridge` Plugin, Native Kotlin App (`AntiTheftGuardService`, `WakeWordVoiceService`, `WhatsAppAndCallController`, `MobileToggleManager`, `ScreenVisionScrollService`, `EmergencySosManager`, `VoiceCameraController`) + On-Device Privacy Mode | **7 / 7 COMPLETE** |
| **Milestone 13: Closed-Loop Autonomous Screen Agent** | 2 / 2 | Hybrid UIA + Vision/OCR Grounding (`core/evolution/screen_agent.py`), Post-Action Settle Polling (up to 3s), Stuck-State Recovery + `ChoiceBanner` Fallback, Multi-Step Wiring (`planner.py`, `autonomous_brain.py`, `plugins/autonomous_screen_agent.py`, `main.py`), `CONFIRM`/`LOCKED` Irreversible Gate, ESC / Corner / `"Jarvis stop"` Kill-Switches, Step Telemetry for `habit_compiler.py` | **COMPLETE** |

---

## Milestone 13 — Closed-Loop Autonomous Screen Agent (`53aa8e3`)

### 1. Closed-Loop Visual Controller (`core/evolution/screen_agent.py`, `plugins/autonomous_screen_agent.py`)
- **Hybrid Screen Grounding:** Combines `core/screenview.py` UI Automation accessibility trees and logical bounding boxes with `actions/screen_processor.py` JPEG capture, SHA-256 frame hashing, and `core/gemini.py` Vision/OCR fallback grounding (`ground_with_vision`, `observe_screen`) whenever UIA trees are thin, degraded, or missing target controls.
- **Post-Action Verification Loop:** Polls for UI settle up to `3.0s` after every click, hotkey, app launch, or typing step—watching foreground window title, process transitions, UIA digest, and screenshot frame hash before confirming progress.
- **Stuck-State Recovery & Choice Fallback:** If the desktop state does not transition or expected postconditions remain unmet, executes one alternative recovery strategy (app focus/launch, HUD unfocus `alt+tab`, hotkey fallback, or Vision coordinate click) and invokes `core/confirm.py` `ChoiceBanner` (`request_choice`) when multiple ambiguous candidates or dialogs block progress.
- **Multi-Step Wiring (`core/evolution/planner.py`, `plugins/autonomous_brain.py`, `main.py`):** Added `is_gui_screen_goal()` and `requires_screen_loop` routing so interactive multi-step GUI workflows engage `AutonomousScreenAgent` (`max_steps=12`) with live HUD step narration via `write_log` and `show_code_job`.

### 2. Live HUD Feedback, Hard Safety Guardrails & Telemetry
- **Irreversible Action Gate (`core/evolution/safety_guard.py`, `core/confirm.py`):** Added `("autonomous_screen_agent", "irreversible_act"): CONFIRM` and `("autonomous_screen_agent", "payment_or_power"): LOCKED` in `TOOL_RISK_POLICY`, blocking external comms (`Send`), destructive `Delete`, `Pay/Checkout`, `Install/Admin UAC`, and `Power` clicks/hotkeys behind HUD confirmation and Biometric Voice Lock without false-triggering on search queries or local file saves.
- **Instant Abort Kill-Switch:** Halts immediately (`status="aborted"`) on `ESC` key polling (`GetAsyncKeyState(0x1B)` inside `core/evolution/screen_agent.py`), `pyautogui.FAILSAFE` corner failsafe exceptions, `autonomous_screen_agent(action="stop")`, `autonomous_brain(action="cancel")`, or live voice utterances matching `"Jarvis stop"` in `main.py`.
- **Habit Compiler Telemetry & Test Isolation (`tests/conftest.py`, `tests/test_m13_screen_agent.py`):** Logs per-step tool telemetry (`tool_name="autonomous_screen_agent"`, step-specific `action`, app, title, latency, and verification result) into `memory/telemetry.sqlite` so `habit_compiler.py` can detect repeated GUI workflows, while `tests/conftest.py` isolates test runs from live `memory/voiceprints.json` and `memory/autonomous_missions.json`.

### Milestone 13 Verification Summary
- `scripts/validate_plugin.py`: **PASS** (0 errors, 0 warnings across `plugins/autonomous_screen_agent.py` and `plugins/autonomous_brain.py`)
- `pytest -B -v`: **44 passed in 21.28s, 0 failed** (including 8/8 new closed-loop tests in `tests/test_m13_screen_agent.py`)
- `python -B -m providers._tests`: **108 passed, 0 failed**
- `scripts/verify_wake_activation.py`: **8/8 passed** (live WASAPI RAW `false_hits=0`)
- `scripts/health_check.py --allow-dirty`: **ALL HEALTHY (10.10s)** (`active=32, rejected=0, core_checked=28, unignored_secrets=none`, `dirty=13` from unrelated concurrent local UI/goddess working-tree edits left untouched)

---

## Milestone 12 — 7/7 Android MAX Companion & PC <-> Android Bridge (`7f2d1be`)

### 1. Zero-Friction Android Pre-Flight (`scripts/setup_android_env.ps1`)
- Installed and detected **Eclipse Temurin OpenJDK 17** (`C:\Program Files\Eclipse Adoptium\jdk-17.0.21.10002-hotspot`), **Gradle 8.10.2** (`C:\Users\tanis\AppData\Local\Android\tools\gradle-8.10.2`), **Android Command-Line Tools** (`sdkmanager.bat`), **`platform-tools` (`adb.exe`)**, **`platforms;android-35`**, and **Build-Tools `34.0.0` / `35.0.0`**.

### 2. Bidirectional PC <-> Android Bridge, PWA Fallback & Plugin (`dashboard/server.py`, `plugins/android_bridge.py`, `main.py`)
- Upgraded `dashboard/server.py` `/ws` with typed bidirectional RPC: `device_register`, `heartbeat` telemetry updates, `pc_to_android` -> `android_result` correlated futures, `tool_invoke` -> `main.py:_execute_tool` (recording telemetry & honoring `safety_guard` gates), and `android_event` broadcasts.
- Added `/api/android/pair` (rate-limited PIN pairing returning session token, device token, & TLS SHA-256 fingerprint), `/api/android/devices`, `/api/android/command` (server-side risk gate enforcement), `/api/cert-fingerprint`, and installable PWA routes (`/pwa`, `/manifest.webmanifest`, `/sw.js`, `/pwa-icon.svg`).
- Created `plugins/android_bridge.py` (`SAFE` status/devices/pair/battery_gps/toggle/scroll/speak, `CONFIRM` siren/lock_screen/camera/whatsapp/call, `LOCKED` `sos` protected by Biometric Voice Lock + HUD confirmation).
- Fixed test-pollution bug in `providers/_tests.py` `[20]` so synthetic fallback tests run against an isolated temporary telemetry SQLite DB instead of polluting live `memory/telemetry.sqlite`.

### 3. Native Kotlin Android MAX Companion App (`android_companion/`, `minSdk 26`, `compileSdk 35`)
- **Core (`core/`):** `PrivacyConfig.kt` (100% On-Device Privacy Mode toggle), `CryptoEnvelope.kt` (PBKDF2 200k + AES-256-GCM matching `dashboard/server.py`), `ProtocolModels.kt`, and `JarvisWebSocketClient.kt` (OkHttp TLS SHA-256 certificate pinning + 25s heartbeat).
- **7/7 MAX Modules (`modules/`):**
  1. `antitheft/` (`AntiTheftGuardService`, `MotionTamperDetector`, `JarvisDeviceAdminReceiver`, `ChargerDisconnectReceiver`)
  2. `wakeword/` (`WakeWordVoiceService`, `WakePhraseMatcher` for English / Hindi Devanagari / Hinglish)
  3. `comms/` (`WhatsAppAndCallController`, `JarvisNotificationListener`)
  4. `toggles/` (`MobileToggleManager` — flashlight, Wi-Fi, Bluetooth, DND, brightness, ringer, alarms, battery, app launch)
  5. `vision/` (`ScreenVisionScrollService` `AccessibilityService` — continuous auto-scroll, read screen aloud, label auto-tap, auto-type)
  6. `sos/` (`EmergencySosManager` — `FusedLocationProviderClient` GPS SMS/WhatsApp + PC alert with safety confirmation)
  7. `camera/` (`VoiceCameraController` — CameraX front/rear capture, zoom, video recording)
- **Material 3 Compose UI (`ui/MainActivity.kt`):** Cybernetic HUD pairing screen, TLS SHA-256 pin field, 100% Privacy Mode toggle, 7 MAX module controls, and live telemetry feed.

### Milestone 12 Verification Summary
- `scripts/validate_plugin.py plugins/android_bridge.py`: **PASS** (0 errors, 0 warnings)
- `pytest -B -v`: **36 passed, 0 failed** (`47.82s`; Milestone 10 + 11 + 12 Python suites)
- `.\gradlew.bat testDebugUnitTest assembleDebug`: **BUILD SUCCESSFUL** (`GRADLE_EXIT=0`), **4/4 JUnit tests passed** (`MaxModulesUnitTest`), APK built at git-ignored `android_companion/app/build/outputs/apk/debug/app-debug.apk` (`18,700,363` bytes)
- `scripts/verify_wake_activation.py`: **8/8 passed** (live WASAPI RAW `false_hits=0`)
- `scripts/verify_cybersec.py`: **8/8 checks PASSED** (`ALL CYBERSEC CHECKS PASSED`)
- `scripts/verify_m3_reflector.py`: **14/14 checks PASSED**
- `scripts/verify_m4_skill_workflow.py`: **18/18 checks PASSED** (`active=31, rejected=0, core_checked=28`)
- `python -B -m providers._tests`: **108 passed, 0 failed**
- `scripts/health_check.py`: Re-verified clean (`dirty=0`, `active=31`, `rejected=0`, `unignored_secrets=none`).

---

## Milestone 11 — 41/41 Windows Desktop Parity (`ccb9420`)

### 1. Security & System
1. **Biometric Voice Lock (`plugins/voice_lock.py`, `core/evolution/safety_guard.py`):** Immutable 84-dimensional MFCC/spectral/F0 acoustic speaker embedding stored in git-ignored `memory/voiceprints.json` (with `-40 dB` spectral floor & formant liftering, zero interference with WASAPI RAW wake). Enforces `[VOICE_LOCKED]` pre-gate across `LOCKED` actions (`file_vault`, `smart_form_filler`, `voice_lock.clear`, evolution approvals) and power actions until `verify` unlocks the configurable TTL.
2. **Cron SQLite Task Scheduler (`core/routines/rules.py`, `core/routines/engine.py`, `actions/routines.py`):** Added 5-field `cron` rule scheduling via `croniter`, WAL SQLite persistence (`memory/routines.sqlite` with `routines`, `routine_meta`, and `run_history` tables synced with JSON), and automatic JSON-to-SQLite migration on load.
3. **Network IP & Speed Report (`actions/computer_settings.py`):** Added `network_info` (hostname, local interface IPv4s, public IP + ISP metadata) and `network_speed` / `speed_test` (latency ms, download Mbps, upload Mbps).
4. **System Cleaner (`plugins/system_cleaner.py`):** Read-only `status`/`scan` reclaimable-space estimator over `%TEMP%`, browser caches (Chrome/Edge/Brave/Firefox), and optional targets, plus `CONFIRM`-gated `clean` with in-use skip tolerance and optional Windows Recycle Bin cleanup.

### 2. Vision & Media
1. **Spotify Web API (`plugins/spotify_control.py`, `core/env_loader.py`):** Full `spotipy` control (`now_playing`, `search`, `play`, `pause`, `next`, `previous`, `volume`, `shuffle`, `devices`, `queue`) with clean `.env`/API-key credential validation via `core/env_loader.py`.
2. **Touchless Hand-Gesture Control (`core/hand_gestures.py`, `plugins/hand_gesture_control.py`):** 21-landmark classifier (`open_palm`, `fist`, `thumbs_up`, `thumbs_down`, `peace`, `point_up`, `pinch`, `swipe_left`, `swipe_right`), `GestureTracker` debouncer/cooldown, and background MediaPipe + OpenCV fallback controller.
3. **Desktop Automation (`plugins/desktop_automation.py`):** Continuous voice-controlled `SmartScrollController` (up/down/faster/slower/stop loop) and `NotepadDictationSession` streaming dictation into `notepad.exe`.

### 3. Productivity & Agents
1. **PowerPoint Deck Generator (`plugins/deck_generator.py`, `core/tasks/research.py`):** Widescreen `.pptx` generation via `python-pptx` (dark HUD & light themes, speaker notes) plus `export_to_pptx()` helper wired into `core/tasks/research.py`.
2. **Playwright + BS4 Web Scraper & Price Comparator (`plugins/web_scraper.py`):** Structured CSS/XPath/table DOM extraction wrapped in `<<<UNTRUSTED>>>` markers and multi-store ranked `shopping_compare` table builder.
3. **Single-Prompt AI Website Builder (`plugins/website_builder.py`):** Generates responsive HTML5 + CSS Grid + interactive JS + animated `<canvas>` sites (`saas`, `portfolio`, `landing`, `dashboard`) with optional browser preview.
4. **AI Image Generator (`plugins/image_generator.py`):** Hardware capability probing (ComfyUI, CUDA diffusers, cloud API keys) + deterministic Pillow cybernetic `local_poster` fallback (`generate`).
5. **Full Office Suite (`plugins/office_suite.py`, `actions/file_processor.py`):** Complete `openpyxl` Excel CRUD/filter/style (`excel_create`, `excel_read`, `excel_append`, `excel_update`, `excel_filter`, `excel_format`), `pdf_tables_to_csv` (`pdfplumber` wired into `file_processor.py`), and `batch_convert` across `.docx`/`.xlsx`/`.pptx`/`.pdf`/`.csv`/images.
6. **Email & WhatsApp Upgrades (`plugins/email_automator.py`, `actions/send_message.py`):** `CONFIRM`-gated SMTP send with validated attachments + `<<<UNTRUSTED>>>` IMAP `inbox_summary`; WhatsApp `media_path` file/image attachments and `call_type` (`voice`/`video`) routed through `safety_guard.enforce_gate()`.

### 4. Dev Workspace & Smart Analytics
1. **Dev Workspace & Project Config (`plugins/dev_workspace.py`):** Python AST (`pytest`) and JS/TS (`jest`) unit test generator & runner, syntax linter, `.vscode/settings.json` generator, and persistent per-project config in git-ignored `memory/project_profiles.json`.
2. **Encrypted Smart Form Filler (`plugins/smart_form_filler.py`):** Encrypts form profile data at rest using AES-256-GCM (`plugins/file_vault.py`) in `memory/form_profile.jvault`, inspects HTML `<form>` DOM semantics, and gates Playwright `fill_form` at the `LOCKED` tier.
3. **YouTube Whisper Audio Fallback (`actions/youtube_video.py`):** Automatically falls back to `yt_dlp` audio download + `faster_whisper` transcription when captions are unavailable for `summarize`.
4. **Market Finance (`plugins/market_finance.py`):** Stocks, indices, crypto, and forex quotes & watchlists with alias resolution (`nifty`, `sp500`, `btc`, `usdinr`). *Platform note:* Windows Smart App Control blocks unsigned `pandas` C-extension DLLs inside `yfinance` (`DLL load failed while importing nattype`); uses pure-HTTP Yahoo chart REST first with lazy `yfinance` fallback.
5. **Standalone Transparent 2D Avatar Overlay (`core/avatar_gestures.py`, `core/avatar.py`, `gui/avatar_overlay.py`, `gui/window.py`, `ui.py`, `plugins/avatar_control.py`):** Frameless, translucent, always-on-top draggable overlay wired to `HoloAvatar` and `VisemeStream` (`core/viseme.py`), featuring 8 sentiment-triggered gestures (`greeting`, `approve`, `think`, `alert`, `empathy`, `celebrate`, `focus`, `rest`).

### Milestone 11 Files (`ccb9420` on `milestone/11-windows-desktop-parity`)
- **Created:**
  - `core/env_loader.py`, `core/hand_gestures.py`, `core/avatar_gestures.py`, `gui/avatar_overlay.py`
  - `plugins/voice_lock.py`, `plugins/system_cleaner.py`, `plugins/spotify_control.py`, `plugins/hand_gesture_control.py`, `plugins/desktop_automation.py`, `plugins/deck_generator.py`, `plugins/web_scraper.py`, `plugins/website_builder.py`, `plugins/image_generator.py`, `plugins/office_suite.py`, `plugins/email_automator.py`, `plugins/dev_workspace.py`, `plugins/smart_form_filler.py`, `plugins/market_finance.py`, `plugins/avatar_control.py`
  - `tests/test_m11_security_system.py`, `tests/test_m11_vision_media.py`, `tests/test_m11_productivity_agents.py`, `tests/test_m11_dev_analytics_avatar.py`
- **Modified:**
  - `.gitignore`, `actions/computer_settings.py`, `actions/file_processor.py`, `actions/routines.py`, `actions/send_message.py`, `actions/youtube_video.py`, `core/avatar.py`, `core/evolution/safety_guard.py`, `core/routines/engine.py`, `core/routines/rules.py`, `core/tasks/research.py`, `gui/window.py`, `ui.py`

### Milestone 11 Verification Summary (Windows Python 3.13.17)
- `scripts/validate_plugin.py`: **15/15 PASS** (0 errors, 0 warnings across all new Milestone 11 plugins)
- `pytest -B -v`: **34 passed, 0 failed** (20.88s; 17 Milestone 10 + 17 Milestone 11 tests)
- `scripts/verify_wake_activation.py`: **8/8 passed** (live WASAPI RAW `false_hits=0`)
- `scripts/verify_cybersec.py`: **8/8 checks PASSED** (`ALL CYBERSEC CHECKS PASSED`)
- `scripts/verify_m3_reflector.py`: **14/14 checks PASSED** (`ALL PASSED`)
- `scripts/verify_m4_skill_workflow.py`: **18/18 checks PASSED** (`ALL PASSED`, `active=30, rejected=0, core_checked=28`)
- `python -m providers._tests`: **108 passed, 0 failed**
- `scripts/health_check.py`: Re-verified clean (`dirty=0`, `active=30`, `rejected=0`, `unignored_secrets=none`) after committing `EVOLUTION_PROGRESS.md`.

---

## Milestone 10 — Completed Modules & Upgrades (`2c79c7d` / `d1ce834`)

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

## Merge to `main` & Restart Live Mark-LVII (Run When Ready)
```powershell
# 1. Stash or commit any concurrent local UI edits before switching branches, then merge Milestone 13 into main:
cd C:\Users\tanis\Downloads\Mark-LVII-main\Mark-LVII-main
git checkout main
git merge --no-ff milestone/13-autonomous-screen-loop -m "merge: Milestone 13 Closed-Loop Autonomous Screen Agent"

# 2. Restart live Mark-LVII:
C:\Users\tanis\AppData\Local\Python\pythoncore-3.13-64\python.exe -B -u .\scripts\launch_jarvis.pyw
```
