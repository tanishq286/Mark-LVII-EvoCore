"""Milestone 6 verification script — Free Local Specialist Router & 12 GB VRAM Guard.

Checks (100% free / zero paid APIs, non-destructive, zero secrets printed):
  1. Lazy loading check: importing `core.gemini` does NOT import `providers.*`.
  2. Deterministic classification unit tests:
     - fast chat / summaries / tool calls -> `fast` (`qwen3:8b`)
     - coding / debugging / refactoring / scripts -> `coding` (`qwen2.5-coder:14b`)
     - deep reasoning / architecture / trade-offs -> `reasoning` (`qwen2.5:14b` / `gemma4:12b`)
  3. Clean fallback when a specialist model is absent (e.g., `qwen2.5:14b` absent -> `gemma4:12b`;
     both 14B/12B absent -> `qwen3:8b`).
  4. Mock Ollama VRAM-switch safety guard test: verifies `/api/ps` inspection + `keep_alive: 0`
     unload sent to `/api/generate` for competing LLM while keeping tiny `nomic-embed-text` warm.
  5. `<think>` block stripping across routed responses (closed, multi-tag, unterminated).
  6. Live RTX 5070 Ti Ollama probes on `http://127.0.0.1:11434`:
     - verifies `qwen3:8b` (fast) responds cleanly via `local_router.complete_local()`
     - switches to `qwen2.5-coder:14b` (coding) verifying `qwen3:8b` is unloaded first via `keep_alive: 0`
       so both never collide in 12 GB VRAM, and verifies clean code answer + post-switch `/api/ps`
     - unloads `qwen2.5-coder:14b` cleanly afterwards so GPU VRAM is left free for daily use.
"""
from __future__ import annotations

from http.server import BaseHTTPRequestHandler, HTTPServer
import json
from pathlib import Path
import subprocess
import sys
import threading

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def _check(label: str, ok: bool, detail: str = "") -> bool:
    status = "PASS" if ok else "FAIL"
    suffix = f" ({detail})" if detail else ""
    print(f"  [{status}] {label}{suffix}")
    return ok


def main() -> int:
    all_ok = True
    print("=== Milestone 6: Free Local Specialist Router & 12 GB VRAM Guard Verification ===")

    # 1. Lazy import check in a clean subprocess
    lazy_code = (
        f"import sys, json; sys.path.insert(0, {str(ROOT)!r}); "
        "import core.gemini as g; "
        "mods = [k for k in sys.modules if k.startswith('providers')]; "
        "print(json.dumps({'mods': mods}))"
    )
    r = subprocess.run([sys.executable, "-c", lazy_code], capture_output=True, text=True, check=True)
    lazy_info = json.loads(r.stdout.strip().splitlines()[-1])
    all_ok &= _check(
        "providers/ (including local_router) stays 100% lazy-loaded at startup",
        lazy_info["mods"] == [],
        f"loaded={lazy_info['mods']}",
    )

    from providers import local_router as lr

    # 2. Classification unit tests (fast vs coding vs reasoning)
    installed_full = {"qwen3:8b", "qwen2.5-coder:14b", "qwen2.5:14b", "gemma4:12b", "nomic-embed-text:latest"}
    fast_cases = [
        ("Summarize this status in one sentence: server is up.", "fast"),
        ("Reply with one word: READY", "fast"),
        ("Extract the city name from: Meeting in Tokyo tomorrow.", "fast"),
    ]
    coding_cases = [
        ("Refactor this Python function in plugins/feedback_logger.py to avoid globals.", "smart"),
        ("Fix SyntaxError in my bash script: ```python\ndef foo(x):\n return x+1\n```", "fast"),
        ("Write a pytest unit test for subprocess.run argument escaping.", "smart"),
    ]
    reasoning_cases = [
        ("Analyze the architectural trade-offs between SQLite RAG and external vector DBs.", "smart"),
        ("Conduct a root-cause analysis and threat model for an MCP stdio bridge.", "smart"),
        ("Compare pros and cons of QLoRA rank 16 vs rank 32 step by step.", "fast"),
    ]

    fast_ok = all(
        lr.route_prompt(p, tier=t, available_models=installed_full)[0].category == lr.CATEGORY_FAST
        and lr.route_prompt(p, tier=t, available_models=installed_full)[0].selected_model == "qwen3:8b"
        for p, t in fast_cases
    )
    coding_ok = all(
        lr.route_prompt(p, tier=t, available_models=installed_full)[0].category == lr.CATEGORY_CODING
        and lr.route_prompt(p, tier=t, available_models=installed_full)[0].selected_model == "qwen2.5-coder:14b"
        for p, t in coding_cases
    )
    reasoning_ok = all(
        lr.route_prompt(p, tier=t, available_models=installed_full)[0].category == lr.CATEGORY_REASONING
        and lr.route_prompt(p, tier=t, available_models=installed_full)[0].selected_model == "qwen2.5:14b"
        for p, t in reasoning_cases
    )
    all_ok &= _check("Fast prompts route to qwen3:8b (3/3)", fast_ok)
    all_ok &= _check("Coding prompts route to qwen2.5-coder:14b (3/3)", coding_ok)
    all_ok &= _check("Deep reasoning prompts route to qwen2.5:14b (3/3)", reasoning_ok)

    # 3. Fallback when specialist models are absent
    d_gemma, _ = lr.route_prompt(
        "Analyze the architectural trade-offs of local inference.",
        tier="smart",
        available_models={"gemma4:12b", "qwen3:8b"},
    )
    d_qwen_only, _ = lr.route_prompt(
        "Refactor this Python module and write unit tests.",
        tier="smart",
        available_models={"qwen3:8b"},
    )
    all_ok &= _check(
        "Graceful fallback when specialist model absent (qwen2.5:14b -> gemma4:12b; coding -> qwen3:8b)",
        d_gemma.selected_model == "gemma4:12b" and d_qwen_only.selected_model == "qwen3:8b",
        f"reasoning_fb={d_gemma.selected_model}, coding_fb={d_qwen_only.selected_model}",
    )

    # 4. Mock Ollama VRAM-switch safety guard test
    unloaded_requests: list[dict] = []

    class MockOllama(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path == "/api/ps":
                body = json.dumps({
                    "models": [
                        {"name": "qwen3:8b", "size_vram": 5_400_000_000},
                        {"name": "nomic-embed-text:latest", "size_vram": 280_000_000},
                    ]
                }).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            self.send_response(404)
            self.end_headers()

        def do_POST(self):
            length = int(self.headers.get("Content-Length", "0"))
            data = json.loads(self.rfile.read(length).decode("utf-8"))
            if self.path == "/api/generate":
                unloaded_requests.append(data)
                body = json.dumps({"done": True}).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            self.send_response(404)
            self.end_headers()

        def log_message(self, *_a):
            pass

    srv = HTTPServer(("127.0.0.1", 0), MockOllama)
    port = srv.server_address[1]
    th = threading.Thread(target=srv.serve_forever, daemon=True)
    th.start()
    try:
        vram_res = lr.ensure_vram_ready("qwen2.5-coder:14b", f"http://127.0.0.1:{port}")
    finally:
        srv.shutdown()
        srv.server_close()

    all_ok &= _check(
        "VRAM guard sends keep_alive=0 to evict competing 8B LLM while preserving tiny embedder",
        vram_res["unloaded"] == ["qwen3:8b"]
        and len(unloaded_requests) == 1
        and unloaded_requests[0].get("model") == "qwen3:8b"
        and unloaded_requests[0].get("keep_alive") == 0,
        f"unloaded={vram_res['unloaded']}, reqs={unloaded_requests}",
    )

    # 5. <think> stripping check
    strip_ok = (
        lr.strip_thinking("<think>chain</think>OK") == "OK"
        and lr.strip_thinking("<reasoning>a</reasoning>B<thinking>c</thinking>") == "B"
        and lr.strip_thinking("Clean.<think>unterminated...") == "Clean."
    )
    all_ok &= _check("Consistent <think>/<thinking>/<reasoning> stripping", strip_ok)

    # 6. Live RTX 5070 Ti Ollama verification: qwen3:8b (fast) -> VRAM-safe switch -> qwen2.5-coder:14b (coding)
    live_models = lr.list_installed_models(use_cache=False) or set()
    all_ok &= _check(
        "Live Ollama inventory has qwen3:8b, qwen2.5-coder:14b, qwen2.5:14b, gemma4:12b",
        {"qwen3:8b", "qwen2.5-coder:14b", "qwen2.5:14b", "gemma4:12b"}.issubset(live_models),
        f"installed={len(live_models)}",
    )

    fast_reply = lr.complete_local(
        "Reply with exactly one word: FAST_OK",
        tier="fast",
        timeout_s=45.0,
    )
    all_ok &= _check(
        "Live fast route answered via qwen3:8b with zero <think> leakage",
        fast_reply is not None
        and fast_reply["model"] == "qwen3:8b"
        and fast_reply["category"] == "fast"
        and "<think" not in fast_reply["text"].lower()
        and bool(fast_reply["text"]),
        f"model={fast_reply['model'] if fast_reply else None}, text={fast_reply['text'][:40] if fast_reply else None!r}",
    )

    coding_reply = lr.complete_local(
        "Write a one-line Python lambda that returns x * 2. Output only code.",
        tier="smart",
        timeout_s=60.0,
    )
    active_after_switch = [m.get("name") for m in lr.get_loaded_models()]
    # Evict heavy 14B after test so daily GPU VRAM returns to idle headroom
    lr.unload_model("qwen2.5-coder:14b")

    all_ok &= _check(
        "Live coding route evicted qwen3:8b first and answered via qwen2.5-coder:14b (no 12 GB VRAM collision)",
        coding_reply is not None
        and coding_reply["model"] == "qwen2.5-coder:14b"
        and coding_reply["category"] == "coding"
        and "qwen3:8b" in (coding_reply.get("unloaded") or [])
        and "qwen3:8b" not in active_after_switch
        and "lambda" in coding_reply["text"].lower()
        and "<think" not in coding_reply["text"].lower(),
        f"model={coding_reply['model'] if coding_reply else None}, unloaded={coding_reply.get('unloaded') if coding_reply else None}, resident_after={active_after_switch}",
    )

    print("=== RESULT:", "ALL PASSED" if all_ok else "FAILED", "===")
    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main())
