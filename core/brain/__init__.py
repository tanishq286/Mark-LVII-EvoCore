"""core.brain — Specialist Expert Routing & Verified Reasoning Modules (Milestone 18)."""
from __future__ import annotations

from .expert_router import (
    EXPERT_MODES,
    ExpertMode,
    ExpertRoute,
    VerificationReport,
    classify_expert_intent,
    format_expert_context,
    heuristic_verify_draft,
    record_solved_task,
    select_tool_set,
    verify_and_respond,
)

__all__ = [
    "EXPERT_MODES",
    "ExpertMode",
    "ExpertRoute",
    "VerificationReport",
    "classify_expert_intent",
    "format_expert_context",
    "heuristic_verify_draft",
    "record_solved_task",
    "select_tool_set",
    "verify_and_respond",
]
