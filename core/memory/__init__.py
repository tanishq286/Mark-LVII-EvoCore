"""Entity-Relation Knowledge Graph and memory subsystem helpers for Mark-LVII."""
from __future__ import annotations

from core.memory.knowledge_graph import (
    DEFAULT_GRAPH_DB,
    ENTITY_TYPES,
    add_relation,
    consolidate_graph,
    graph_stats,
    ingest_conversation_turn,
    ingest_memory_dict,
    init_db,
    query_graph,
    upsert_entity,
)

__all__ = [
    "DEFAULT_GRAPH_DB",
    "ENTITY_TYPES",
    "add_relation",
    "consolidate_graph",
    "graph_stats",
    "ingest_conversation_turn",
    "ingest_memory_dict",
    "init_db",
    "query_graph",
    "upsert_entity",
]
