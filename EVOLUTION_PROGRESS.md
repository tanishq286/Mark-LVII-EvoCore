# Mark-LVII EvoCore & OmniCore Upgrade Progress

- **Active Milestone:** **Milestone 18 — Specialist Expert Router, Auto-RAG, Lifelike Presence, `romantic_charm` Persona & Ethical `persuasion_coach`**
- **Current Branch:** `main`
- **Tags:** `v16-evocore-40emotions` (`da424a1`), `v17-privacy-tunnel-ready` (`9f5a48c`), `v18-expert-alive-charm`
- **Test Runners Installed:**
  - `pytest 9.2.0` (`C:\Users\tanishq\AppData\Local\Python\pythoncore-3.13-64\python.exe` / Windows Python `3.13.17`)
  - OpenJDK 17 (`EclipseAdoptium.Temurin.17.JDK`), Gradle `8.10.2`, Android SDK `android-35` + `build-tools 34.0.0/35.0.0` + `adb`

---

## Final Modules + OmniCore Evolution Engines Completion Scorecard

| Track | Target | Verified Implementation | Status |
| :--- | :--- | :--- | :--- |
| **Milestone 18: Expert Intelligence, Lifelike Presence & Charm** | 3 / 3 | 5-mode Specialist Expert Router + Silent `Think -> Critic-Verify -> Respond` (`core/brain/expert_router.py`), Auto-Compounding `knowledge_rag` + `knowledge_graph.sqlite`, Emotion-matched sentence-by-sentence TTS + Soul/Avatar breathing/blink/gaze/listening reactivity, Configurable quiet-hours KG+Mind proactive check-ins, `romantic_charm` English/Hinglish persona + callbacks & ethical `persuasion_coach` (`plugins/persuasion_coach.py`) | **COMPLETE** |
| **Milestone 17: Privacy Mode, Cloudflared Tunnel & Setup Wizard** | 3 / 3 | 3-Layer Strict Local Privacy Mode (`core/privacy.py`, local Ollama/Whisper/SAPI5, blocked external sockets/tools/cloud LLMs), 1-command Phone Remote Cloudflare Quick Tunnel + 5-min PIN + ASCII QR (`scripts/phone_link.py`, `dashboard/server.py`, `gui/overlays.py`), IP & Privacy Audit (`IP_AND_PRIVACY_AUDIT.md`) + `.env`/MCP Setup Wizard (`scripts/setup_integrations.py`) | **COMPLETE (`9f5a48c`)** |
| **Milestone 16: 40-Emotion Affective Engine & Goddess HUD** | 1 / 1 | 40-emotion 6-family engine (`core/emotions.py`), facial warps (`core/goddess_warp.py`), painted portrait HUD (`core/goddess_portrait.py`), `emotion_soul` & Antigravity bridge (`core/antigravity_bridge.py`) | **COMPLETE (`da424a1`)** |
| **EvoCore Auto-Evolution Engines** | 5 / 5 | Telemetry SQLite WAL, Safety Guard + Rollback, Self-Healer + Replay Benchmark, Habit Compiler, Skill Synthesizer + Reflection Loop | **5 / 5 COMPLETE** |
| **Milestone 10: Security, Hybrid Router & Planner Core** | 6 / 6 | Adaptive Router (`local_router.py`), RAG Consolidation & Failure Immunity, Replay Self-Benchmarking, Verified Planner + Choice HUD (`confirm.py`), Uniform `SAFE`/`CONFIRM`/`LOCKED` gates, AES-256-GCM `file_vault` + Defender + 3-Persona | **6 / 6 COMPLETE** |
| **Milestone 11: Windows Desktop Parity** | 41 / 41 | Biometric Voice Lock, Cron SQLite Routines, Network Speed/IP, `system_cleaner`, Spotify, MediaPipe Gestures, Auto-Scroll & Notepad Dictation, `.pptx` Decks, Scraper + Price Comparator, Website & Image Generators, `office_suite` (Excel/PDF/Convert), Email SMTP/IMAP, WhatsApp Media/Calls, Dev Workspace + Profiles, AES Form Filler, YouTube Whisper Fallback, Market Finance, 2D Avatar Overlay (8 Gestures) | **41 / 41 COMPLETE** |
| **Milestone 12: Android MAX Companion & PC Bridge** | 7 / 7 | Setup Pre-Flight (`setup_android_env.ps1`), Bidirectional TLS `/ws` Bridge + Instant PWA (`/pwa`), `android_bridge` Plugin, Native Kotlin App (`AntiTheftGuardService`, `WakeWordVoiceService`, `WhatsAppAndCallController`, `MobileToggleManager`, `ScreenVisionScrollService`, `EmergencySosManager`, `VoiceCameraController`) + On-Device Privacy Mode | **7 / 7 COMPLETE** |
| **Milestone 13: Closed-Loop Autonomous Screen Agent** | 2 / 2 | Hybrid UIA + Vision/OCR Grounding (`core/evolution/screen_agent.py`), Post-Action Settle Polling (up to 3s), Stuck-State Recovery + `ChoiceBanner` Fallback, Multi-Step Wiring (`planner.py`, `autonomous_brain.py`, `plugins/autonomous_screen_agent.py`, `main.py`), `CONFIRM`/`LOCKED` Irreversible Gate, ESC / Corner / `"Jarvis stop"` Kill-Switches, Step Telemetry for `habit_compiler.py` | **COMPLETE** |
| **Milestone 14: Phone HTTPS Fix & Mark-LVII OmniCore** | 6 / 6 | LAN Wi-Fi + SAN HTTPS `:8000` + QR Pairing + 4G/5G Tunnel + Live Screen/Voice/1-Tap Approval (`dashboard/server.py`, `pwa.html`, `android_companion/`), Parallel Multi-Agent Swarm + Critic (`core/evolution/swarm.py`), Persistent SQLite Missions (`core/evolution/mission_runner.py`), Entity-Relation Knowledge Graph (`core/memory/knowledge_graph.py`), Idle Meta-Optimizer (`core/evolution/meta_optimizer.py`), Universal MCP Client Bridge (`core/mcp_client.py`, `plugins/mcp_bridge.py`) | **6 / 6 COMPLETE** |
| **Milestone 15: Live Fixes + 24/7 Agents** | 5 / 5 | Per-Monitor-V2 DPI + non-blank threaded capture + alias-aware verification + spoken step status + async goal queue (`core/dpi.py`, `screen_grounding.py`), Chrome default everywhere (`config_manager`, `browser_control`, `web_search`, `core/tasks/web.py` Playwright `channel="chrome"`), append-only cross-session memory (`conversation_store.py`, `memory_extractor.py`, `live_memory.py`), idle/budgeted Researcher (`research_daemon.py`), worktree-isolated Engineer (`engineer_daemon.py`, `agents_247.py`) | **5 / 5 COMPLETE** |

---

## Milestone 18 — Specialist Expert Router, Auto-Compounding Knowledge, Lifelike Presence & `romantic_charm`

- **Specialist Expert Router (`core/brain/expert_router.py`):** 5 domain modes (`software_architect`, `deep_researcher`, `finance_strategy`, `science_math`, `creative_writer`), automatic intent classification & privacy-filtered tool-set selection, and silent `Think -> Critic-Verify -> Respond` pass verifying Python AST syntax, safe AST arithmetic identities, and internal-thought stripping before speaking; wired into `main.py` & `core/evolution/planner.py`.
- **Auto-Compounding Knowledge (`plugins/knowledge_rag.py`, `core/tasks/engine.py`, `core/tasks/research.py`):** Added `auto_compound_artifact()` with SHA-256 deduplication writing `knowledge/compounded/*.md`, indexing chunks into `.rag_index.sqlite`, and upserting structured entities/relations into `knowledge_graph.sqlite` automatically on completed tasks and research reports.
- **Lifelike Presence & Expressiveness (`core/emotions.py`, `providers/tts_system.py`, `providers/engine_pipeline.py`, `core/goddess_soul.py`, `core/avatar.py`, `gui/avatar_overlay.py`, `gui/hud.py`, `ui.py`, `actions/proactive.py`, `memory/config_manager.py`):** Emotion-matched sentence-by-sentence TTS rate & breathing pause pacing; `dt`-integrated smooth idle breathing, natural blink/gaze shifts, and reactive user-voice listening expressions across `Soul`, `HoloAvatar`, `HudCanvas`, and `AvatarOverlayWindow`; configurable quiet hours + Knowledge Graph deadlines + `goddess_mind` drive check-ins in `ProactiveEngine`.
- **`romantic_charm` Persona & Ethical `persuasion_coach` (`core/persona.py`, `plugins/persona_control.py`, `plugins/persuasion_coach.py`):** Playful, witty, deeply affectionate English/Hinglish companion preset synced with `tender`/`playful`/`shy`/`loving` expression cues and `conversations.sqlite` callbacks, plus an ethical negotiation & pitch preparation strategist with anti-manipulation guardrails.
- **Immutability Preserved:** `core/evolution/safety_guard.py`, `plugins/file_vault.py`, `plugins/voice_lock.py`, and `core/confirm.py` untouched.
- **Verification:** `pytest -B -v` **98 passed, 4 skipped**; `providers._tests` **108 passed, 0 failed**; `scripts/validate_plugin.py` **36/36 PASS (0 errors, 0 warnings)**; `scripts/verify_wake_activation.py` **8/8 passed**.

---

## Milestone 17 — Privacy Mode, Cloudflare Quick Tunnel, IP Audit & Setup Wizard (`9f5a48c`, `v17-privacy-tunnel-ready`)

- **Strict Local Privacy Mode (`core/privacy.py`):** Blocks outbound sockets outside loopback, forces local Ollama/Whisper/SAPI5 routing, gates external tools behind explicit allowlists, and requires on-screen HUD confirmation to disable.
- **1-Command Phone Tunnel & Setup (`scripts/phone_link.py`, `dashboard/server.py`, `gui/overlays.py`, `scripts/setup_integrations.py`, `IP_AND_PRIVACY_AUDIT.md`):** Auto-starts Cloudflare Quick Tunnel + 5-min pairing PIN + terminal ASCII QR code, non-blocking tunnel UI activation, clean provenance/IP audit, and interactive `.env`/MCP wizard.

---

## Milestone 15 — Live Fixes, Cross-Session Memory & 24/7 Agents (`branch` merged)

### Part 1 — Live bugs, diagnosed from real runtime logs/telemetry (not mocks)
- **(1) Screen vision + autonomous completion.** Root causes found in `startup.log` / telemetry:
  - The process started DPI-unaware and the first `mss` grab flipped it mid-run, so UIA rects, pyautogui clicks and screenshots used different coordinate spaces on the 150 % 2560x1600 panel → `core/dpi.py` sets Per-Monitor-V2 once in `main.py` before Qt/mss/pyautogui load.
  - Blank/black frames were sent to Gemini Vision → `screen_processor.capture_screen_meta()` measures frame std, falls back to `PIL.ImageGrab`, and records monitor geometry; Vision now answers in 0-1000 normalized coordinates mapped to physical pixels (`screen_grounding.map_vision_point`).
  - `press win` "UNVERIFIED" because the expected `SearchHost.exe` never matched the UIA app `Search`, so recovery re-pressed Win and closed Start → alias-aware `app_matches` + toggle-key recovery waits instead of re-pressing.
  - Swarm sent `action='goal'` (rejected) and concurrent goals were refused → verb aliases + FIFO goal queue (max 5) on a worker thread; the voice loop never blocks.
  - Synchronous SQLite telemetry ran on the asyncio loop after every tool call → "1011 keepalive ping timeout" disconnects; bookkeeping moved to `asyncio.to_thread`.
  - Spoken status: throttled `StepNarrator` ("Looking at your screen.", "Opening Chrome.", ...).
- **(2) Chrome default everywhere.** `config_manager.find_chrome_exe()/get_default_browser()/save_default_browser()`; `browser_control` prefers Jarvis's browser over the OS default (Edge on this PC) and launches `chrome.exe <url>` directly; `web_search` can open results in Chrome; `core/tasks/web.py` uses the installed Chrome UA and a Playwright `channel="chrome"` render fallback (bundled Chromium if Chrome is missing, SSRF guard on every navigation). Missing Chrome → graceful fallback to the OS default.
- **(3) Cross-session memory.** `memory/conversations.sqlite` (WAL, `synchronous=FULL`) with append-only `conversation_history`, `session_summaries`, `memory_facts` (UPDATE/DELETE triggers abort). Every turn is recorded off-loop; rule facts are extracted per user turn; on sleep/next boot the session is summarised (Ollama first, Gemini fallback, extractive fallback) and facts flow into the knowledge graph and `knowledge_rag` (`jarvis_memory/remembered_facts.md`). A `[CROSS-SESSION MEMORY]` block is injected into every new Live session config (also survives the frequent inactivity reconnects) and a one-time primer is sent on wake.

### Part 2 — 24/7 agents
- **(4) Researcher** (`core/evolution/research_daemon.py`): runs only when idle ≥ 5 min, CPU ≤ 35 %, GPU ≤ 30 %; strict per-day budget in `memory/research_queue.sqlite` (40k cloud tokens, 15 cloud calls, 120 local calls, 12 web queries; overridable via `agents_247` plugin config); Ollama first, Gemini only within the cap. Analyses tool telemetry, routing failures, user corrections in conversation history, and web references (stored as untrusted). Writes ranked, de-duplicated specs.
- **(5) Engineer** (`core/evolution/engineer_daemon.py`, scheduler `agents_247.py`, voice controls in `plugins/evolution_control.py`): picks the top `plugin_fix` spec (max 3 runs/day), builds an `evolve/auto-<slug>-<stamp>` worktree from the committed HEAD (live checkout and `main` never touched), writes fix + `tests/evolved/test_auto_<slug>.py`, then gates: safety AST audit → `validate_plugin` (0 errors/0 warnings) → pytest → replay benchmark must beat baseline → diff may only touch the plugin and its test. Any failure discards the branch. Success is queued in `memory/evolution_digest.json` as `approve_evolve` with a HUD/phone notification ("Approve upgrade N"). `safety_guard`, `file_vault`, both `voice_lock`s, `core/confirm`, the gates/daemons themselves and all `memory/*`/`*.sqlite` are refused outright. `approve_upgrade` snapshots `conversations.sqlite` + `knowledge_graph.sqlite` to `memory/upgrade_snapshots/` before any merge.
- Voice: "pause/resume the 24/7 agents", "show what the researcher and engineer built today", "agents status", "run the agents now". Pause state persists in `memory/agents_state.json`.

### Milestone 15 Files
- New: `core/dpi.py`, `core/evolution/screen_grounding.py`, `core/memory/conversation_store.py`, `core/memory/memory_extractor.py`, `core/memory/live_memory.py`, `core/evolution/research_daemon.py`, `core/evolution/engineer_daemon.py`, `core/evolution/agents_247.py`, `tests/test_m15_memory_and_agents.py`, `tests/test_m15_live_screen_chrome.py`
- Changed: `main.py` (M15 hunks only), `memory/config_manager.py` (M15 hunk only), `.gitignore` (M15 lines only), `actions/screen_processor.py`, `actions/browser_control.py`, `actions/web_search.py`, `core/tasks/web.py`, `core/evolution/screen_agent.py`, `core/memory/knowledge_graph.py`, `plugins/autonomous_screen_agent.py`, `plugins/knowledge_rag.py`, `plugins/evolution_control.py`, `plugins/claude_code.py` (missing brief), `tests/conftest.py`, `EVOLUTION_PROGRESS.md`

### Milestone 15 Verification Summary (Windows Python 3.13)
- `pytest -v` (full): **81 passed, 0 failed, 4 skipped** (skips = opt-in live tests)
- Live real-desktop run (`JARVIS_LIVE_GUI=1 JARVIS_LIVE_NET=1`, `test_m15_live_screen_chrome.py`): **14 / 14 passed** — DPI-aware non-blank capture from a background thread, UIA active-window read, Notepad open → type → read-back verified, real Chrome window launch, Playwright `channel="chrome"` render
- Cross-session E2E (two separate processes): session 1 says "My favorite project is Project Nova and my deadline is Friday" and shuts down; session 2 boots with both facts + summary in its bootstrap — **passed**
- `scripts/validate_plugin.py`: **37 / 37 PASS, 0 errors, 0 warnings**
- `python -m providers._tests`: **108 passed, 0 failed**
- `scripts/verify_wake_activation.py`: **8 / 8 passed**
- `scripts/health_check.py --allow-dirty`: **ALL HEALTHY**

### Known follow-ups (outside the M15 file list)
- `actions/send_message.py`, `actions/weather_report.py`, `plugins/website_builder.py` still call `webbrowser.open` (OS default = Edge). To make Windows itself use Chrome: `start ms-settings:defaultapps`.
- Live Gemini recall test is opt-in: `JARVIS_LIVE_GEMINI=1`.

---

## Milestone 14 — Phone Remote HTTPS Supercharge & Mark-LVII OmniCore

### Part 1: Phone Remote Root-Cause Fix & Supercharge (`scripts/fix_phone_remote.ps1`, `dashboard/server.py`, `dashboard/static/pwa.html`, `gui/overlays.py`, `android_companion/`)
- **Root-Cause Network & TLS Fix:**
  - `detect_lan_ipv4()` filters virtual switches (`WSL`, `vEthernet`, `Hyper-V`, `Docker`, `VMware`, `Tailscale`, `Bluetooth`) by adapter name rather than IP prefix, resolving the active Windows Wi-Fi IPv4 (`172.16.6.143` on Private profile) instead of virtual adapters.
  - `DashboardServer` now binds **`0.0.0.0:8000` over HTTPS/TLS by default**, auto-generating/refreshing git-ignored `memory/certs/{jarvis.crt,jarvis.key}` with `SubjectAlternativeName` entries covering `DNS:localhost`, `IP:127.0.0.1`, the real Wi-Fi/Ethernet LAN IPv4 (`IP:172.16.6.143`), and any active Tailscale IP, plus an optional HTTP->HTTPS redirector on `:8001`.
  - Added `scripts/fix_phone_remote.ps1` to verify the active LAN adapter, Windows Defender Firewall TCP 8000 Private/Domain allow rules, SAN TLS certificates, Tailscale/Cloudflare tunnel status, and live HTTPS endpoint reachability.
- **1-Tap QR Pairing & 4G/5G Anywhere Control:**
  - Added `/api/android/qr` and `/api/android/tunnel` (`detect_tailscale()`, `start_quick_tunnel()` / `stop_quick_tunnel()` via `cloudflared`), plus upgraded `RemoteKeyOverlay` in `gui/overlays.py` with 1-tap pairing URLs (`https://172.16.6.143:8000/pwa?pin=<PIN>&fp=<SHA256>`), TLS SHA-256 display, and a `4G TUNNEL` toggle.
- **Supercharged `/pwa` & Native Kotlin Companion (`android_companion/`):**
  - **Live PC Screen Stream + Remote Tap/Type:** `/api/screen/frame`, `/api/screen/input`, and `/ws` `screen_frame_req` / `screen_input` with normalized coordinate mapping (`nx`/`ny` & `norm_x`/`norm_y`), double-tap, right-click, and remote keyboard input.
  - **Push-to-Talk Voice + Live Mission Stream:** WebAudio / `AudioRecord` 16 kHz PCM16 streaming over `wss://<host>:8000/ws/phone-audio` (auto-waking sleeping Jarvis) alongside `/api/missions/live` real-time Brain/Swarm/MissionRunner log streaming.
  - **1-Tap Remote HUD Approvals:** `/api/confirm/pending` and `/api/confirm/resolve` (+ `main.py` `broadcast_hud_prompt` hooks) surface Mark-LVII's `CONFIRM` and `ChoiceBanner` prompts directly on the phone for instant 1-tap approval or rejection while keeping `core/confirm.py` strictly immutable.

### Part 2: Mark-LVII OmniCore (Autonomous Multi-Agent & Meta-Evolution)
1. **Parallel Multi-Agent Swarm + Independent Critic (`core/evolution/swarm.py`, `plugins/swarm_orchestrator.py`, `plugins/autonomous_brain.py`):**
   - Spawns parallel dependency waves of specialized sub-agents (`researcher`, `coder`, `visual_screen_operator`, `security_auditor`) sharing a WAL SQLite blackboard (`memory/swarm_blackboard.sqlite`) seeded with multi-hop Knowledge Graph context.
   - Every sub-agent action and output is audited by `CriticAgent` (pre-execution `safety_guard` side-effect gate, AST evasion & secret-leak check, cross-agent security blocker check, and automatic remediation retry before synthesis).
2. **Persistent Long-Horizon Missions (`core/evolution/mission_runner.py`, `main.py`):**
   - SQLite-persisted missions (`memory/missions.sqlite`) with step-level checkpoints surviving process restarts (`_recover_interrupted_missions`), waking on `immediate`, `interval`, 5-field `cron`, `event` (`notify_event`), or desktop `screen_trigger` conditions, self-recovering via exponential backoff, and pushing live alerts/approval requests to the phone.
3. **Entity-Relation Knowledge Graph (`core/memory/knowledge_graph.py`, `plugins/knowledge_rag.py`, `memory/memory_manager.py`, `main.py`):**
   - Local WAL SQLite graph (`memory/knowledge_graph.sqlite`) modeling `person`, `project`, `file_path`, `preference`, `recurring_task`, `past_outcome`, `tool`, and `concept` entities with weighted directional relations. Auto-ingests conversation turns and `memory_manager` updates, injects 2-hop relational subgraphs into `VerifiedPlanner`, `SwarmCoordinator`, and `knowledge_rag`, and merges alias duplicates during idle consolidation.
4. **Meta-Evolution & Prompt/Skill Self-Optimizer (`core/evolution/meta_optimizer.py`, `skill_synthesizer.py`, `reflection_loop.py`, `providers/local_router.py`, `plugins/evolution_control.py`, `core/auto_evolution.py`):**
   - Analyzes `memory/telemetry.sqlite` during PC idle time (`GetLastInputInfo`) for underperforming mutable plugins, degraded model routes, and recurring prompt/argument friction.
   - Stages candidate plugin upgrades on isolated `evolve/*` Git worktrees, runs AST safety + `validate_plugin.py` + `pytest` + historical replay & 10D quality benchmarks, **auto-reverts** any non-improving candidate, and queues ONLY measurably better upgrades into `memory/evolution_digest.json` (`"Approve upgrade 1"`), while live-applying approved routing/prompt overrides in `local_router.py` and `reflection_loop.py`. Strictly blocks `safety_guard.py`, `file_vault.py`, `voice_lock.py`, and `core/confirm.py`.
5. **Universal MCP Client Bridge (`core/mcp_client.py`, `plugins/mcp_bridge.py`, `config/mcp_servers.example.json`):**
   - Dynamic `tools/list` discovery and SQLite tool mounting (`mcp__<server>__<tool>` in `memory/mcp_registry.sqlite`), built-in presets for GitHub, Google Workspace, Notion, PostgreSQL Databases, Smart Home (Home Assistant), Claude-Mem, and Chrome DevTools, ecosystem summary (`mode='ecosystem'`), and automatic 3-tier `safety_guard` risk wrapping (`SAFE`, `CONFIRM`, `LOCKED` with Biometric Voice Lock + SQL statement inspection + `<<<UNTRUSTED>>>` isolation).

### Milestone 14 Verification Summary
- **Live Wi-Fi LAN HTTPS Reachability (`172.16.6.143:8000`):**
  - `GET https://172.16.6.143:8000/pwa` -> **200 OK** (`30,754` bytes)
  - `GET https://172.16.6.143:8000/api/android/qr` -> **200 OK**
  - `GET https://172.16.6.143:8000/api/cert-fingerprint` -> **200 OK** (`https=True`, SAN=`DNS:localhost, IP:127.0.0.1, IP:172.16.6.143`, SHA-256=`1e56127cdf1910e72fdc15a602eafeea032ee7fc841b8565d927322de0581bba`)
  - `WSS wss://172.16.6.143:8000/ws`: invalid token rejected (`HTTP 403`), valid paired token accepted (`confirm_pending`)
- `scripts/validate_plugin.py`: **5/5 PASS** (**0 errors, 0 warnings** across `swarm_orchestrator`, `autonomous_brain`, `knowledge_rag`, `mcp_bridge`, `evolution_control`; 10D scores `87.5`–`99.0/100`)
- `pytest -B -v`: **50 passed in 25.79s, 0 failed** (including 6/6 end-to-end suites in `tests/test_m14_omnicore.py`)
- `.\gradlew.bat testDebugUnitTest assembleDebug`: **BUILD SUCCESSFUL in 20s** (5/5 Kotlin JUnit tests passed + updated `app-debug.apk` built)
- `python -B -m providers._tests`: **108 passed, 0 failed**
- `scripts/verify_wake_activation.py`: **8/8 passed** (live WASAPI RAW `false_hits=0`, `plugins_rejected=0, overlap=none`)
- `scripts/verify_m7_knowledge_rag.py`: **ALL PASSED** (`active=33, rejected=0`)
- `scripts/health_check.py --allow-dirty`: **ALL HEALTHY** (`active=33, rejected=0, core_checked=28, unignored_secrets=none`; immutable files `safety_guard.py`, `file_vault.py`, `voice_lock.py`, `core/confirm.py` verified untouched)

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

## Merge to `main` & Restart Live Mark-LVII (Run When Ready — after your review)
```powershell
# 1. Stash your concurrent local UI/goddess edits, merge Milestone 15 into main, then restore UI edits:
cd C:\Users\tanis\Downloads\Mark-LVII-main\Mark-LVII-main
git stash push -u -m "local-ui-edits-before-m15-merge"
git checkout main
git merge --no-ff milestone/15-live-fixes-memory-and-247-agents -m "merge: Milestone 15 live fixes, cross-session memory & 24/7 agents"
git stash pop

# 2. Restart Mark-LVII (close the running instance first):
C:\Users\tanis\AppData\Local\Python\pythoncore-3.13-64\python.exe -B -u .\scripts\launch_jarvis.pyw
```
