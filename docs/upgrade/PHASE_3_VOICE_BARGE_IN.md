# Phase 3 — Voice barge-in

## Goal

Let the user interrupt J.A.R.V.I.S by speaking while it is playing audio, without
replacing Gemini Live, the local STT → LLM → TTS pipeline, the avatar, or the
existing audio devices and queues.

## Safety and rollout

Voice barge-in is **off by default**. Enable it for a hardware test by setting
`JARVIS_VOICE_BARGE_IN=1` in the process environment before launching J.A.R.V.I.S.
For example, in PowerShell:

```powershell
$env:JARVIS_VOICE_BARGE_IN = "1"
python main.py
```

The feature observes the microphone only while playback is active, respects mute,
phone-microphone ownership and push-to-talk state, and dispatches the interrupt
onto the asyncio event loop rather than manipulating asyncio queues from the
PortAudio callback thread.

## Detection

- The existing EchoGuard remains the source of truth for separating microphone
  speech from the speaker echo.
- The detector does not call EchoGuard during active playback until the room
  profile is calibrated by the existing playback-tail path. This avoids training
  the room profile on the user's interrupting voice.
- A candidate must remain above the learned residual threshold for the guard's
  room-dependent number of consecutive blocks.
- Only the contiguous blocks classified as user speech are replayed into the
  input queue. Earlier speaker echo is not replayed as user audio.
- The detector is one-shot per playback stretch and re-arms when playback stops.

## Engine behavior

**Gemini Live:** the client forwards the confirmed microphone blocks. Gemini
Live's default server-side VAD can cancel generation when it detects the user's
speech. The receive loop also handles the server's `server_content.interrupted`
signal and drains queued playback immediately.

**Local pipeline:** the existing generation token is invalidated through
`PipelineEngine.interrupt()`. The confirmed microphone blocks are then passed
into the existing STT input path; the normal local VAD decides the end of the
utterance.

The interrupted assistant transcript is discarded. A user transcript received
for the interrupting utterance is retained and logged at turn completion.

## Validation completed in code

- Deterministic unit tests cover calibration gating, consecutive positive blocks,
  candidate reset on echo, one-shot triggering, and bounded buffering.
- No microphone, speaker, AEC, or real-room validation is claimed by unit tests.

## Hardware acceptance checklist

1. Start with the environment flag unset and verify existing behavior is unchanged.
2. Enable the flag with speakers at a normal listening volume. Let several replies
   complete so the echo profile can calibrate.
3. Interrupt with a short phrase while J.A.R.V.I.S is speaking. Verify playback
   stops promptly and the full phrase is transcribed.
4. Repeat with headphones, different speaker volume, and a noisier room.
5. Verify keyboard / mouse noise, coughs, and J.A.R.V.I.S's own voice do not cause
   false interruptions.
6. Verify mute, phone microphone, and push-to-talk continue to take precedence.
7. Repeat with both Gemini Live and the local pipeline.

Do not make the environment flag default-on until those hardware checks pass.
