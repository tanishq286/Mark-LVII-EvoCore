"""Thread-safe turn state and generation fencing for voice playback.

This module deliberately does not own microphones, audio devices, model calls,
queues, or provider sessions. It is the small coordination primitive those
parts can share so an interrupted turn cannot publish fresh audio after a
newer user turn has taken control.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from threading import RLock


class TurnState(str, Enum):
    IDLE = "idle"
    LISTENING = "listening"
    THINKING = "thinking"
    SPEAKING = "speaking"
    CLOSED = "closed"


@dataclass(frozen=True, slots=True)
class TurnSnapshot:
    """Immutable view of the coordinator's current state."""

    generation: int
    state: TurnState


class VoiceTurnCoordinator:
    """Coordinate a single voice turn across capture, inference, and playback.

    Every capture receives a monotonically increasing generation token. The
    same token travels through STT, the model, and TTS. Starting a newer capture
    (including a barge-in) invalidates every callback and audio chunk carrying
    the old token. Consumers must check `accepts_audio(token)` immediately
    before publishing PCM to the playback queue.

    All methods are safe to call from different threads. No I/O or DSP is done
    while the internal lock is held.
    """

    def __init__(self) -> None:
        self._lock = RLock()
        self._generation = 0
        self._state = TurnState.IDLE

    def snapshot(self) -> TurnSnapshot:
        with self._lock:
            return TurnSnapshot(self._generation, self._state)

    def begin_capture(self) -> int:
        """Start a user capture and invalidate any older in-flight response."""
        with self._lock:
            self._ensure_open()
            self._generation += 1
            self._state = TurnState.LISTENING
            return self._generation

    def interrupt(self) -> int:
        """Take the floor from the assistant and invalidate its queued/future PCM."""
        return self.begin_capture()

    def finish_capture(self, generation: int) -> bool:
        """Move a current capture to inference; return False for stale calls."""
        with self._lock:
            if not self._matches(generation) or self._state is not TurnState.LISTENING:
                return False
            self._state = TurnState.THINKING
            return True

    def begin_speaking(self, generation: int) -> bool:
        """Allow playback for the current response generation."""
        with self._lock:
            if not self._matches(generation):
                return False
            if self._state is TurnState.SPEAKING:
                return True  # subsequent sentences/chunks share this response
            if self._state is not TurnState.THINKING:
                return False
            self._state = TurnState.SPEAKING
            return True

    def accepts_audio(self, generation: int) -> bool:
        """Check immediately before queueing a PCM chunk."""
        with self._lock:
            return self._matches(generation) and self._state is TurnState.SPEAKING

    def finish_response(self, generation: int) -> bool:
        """Finish a response without allowing a stale completion to alter state."""
        with self._lock:
            if not self._matches(generation):
                return False
            if self._state not in (TurnState.THINKING, TurnState.SPEAKING):
                return False
            self._state = TurnState.IDLE
            return True

    def cancel(self, generation: int) -> bool:
        """Cancel the current generation; a newer generation is never affected."""
        with self._lock:
            if not self._matches(generation):
                return False
            if self._state is TurnState.CLOSED:
                return False
            self._generation += 1
            self._state = TurnState.IDLE
            return True

    def close(self) -> None:
        """Permanently close the coordinator and invalidate outstanding work."""
        with self._lock:
            if self._state is TurnState.CLOSED:
                return
            self._generation += 1
            self._state = TurnState.CLOSED

    def _matches(self, generation: int) -> bool:
        return self._state is not TurnState.CLOSED and generation == self._generation

    def _ensure_open(self) -> None:
        if self._state is TurnState.CLOSED:
            raise RuntimeError("voice turn coordinator is closed")
