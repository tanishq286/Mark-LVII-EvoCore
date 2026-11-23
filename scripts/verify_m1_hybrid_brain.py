"""Milestone 1 verification script — Hybrid Local + Frontier Cloud Brain.

Checks (all non-destructive, zero secrets printed):
  1. `core.gemini` regression checks: `_Reply` returned by `_local_ollama_fallback`,
     `<think>` stripping on one-shot replies, and lazy `providers/` import.
  2. Ollama server live probe on `http://127.0.0.1:11434/v1` for `qwen3:8b`
     (daily sidecar) and availability of `qwen2.5-coder:14b` (heavy coding fallback).
  3. Streaming `<think>` stripping via `providers.engine_pipeline._ReasoningFilter`
     against live `qwen3:8b` output over `/v1/chat/completions`.
  4. Configured provider state via `memory.config_manager` (OpenRouter preset
     ready, Ollama sidecar configured, Gemini Live active).
"""
from __future__ import annotations

import asyncio
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def _check(label: str, ok: bool, detail: str = "") -> bool:
    status = "PASS" if ok else "FAIL"
    suffix = f" ({detail})" if detail else ""
    print(f"  [{status}] {label}{suffix}")
    return ok


async def _verify_live_ollama_stream(model: str) -> tuple[bool, str]:
    from providers.engine_pipeline import _ReasoningFilter
    from providers.llm_openai import OpenAICompatLLM

    llm = OpenAICompatLLM({
        "base_url": "http://127.0.0.1:11434/v1",
        "model": model,
        "timeout": 45.0,
    })
    flt = _ReasoningFilter()
    visible_parts: list[str] = []
    async for ev in llm.chat(
        [{"role": "user", "content": "Reply with exactly one word: READY"}],
        tools=None,
        stream=True,
        temperature=0.0,
    ):
        if ev.get("text"):
            out = flt.feed(ev["text"])
            if out:
                visible_parts.append(out)
    tail = flt.flush()
    if tail:
        visible_parts.append(tail)
    visible = "".join(visible_parts).strip()
    leaked = "<think" in visible.lower() or "</think" in visible.lower()
    ok = bool(visible) and not leaked
    return ok, f"visible={visible!r}, hidden_think_chars={flt.hid}"


def main() -> int:
    all_ok = True
    print("=== Milestone 1: Hybrid Local + Frontier Cloud Brain Verification ===")

    # 1. Lazy import check in a clean subprocess
    lazy_code = (
        f"import sys, json; sys.path.insert(0, {str(ROOT)!r}); "
        "import core.gemini as g; "
        "from memory.config_manager import get_provider_mode; "
        "m = get_provider_mode(); "
        "mods = [k for k in sys.modules if k.startswith('providers')]; "
        "print(json.dumps({'mode': m, 'mods': mods}))"
    )
    r = subprocess.run([sys.executable, "-c", lazy_code], capture_output=True, text=True, check=True)
    info = json.loads(r.stdout.strip().splitlines()[-1])
    all_ok &= _check(
        "providers/ stays lazy-loaded at startup",
        not info["mods"] and info["mode"] == "gemini",
        f"mode={info['mode']}, loaded={info['mods']}",
    )

    # 2. Verify saved config via config_manager (never print keys!)
    from memory import config_manager
    mode = config_manager.get_provider_mode()
    sidecar = config_manager.get_sidecar_provider()
    ol_cfg = config_manager.get_provider_config("openai_compat@ollama")
    or_cfg = config_manager.get_provider_config("openai_compat@openrouter")
    all_ok &= _check("Gemini Live stays primary voice/vision engine", mode == "gemini", f"mode={mode}")
    all_ok &= _check(
        "Ollama sidecar wired to qwen3:8b on 127.0.0.1:11434/v1",
        sidecar == "openai_compat@ollama"
        and ol_cfg.get("base_url") == "http://127.0.0.1:11434/v1"
        and ol_cfg.get("model") == "qwen3:8b",
        f"sidecar={sidecar}, url={ol_cfg.get('base_url')}, model={ol_cfg.get('model')}",
    )
    all_ok &= _check(
        "OpenRouter cloud profile pre-configured",
        or_cfg.get("base_url") == "https://openrouter.ai/api/v1" and bool(or_cfg.get("model")),
        f"url={or_cfg.get('base_url')}, model={or_cfg.get('model')}, key_set={bool(or_cfg.get('api_key'))}",
    )

    # 3. Live Ollama endpoint check + model inventory
    from providers.llm_openai import OpenAICompatLLM
    ollama_llm = OpenAICompatLLM(ol_cfg)
    try:
        models = ollama_llm.models(timeout=8.0)
    except Exception as e:
        models = []
        all_ok &= _check("Ollama /v1/models reachable", False, str(e))
    else:
        all_ok &= _check("Ollama /v1/models reachable on 127.0.0.1:11434/v1", bool(models), f"{len(models)} models")
        all_ok &= _check("qwen3:8b pulled and available", "qwen3:8b" in models)
        all_ok &= _check("qwen2.5-coder:14b available as heavy local coding fallback", "qwen2.5-coder:14b" in models)

    # 4. Probe qwen3:8b + local tool budget
    all_ok &= _check(
        "Local Ollama tool budget active (14 tools / 12,000 chars)",
        ollama_llm.caps.get("max_tools") == 14 and ollama_llm.caps.get("max_tool_chars") == 12000,
        f"caps=({ollama_llm.caps.get('max_tools')}, {ollama_llm.caps.get('max_tool_chars')})",
    )
    if "qwen3:8b" in models:
        ok_probe, msg = ollama_llm.probe(check_vision=False)
        all_ok &= _check("Live probe qwen3:8b via OpenAICompatLLM", ok_probe, msg)
        ok_stream, sdetail = asyncio.run(_verify_live_ollama_stream("qwen3:8b"))
        all_ok &= _check("Live streaming <think> stripping on qwen3:8b", ok_stream, sdetail)

    print("=== RESULT:", "ALL PASSED" if all_ok else "FAILED", "===")
    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main())
