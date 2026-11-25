"""plugins/persuasion_coach.py — Ethical Persuasion, Negotiation Framing & Pitch Strategist."""
from __future__ import annotations

import re
from typing import Any

_MANIPULATION_BLOCK_RE = re.compile(
    r"\b(deceiv(?:e|ing)|manipulat(?:e|ive|ion)|gaslight|blackmail|extort|"
    r"coerc(?:e|ion)|phish(?:ing)?|pretext(?:ing)?|social[- ]?engineer(?:ing)?|"
    r"fake (?:scarcity|urgency|offer|credentials|reviews?)|fabricat(?:e|ed) (?:proof|numbers|data)|"
    r"trick them into|exploit their (?:fear|trauma|insecurity)|lie to)\b",
    re.IGNORECASE,
)

PLUGIN = {
    "name": "persuasion_coach",
    "brief": "Prepare ethical negotiation briefs, empathetic framing, objections, and pitch scripts.",
    "description": (
        "Ethical negotiation strategist and pitch coach. Helps prepare compelling, empathetic, "
        "truthful arguments, stakeholder alignment, BATNA/ZOPA boundaries, objection playbooks, "
        "and executive pitch scripts for real-world meetings without deception or manipulation."
    ),
    "risk": "safe",
    "tier": "deep",
    "keywords": [
        "persuasion", "negotiate", "negotiation", "pitch", "objections", "framing",
        "argument", "stakeholder", "batna", "meeting", "proposal", "strategy",
    ],
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "action": {
                "type": "STRING",
                "enum": [
                    "status",
                    "prepare_pitch",
                    "negotiation_brief",
                    "objection_playbook",
                    "argument_audit",
                    "reframe_message",
                ],
                "description": "Coaching operation to run (default 'prepare_pitch' when goal is given).",
            },
            "goal": {
                "type": "STRING",
                "description": "What outcome you want to achieve honestly in the meeting or proposal.",
            },
            "audience": {
                "type": "STRING",
                "description": "Who you are speaking with, their role, priorities, and concerns.",
            },
            "context": {
                "type": "STRING",
                "description": "Real facts, metrics, constraints, timeline, or tradeoffs involved.",
            },
            "batna": {
                "type": "STRING",
                "description": "Your walk-away alternative / fallback plan if agreement is not reached.",
            },
            "draft_message": {
                "type": "STRING",
                "description": "Optional draft message or pitch to audit and strengthen ethically.",
            },
            "tone": {
                "type": "STRING",
                "enum": ["collaborative", "executive", "empathetic", "assertive"],
                "description": "Desired delivery style.",
            },
        },
        "required": [],
    },
}


def _check_ethical_request(*fields: str) -> str | None:
    joined = " ".join(f for f in fields if f)
    m = _MANIPULATION_BLOCK_RE.search(joined)
    if m:
        return (
            f"Refused: '{m.group(0)}' crosses into deceptive or manipulative tactics. "
            "persuasion_coach strictly builds honest, empathetic, evidence-backed persuasion, "
            "shared-interest framing, and principled negotiation strategies."
        )
    return None


def _llm_polish(prompt: str) -> str:
    """Optional local-first refinement; strictly skips cloud if Privacy Mode is active."""
    try:
        from providers import local_router
        res = local_router.complete_local(prompt, force_category="reasoning", timeout_s=25.0)
        if isinstance(res, dict) and str(res.get("text") or "").strip():
            return str(res["text"]).strip()
    except Exception:
        pass
    try:
        from core import privacy
        if privacy.is_active():
            return ""
    except Exception:
        pass
    return ""


def _build_structured_brief(
    *,
    action: str,
    goal: str,
    audience: str,
    context: str,
    batna: str,
    draft: str,
    tone: str,
) -> str:
    aud = audience or "Decision maker / counterparty"
    ctx = context or "Establish clear, verifiable metrics and mutual outcomes before anchoring numbers."
    walk = batna or "Define your minimum acceptable floor and credible walk-away option privately before the call."

    shared_ground = (
        f"Lead with what {aud} cares about most: reducing risk, protecting upside, and predictable execution."
    )
    framing = (
        f"Tone: {tone}. Anchor around mutual value rather than zero-sum demands; state evidence transparently."
    )
    pitch_script = "\n".join([
        f"1. Empathy Hook: Acknowledge {aud}'s primary constraint upfront ('I know keeping {ctx[:60].strip()} clean matters to your team').",
        f"2. Problem & Honest Cost: Quantify the current gap plainly without inflating urgency.",
        f"3. Proposal ({goal}): Present concrete deliverables, timeline, and measurable proof.",
        f"4. Shared Win & Clear Ask: Propose a concrete next step with low-friction verification.",
    ])
    objections = "\n".join([
        "- 'Too expensive / budget is tight' -> Break value into phased milestones or ROI payback window.",
        "- 'Not a priority right now' -> Clarify cost of waiting using only verified facts, or agree on a concrete follow-up trigger.",
        "- 'Too risky / unproven' -> Offer a bounded pilot, clear success criteria, and transparent exit terms.",
    ])
    audit_notes = ""
    if draft:
        has_why = any(w in draft.lower() for w in ("because", "so that", "save", "reduce", "increase", "proof", "metric"))
        has_ask = ("?" in draft) or any(w in draft.lower() for w in ("let's", "propose", "can we", "next step"))
        gaps = []
        if not has_why:
            gaps.append("Add a concrete 'why it matters to them' backed by real proof/metrics.")
        if not has_ask:
            gaps.append("End with one specific, respectful next-step question.")
        if not gaps:
            gaps.append("Message includes rationale and a clear ask; tighten filler words for executive clarity.")
        audit_notes = "\nDraft Audit:\n" + "\n".join(f"- {g}" for g in gaps)

    lines = [
        f"Ethical Persuasion & Negotiation Brief ({action}, tone={tone})",
        f"Objective: {goal}",
        f"Audience: {aud}",
        f"Shared Ground: {shared_ground}",
        f"Framing: {framing}",
        f"BATNA / Walk-Away Floor: {walk}",
        "",
        "Structured Pitch Script:",
        pitch_script,
        "",
        "Likely Objections & Principled Responses:",
        objections,
    ]
    if audit_notes:
        lines.append(audit_notes)
    return "\n".join(lines)


def run(parameters: dict[str, Any] | None = None, player=None, session_memory=None) -> str:
    """Prepare ethical negotiation framing, argument audits, objection playbooks, and pitch scripts."""
    _ = player, session_memory
    params = dict(parameters or {})
    goal = str(params.get("goal") or "").strip()
    action = str(params.get("action") or "").strip().lower()
    if not action:
        action = "prepare_pitch" if goal or params.get("draft_message") else "status"

    if action == "status":
        return (
            "Persuasion Coach ready (ethical mode active: honest framing, empathy mapping, "
            "BATNA/ZOPA negotiation prep, objection playbook, pitch scripts; zero deception)."
        )

    audience = str(params.get("audience") or "").strip()
    context = str(params.get("context") or "").strip()
    batna = str(params.get("batna") or "").strip()
    draft = str(params.get("draft_message") or "").strip()
    tone = str(params.get("tone") or "collaborative").strip().lower()
    if tone not in ("collaborative", "executive", "empathetic", "assertive"):
        tone = "collaborative"

    refusal = _check_ethical_request(goal, audience, context, batna, draft)
    if refusal:
        return refusal

    effective_goal = goal or "Align constructively with the counterparty on a clear, mutually beneficial next step"
    brief = _build_structured_brief(
        action=action,
        goal=effective_goal,
        audience=audience,
        context=context,
        batna=batna,
        draft=draft,
        tone=tone,
    )

    if params.get("use_llm"):
        polished = _llm_polish(
            f"Refine this ethical negotiation brief cleanly in {tone} tone without adding false claims:\n{brief}"
        )
        if polished:
            return polished
    return brief
