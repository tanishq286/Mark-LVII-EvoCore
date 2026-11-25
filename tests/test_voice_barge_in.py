from __future__ import annotations

from core.echo import BargeInDetector


class FakeEchoGuard:
    def __init__(self, *, calibrated: bool = True, required_blocks: int = 3):
        self.calibrated = calibrated
        self.required_blocks = required_blocks
        self.speech_run = 0
        self.calls = 0

    def reset_speech_run(self) -> None:
        self.speech_run = 0

    def is_user_speech(self, pcm, sr, level, when=None) -> bool:
        self.calls += 1
        if level >= 0.5:
            self.speech_run += 1
            return True
        self.speech_run = 0
        return False


def test_detector_waits_for_room_calibration_without_training_from_playback():
    guard = FakeEchoGuard(calibrated=False)
    detector = BargeInDetector(guard)

    for _ in range(6):
        assert detector.observe(b"speech", 16_000, 1.0) is None

    assert guard.calls == 0
    assert guard.speech_run == 0


def test_detector_returns_only_contiguous_confirmed_speech_blocks():
    guard = FakeEchoGuard(required_blocks=3)
    detector = BargeInDetector(guard)

    assert detector.observe(b"echo", 16_000, 0.1) is None
    assert detector.observe(b"voice-1", 16_000, 0.9) is None
    assert detector.observe(b"voice-2", 16_000, 0.9) is None
    confirmed = detector.observe(b"voice-3", 16_000, 0.9)

    assert confirmed == (b"voice-1", b"voice-2", b"voice-3")


def test_echo_block_breaks_the_consecutive_speech_run():
    guard = FakeEchoGuard(required_blocks=3)
    detector = BargeInDetector(guard)

    assert detector.observe(b"voice-1", 16_000, 0.9) is None
    assert detector.observe(b"voice-2", 16_000, 0.9) is None
    assert detector.observe(b"echo", 16_000, 0.1) is None
    assert detector.observe(b"voice-3", 16_000, 0.9) is None
    assert detector.observe(b"voice-4", 16_000, 0.9) is None
    assert detector.observe(b"voice-5", 16_000, 0.9) == (
        b"voice-3", b"voice-4", b"voice-5"
    )


def test_detector_is_one_shot_until_reset():
    guard = FakeEchoGuard(required_blocks=2)
    detector = BargeInDetector(guard)

    assert detector.observe(b"voice-1", 16_000, 0.9) is None
    assert detector.observe(b"voice-2", 16_000, 0.9) == (b"voice-1", b"voice-2")
    assert detector.observe(b"voice-3", 16_000, 0.9) is None

    detector.reset()
    assert detector.observe(b"voice-4", 16_000, 0.9) is None
    assert detector.observe(b"voice-5", 16_000, 0.9) == (b"voice-4", b"voice-5")


def test_candidate_buffer_is_bounded():
    guard = FakeEchoGuard(required_blocks=5)
    detector = BargeInDetector(guard, max_candidate_blocks=2)

    assert detector.observe(b"a", 16_000, 0.9) is None
    assert detector.observe(b"b", 16_000, 0.9) is None
    assert detector.observe(b"c", 16_000, 0.9) is None
    assert detector.observe(b"d", 16_000, 0.9) is None
    assert detector.observe(b"e", 16_000, 0.9) == (b"d", b"e")
