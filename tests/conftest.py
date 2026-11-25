"""Shared pytest fixtures isolating live user state from test runs."""
from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def _isolate_live_user_memory(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep tests isolated from live enrolled voiceprints, missions, swarm blackboards, and graphs."""
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

    try:
        from core.evolution import swarm
        monkeypatch.setattr(swarm, "DEFAULT_BLACKBOARD_DB", tmp_path / "swarm_blackboard.sqlite")
    except Exception:
        pass

    try:
        from core.evolution import mission_runner
        monkeypatch.setattr(mission_runner, "DEFAULT_MISSIONS_DB", tmp_path / "missions.sqlite")
    except Exception:
        pass

    try:
        from core.memory import knowledge_graph
        monkeypatch.setattr(knowledge_graph, "DEFAULT_GRAPH_DB", tmp_path / "knowledge_graph.sqlite")
    except Exception:
        pass

    try:
        from core.evolution import meta_optimizer
        monkeypatch.setattr(meta_optimizer, "DEFAULT_META_DB", tmp_path / "meta_optimizer.sqlite")
    except Exception:
        pass

    try:
        from core import mcp_client
        monkeypatch.setattr(mcp_client, "DEFAULT_REGISTRY_DB", tmp_path / "mcp_registry.sqlite")
    except Exception:
        pass

    # Milestone 15: cross-session conversation memory, RAG memory note, 24/7 agents.
    monkeypatch.setenv("JARVIS_CONVERSATIONS_DB", str(tmp_path / "conversations.sqlite"))
    try:
        from core.memory import conversation_store
        monkeypatch.setattr(conversation_store, "DEFAULT_DB", tmp_path / "conversations.sqlite")
        monkeypatch.setattr(conversation_store, "_CURRENT", {})
    except Exception:
        pass

    try:
        from plugins import knowledge_rag
        monkeypatch.setattr(knowledge_rag, "KNOWLEDGE_DIR", tmp_path / "knowledge")
    except Exception:
        pass

    try:
        from core.evolution import research_daemon
        monkeypatch.setattr(research_daemon, "DEFAULT_QUEUE_DB", tmp_path / "research_queue.sqlite")
    except Exception:
        pass

    monkeypatch.setenv("JARVIS_DISABLE_247_AGENTS", "1")
    try:
        from core.evolution import agents_247
        monkeypatch.setattr(agents_247, "DEFAULT_STATE_PATH", tmp_path / "agents_state.json")
    except Exception:
        pass
