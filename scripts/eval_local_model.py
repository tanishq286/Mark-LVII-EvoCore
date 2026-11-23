"""scripts/eval_local_model.py — Side-by-Side Local Model Eval & Zero-Regression Promotion Gate.

Benchmarks a base Ollama model (default: `qwen3:8b`) against a fine-tuned candidate
(default: `jarvis-custom`) over `http://127.0.0.1:11434/v1` using a 20-prompt
held-out eval suite (`training_data/eval_set.jsonl`, auto-seeded from
`scripts/eval_set.template.jsonl` if absent).

Metrics:
  1. Valid tool-call JSON rate (`category == "tool_call"`, real OpenAI tool schemas)
  2. Overall accuracy / constraint pass rate (tool_call + coding + voice)
  3. Median latency (seconds per prompt)

Zero-regression promotion gate:
  - Emits `PROMOTE` (exit 0) ONLY when candidate >= base on both tool-call JSON
    validity AND overall accuracy, AND median latency <= max(base * 1.35, base + 0.5s).
  - Otherwise emits `REJECT — KEEP BASE MODEL` (exit 1).
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import shutil
import statistics
import sys
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

DEFAULT_TEMPLATE = ROOT / "scripts" / "eval_set.template.jsonl"
DEFAULT_EVAL_SET = ROOT / "training_data" / "eval_set.jsonl"
DEFAULT_BASE_URL = "http://127.0.0.1:11434/v1"
DEFAULT_BASE_MODEL = "qwen3:8b"
DEFAULT_CANDIDATE_MODEL = "jarvis-custom"

# Compact OpenAI function schemas matching real Mark-LVII tool contracts for tool_call evals
_EVAL_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "feedback_logger",
            "description": "Log a user correction (mode='log', user_prompt, ideal_response, optional lesson, category) or check stats (mode='stats').",
            "parameters": {
                "type": "object",
                "properties": {
                    "mode": {"type": "string", "enum": ["log", "stats"]},
                    "user_prompt": {"type": "string"},
                    "ideal_response": {"type": "string"},
                    "lesson": {"type": "string"},
                    "category": {"type": "string", "enum": ["coding", "voice", "tool_use", "preference", "general"]},
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "open_app",
            "description": "Launch a desktop application by name.",
            "parameters": {
                "type": "object",
                "properties": {"app_name": {"type": "string"}},
                "required": ["app_name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "recall_memory",
            "description": "Look up stored long-term facts about the user or their projects.",
            "parameters": {
                "type": "object",
                "properties": {"query": {"type": "string"}},
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "save_memory",
            "description": "Save a durable user fact or preference.",
            "parameters": {
                "type": "object",
                "properties": {
                    "key": {"type": "string"},
                    "value": {"type": "string"},
                    "category": {"type": "string"},
                },
                "required": ["key", "value"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "web_search",
            "description": "Search the web for current facts or release notes.",
            "parameters": {
                "type": "object",
                "properties": {"query": {"type": "string"}},
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "routines",
            "description": "Manage scheduled JARVIS routines (list, add, update, remove, run_now).",
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {"type": "string", "enum": ["list", "add", "update", "remove", "run_now", "quiet_hours"]},
                    "name": {"type": "string"},
                },
                "required": ["action"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "skill_drafter",
            "description": "Draft a plugin on a skill/* branch, list pending skill/* branches, or validate a plugin.",
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {"type": "string", "enum": ["list_branches", "draft", "validate"]},
                    "skill_name": {"type": "string"},
                    "specification": {"type": "string"},
                },
                "required": [],
            },
        },
    },
]

_THINK_BLOCK_RE = re.compile(r"(?is)<(?:think|thinking|reasoning)>.*?</(?:think|thinking|reasoning)>")
_THINK_UNTERM_RE = re.compile(r"(?is)<(?:think|thinking|reasoning)>.*$")


def strip_thinking(text: str) -> str:
    """Remove `<think>` / `<thinking>` / `<reasoning>` blocks before grading."""
    try:
        from core.gemini import _strip_thinking
        return _strip_thinking(text)
    except Exception:
        cleaned = _THINK_BLOCK_RE.sub("", text or "")
        cleaned = _THINK_UNTERM_RE.sub("", cleaned)
        return cleaned.strip()


def ensure_eval_set(eval_path: Path, template_path: Path = DEFAULT_TEMPLATE) -> Path:
    """Seed git-ignored `training_data/eval_set.jsonl` from template if absent."""
    if not eval_path.is_file() and template_path.is_file():
        eval_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(template_path, eval_path)
    return eval_path


def load_eval_items(eval_path: Path) -> list[dict]:
    """Load and validate all held-out eval prompts from JSONL."""
    if not eval_path.is_file():
        raise FileNotFoundError(f"Eval set not found: {eval_path}")
    items: list[dict] = []
    seen_ids: set[str] = set()
    for lineno, raw in enumerate(eval_path.read_text(encoding="utf-8").splitlines(), 1):
        stripped = raw.strip()
        if not stripped:
            continue
        obj = json.loads(stripped)
        if not isinstance(obj, dict):
            raise ValueError(f"Line {lineno}: expected JSON object")
        iid = str(obj.get("id") or "").strip()
        cat = str(obj.get("category") or "").strip()
        prompt = str(obj.get("prompt") or "").strip()
        if not iid or iid in seen_ids:
            raise ValueError(f"Line {lineno}: missing or duplicate id {iid!r}")
        if cat not in ("tool_call", "coding", "voice"):
            raise ValueError(f"Line {lineno}: invalid category {cat!r}")
        if not prompt:
            raise ValueError(f"Line {lineno}: empty prompt")
        if cat == "tool_call" and not obj.get("expected_tool"):
            raise ValueError(f"Line {lineno}: tool_call item requires expected_tool")
        seen_ids.add(iid)
        items.append(obj)
    return items


def grade_tool_call_response(item: dict, raw_message: dict) -> tuple[bool, str]:
    """Grade whether the model emitted a valid tool call matching the item's contract."""
    expected_tool = str(item["expected_tool"])
    required_args = list(item.get("required_args") or [])
    allowed_values = dict(item.get("allowed_arg_values") or {})

    tool_calls = raw_message.get("tool_calls") or []
    parsed_name = ""
    parsed_args: dict | None = None

    if tool_calls and isinstance(tool_calls, list):
        fn = (tool_calls[0] or {}).get("function") or {}
        parsed_name = str(fn.get("name") or "").strip()
        raw_args = fn.get("arguments")
        if isinstance(raw_args, dict):
            parsed_args = raw_args
        elif isinstance(raw_args, str):
            try:
                loaded = json.loads(raw_args)
                if isinstance(loaded, dict):
                    parsed_args = loaded
            except json.JSONDecodeError:
                return False, f"invalid tool_call JSON arguments: {raw_args[:80]!r}"
    else:
        # Fallback: check if model returned a single clean JSON tool-call object in content
        visible = strip_thinking(str(raw_message.get("content") or ""))
        m = re.search(r"\{.*\}", visible, re.S)
        if m:
            try:
                obj = json.loads(m.group(0))
                if isinstance(obj, dict):
                    parsed_name = str(obj.get("name") or obj.get("tool") or "").strip()
                    maybe_args = obj.get("arguments") or obj.get("args")
                    if isinstance(maybe_args, dict):
                        parsed_args = maybe_args
            except json.JSONDecodeError:
                return False, "no valid tool_call JSON found"

    if not parsed_name or parsed_args is None:
        return False, "model did not emit a valid tool call"
    if parsed_name != expected_tool:
        return False, f"wrong tool: got {parsed_name!r}, expected {expected_tool!r}"

    for req_key in required_args:
        val = parsed_args.get(req_key)
        if val is None or (isinstance(val, str) and not val.strip()):
            return False, f"missing required arg {req_key!r}"

    for k, allowed in allowed_values.items():
        if not allowed:
            continue
        actual = str(parsed_args.get(k, "")).strip().lower()
        allowed_low = [str(x).strip().lower() for x in allowed]
        if actual not in allowed_low:
            return False, f"arg {k}={actual!r} not in {allowed_low}"

    return True, f"ok ({parsed_name})"


def grade_text_response(item: dict, visible_text: str) -> tuple[bool, str]:
    """Grade a coding or voice response after `<think>` stripping."""
    text = strip_thinking(visible_text)
    if not text:
        return False, "empty visible response"
    low = text.lower()

    for req in item.get("must_contain_all") or []:
        if str(req).lower() not in low:
            return False, f"missing required substring {req!r}"

    any_list = item.get("must_contain_any") or []
    if any_list and not any(str(tok).lower() in low for tok in any_list):
        return False, f"none of expected keywords matched ({any_list[:4]})"

    for bad in item.get("must_not_contain") or []:
        if str(bad).lower() in low:
            return False, f"contained forbidden pattern {bad!r}"

    max_words = item.get("max_words")
    if isinstance(max_words, int) and max_words > 0:
        wc = len(text.split())
        if wc > max_words:
            return False, f"too verbose ({wc} words > {max_words})"

    return True, "ok"


def evaluate_promotion_gate(
    base_metrics: dict,
    cand_metrics: dict,
    *,
    max_latency_ratio: float = 1.35,
    latency_grace_s: float = 0.5,
) -> dict:
    """Enforce zero-regression promotion rules between base and candidate models."""
    reasons: list[str] = []

    base_tc = base_metrics.get("tool_call_valid_rate")
    cand_tc = cand_metrics.get("tool_call_valid_rate")
    if base_tc is not None and cand_tc is not None and cand_tc < base_tc:
        reasons.append(
            f"tool-call JSON validity regressed ({cand_tc:.1%} < base {base_tc:.1%})"
        )

    base_acc = float(base_metrics.get("accuracy_rate") or 0.0)
    cand_acc = float(cand_metrics.get("accuracy_rate") or 0.0)
    if cand_acc < base_acc:
        reasons.append(
            f"overall accuracy regressed ({cand_acc:.1%} < base {base_acc:.1%})"
        )

    base_lat = float(base_metrics.get("median_latency_s") or 0.0)
    cand_lat = float(cand_metrics.get("median_latency_s") or 0.0)
    lat_limit = max(base_lat * max_latency_ratio, base_lat + latency_grace_s)
    if base_lat > 0 and cand_lat > lat_limit:
        reasons.append(
            f"median latency regressed ({cand_lat:.2f}s > allowed {lat_limit:.2f}s)"
        )

    promote = len(reasons) == 0
    return {
        "promote": promote,
        "verdict": "PROMOTE" if promote else "REJECT — KEEP BASE MODEL",
        "reasons": reasons,
    }


def _call_chat_completion(
    base_url: str,
    model: str,
    prompt: str,
    *,
    use_tools: bool,
    timeout: float = 90.0,
) -> tuple[dict, float]:
    """Call OpenAI-compatible `/v1/chat/completions` and return `(message_dict, elapsed_s)`."""
    url = base_url.rstrip("/") + "/chat/completions"
    body: dict = {
        "model": model,
        "temperature": 0.0,
        "stream": False,
        "messages": [
            {
                "role": "system",
                "content": (
                    "You are J.A.R.V.I.S. Follow instructions precisely. "
                    "When a tool fits the user request, call it with valid JSON arguments. "
                    "Keep plain-text replies concise."
                ),
            },
            {"role": "user", "content": prompt},
        ],
    }
    if use_tools:
        body["tools"] = _EVAL_TOOLS
        body["tool_choice"] = "auto"

    req = Request(
        url,
        data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    t0 = time.perf_counter()
    try:
        with urlopen(req, timeout=timeout) as resp:
            payload = json.loads(resp.read().decode("utf-8", errors="replace"))
    except (HTTPError, URLError, TimeoutError) as exc:
        raise RuntimeError(f"HTTP error calling {model} at {url}: {exc}") from exc
    elapsed = time.perf_counter() - t0
    choices = payload.get("choices") or []
    msg = (choices[0] or {}).get("message") or {} if choices else {}
    return msg, elapsed


def evaluate_model(
    model: str,
    items: list[dict],
    base_url: str,
    timeout: float = 90.0,
) -> dict:
    """Run all eval items against one Ollama model and aggregate metrics."""
    latencies: list[float] = []
    results: list[dict] = []
    tc_total = 0
    tc_pass = 0
    total_pass = 0

    for item in items:
        is_tc = item["category"] == "tool_call"
        try:
            msg, elapsed = _call_chat_completion(
                base_url, model, item["prompt"], use_tools=is_tc, timeout=timeout
            )
            latencies.append(elapsed)
            if is_tc:
                tc_total += 1
                passed, detail = grade_tool_call_response(item, msg)
                if passed:
                    tc_pass += 1
            else:
                passed, detail = grade_text_response(item, str(msg.get("content") or ""))
        except Exception as exc:
            elapsed = 0.0
            passed = False
            detail = f"error: {exc}"
            if is_tc:
                tc_total += 1

        if passed:
            total_pass += 1
        results.append({
            "id": item["id"],
            "category": item["category"],
            "passed": passed,
            "latency_s": round(elapsed, 3),
            "detail": detail,
        })

    n = len(items)
    return {
        "model": model,
        "total_items": n,
        "passed_items": total_pass,
        "accuracy_rate": round(total_pass / n, 4) if n else 0.0,
        "tool_call_items": tc_total,
        "tool_call_valid": tc_pass,
        "tool_call_valid_rate": round(tc_pass / tc_total, 4) if tc_total else None,
        "median_latency_s": round(statistics.median(latencies), 3) if latencies else 0.0,
        "results": results,
    }


def _run_dry_run(eval_path: Path, as_json: bool) -> int:
    """Validate all 20 eval prompts and verify both PROMOTE and REJECT gate logic offline."""
    items = load_eval_items(eval_path)
    cat_counts: dict[str, int] = {"tool_call": 0, "coding": 0, "voice": 0}
    for it in items:
        cat_counts[it["category"]] = cat_counts.get(it["category"], 0) + 1

    # Deterministic offline checks on grading + <think> stripping + promotion gate
    think_ok = strip_thinking("<think>secret</think>set -euo pipefail") == "set -euo pipefail"
    tc_ok, _ = grade_tool_call_response(
        items[0],
        {
            "tool_calls": [{
                "function": {
                    "name": "feedback_logger",
                    "arguments": json.dumps({
                        "mode": "log",
                        "user_prompt": "run tests",
                        "ideal_response": "preserve exit code",
                        "category": "coding",
                    }),
                }
            }]
        },
    )
    gate_promote = evaluate_promotion_gate(
        {"tool_call_valid_rate": 0.875, "accuracy_rate": 0.85, "median_latency_s": 1.40},
        {"tool_call_valid_rate": 1.000, "accuracy_rate": 0.90, "median_latency_s": 1.50},
    )
    gate_reject_acc = evaluate_promotion_gate(
        {"tool_call_valid_rate": 0.875, "accuracy_rate": 0.90, "median_latency_s": 1.40},
        {"tool_call_valid_rate": 0.875, "accuracy_rate": 0.80, "median_latency_s": 1.40},
    )
    gate_reject_tc = evaluate_promotion_gate(
        {"tool_call_valid_rate": 1.000, "accuracy_rate": 0.85, "median_latency_s": 1.40},
        {"tool_call_valid_rate": 0.750, "accuracy_rate": 0.90, "median_latency_s": 1.40},
    )
    gate_reject_lat = evaluate_promotion_gate(
        {"tool_call_valid_rate": 0.875, "accuracy_rate": 0.85, "median_latency_s": 1.00},
        {"tool_call_valid_rate": 0.875, "accuracy_rate": 0.85, "median_latency_s": 2.10},
    )

    all_ok = (
        len(items) == 20
        and cat_counts == {"tool_call": 8, "coding": 6, "voice": 6}
        and think_ok
        and tc_ok
        and gate_promote["promote"] is True
        and gate_reject_acc["promote"] is False
        and gate_reject_tc["promote"] is False
        and gate_reject_lat["promote"] is False
    )

    payload = {
        "mode": "dry_run",
        "eval_path": str(eval_path),
        "total_prompts": len(items),
        "categories": cat_counts,
        "think_strip_ok": think_ok,
        "tool_call_grader_ok": tc_ok,
        "gate_checks": {
            "promote_branch": gate_promote["verdict"],
            "reject_accuracy_branch": gate_reject_acc["verdict"],
            "reject_tool_call_branch": gate_reject_tc["verdict"],
            "reject_latency_branch": gate_reject_lat["verdict"],
        },
        "passed": all_ok,
    }
    if as_json:
        print(json.dumps(payload, indent=2, ensure_ascii=False))
    else:
        print("=== Mark-LVII Local Model Eval Gate (DRY RUN) ===")
        print(f"  Eval Suite  : {eval_path} ({len(items)} prompts: {cat_counts})")
        print(f"  <think> strip: {'OK' if think_ok else 'FAIL'} | tool_call grader: {'OK' if tc_ok else 'FAIL'}")
        print(f"  Gate PROMOTE: {gate_promote['verdict']}")
        print(f"  Gate REJECT : acc={gate_reject_acc['verdict']} | tool={gate_reject_tc['verdict']} | lat={gate_reject_lat['verdict']}")
        print(f"=== DRY RUN: {'PASSED' if all_ok else 'FAILED'} ===")
    return 0 if all_ok else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Side-by-side promotion gate for local Ollama models (base vs candidate)."
    )
    parser.add_argument("--base", default=DEFAULT_BASE_MODEL, help="Base Ollama model (default: qwen3:8b)")
    parser.add_argument("--candidate", default=DEFAULT_CANDIDATE_MODEL, help="Candidate Ollama model (default: jarvis-custom)")
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL, help="OpenAI-compatible Ollama endpoint")
    parser.add_argument("--eval-set", default=str(DEFAULT_EVAL_SET), help="Path to held-out eval JSONL")
    parser.add_argument("--limit", type=int, default=0, help="Evaluate only the first N prompts (0 = all)")
    parser.add_argument("--timeout", type=float, default=90.0, help="Per-prompt HTTP timeout in seconds")
    parser.add_argument("--max-latency-ratio", type=float, default=1.35, help="Max candidate/base median latency ratio")
    parser.add_argument("--dry-run", action="store_true", help="Validate eval suite & promotion gate offline")
    parser.add_argument("--json", action="store_true", dest="as_json", help="Emit JSON output")
    args = parser.parse_args(argv)

    eval_path = ensure_eval_set(Path(args.eval_set))
    if args.dry_run:
        return _run_dry_run(eval_path, as_json=args.as_json)

    items = load_eval_items(eval_path)
    if args.limit > 0:
        items = items[: args.limit]

    base_metrics = evaluate_model(args.base, items, args.base_url, timeout=args.timeout)
    if args.candidate == args.base:
        # Useful for live smoke testing a single local model without double compute
        cand_metrics = dict(base_metrics, model=f"{args.candidate} (self-smoke)")
    else:
        cand_metrics = evaluate_model(args.candidate, items, args.base_url, timeout=args.timeout)

    gate = evaluate_promotion_gate(
        base_metrics,
        cand_metrics,
        max_latency_ratio=args.max_latency_ratio,
    )
    report = {
        "base": base_metrics,
        "candidate": cand_metrics,
        "gate": gate,
    }

    if args.as_json:
        print(json.dumps(report, indent=2, ensure_ascii=False))
    else:
        print("=== Mark-LVII Local Model Promotion Gate ===")
        for label, m in (("BASE", base_metrics), ("CANDIDATE", cand_metrics)):
            tc_str = f"{m['tool_call_valid']}/{m['tool_call_items']} ({m['tool_call_valid_rate']:.1%})" if m["tool_call_items"] else "n/a"
            print(
                f"  {label:<9} {m['model']:<24} | acc={m['passed_items']}/{m['total_items']} "
                f"({m['accuracy_rate']:.1%}) | tool_json={tc_str} | median_lat={m['median_latency_s']:.2f}s"
            )
        print(f"\n  VERDICT: {gate['verdict']}")
        for r in gate["reasons"]:
            print(f"    - {r}")
    return 0 if gate["promote"] else 1


if __name__ == "__main__":
    sys.exit(main())
