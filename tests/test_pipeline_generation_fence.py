from __future__ import annotations

import asyncio
from types import SimpleNamespace

from core.voice_turn import TurnState
from providers.base import Msg
from providers.engine_pipeline import PipelineEngine


class _IdleLLM:
    caps = {"tools": False, "tool_format": None, "vision": False}

    async def chat(self, messages, tools):
        if False:  # Keep this an async generator; it intentionally returns no text.
            yield {"text": ""}


def _engine() -> PipelineEngine:
    return PipelineEngine(_IdleLLM())


def test_pipeline_rejects_audio_until_the_generation_owns_playback() -> None:
    async def scenario() -> None:
        engine = _engine()
        token = engine._begin_generation()

        await engine._emit(Msg.audio(b"\x00\x00" * 160, token))
        assert engine._queue.empty()

        assert engine._turns.begin_speaking(token)
        await engine._emit(Msg.audio(b"\x00\x00" * 160, token))
        assert not engine._queue.empty()

        await engine.close()

    asyncio.run(scenario())


def test_pipeline_receive_discards_audio_queued_before_interrupt() -> None:
    async def scenario() -> None:
        engine = _engine()
        token = engine._begin_generation()
        assert engine._turns.begin_speaking(token)
        await engine._emit(Msg.audio(b"\x00\x00" * 160, token))
        assert not engine._queue.empty()

        engine.interrupt()
        await engine.close()

        received = [msg async for msg in engine.receive()]
        assert received == []

    asyncio.run(scenario())


def test_late_tool_result_cannot_restart_an_interrupted_turn() -> None:
    async def scenario() -> None:
        engine = _engine()
        token = engine._begin_generation()
        engine._awaiting["tool-call-1"] = "open_app"
        engine._pending_sigs["tool-call-1"] = "open_app:{}"
        engine._pending_generation_ids["tool-call-1"] = token

        engine.interrupt()
        result = SimpleNamespace(
            id="tool-call-1",
            name="open_app",
            response={"result": "Opened"},
        )
        await engine.send_tool_response(function_responses=[result])

        assert engine._history == []
        assert engine._gen is None
        assert engine._turns.snapshot().state is TurnState.LISTENING
        await engine.close()

    asyncio.run(scenario())


def test_new_local_turn_can_start_after_interruption() -> None:
    async def scenario() -> None:
        engine = _engine()
        engine.interrupt()
        engine._queue_generation()
        token = engine._active_generation

        assert token is not None
        assert engine._turns.is_current(token)
        assert engine._turns.snapshot().state is TurnState.THINKING

        assert engine._gen is not None
        await engine._gen
        assert engine._turns.snapshot().state is TurnState.IDLE
        await engine.close()

    asyncio.run(scenario())


def test_interrupt_removes_only_unresolved_tool_calls_from_history() -> None:
    engine = _engine()
    engine._history.extend([
        {
            "role": "assistant",
            "content": "I opened the first item; cancelling the second.",
            "tool_calls": [
                {"id": "completed-call", "type": "function"},
                {"id": "pending-call", "type": "function"},
            ],
        },
        {
            "role": "tool",
            "tool_call_id": "completed-call",
            "name": "open_app",
            "content": "Opened",
        },
    ])
    engine._awaiting["pending-call"] = "send_message"
    engine._pending_generation_ids["pending-call"] = engine._begin_generation()

    engine.interrupt()

    assert engine._history[0]["content"] == "I opened the first item; cancelling the second."
    assert [c["id"] for c in engine._history[0]["tool_calls"]] == ["completed-call"]
    assert engine._history[1]["tool_call_id"] == "completed-call"
