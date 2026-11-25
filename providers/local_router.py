"""providers/local_router.py — $0 Local Specialist Model Router & 12 GB VRAM Guard.

Routes local Ollama one-shot requests across installed specialist models with
zero paid APIs and zero extra dependencies (pure Python stdlib):

  - fast      : chat, summaries, extraction, tool calls  -> qwen3:8b (~5.2 GB)
  - coding    : code, debugging, refactoring, scripts    -> qwen2.5-coder:14b (~9.0 GB)
  - reasoning : deep analysis, architecture, trade-offs  -> qwen2.5:14b / gemma4:12b (~8-9 GB)

Voice and vision remain on free Gemini Live; this module handles local text
specialist routing only and is imported lazily from `core/gemini.py`.

VRAM Safety Guard (RTX 5070 Ti — 12 GB VRAM):
  Before switching from an active 8B or 14B Ollama model to a different model,
  queries `/api/ps` and sends `keep_alive: 0` to `/api/generate` for every
  inactive LLM so two large models never collide in 12 GB VRAM.
"""
from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import re
import threading
import time
from typing import Iterable, Sequence
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen

CATEGORY_FAST = "fast"
CATEGORY_CODING = "coding"
CATEGORY_REASONING = "reasoning"

DEFAULT_OLLAMA_ORIGIN = "http://127.0.0.1:11434"
FAST_KEEP_ALIVE = "30m"
HEAVY_KEEP_ALIVE = "5m"

# Specialist preference ladders (first installed model wins; always falls back to qwen3:8b)
SPECIALIST_LADDERS: dict[str, tuple[str, ...]] = {
    CATEGORY_FAST: (
        "qwen3:8b",
        "gemma4:e2b",
        "qwen3:4b",
        "qwen2.5-coder:7b",
    ),
    CATEGORY_CODING: (
        "qwen2.5-coder:14b",
        "qwen2.5-coder:7b",
        "qwen3:8b",
    ),
    CATEGORY_REASONING: (
        "qwen2.5:14b",
        "gemma4:12b",
        "qwen3:8b",
        "gemma4:e2b",
    ),
}

_THINK_BLOCK_RE = re.compile(r"(?is)<(?:think|thinking|reasoning)>.*?</(?:think|thinking|reasoning)>")
_THINK_OPEN_RE = re.compile(r"(?is)<(?:think|thinking|reasoning)>.*$")

# Strong code / debugging / scripting indicators
_CODE_STRONG_RE = re.compile(
    r"(```|traceback \(most recent call last\)|syntaxerror|typeerror|nameerror|"
    r"attributeerror|importerror|keyerror|valueerror|indentationerror|"
    r"\b(?:def |async def |class |lambda\b|import |from [a-z_][a-z0-9_.]* import|"
    r"pytest|unittest|refactor|debug|stack ?trace|regex|sql query|dockerfile|"
    r"git diff|pull request|unit test|code review|bug ?fix|compile error|"
    r"audit_code|code[- ]?audit|static analysis|\bsast\b|\bcwe-\d+\b|semgrep|codeql|"
    r"vuln(?:erability)? remediation|"
    r"patch(?:ing)? (?:the |this )?(?:vuln|cve|sql injection|xss|ssrf|rce|cwe)|"
    r"fix (?:the |this )?(?:sql injection|xss|csrf|ssrf|cwe-\d+|cve-\d{4}-\d+|hardcoded secret|security bug)|"
    r"sanitize (?:sql|html|input)|"
    r"python (?:script|function|lambda|code|module|class|snippet|one-liner)|"
    r"bash (?:script|one-liner|command)|shell script|powershell script|"
    r"javascript|typescript|write a (?:script|function|class|plugin|parser|cli))\b|"
    r"\b[a-z0-9_/-]+\.(?:py|js|ts|tsx|jsx|go|rs|sh|ps1|sql|json|yaml|toml)\b)",
    re.IGNORECASE,
)

_CODE_WEAK_RE = re.compile(
    r"\b(code|coding|python|golang|rust|bash|powershell|sql|script|function|"
    r"lambda|module|plugin|patch|implement|algorithm|endpoint|api|json schema|"
    r"cli|linter|ast|subprocess|dataclass|decorator|traceback)\b",
    re.IGNORECASE,
)

# Deep reasoning / analysis / architecture indicators
_REASONING_STRONG_RE = re.compile(
    r"\b(analy[sz]e|analysis|architect(?:ure|ural)?|trade[- ]?offs?|pros and cons|"
    r"root[- ]?cause|threat model|stride|owasp(?: top 10| asvs)?|mitre att&?ck|"
    r"defense[- ]?in[- ]?depth|attack surface|log triage|incident triage|"
    r"step[- ]by[- ]step|deep dive|evaluate|critique|"
    r"compare in depth|design a system|strategic|strategy|synthesi[sz]e|"
    r"security audit|multi[- ]step|first principles|prove that|derive|explain why)\b",
    re.IGNORECASE,
)

# Explicit fast / brief / classification / extraction indicators
_FAST_HINT_RE = re.compile(
    r"\b(summarize|summary|tldr|tl;dr|one word|one sentence|briefly|concise|"
    r"classify|categorize|extract|translate|quick answer|yes or no|reply with)\b",
    re.IGNORECASE,
)

_SWITCH_LOCK = threading.Lock()
_TAGS_LOCK = threading.Lock()
_TAGS_CACHE: dict[str, tuple[float, set[str]]] = {}
_TAGS_TTL_S = 30.0
_EMBED_MIN_VRAM_BYTES = 512 * 1024 * 1024  # keep tiny embedders (<512 MB) untouched


@dataclass(frozen=True)
class RouteDecision:
    category: str
    preferred_models: tuple[str, ...]
    reason: str
    selected_model: str = ""
    escalate_to_cloud: bool = False
    telemetry_note: str = ""


def adaptive_rank_models(
    category: str,
    ladder: Sequence[str],
    *,
    db_path: Path | None = None,
    min_samples: int = 3,
    min_success_rate: float = 0.65,
    max_p95_ms: float = 25_000.0,
) -> tuple[list[str], bool, str]:
    """Re-order `ladder` using SQLite telemetry and decide if cloud escalation is warranted.

    Models without `min_samples` retain their deterministic static priority until
    enough observations accumulate. Once candidates have sufficient samples, healthy
    models (`success_rate >= min_success_rate` and `p95_ms <= max_p95_ms`) sort ahead
    of degraded ones. If every candidate with sufficient samples is degraded, sets
    `escalate=True`. Never raises.
    """
    ordered = list(ladder)
    if not ordered:
        return [], False, ""
    try:
        from core.evolution.telemetry import route_metrics
        stats = route_metrics(category=category, db_path=db_path)
    except Exception:
        return ordered, False, ""

    # Apply approved Meta-Optimizer routing overrides (when present in meta_optimizer.sqlite)
    preferred_override = ""
    demoted_overrides: set[str] = set()
    try:
        from core.evolution import meta_optimizer
        for ov in meta_optimizer.list_tuning_overrides():
            if ov.get("kind") == "routing":
                pl = ov.get("payload") or {}
                if str(pl.get("category") or "") == category:
                    if pl.get("demote_model"):
                        demoted_overrides.add(str(pl["demote_model"]))
                    if pl.get("prefer_model") and str(pl["prefer_model"]) != "gemini_cloud_fallback":
                        preferred_override = str(pl["prefer_model"])
    except Exception:
        pass

    if not stats and not preferred_override and not demoted_overrides:
        return ordered, False, ""

    evaluated_flags: list[bool] = []
    degraded_flags: list[bool] = []
    scored: list[tuple[tuple[int, float, float, int], str]] = []

    for idx, model in enumerate(ordered):
        rec = stats.get((category, model))
        ov_bonus = -1 if (preferred_override and model == preferred_override) else (1 if model in demoted_overrides else 0)
        if not rec or int(rec.get("calls", 0)) < min_samples:
            # Cold-start: preserve static order among unmeasured candidates without outranking proven healthy ones
            scored.append(((ov_bonus, -min_success_rate, 1e9, idx), model))
            evaluated_flags.append(False)
            degraded_flags.append(False)
            continue

        evaluated_flags.append(True)
        sr = float(rec.get("success_rate", 1.0))
        p95 = float(rec.get("p95_ms", 0.0))
        is_bad = (sr < min_success_rate) or (p95 > max_p95_ms) or (model in demoted_overrides)
        degraded_flags.append(is_bad)
        tier_rank = (1 if is_bad else 0) + ov_bonus
        scored.append(((tier_rank, -sr, p95, idx), model))

    scored.sort(key=lambda item: item[0])
    ranked = [m for _, m in scored]

    # Escalate when every candidate with >= min_samples is degraded (and at least one was evaluated)
    all_eval_bad = any(evaluated_flags) and all(
        degraded for evaluated, degraded in zip(evaluated_flags, degraded_flags) if evaluated
    ) and all(evaluated_flags)
    if all_eval_bad:
        note = (
            f"escalate: all local {category} models degraded "
            f"(success_rate < {min_success_rate:.2f} or p95 > {max_p95_ms:.0f}ms)"
        )
        return ranked, True, note

    if ranked != ordered:
        top_m = ranked[0]
        rec = stats.get((category, top_m), {})
        note = (
            f"re-ranked {category} -> {top_m} "
            f"(sr={rec.get('success_rate', 1.0):.2f}, p95={rec.get('p95_ms', 0.0):.0f}ms)"
        )
        return ranked, False, note
    return ranked, False, ""


def strip_thinking(raw: str) -> str:
    """Strip inline `<think>`, `<thinking>`, `<reasoning>`, and unterminated blocks."""
    if not raw:
        return ""
    cleaned = _THINK_BLOCK_RE.sub("", raw)
    cleaned = _THINK_OPEN_RE.sub("", cleaned)
    return cleaned.strip()


def normalize_ollama_origin(url_or_origin: str | None) -> str:
    """Turn `http://127.0.0.1:11434/v1` or `/api/chat` into `http://127.0.0.1:11434`."""
    raw = str(url_or_origin or "").strip()
    if not raw:
        return DEFAULT_OLLAMA_ORIGIN
    parsed = urlparse(raw)
    if parsed.scheme and parsed.netloc:
        return f"{parsed.scheme}://{parsed.netloc}"
    return DEFAULT_OLLAMA_ORIGIN


def is_local_ollama_url(url: str | None) -> bool:
    """True when `url` points to a local Ollama server on port 11434."""
    parsed = urlparse(str(url or "").strip())
    host = (parsed.hostname or "").lower()
    return host in ("127.0.0.1", "localhost", "::1") and (parsed.port in (11434, None))


def _norm_model(name: str) -> str:
    s = str(name or "").strip().lower()
    return s[:-7] if s.endswith(":latest") else s


def classify_task(prompt: str, tier: str = CATEGORY_FAST) -> RouteDecision:
    """Deterministically classify a text prompt into `fast`, `coding`, or `reasoning`."""
    text = str(prompt or "").strip()
    tier_low = str(tier or CATEGORY_FAST).strip().lower()

    code_strong = len(_CODE_STRONG_RE.findall(text))
    code_weak = len(_CODE_WEAK_RE.findall(text))
    reason_hits = len(_REASONING_STRONG_RE.findall(text))
    fast_hits = len(_FAST_HINT_RE.findall(text))

    # 1. Explicit coding / debugging / script generation
    if code_strong >= 1 or (code_weak >= 2 and fast_hits == 0):
        return RouteDecision(
            category=CATEGORY_CODING,
            preferred_models=SPECIALIST_LADDERS[CATEGORY_CODING],
            reason=f"coding signals (strong={code_strong}, weak={code_weak})",
        )

    # 2. Deep reasoning / analysis / architectural design
    if reason_hits >= 1 and not (tier_low == CATEGORY_FAST and fast_hits >= 1 and len(text) < 220):
        return RouteDecision(
            category=CATEGORY_REASONING,
            preferred_models=SPECIALIST_LADDERS[CATEGORY_REASONING],
            reason=f"reasoning signals (hits={reason_hits}, tier={tier_low})",
        )

    # 3. Explicit SMART tier without coding/fast cues -> reasoning specialist
    if tier_low == "smart" and (fast_hits == 0 or len(text) >= 600):
        return RouteDecision(
            category=CATEGORY_REASONING,
            preferred_models=SPECIALIST_LADDERS[CATEGORY_REASONING],
            reason=f"smart tier analytical default (chars={len(text)})",
        )

    # 4. Default: fast daily sidecar (chat, summaries, tool calls, extraction)
    return RouteDecision(
        category=CATEGORY_FAST,
        preferred_models=SPECIALIST_LADDERS[CATEGORY_FAST],
        reason=f"fast/daily default (tier={tier_low}, fast_hints={fast_hits})",
    )


def list_installed_models(
    ollama_origin: str = DEFAULT_OLLAMA_ORIGIN,
    *,
    timeout: float = 2.5,
    use_cache: bool = True,
) -> set[str] | None:
    """Query Ollama `/api/tags` for installed model names. Returns None if unreachable."""
    origin = normalize_ollama_origin(ollama_origin)
    now = time.monotonic()
    if use_cache:
        with _TAGS_LOCK:
            cached = _TAGS_CACHE.get(origin)
            if cached and (now - cached[0]) < _TAGS_TTL_S:
                return set(cached[1])

    url = f"{origin}/api/tags"
    req = Request(url, method="GET")
    try:
        with urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8", errors="replace"))
    except (HTTPError, URLError, TimeoutError, OSError, json.JSONDecodeError):
        return None

    names: set[str] = set()
    for item in data.get("models") or []:
        if isinstance(item, dict) and item.get("name"):
            names.add(str(item["name"]).strip())
    with _TAGS_LOCK:
        _TAGS_CACHE[origin] = (now, set(names))
    return names


def select_candidate_ladder(
    decision: RouteDecision,
    available_models: Iterable[str] | None = None,
    *,
    fallback_model: str = "qwen3:8b",
) -> list[str]:
    """Resolve ordered specialist models against `available_models`, always falling back cleanly."""
    ordered = list(decision.preferred_models)
    if fallback_model and fallback_model not in ordered:
        ordered.append(fallback_model)

    if available_models is None:
        return ordered

    avail_list = [str(m).strip() for m in available_models if str(m).strip()]
    if not avail_list:
        return ordered

    by_exact = {m.lower(): m for m in avail_list}
    by_norm = {_norm_model(m): m for m in avail_list}

    picked: list[str] = []
    for cand in ordered:
        exact = by_exact.get(cand.lower())
        match = exact or by_norm.get(_norm_model(cand))
        if match and match not in picked:
            picked.append(match)

    if not picked:
        # Guaranteed fallback to qwen3:8b if present, else first available non-embed model
        qwen_fallback = by_exact.get("qwen3:8b") or by_norm.get("qwen3:8b")
        if qwen_fallback:
            picked.append(qwen_fallback)
        else:
            for m in avail_list:
                if "embed" not in m.lower():
                    picked.append(m)
                    break
    return picked or [fallback_model]


def route_prompt(
    prompt: str,
    *,
    tier: str = CATEGORY_FAST,
    ollama_origin: str = DEFAULT_OLLAMA_ORIGIN,
    available_models: Iterable[str] | None = None,
    fallback_model: str = "qwen3:8b",
    force_category: str | None = None,
    use_telemetry: bool = True,
    telemetry_db_path: Path | None = None,
) -> tuple[RouteDecision, list[str]]:
    """Classify `prompt` and resolve the concrete installed Ollama model ladder."""
    if force_category in SPECIALIST_LADDERS:
        decision = RouteDecision(
            category=force_category,
            preferred_models=SPECIALIST_LADDERS[force_category],
            reason=f"explicit category={force_category}",
        )
    else:
        decision = classify_task(prompt, tier=tier)

    avail = available_models
    if avail is None:
        avail = list_installed_models(ollama_origin)
    ladder = select_candidate_ladder(decision, avail, fallback_model=fallback_model)
    escalate = False
    t_note = ""
    if use_telemetry and ladder:
        ladder, escalate, t_note = adaptive_rank_models(
            decision.category,
            ladder,
            db_path=telemetry_db_path,
        )
    resolved = RouteDecision(
        category=decision.category,
        preferred_models=decision.preferred_models,
        reason=decision.reason,
        selected_model=ladder[0],
        escalate_to_cloud=escalate,
        telemetry_note=t_note,
    )
    return resolved, ladder


def get_loaded_models(
    ollama_origin: str = DEFAULT_OLLAMA_ORIGIN,
    *,
    timeout: float = 2.5,
) -> list[dict]:
    """Read active models currently resident in Ollama VRAM via `GET /api/ps`."""
    origin = normalize_ollama_origin(ollama_origin)
    req = Request(f"{origin}/api/ps", method="GET")
    try:
        with urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8", errors="replace"))
    except (HTTPError, URLError, TimeoutError, OSError, json.JSONDecodeError):
        return []
    models = data.get("models")
    return [m for m in models if isinstance(m, dict)] if isinstance(models, list) else []


def unload_model(
    model_name: str,
    ollama_origin: str = DEFAULT_OLLAMA_ORIGIN,
    *,
    timeout: float = 5.0,
) -> bool:
    """Tell Ollama to immediately evict `model_name` from VRAM (`keep_alive: 0`)."""
    origin = normalize_ollama_origin(ollama_origin)
    payload = json.dumps({
        "model": model_name,
        "keep_alive": 0,
        "stream": False,
    }).encode("utf-8")
    req = Request(
        f"{origin}/api/generate",
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urlopen(req, timeout=timeout) as resp:
            resp.read()
        return True
    except (HTTPError, URLError, TimeoutError, OSError):
        return False


def ensure_vram_ready(
    target_model: str,
    ollama_origin: str = DEFAULT_OLLAMA_ORIGIN,
    *,
    timeout: float = 5.0,
) -> dict:
    """Prevent 8B + 14B VRAM collisions on 12 GB GPUs by unloading other resident LLMs."""
    origin = normalize_ollama_origin(ollama_origin)
    target_norm = _norm_model(target_model)
    unloaded: list[str] = []
    active_before: list[str] = []
    already_loaded = False

    with _SWITCH_LOCK:
        running = get_loaded_models(origin, timeout=min(2.5, timeout))
        for entry in running:
            name = str(entry.get("name") or entry.get("model") or "").strip()
            if not name:
                continue
            active_before.append(name)
            if _norm_model(name) == target_norm:
                already_loaded = True
                continue
            size_vram = int(entry.get("size_vram") or 0)
            # Keep tiny embedding models (<512 MB) warm; evict any competing LLM
            if "embed" in name.lower() and 0 < size_vram < _EMBED_MIN_VRAM_BYTES:
                continue
            if unload_model(name, origin, timeout=timeout):
                unloaded.append(name)

    return {
        "target": target_model,
        "already_loaded": already_loaded,
        "active_before": active_before,
        "unloaded": unloaded,
    }


def fast_keep_alive_for(category: str) -> str:
    """Keep fast daily model (qwen3:8b) warm for 30m; keep heavy 14B models at 5m."""
    return FAST_KEEP_ALIVE if category == CATEGORY_FAST else HEAVY_KEEP_ALIVE


def complete_local(
    prompt: str,
    *,
    tier: str = CATEGORY_FAST,
    system: str = "",
    ollama_origin: str = DEFAULT_OLLAMA_ORIGIN,
    chat_url: str | None = None,
    timeout_s: float = 60.0,
    available_models: Iterable[str] | None = None,
    force_category: str | None = None,
    keep_alive: str | int | None = None,
    allow_thinking: bool | None = None,
) -> dict | None:
    """Route `prompt` to the best installed local Ollama specialist, guard VRAM, and strip `<think>`.

    Disables hidden thinking (`think: False`) on fast/chat/coding routes so `qwen3:8b`
    answers in ~0.2-0.5s instead of 2-13s, while keeping full thinking enabled for
    explicit deep `reasoning` routes.
    """
    origin = normalize_ollama_origin(chat_url or ollama_origin)
    endpoint = chat_url or f"{origin}/api/chat"
    decision, ladder = route_prompt(
        prompt,
        tier=tier,
        ollama_origin=origin,
        available_models=available_models,
        force_category=force_category,
    )
    resolved_keep_alive = fast_keep_alive_for(decision.category) if keep_alive is None else keep_alive
    use_thinking = (decision.category == CATEGORY_REASONING) if allow_thinking is None else bool(allow_thinking)

    messages: list[dict[str, str]] = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})

    for model in ladder:
        vram_info = ensure_vram_ready(model, origin, timeout=min(5.0, timeout_s))
        body: dict = {
            "model": model,
            "messages": messages,
            "stream": False,
            "keep_alive": resolved_keep_alive,
        }
        if not use_thinking:
            body["think"] = False
        payload = json.dumps(body).encode("utf-8")
        req = Request(
            endpoint,
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        t_start = time.perf_counter()
        try:
            with urlopen(req, timeout=timeout_s) as resp:
                data = json.loads(resp.read().decode("utf-8", errors="replace"))
        except Exception as exc:
            _record_route_safe(
                decision.category,
                model,
                "ollama",
                False,
                (time.perf_counter() - t_start) * 1000.0,
                error_snippet=f"{type(exc).__name__}: {exc}",
            )
            continue
        elapsed_ms = (time.perf_counter() - t_start) * 1000.0
        raw_txt = str((data.get("message") or {}).get("content") or "")
        txt = strip_thinking(raw_txt)
        if txt:
            _record_route_safe(decision.category, model, "ollama", True, elapsed_ms)
            return {
                "text": txt,
                "model": model,
                "category": decision.category,
                "reason": decision.reason,
                "thinking_enabled": use_thinking,
                "keep_alive": resolved_keep_alive,
                "unloaded": vram_info.get("unloaded") or [],
            }
        _record_route_safe(
            decision.category,
            model,
            "ollama",
            False,
            elapsed_ms,
            error_snippet="empty response",
        )
    return None


def _record_route_safe(
    category: str,
    model: str,
    provider: str,
    success: bool,
    latency_ms: float,
    *,
    escalated: bool = False,
    error_snippet: str = "",
) -> None:
    try:
        from core.evolution.telemetry import record_route_event
        record_route_event(
            category,
            model,
            provider,
            success,
            latency_ms,
            escalated=escalated,
            error_snippet=error_snippet,
        )
    except Exception:
        pass
