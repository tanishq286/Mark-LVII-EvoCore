# Mark-LVII — IP, Copyright & Outbound Data-Privacy Audit

Generated: 2026-11-25  
Repository: `Mark-LVII-EvoCore` (`baseline-before-upgrade` -> `HEAD`)

---

## 1. License & Third-Party Asset Inspection

- **Root `LICENSE` file**: Present (`417` lines). Declares **`MARK 56 — JARVIS`**, `Copyright (c) 2026 FatihMakes`, under the **Creative Commons Attribution-NonCommercial 4.0 International License (`CC BY-NC 4.0`)**.
  - **Commercial restriction**: Commercial use is explicitly **not permitted** without separate permission/relicensing from `FatihMakes` (`Section 2(a)(1)`).
  - **Attribution requirement**: Redistribution or adaptation requires retaining attribution, copyright notice, license text/URI, warranty disclaimer, and an indication of modifications (`Section 3(a)`).
- **Bundled third-party components in repo**:
  - `core/face_model.obj`: **Apache License 2.0** (`Copyright 2019-2023 The MediaPipe Authors`).
  - `dashboard/static/crypto-js.min.js`: MIT-licensed CryptoJS bundle inherited from baseline.
  - `assets/goddess_portrait.jpg`: Added locally in commit `da424a1` (`v16-evocore-40emotions`); verify artwork provenance/license before commercial distribution.
  - Installed runtime packages (`PyQt6`, `faster-whisper`, `openwakeword`, `google-genai`, `fastapi`, `uvicorn`, `spotipy`, `cloudflared`, Android Jetpack/Gradle wrapper): governed by their respective upstream open-source/vendor licenses.

---

## 2. Provenance Breakdown (`baseline-before-upgrade` vs. Milestones 1–17)

Comparing `baseline-before-upgrade` (`75b97a2d`, 121 tracked files) against `6df2ee2` (M9, 147 files) and `HEAD` (272+ files):

| Category | Count | Licensing / Ownership Posture | Representative Paths |
|---|---:|---|---|
| **Unchanged Baseline Files** | 74 | Upstream **`CC BY-NC 4.0`** (`FatihMakes`) — non-commercial only | `LICENSE`, `AGENTS.md`, `readme.md`, `setup.py`, `requirements.txt`, `core/cloud.py`, `core/screenact.py`, `core/screenview.py`, `core/tts.py`, `core/stt.py`, `core/llm_client.py`, `core/tasks/{agents,brain,desktop,store,tools}.py`, `providers/{audio,base,engine_pipeline,registry,schema,stt_whisper,tts_*}.py`, `actions/{computer_control,dev_agent,open_app,reminder,...}.py` |
| **Modified Baseline Files** | 47 | Derivative of **`CC BY-NC 4.0`** upstream + EvoCore/OmniCore/Privacy additions | `main.py`, `ui.py`, `gui/{hud,overlays,theme,widgets,window}.py`, `dashboard/{server.py,static/app.html}`, `core/{auto_evolution,avatar,confirm,cross_os,gemini,prompt.txt,wake_word}.py`, `core/routines/*`, `core/tasks/{engine,research,web}.py`, `memory/{config_manager,memory_manager}.py`, `actions/{browser_control,computer_settings,file_controller,file_processor,proactive,routines,screen_processor,send_message,weather_report,web_search,youtube_video}.py`, `plugins/{autonomous_brain,claude_code,forge_skill_factory,linux_wsl_mastery,omega_council,self_evolve,universal_omnibridge}.py`, `.gitignore` |
| **Added in Milestones 1–9** | 26 | **Original EvoCore modules** built atop Mark-LVII plugin/provider interfaces | `plugins/{feedback_logger,knowledge_rag,mcp_bridge,skill_drafter,_skill_gate}.py`, `providers/local_router.py`, `core/tasks/snapshots.py`, `agents/reflector.md`, `config/mcp_servers.example.json`, `docs/SELF_EVOLUTION.md`, `scripts/{health_check,validate_plugin,eval_local_model,train_local_lora,wsl_train,verify_m1..m9_*}.py` |
| **Added in Milestones 10–17** | 125+ | **Original OmniCore / Android / Emotion / Privacy modules** | `core/privacy.py`, `core/emotions.py`, `core/goddess_{avatar,face,figure,mind,portrait,soul,warp}.py`, `core/antigravity_bridge.py`, `core/env_loader.py`, `core/mcp_client.py`, `core/clap_detector.py`, `core/wasapi_raw.py`, `core/evolution/*` (15 files), `core/memory/*` (4 files), `android_companion/*` (28 files), `dashboard/static/{pwa.html,manifest.webmanifest,pwa-icon.svg,sw.js}`, `plugins/{android_bridge,antigravity_agent,autonomous_screen_agent,avatar_control,cybersec_defender,deck_generator,desktop_automation,dev_workspace,email_automator,emotion_soul,evolution_control,file_vault,hand_gesture_control,image_generator,market_finance,office_suite,persona_control,smart_form_filler,spotify_control,swarm_orchestrator,system_cleaner,voice_lock,web_scraper,website_builder}.py`, `scripts/{launch_jarvis.pyw,fix_phone_remote.ps1,setup_android_env.ps1,phone_link.py,phone_link.cmd,setup_integrations.py,verify_cybersec.py,verify_wake_activation.py}`, `tests/*` |

---

## 3. Clean-Room Rewrite & Rebranding Checklist (Before Public / Commercial Release)

Before publishing or commercializing outside non-commercial `CC BY-NC 4.0` terms, the following items must be addressed:

1. **Upstream `CC BY-NC 4.0` Clean-Room Rewrite Scope**:
   - Either obtain a written commercial license from `FatihMakes`, **or** cleanly rewrite the 74 unchanged + 47 modified baseline files (`main.py` session loop, `ui.py` / `gui/window.py` / `gui/hud.py` / `gui/overlays.py`, `dashboard/server.py` core HTTP/WebSocket shell, `core/cloud.py`, `core/screenview.py`, `core/screenact.py`, `core/audio_devices.py`, `core/tts.py`, `core/stt.py`, `providers/engine_pipeline.py` & baseline provider adapters, `memory/{config,memory}_manager.py`, and baseline `actions/*`).
   - Standalone M10–M17 subsystems (`core/privacy.py`, `core/evolution/*`, `core/memory/*`, `core/emotions.py`, `core/goddess_*`, `core/mcp_client.py`, `android_companion/*`, and original M10–M17 `plugins/*`) have clean authorship and can be retained directly once decoupled from baseline interfaces.
2. **Upstream Branding & Attribution Strings to Purge/Replace**:
   - `LICENSE`: CC BY-NC 4.0 / `Copyright (c) 2026 FatihMakes`.
   - `gui/window.py:2313`: HUD footer string `"F4 MUTE   ·   F11 FULLSCREEN   ·   ESC INTERRUPT   ·   BY FATIHMAKES"`.
   - `readme.md`: Lines `1–4`, `805–806`, `1010–1025` (`By FatihMakes`, `@FatihMakes` YouTube/Instagram links, `github.com/FatihMakes/Mark-LVII.git`, `CC BY-NC 4.0` notice).
   - `setup.py`: Lines `2, 50, 70` (`MARK LIV` branding).
   - `core/screenview.py:439`: Upstream comment referencing `fatihmakes`.
   - `MARK 56` / `MARK LVI` / `MARK-LVII` / `J.A.R.V.I.S` product names across `core/prompt.txt`, `ui.py`, `gui/*`, `dashboard/static/pwa.html`, `android_companion/app/src/main/res/values/strings.xml` (`"J.A.R.V.I.S"` carries external trademark considerations for commercial products).
3. **Sample LAN IP Cleanup & Asset Verification**:
   - `android_companion/app/src/main/java/com/marklvii/jarvis/core/ProtocolModels.kt`, `MainActivity.kt`, and `MaxModulesUnitTest.kt` reference sample LAN address `172.16.6.143:8000`; replace with an RFC-5737 documentation address (`192.0.2.10:8000`) or empty placeholder before public distribution.
   - Replace or verify commercial rights for `assets/goddess_portrait.jpg`, `config/jarvis.ico`, and `config/jarvis.png`.

---

## 4. Outbound Network, Webhook & Telemetry Audit

Full source-tree URL and keyword scan (`*.py`, `*.pyw`, `*.js`, `*.html`, `*.kt`, `*.ps1`, `*.sh`, `*.json`, excluding runtime `memory/`, `reports/`, `knowledge/`, `tests/`, `.venv/`):

- **Third-Party Analytics / Crash Reporters / Developer Webhooks**: **NONE FOUND (`0` occurrences)**.
  - Verified zero references to `posthog`, `sentry.io`, `mixpanel`, `segment.io`, `google-analytics`, `gtag(`, `amplitude`, `datadog`, `bugsnag`, or hidden phone-home endpoints.
  - `core/evolution/telemetry.py` writes strictly to git-ignored local SQLite (`memory/telemetry.sqlite`).
- **Legitimate User-Configured / Tool-Triggered External Endpoints**:
  - **Model & Speech APIs (when not in Privacy Mode)**: `generativelanguage.googleapis.com` (Google Gemini), `api.openai.com`, `api.groq.com`, `api.deepseek.com`, `api.mistral.ai`, `api.x.ai`, `openrouter.ai`, `api.together.xyz`, `api.cerebras.ai`, `api.minimax.io`, `api.elevenlabs.io`, `huggingface.co` (on-demand Piper/Whisper model weights), `download.pytorch.org` (optional CUDA setup).
  - **Explicit Tool / Integration Endpoints**: DuckDuckGo / Google / Bing / Yandex (`actions/browser_control.py`, `web_search.py`), `api.open-meteo.com` & `geocoding-api.open-meteo.com` (`core/cloud.py`), `api.telegram.org` (opt-in Telegram bridge in `core/cloud.py`), `api.osv.dev` (`plugins/cybersec_defender.py`), `query1.finance.yahoo.com` (`plugins/market_finance.py`), `store.steampowered.com` (`actions/game_updater.py`), `api.ipify.org` / `ipinfo.io` / `speed.cloudflare.com` (`actions/computer_settings.py`, `core/cloud.py`), `www.amazon.in` / `www.flipkart.com` / `www.croma.com` (`plugins/web_scraper.py`), `www.youtube.com` (`actions/youtube_video.py`, `video_player.py`), `wa.me` / `maps.google.com` (Android companion user actions), plus user-configured Spotify (`spotipy`), SMTP/IMAP servers (`plugins/email_automator.py`), and `config/mcp_servers.json` targets.
  - **Opt-In Remote Phone Tunnel**: `trycloudflare.com` (`cloudflared`) and fallback `localhost.run` (OpenSSH) in `dashboard/server.py` — terminated and blocked whenever Strict Local Privacy Mode is active.
- **Localhost / Loopback Services**:
  - `127.0.0.1:11434` (local Ollama), `127.0.0.1:8188` (local ComfyUI), `127.0.0.1:8000` / `8766` (Mark-LVII dashboard + tunnel loopback), `127.0.0.1:8888` (local Spotify OAuth callback), `127.0.0.1:8765` (optional local MCP HTTP example).

---

## 5. Strict Local Privacy Mode (`privacy_mode`) Controls

Implemented in `core/privacy.py` and enforced across `main.py`, `core/gemini.py`, `core/llm_client.py`, `core/tasks/brain.py`, `core/action_loader.py`, `core/plugin_loader.py`, `core/evolution/agents_247.py`, `core/memory/memory_extractor.py`, `providers/local_router.py`, `providers/stt_whisper.py`, `dashboard/server.py`, `ui.py`, and `gui/window.py`:

1. **Process-Wide Socket & DNS Guard**: Blocks non-loopback `socket.connect`, `connect_ex`, `sendto`, `getaddrinfo`, `gethostbyname`, `gethostbyname_ex`, and Windows `IocpProactor.connect`/`sendto` (`PrivacyBlocked`), plus strips `HTTP_PROXY`/`HTTPS_PROXY`/`ALL_PROXY` and forces `NO_PROXY=*`.
2. **Strict Local Routing (Fail-Closed)**: Forces live voice/text sessions, one-shot `core/gemini.py`, `core/llm_client.py`, and `core/tasks/brain.py` onto loopback Ollama (`http://127.0.0.1:11434`, rejecting `:cloud`/`-cloud` models), local cached Whisper STT (`local_files_only=True`), and local system SAPI5 TTS — zero Gemini/OpenAI fallback.
3. **Tool & Memory Containment**: Restricts tool calls to `ALLOWED_TOOLS` (local-only tools; blocks `save_memory`, web search, browser, email, Spotify, MCP, cloud/CLI agents), suppresses persistent conversation logging (`_session_log`, `LiveMemoryBridge`, `knowledge_graph`, emotional appraisal, 24/7 background agents, web news/monitors), and halts public Quick Tunnels.
4. **Confirmed Exit**: Enabling works via HUD button, voice phrase, typed command, phone text command, or `privacy_mode` tool; disabling always clears session context and requires an explicit on-screen HUD confirmation (`core/confirm.py`).
