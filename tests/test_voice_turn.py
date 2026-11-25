from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

import pytest

from core.voice_turn import TurnState, VoiceTurnCoordinator


def test_happy_path_capture_inference_speaking_and_finish() -> None:
    turns = VoiceTurnCoordinator()
    token = turns.begin_capture()
    assert turns.snapshot().state is TurnState.LISTENING
    assert turns.finish_capture(token)
    assert turns.snapshot().state is TurnState.THINKING
    assert turns.begin_speaking(token)
    assert turns.accepts_audio(token)
    assert turns.finish_response(token)
    assert turns.snapshot().state is TurnState.IDLE
    assert not turns.accepts_audio(token)


def test_interrupt_invalidates_already_issued_playback_generation() -> None:
    turns = VoiceTurnCoordinator()
    old = turns.begin_capture()
    assert turns.finish_capture(old)
    assert turns.begin_speaking(old)
    assert turns.accepts_audio(old)

    new = turns.interrupt()
    assert new > old
    assert turns.snapshot().state is TurnState.LISTENING
    assert not turns.accepts_audio(old)
    assert not turns.finish_response(old)
    assert turns.snapshot().generation == new


def test_stale_capture_and_response_callbacks_cannot_change_new_turn() -> None:
    turns = VoiceTurnCoordinator()
    stale = turns.begin_capture()
    current = turns.begin_capture()

    assert not turns.finish_capture(stale)
    assert turns.finish_capture(current)
    assert turns.begin_speaking(current)
    assert not turns.finish_response(stale)
    assert turns.snapshot().state is TurnState.SPEAKING


def test_only_thinking_response_can_start_playback() -> None:
    turns = VoiceTurnCoordinator()
    token = turns.begin_capture()
    assert not turns.begin_speaking(token)
    assert turns.finish_capture(token)
    assert turns.begin_speaking(token)
    assert turns.begin_speaking(token)  # idempotent for later speech chunks


def test_cancel_invalidates_token_and_is_idempotent_for_stale_token() -> None:
    turns = VoiceTurnCoordinator()
    token = turns.begin_capture()
    assert turns.finish_capture(token)
    assert turns.cancel(token)
    assert not turns.cancel(token)
    assert not turns.accepts_audio(token)
    assert turns.snapshot().state is TurnState.IDLE


def test_close_invalidates_every_token_and_prevents_reopening() -> None:
    turns = VoiceTurnCoordinator()
    token = turns.begin_capture()
    turns.close()
    snapshot = turns.snapshot()

    assert snapshot.state is TurnState.CLOSED
    assert snapshot.generation > token
    assert not turns.finish_capture(token)
    assert not turns.accepts_audio(token)
    turns.close()  # idempotent
    with pytest.raises(RuntimeError, match="closed"):
        turns.begin_capture()


def test_coordinator_is_thread_safe_for_generation_invalidation() -> None:
    turns = VoiceTurnCoordinator()
    with ThreadPoolExecutor(max_workers=8) as pool:
        tokens = list(pool.map(lambda _: turns.begin_capture(), range(64)))
    assert len(set(tokens)) == 64
    assert turns.snapshot().generation == max(tokens)
    assert turns.finish_capture(max(tokens))
