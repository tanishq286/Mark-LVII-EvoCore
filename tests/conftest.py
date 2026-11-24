"""Shared pytest fixtures isolating live user state from test runs."""
from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def _isolate_live_user_memory(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep tests isolated from live enrolled voiceprints and mission history logs."""
    try:
        from plugins import voice_lock
        monkeypatch.setattr(voice_lock, "DEFAULT_STORE_PATH", tmp_path / "voiceprints.json")
    except Exception:
        pass

    try:
        from plugins import autonomous_brain
        monkeypatch.setattr(autonomous_brain, "_MISSION_LOG", tmp_path / "autonomous_missions.json")
    except Exception:
        pass
