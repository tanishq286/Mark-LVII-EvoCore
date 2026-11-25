# Phase 2 — Voice Intelligence: Technical Design

**Status:** First isolated primitive implemented and unit-tested; microphone/playback integration remains gated on local working-tree preservation, CI validation, and real-device testing.

## Goal

Make J.A.R.V.I.S feel like a responsive, natural, interruptible voice companion while preserving the existing Mark-LVII architecture, identity, memory, avatar, plugins, safety gates, and provider choices.

The first priority is not adding another model. It is making the microphone, turn-taking, interruption, playback, and voice-prosody paths behave as one reliable real-time system.

## Preserve these existing foundations

- Gemini Live remains the primary realtime conversation engine.
- `providers/base.py` remains the provider contract; provider engines continue to adapt to the Live-shaped events consumed by `main.py`.
- `providers/engine_pipeline.py` remains the offline/cloud-fallback STT → LLM → TTS engine.
- Keep Whisper STT, Piper local neural TTS, ElevenLabs hosted TTS, and operating-system TTS as selectable providers.
- Keep `core/echo.py`, `core/emotions.py`, `core/persona.py`, `core/viseme.py`, avatar/soul components, and the existing playback queue. Improve their contracts rather than replacing them.
- Do not rewrite the large `main.py` receive/audio loop merely to make providers symmetrical.

## Verified current gaps

| Area | Existing behaviour | Consequence |
|---|---|---|
| Live microphone while speaking | `main.py::_listen_audio` returns immediately from the callback while `_is_speaking` is true | The user's voice is not streamed to Gemini during assistant playback, so server-side interruption cannot activate reliably |
| Live echo tail | `EchoGuard` classifies the short post-playback tail | It helps avoid the assistant hearing its own last words, but does not currently protect the active playback interval |
| Local pipeline microphone | `PipelineEngine.send_realtime_input` buffers PCM; `flush_microphone` invokes STT at turn end | Local recognition is utterance-based, not a continuously streaming ASR experience |
| Local turn detection | Energy/silence VAD with a 700 ms silence endpoint; `barge_in` capability is explicitly false | There is no honest local voice-interruption capability yet |
| Local interrupt | `PipelineEngine.interrupt()` cancels its active generation; the UI interrupt path drains queued audio and clears visemes | A cancellation path exists, but live detection and race-free interruption still need to be wired and tested |
| Emotion-to-voice | `_speak` forwards `rate` and `pause_ms` when a provider accepts them | Pitch and richer expressive cues are not represented as a provider-neutral capability contract |
| ElevenLabs controls | Existing payload sets stability and similarity | Style/speed controls are not currently passed; support depends on the selected model |
| Voice telemetry | Pipeline records per-stage timings and first-sound timing for console diagnostics | Need structured, privacy-safe voice quality metrics for regression analysis |

## Target architecture

### 1. One continuous capture path

The sound-device callback must remain extremely small: copy each fixed-size PCM frame into a bounded, thread-safe queue/ring buffer and return. No network calls, model calls, disk writes, blocking locks, or expensive feature extraction belong in the audio callback.

An audio worker owns frame processing and routing. It keeps a short in-memory pre-roll so the first consonant is not lost when speech is detected. The microphone remains available during playback, but audio is not forwarded blindly: it first passes the echo/noise/VAD path.

### 2. Acoustic echo and speech detection

Keep the existing `EchoGuard` as a conservative content-aware classifier. Prototype a separate optional acoustic echo-cancellation adapter using the actual speaker-render reference (far-end audio) and microphone capture (near-end audio). AEC must not be implemented by merely muting the microphone while J.A.R.V.I.S speaks.

Candidate for a compatibility spike: `pywebrtc-audio`, a Python binding around WebRTC Audio Processing with AEC3, noise suppression, AGC, and VAD. It is not approved as a mandatory runtime dependency yet. First prove Windows/Python 3.13 wheel/install support, correct far-end reference alignment, real device behaviour, CPU cost, and graceful absence.

Fail safely: if echo processing is unavailable or uncalibrated, preserve the existing conservative no-barge-in behaviour rather than letting the assistant interrupt itself or trigger on keyboard/noise events.

### 3. Provider-aware interruption

**Gemini Live:** Continue streaming microphone audio while J.A.R.V.I.S speaks, after local echo processing. Use 20–40 ms chunks as recommended by Google's Live API guidance. Let the Live session's VAD identify user speech and interruption; when an interruption event arrives, immediately stop/flush the local playback queue, invalidate stale queued chunks, reset the viseme timeline, and keep the already-heard portion of the assistant turn in conversation context.

**Local PipelineEngine:** Do not claim `barge_in=True` until near-end speech can be distinguished from speaker echo. Once the local VAD/AEC path confirms a user interruption, invoke the engine cancellation path exactly once, clear playback output, retain the pre-roll audio, and continue capturing the user's speech. Endpoint the user's turn and then pass the captured utterance to STT. Ensure late TTS chunks from the cancelled generation cannot re-enter the playback queue.

Both paths need an explicit turn/playback state machine rather than scattered booleans. State transitions must be idempotent and safe across the audio callback, asyncio loop, and playback worker.

### 4. Natural endpointing and local STT

Keep push-to-talk as the deterministic fallback. For hands-free local mode, improve endpointing with speech probability, adaptive noise floor, pre-roll, minimum speech duration, and a context-sensitive silence window. Avoid clipping pauses inside a sentence. A streaming/partial Whisper adapter can be evaluated separately; faster-whisper's existing utterance call should remain available as the accuracy fallback.

Language selection must continue to use Whisper's detected language and confidence. Short ambiguous utterances must not randomly switch the TTS voice. English, Hindi, and natural Hinglish code-switching need explicit regression samples.

### 5. Expressive voice contract

Add a provider-neutral voice-style data object in a later implementation step. Candidate fields:

- `rate` / `speed` — only when the selected provider supports it
- `pause_ms` — playback-side pause, independent of provider generation
- `pitch_hint` — advisory only; do not pretend a provider can change pitch if it cannot
- `expressivity` / `style` — mapped only to supported provider/model controls
- `delivery_tags` — only for models that document support for such tags

The emotion engine remains the source of affective intent; the provider adapter translates that intent into controls it actually supports. Never send unknown parameters or silently change voice identity between turns.

The existing ElevenLabs model list is model-specific. Current documentation describes style/speed support on some model families and different constraints on newer families. Implement capability checks by selected model and retain safe defaults. The current payload should not be modified by assuming every model accepts every setting.

### 6. Playback integrity and avatar sync

Every generated audio turn should have a generation identifier. Interruption invalidates the current generation so late chunks cannot play after the user has taken the floor. On interruption, drain playback, clear stale audio, reset visemes, and restart the play cursor. Text transcript, audio, and avatar mouth timing must always refer to the same generation.

Continue sentence/chunk streaming so the first sound can play before the entire reply is generated. Tune chunk size only with measured first-audio latency and natural phrase boundaries; do not split words or expose partial reasoning.

### 7. Privacy-safe observability

Record timing and event metadata only: capture queue delay, VAD decision time, interruption detection-to-silence time, STT duration, model first-token time, TTS first-audio time, underruns, dropped stale chunks, and provider fallback reason.

Do not persist raw microphone audio, speaker reference audio, transcripts beyond the existing conversation-memory policy, API secrets, or voiceprints as telemetry. Redact exception traces before broad telemetry export.

## Implementation sequence

1. **Preflight and baseline:** verify the user's local Git status, exact HEAD, branches/tags, untracked/ignored runtime data, and backup of memory databases, config/secrets, voiceprints, downloaded voices, and model files. Run the corrected CI and local health check. No app-code merge until this is done.
2. **Deterministic turn-state module:** implement and unit-test the provider-neutral state machine and generation IDs without enabling hardware interruption.
3. **Audio worker and bounded frame queue:** separate callback work from processing; preserve the current wake-gate privacy boundary.
4. **AEC/VAD compatibility spike:** evaluate optional WebRTC APM; keep it disabled by default until test and hardware evidence supports it.
5. **Gemini Live barge-in:** route cleaned mic frames during playback, handle interruption events, and guarantee immediate playback flush.
6. **Local pipeline barge-in:** connect local VAD/AEC to `interrupt()`, pre-roll, endpointing, and STT; advertise capability only after passing tests.
7. **Prosody adapter:** map emotion/persona cues to provider-supported speed/style/pause controls while keeping one stable voice identity.
8. **Voice observability and tuning:** persist non-sensitive metrics and tune against repeatable tests plus a real microphone/speaker smoke test.

## Acceptance criteria

- No audio callback performs blocking I/O, inference, or disk writes.
- No self-interruption in deterministic echo-only fixtures; real-speaker hardware tests show no recurring false barge-ins in the tested room/device configurations.
- User interruption reliably stops playback and prevents stale audio from restarting.
- A user's initial phonemes survive VAD turn start; normal within-sentence pauses do not prematurely close a turn.
- The same provider-independent event contract works for Gemini Live and the local pipeline.
- Local barge-in capability is advertised only when the local path genuinely supports it.
- English, Hindi, and Hinglish tests verify language stability and intelligible voice selection.
- Voice style controls are capability-gated; unsupported pitch/style controls are not falsely advertised.
- Existing test suite, provider contracts, plugin validation, and local health checks pass.
- A real-device smoke test is completed before active barge-in is enabled by default.

## Explicit non-goals

- No rewrite of J.A.R.V.I.S from scratch.
- No forced replacement of Gemini Live or the provider architecture.
- No mandatory paid TTS service or new cloud account.
- No automatic upload of microphone audio while the wake gate is closed.
- No always-on microphone recording or telemetry containing raw audio.
- No copying of proprietary code, prompts, voices, or assets from other assistants.
- No claim of AGI or human consciousness; this phase improves measurable voice interaction quality.

## References

- Google Gemini Live API — bidirectional streaming, audio formats, VAD, and interruption handling: https://docs.cloud.google.com/gemini-enterprise-agent-platform/reference/models/live-api
- Google Gemini Live API — 20–40 ms audio chunk guidance and interruption troubleshooting: https://docs.cloud.google.com/gemini-enterprise-agent-platform/models/live-api/troubleshooting
- WebRTC Audio Processing Python binding candidate: https://github.com/strands-labs/pywebrtc-audio
- ElevenLabs voice settings and model-dependent prosody controls: https://elevenlabs.io/docs/api-reference/voices/settings/get


## Implementation progress

### Stage 1 — generation-safe turn coordinator

Added on `upgrade/phase-2-voice-intelligence`:

- `core/voice_turn.py`: a thread-safe coordinator with explicit IDLE, LISTENING, THINKING, SPEAKING, and CLOSED states.
- `tests/test_voice_turn.py`: seven unit tests covering the happy path, interruption invalidation, stale completion rejection, playback gating, cancellation, close semantics, and concurrent generation allocation.

The coordinator issues monotonically increasing generation IDs. An integration must carry the active ID through the response and check `accepts_audio(generation)` immediately before placing PCM in the playback queue. An interruption allocates a newer ID, making all old-generation audio and callbacks stale.

**Validation performed:** the isolated test file ran locally with 7 passed. This is not evidence that the full repository CI passes.

**Not yet integrated:** no microphone callback, Gemini Live stream, PipelineEngine, playback queue, TTS provider, or avatar code has been changed. The coordinator does not yet make the application interruptible; it provides a tested foundation for wiring that behaviour safely.

**Next:** wire generation IDs to the shared playback publication boundary and interruption path on a separate commit, after the user's local repository state and private runtime backup are verified. Then add mocked race-condition tests and perform a microphone/speaker smoke test before enabling barge-in by default.


### Stage 2 — local pipeline generation fencing

Added:

- `providers/base.py`: optional generation metadata on provider events. The field defaults to `None`, so existing providers and tests retain their prior event contract.
- `providers/engine_pipeline.py`: local pipeline owns a `VoiceTurnCoordinator`; text, audio, and tool-call events carry a generation token; both queue publication and event consumption reject stale generations; late tool results from an interrupted generation are ignored.
- `tests/test_pipeline_generation_fence.py`: focused tests for playback-state gating, stale queued-audio rejection, stale tool-result rejection, and starting a new local turn after interruption.

This hardens the local pipeline's existing cancellation path. It does **not** yet enable voice-detected interruption, and it does not alter the primary Gemini Live path. The local pipeline continues to advertise `barge_in: false` until live near-end speech detection and acoustic echo cancellation are validated.

**Validation status:** Source files and test cases are committed on the feature branch. The focused test suite and full Windows CI run still need a confirmed green result.
