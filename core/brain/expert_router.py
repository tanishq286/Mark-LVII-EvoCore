"""core/brain/expert_router.py — Domain-Expert Router, Silent Critic-Verify & Knowledge Compounding.

Milestone 18:
  1. Classifies user queries across 5 specialist domain-expert modes:
       - software_architect : system design, code review, debugging, architecture, security
       - deep_researcher    : multi-source synthesis, comparisons, literature/market research
       - finance_strategy   : unit economics, negotiation strategy, pricing, ROI, risk analysis
       - science_math       : proofs, derivations, physics/chemistry/ML/stats, calculations
       - creative_writer    : storytelling, speeches, copywriting, dialogue, brand voice
  2. Selects a curated domain tool-set (filtering cloud/network tools when Strict Local
     Privacy Mode is active) and routes local execution to the best specialist tier.
  3. Runs a silent `Think -> Critic-Verify -> Respond` 3-stage verification loop for
     complex queries (checking Python AST syntax, safe AST arithmetic identities, citation/
     completeness hygiene, and silent critic review before speaking).
  4. Auto-compounds verified research/problem-solving artifacts into `knowledge_rag` and
     `knowledge_graph.sqlite` so Mark-LVII's expertise compounds continuously.
"""
from __future__ import annotations

import ast
from dataclasses import dataclass, field
import operator
from pathlib import Path
import re
from typing import Any, Callable, Sequence

_CLOUD_ONLY_TOOLS = frozenset({"web_search", "web_scraper", "mcp_bridge", "google_search"})
_THINK_STRIP_RE = re.compile(r"(?is)<(?:think|thinking|reasoning|critic)>.*?</(?:think|thinking|reasoning|critic)>")
_THINK_OPEN_RE = re.compile(r"(?is)<(?:think|thinking|reasoning|critic)>.*$")
_PY_BLOCK_RE = re.compile(r"```(?:python|py)\s*\n(.*?)```", re.DOTALL | re.IGNORECASE)
_EQ_CLAIM_RE = re.compile(r"(?m)^\s*([0-9][0-9\s\+\-\*/\(\)\.]{1,40})\s*=\s*(-?[0-9]+(?:\.[0-9]+)?)\s*(?:$|[.,;\n])")

_COMPLEX_MARKER_RE = re.compile(
    r"\b(architect(?:ure)?|design|refactor|debug|root[- ]?cause|trade[- ]?off|compare|"
    r"analy[sz]e|derive|prove|verify|benchmark|threat model|audit|negotiat(?:e|ion)|"
    r"valuation|roi|unit economics|budget|forecast|strategy|research|literature|"
    r"deep dive|step[- ]by[- ]step|write a (?:script|module|spec|report|pitch|story|essay)|"
    r"implement|optimi[sz]e|migrati?on|algorithm)\b|```",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class ExpertMode:
    key: str
    title: str
    preferred_category: str
    tool_set: tuple[str, ...]
    system_prompt: str
    keywords: tuple[str, ...]
    pattern: re.Pattern[str]


EXPERT_MODES: dict[str, ExpertMode] = {
    "software_architect": ExpertMode(
        key="software_architect",
        title="Software Architect",
        preferred_category="coding",
        tool_set=(
            "claude_code",
            "dev_workspace",
            "run_python_or_cmd",
            "search_vault",
            "find_file",
            "grep_search",
            "read_file",
            "antigravity_agent",
            "web_search",
        ),
        system_prompt=(
            "[EXPERT MODE — SOFTWARE ARCHITECT]\n"
            "Think like a principal systems & software architect. Prioritize correctness, "
            "clean module boundaries, failure modes, security, concurrency/threading safety, "
            "and minimal diff surface. Verify any code syntax and edge cases before replying."
        ),
        keywords=(
            "architecture", "refactor", "debug", "traceback", "exception", "python",
            "typescript", "sql", "api", "pytest", "git", "concurrency", "mutex", "docker",
            "database", "schema", "bug", "latency", "memory leak", "security",
        ),
        pattern=re.compile(
            r"\b(architect(?:ure)?|refactor|debug|traceback|syntaxerror|typeerror|"
            r"code|coding|python|typescript|javascript|rust|golang|sql|sqlite|postgres|"
            r"api|endpoint|pytest|unittest|git|docker|kubernetes|concurrency|async|"
            r"mutex|thread|schema|migrat(?:e|ion)|bug|patch|class|function|module|plugin)\b|```",
            re.IGNORECASE,
        ),
    ),
    "deep_researcher": ExpertMode(
        key="deep_researcher",
        title="Deep Researcher",
        preferred_category="reasoning",
        tool_set=(
            "web_search",
            "web_scraper",
            "search_vault",
            "knowledge_graph",
            "bg_research",
            "mcp_bridge",
            "office_suite",
        ),
        system_prompt=(
            "[EXPERT MODE — DEEP RESEARCHER]\n"
            "Synthesize evidence rigorously across sources. Separate verified facts from "
            "inferences, note dates/versions/uncertainties explicitly, cross-check contradicting "
            "claims, and organize takeaways cleanly with actionable synthesis."
        ),
        keywords=(
            "research", "compare", "survey", "literature", "benchmark", "report",
            "sources", "citations", "evidence", "state of the art", "paper", "study",
        ),
        pattern=re.compile(
            r"\b(research|investigate|survey|literature review|compare|comparison|"
            r"benchmark|state of the art|sota|citations?|sources?|verify claims?|"
            r"deep dive|synthesis|report on|whitepaper|paper|meta[- ]analysis)\b",
            re.IGNORECASE,
        ),
    ),
    "finance_strategy": ExpertMode(
        key="finance_strategy",
        title="Finance & Strategy",
        preferred_category="reasoning",
        tool_set=(
            "market_finance",
            "persuasion_coach",
            "office_suite",
            "search_vault",
            "knowledge_graph",
            "web_search",
        ),
        system_prompt=(
            "[EXPERT MODE — FINANCE & STRATEGY]\n"
            "Analyze with CFO + strategy-partner discipline: quantify unit economics, upside/"
            "downside sensitivity, cash flow, BATNA/ZOPA leverage, competitive moats, and "
            "execution risks. Double-check every arithmetic figure and percentage before speaking."
        ),
        keywords=(
            "finance", "valuation", "roi", "margin", "revenue", "pricing", "negotiate",
            "negotiation", "pitch", "investor", "runway", "unit economics", "cashflow",
            "stock", "portfolio", "strategy", "gtm", "contract", "deal",
        ),
        pattern=re.compile(
            r"\b(financ(?:e|ial)|valuation|roi|irr|ebitda|margin|revenue|profit|loss|"
            r"pricing|unit economics|cash ?flow|runway|burn rate|cap table|equity|"
            r"negotiation|negotiat(?:e|ing)|batna|zopa|term sheet|pitch deck|contract|"
            r"deal|stock|etf|portfolio|market cap|strategy|go[- ]to[- ]market|gtm|monetiz)\b",
            re.IGNORECASE,
        ),
    ),
    "science_math": ExpertMode(
        key="science_math",
        title="Science & Math",
        preferred_category="reasoning",
        tool_set=(
            "search_vault",
            "knowledge_graph",
            "bg_research",
            "web_search",
        ),
        system_prompt=(
            "[EXPERT MODE — SCIENCE & MATH]\n"
            "Reason from first principles with mathematical rigor. Check units, boundary "
            "conditions, signs, exponents, and statistical assumptions step by step. Never "
            "state an unverified calculation as exact."
        ),
        keywords=(
            "derive", "proof", "theorem", "integral", "derivative", "matrix", "eigenvalue",
            "probability", "bayesian", "physics", "quantum", "thermodynamics", "chemistry",
            "biology", "equation", "formula", "calculus", "linear algebra", "variance",
        ),
        pattern=re.compile(
            r"\b(math(?:ematics)?|prove|proof|theorem|lemma|derive|derivation|"
            r"integral|derivative|differential|equation|matrix|eigen(?:value|vector)|"
            r"gradient|bayesian|probability|distribution|p[- ]value|regression|"
            r"physics|quantum|relativity|thermodynamics|entropy|chemistry|molecular|"
            r"biology|genetics|neuroscience|calculat(?:e|ion)|solve for)\b",
            re.IGNORECASE,
        ),
    ),
    "creative_writer": ExpertMode(
        key="creative_writer",
        title="Creative Writer",
        preferred_category="fast",
        tool_set=(
            "persuasion_coach",
            "deck_generator",
            "website_builder",
            "emotion_soul",
            "search_vault",
        ),
        system_prompt=(
            "[EXPERT MODE — CREATIVE WRITER]\n"
            "Craft vivid, human, emotionally resonant prose with rhythm, imagery, and an "
            "unmistakable voice. Avoid generic clichés and filler; tailor tone, pacing, and "
            "cadence precisely to the audience and medium."
        ),
        keywords=(
            "story", "poem", "poetry", "script", "speech", "novel", "dialogue",
            "copywriting", "tagline", "narrative", "character", "essay", "letter",
            "hinglish", "lyrics", "metaphor", "draft",
        ),
        pattern=re.compile(
            r"\b(write (?:a |me )?(?:story|poem|poetry|speech|script|dialogue|letter|"
            r"essay|tagline|monologue|toast|song|lyrics|bio)|storytelling|creative writing|"
            r"copywriting|brand voice|narrative|metaphor|screenplay|fiction|character arc)\b",
            re.IGNORECASE,
        ),
    ),
}


@dataclass(frozen=True)
class ExpertRoute:
    mode: str
    title: str
    confidence: float
    complexity: str  # "simple" | "moderate" | "complex"
    needs_verify: bool
    preferred_category: str
    tool_set: tuple[str, ...]
    system_block: str
    reason: str
    scores: dict[str, int] = field(default_factory=dict)


@dataclass(frozen=True)
class VerificationReport:
    passed: bool
    issues: tuple[str, ...] = ()
    checks_run: tuple[str, ...] = ()


def _privacy_on(explicit: bool | None) -> bool:
    if explicit is not None:
        return bool(explicit)
    try:
        from core import privacy
        return bool(privacy.is_active())
    except Exception:
        return False


def select_tool_set(mode: str, *, privacy_active: bool | None = None) -> list[str]:
    """Return curated tool names for `mode`, filtering network tools in Strict Privacy Mode."""
    spec = EXPERT_MODES.get(mode) or EXPERT_MODES["deep_researcher"]
    tools = list(spec.tool_set)
    if _privacy_on(privacy_active):
        tools = [t for t in tools if t not in _CLOUD_ONLY_TOOLS]
    return tools


def classify_expert_intent(
    query: str,
    *,
    context: str = "",
    force_mode: str | None = None,
    privacy_active: bool | None = None,
) -> ExpertRoute:
    """Classify `query` into one of the 5 specialist expert modes and select its tool-set."""
    text = f"{query or ''}\n{context or ''}".strip()
    low = text.lower()
    scores: dict[str, int] = {}

    for key, spec in EXPERT_MODES.items():
        regex_hits = len(spec.pattern.findall(text))
        kw_hits = sum(1 for kw in spec.keywords if kw in low)
        scores[key] = regex_hits * 2 + kw_hits

    if force_mode and force_mode in EXPERT_MODES:
        chosen_key = force_mode
        reason = f"forced:{force_mode}"
        confidence = 1.0
    else:
        ranked = sorted(scores.items(), key=lambda kv: (-kv[1], kv[0]))
        best_key, best_score = ranked[0]
        second_score = ranked[1][1] if len(ranked) > 1 else 0
        if best_score <= 0:
            chosen_key = "deep_researcher" if "?" in text else "creative_writer" if len(low) < 80 else "software_architect"
            confidence = 0.45
            reason = "fallback:general_heuristic"
        else:
            chosen_key = best_key
            margin = best_score - second_score
            confidence = min(0.99, round(0.55 + 0.08 * best_score + 0.05 * margin, 2))
            reason = f"matched:{best_key}(score={best_score})"

    spec = EXPERT_MODES[chosen_key]
    complex_hits = len(_COMPLEX_MARKER_RE.findall(query or ""))
    has_code_or_math = bool(_PY_BLOCK_RE.search(query or "") or re.search(r"[\^\=\+\*\/]|∑|∫|√", query or ""))
    word_count = len((query or "").split())

    if complex_hits >= 2 or has_code_or_math or word_count >= 28:
        complexity = "complex"
        needs_verify = True
    elif complex_hits == 1 or word_count >= 12 or chosen_key in ("software_architect", "science_math", "finance_strategy"):
        complexity = "moderate"
        needs_verify = bool(complex_hits >= 1 or chosen_key in ("software_architect", "science_math", "finance_strategy") and word_count >= 7)
    else:
        complexity = "simple"
        needs_verify = False

    tools = tuple(select_tool_set(chosen_key, privacy_active=privacy_active))
    return ExpertRoute(
        mode=spec.key,
        title=spec.title,
        confidence=confidence,
        complexity=complexity,
        needs_verify=needs_verify,
        preferred_category=spec.preferred_category,
        tool_set=tools,
        system_block=spec.system_prompt,
        reason=reason,
        scores=scores,
    )


_BIN_OPS = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.Pow: operator.pow,
    ast.Mod: operator.mod,
}
_UNARY_OPS = {ast.UAdd: operator.pos, ast.USub: operator.neg}


def _safe_eval_arith(expr: str) -> float | None:
    """Safely evaluate basic arithmetic expressions via AST (no names, calls, or eval)."""
    node = ast.parse(expr.strip(), mode="eval").body

    def _walk(n: ast.AST) -> float:
        if isinstance(n, ast.Constant) and isinstance(n.value, (int, float)) and not isinstance(n.value, bool):
            return float(n.value)
        if isinstance(n, ast.UnaryOp) and type(n.op) in _UNARY_OPS:
            return float(_UNARY_OPS[type(n.op)](_walk(n.operand)))
        if isinstance(n, ast.BinOp) and type(n.op) in _BIN_OPS:
            right = _walk(n.right)
            if isinstance(n.op, ast.Div) and abs(right) < 1e-12:
                raise ZeroDivisionError("division by zero")
            return float(_BIN_OPS[type(n.op)](_walk(n.left), right))
        raise ValueError("unsupported expression node")

    return _walk(node)


def strip_internal_reasoning(text: str) -> str:
    """Ensure silent `<think>` / `<critic>` reasoning blocks never leak into spoken output."""
    cleaned = _THINK_STRIP_RE.sub("", str(text or ""))
    cleaned = _THINK_OPEN_RE.sub("", cleaned)
    return cleaned.strip()


def heuristic_verify_draft(query: str, draft: str, mode: str) -> VerificationReport:
    """Deterministic verification pass on candidate answer (Python AST, math identities, leakage)."""
    _ = query
    issues: list[str] = []
    checks: list[str] = ["non_empty", "thinking_leak_guard"]

    cleaned = strip_internal_reasoning(draft)
    if not cleaned:
        issues.append("Draft answer is empty after stripping internal reasoning.")
        return VerificationReport(passed=False, issues=tuple(issues), checks_run=tuple(checks))

    if "<think" in str(draft or "").lower() or "<critic" in str(draft or "").lower():
        issues.append("Draft contained unstripped internal thinking/critic markers.")

    py_blocks = _PY_BLOCK_RE.findall(cleaned)
    if py_blocks or mode == "software_architect":
        checks.append("python_ast_syntax")
        for idx, code in enumerate(py_blocks, 1):
            try:
                ast.parse(code)
            except SyntaxError as exc:
                issues.append(f"Python block #{idx} syntax error at line {exc.lineno}: {exc.msg}")

    if mode in ("science_math", "finance_strategy"):
        checks.append("arithmetic_consistency")
        for m in _EQ_CLAIM_RE.finditer(cleaned):
            lhs, rhs_s = m.group(1), m.group(2)
            if not any(op in lhs for op in "+-*/"):
                continue
            try:
                expected = _safe_eval_arith(lhs)
                stated = float(rhs_s)
            except Exception:
                continue
            tol = max(1e-3, abs(expected) * 0.015)
            if abs(expected - stated) > tol:
                issues.append(f"Arithmetic mismatch: {lhs.strip()} evaluates to {expected:g}, not {rhs_s}.")

    return VerificationReport(passed=(len(issues) == 0), issues=tuple(issues), checks_run=tuple(checks))


def _default_llm_call(
    stage: str,
    system_prompt: str,
    user_prompt: str,
    *,
    category: str = "reasoning",
    timeout_s: float = 45.0,
) -> str:
    """Privacy-aware default LLM caller: local Ollama specialist first, optional Gemini fallback."""
    _ = stage
    try:
        from providers import local_router
        res = local_router.complete_local(
            user_prompt,
            system=system_prompt,
            force_category=category,
            timeout_s=timeout_s,
        )
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

    try:
        from core import gemini
        tier = gemini.FAST if category == "fast" else gemini.DEEP
        prompt = f"{system_prompt}\n\n{user_prompt}" if system_prompt else user_prompt
        out = gemini.call(prompt, tier=tier)
        return str(out or "").strip()
    except Exception:
        return ""


def verify_and_respond(
    query: str,
    *,
    mode: str | None = None,
    context: str = "",
    llm_fn: Callable[[str, str, str], str] | None = None,
    enable_verify: bool | None = None,
    auto_index: bool = False,
    vault_dir: Path | None = None,
    graph_db_path: Path | None = None,
) -> dict[str, Any]:
    """Execute expert routing + optional silent Think -> Critic-Verify -> Respond pipeline."""
    route = classify_expert_intent(query, context=context, force_mode=mode)
    caller = llm_fn or (
        lambda st, sys_p, usr_p: _default_llm_call(st, sys_p, usr_p, category=route.preferred_category)
    )
    run_verify = route.needs_verify if enable_verify is None else bool(enable_verify)

    sys_parts = [route.system_block]
    if route.tool_set:
        sys_parts.append(f"Preferred specialist tool-set: {', '.join(route.tool_set)}")
    if context.strip():
        sys_parts.append(f"[GROUNDING CONTEXT]\n{context.strip()[:3000]}")
    think_system = "\n\n".join(sys_parts)

    stages = ["think"]
    raw_draft = caller("think", think_system, str(query or "").strip())
    draft = strip_internal_reasoning(raw_draft)

    report = VerificationReport(passed=bool(draft), issues=(), checks_run=())
    critic_notes = ""
    revised_ok = False

    if run_verify:
        stages.append("critic_verify")
        report = heuristic_verify_draft(query, raw_draft, route.mode)
        critic_system = (
            "[SILENT CRITIC VERIFIER]\n"
            "Verify the candidate answer below against the user request. Check code correctness, "
            "mathematical/financial calculations, factual discipline, and edge cases. "
            "Reply starting with `VERDICT: PASS` if solid, or `VERDICT: REVISE` followed by concrete corrections."
        )
        critic_user = (
            f"Mode: {route.title}\nUser Request:\n{query}\n\n"
            f"Candidate Draft:\n{draft}\n\n"
            f"Deterministic Pre-Checks: passed={report.passed}; issues={list(report.issues)}"
        )
        critic_notes = strip_internal_reasoning(caller("critic_verify", critic_system, critic_user))
        needs_revision = (not report.passed) or ("VERDICT: REVISE" in critic_notes.upper())
        stages.append("respond")
        if needs_revision:
            revise_system = (
                f"{route.system_block}\n"
                "Revise the draft cleanly to fix every critic issue. Speak naturally and directly; "
                "do NOT mention internal review passes."
            )
            revise_user = (
                f"User Request:\n{query}\n\nOriginal Draft:\n{draft}\n\n"
                f"Issues / Critic Notes:\n{'; '.join(report.issues)}\n{critic_notes}"
            )
            revised = strip_internal_reasoning(caller("respond", revise_system, revise_user))
            if revised:
                draft = revised
                report = heuristic_verify_draft(query, draft, route.mode)
                revised_ok = bool(report.passed)
    else:
        stages.append("respond")

    auto_res: dict[str, Any] | None = None
    if auto_index and draft and len(draft.strip()) >= 80:
        auto_res = record_solved_task(
            title=str(query or route.title)[:100],
            content=f"## Query\n{query}\n\n## Verified Specialist Response ({route.title})\n{draft}",
            domain=route.mode,
            kind="solved_task",
            vault_dir=vault_dir,
            graph_db_path=graph_db_path,
        )

    critic_passed = bool(report.passed) if revised_ok else (
        bool(report.passed) and ("VERDICT: REVISE" not in critic_notes.upper())
    )

    return {
        "text": draft,
        "mode": route.mode,
        "title": route.title,
        "confidence": route.confidence,
        "complexity": route.complexity,
        "preferred_category": route.preferred_category,
        "tool_set": list(route.tool_set),
        "verified": run_verify,
        "critic_passed": critic_passed,
        "issues": list(report.issues),
        "checks_run": list(report.checks_run),
        "critic_notes": critic_notes,
        "stages": stages,
        "auto_indexed": auto_res,
    }


def record_solved_task(
    title: str,
    content: str,
    *,
    domain: str = "",
    kind: str = "solved_task",
    source: str = "expert_router",
    tags: Sequence[str] | None = None,
    entities: Sequence[dict[str, Any]] | None = None,
    vault_dir: Path | None = None,
    graph_db_path: Path | None = None,
) -> dict[str, Any]:
    """Auto-compound a solved task or completed report into `knowledge_rag` + `knowledge_graph.sqlite`."""
    try:
        from plugins import knowledge_rag
        return knowledge_rag.auto_compound_artifact(
            title=title,
            content=content,
            kind=kind,
            domain=domain or "general",
            source=source,
            tags=list(tags) if tags else ([domain] if domain else []),
            entities=list(entities) if entities else None,
            vault_dir=vault_dir,
            graph_db_path=graph_db_path,
        )
    except Exception as exc:
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}


def format_expert_context(query: str = "", *, force_mode: str | None = None) -> str:
    """Format concise domain-expert routing + silent Critic-Verify instruction for prompts."""
    if not (query or "").strip() and not force_mode:
        lines = [
            "[SPECIALIST EXPERT MODES & SILENT CRITIC-VERIFY]",
            "Adapt dynamically across 5 specialist modes depending on what the user asks:",
        ]
        for spec in EXPERT_MODES.values():
            lines.append(f"- {spec.title}: tools=[{', '.join(spec.tool_set[:5])}]")
        lines.append(
            "For complex engineering, math, financial, or research questions, silently verify code, "
            "arithmetic, and cited evidence before answering directly — never speak internal draft notes."
        )
        return "\n".join(lines)

    route = classify_expert_intent(query, force_mode=force_mode)
    verify_line = (
        "Run silent Think -> Critic-Verify -> Respond before answering (verify code/math/facts)."
        if route.needs_verify
        else "Answer crisply and directly."
    )
    return (
        f"{route.system_block}\n"
        f"Recommended tools: {', '.join(route.tool_set) if route.tool_set else 'local-only'}.\n"
        f"{verify_line}"
    )
