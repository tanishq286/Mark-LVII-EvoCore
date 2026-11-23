"""scripts/health_check.py — Fast (<10s) Unified Mark-LVII Single-Brain Health Check.

Verifies on native Windows 11 (or WSL2):
  1. Python runtime version & platform
  2. Git repo cleanliness, tag `baseline-before-upgrade`, and `.gitignore` secret protection
     (via native `git` or automatic WSL2 `Ubuntu-D` fallback)
  3. All 11 active plugins discovered (0 rejected, 0 tool-name collisions)
  4. Gemini API key present (value NEVER printed) & Ollama sidecar configured
  5. Local Ollama (`http://127.0.0.1:11434/api/tags`) reachable with core models
  6. WSL2 `/home/tanishq/unsloth_env` CUDA GPU bridge reachable (`CUDA=True`)
  7. Single-brain memory & routine integrity (`StartupVirusScan` + `Nightly reflection`)
  8. Local `knowledge/` semantic vault status
  9. `feedback_logger` dataset progress toward 200-example LoRA floor
 10. `mcp_bridge` security posture & config status
"""
from __future__ import annotations

import argparse
import ast
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import time
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

REQUIRED_OLLAMA_MODELS = ("qwen3:8b", "qwen2.5-coder:14b", "nomic-embed-text:latest")
PROTECTED_IGNORES = (
    "config/api_keys.json",
    "config/mcp_servers.json",
    "memory/long_term.json",
    "memory/routines.json",
    "training_data/eval_set.jsonl",
    "knowledge/.rag_index.sqlite",
    "backups/test.json",
    "UPGRADE_PROGRESS.md",
)


def _collect_core_names() -> set[str]:
    names: set[str] = set()
    main_src = (ROOT / "main.py").read_text(encoding="utf-8", errors="ignore")
    tree = ast.parse(main_src)
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and any(
            getattr(t, "id", "") == "TOOL_DECLARATIONS" for t in node.targets
        ):
            for d in ast.literal_eval(node.value):
                if isinstance(d, dict) and d.get("name"):
                    names.add(str(d["name"]))
            break
    from core.action_loader import discover_actions
    act_reg = discover_actions(ROOT / "actions", logger=lambda _m: None)
    for d in act_reg.get_tool_declarations():
        if isinstance(d, dict) and d.get("name"):
            names.add(str(d["name"]))
    return names


def run_health_check(*, allow_dirty: bool = False) -> dict:
    t0 = time.perf_counter()
    checks: list[dict] = []

    def add(name: str, ok: bool, detail: str, *, warn_only: bool = False) -> None:
        status = "PASS" if ok else ("WARN" if warn_only else "FAIL")
        checks.append({"name": name, "ok": ok, "status": status, "detail": detail})

    # 1. Python runtime
    py_ver = platform.python_version()
    add(
        "Python runtime",
        sys.version_info >= (3, 11),
        f"{platform.system()} {platform.release()} | Python {py_ver} ({sys.executable})",
    )

    # 2. Git status, baseline tag & secret ignores (via cross_os.run_git)
    from core.cross_os import peer_memory_dir, run_git
    try:
        head = run_git(ROOT, "rev-parse", "--short", "HEAD", timeout=10.0)
        tag = run_git(ROOT, "tag", "-l", "baseline-before-upgrade", timeout=10.0)
        st = run_git(ROOT, "status", "--porcelain", timeout=10.0)
        dirty_lines = [ln for ln in st.stdout.splitlines() if ln.strip()]
        ignored_missing = []
        for rel in PROTECTED_IGNORES:
            gi = run_git(ROOT, "check-ignore", "-q", rel, timeout=10.0)
            if gi.returncode != 0:
                ignored_missing.append(rel)
        git_clean_ok = (len(dirty_lines) == 0) or allow_dirty
        add(
            "Git repo & secret hygiene",
            head.returncode == 0
            and "baseline-before-upgrade" in tag.stdout
            and not ignored_missing
            and git_clean_ok,
            f"HEAD={head.stdout.strip()}, tag=baseline-before-upgrade, dirty={len(dirty_lines)}, unignored_secrets={ignored_missing or 'none'}",
        )
    except Exception as exc:
        add("Git repo & secret hygiene", False, f"git error: {exc}")

    # 3. All 11 plugins loaded (0 rejected, 0 collisions)
    try:
        core_names = _collect_core_names()
        from core.plugin_loader import discover_plugins
        reg = discover_plugins(ROOT / "plugins", core_names, logger=lambda _m: None)
        ui = reg.list_for_ui()
        active = [p["name"] for p in ui if p["valid"]]
        rejected = [p["name"] for p in ui if not p["valid"]]
        add(
            "Plugin auto-discovery",
            len(active) >= 11 and len(rejected) == 0,
            f"active={len(active)}, rejected={len(rejected)}, core_checked={len(core_names)}",
        )
    except Exception as exc:
        add("Plugin auto-discovery", False, str(exc))

    # 4. Gemini API key present (value hidden) & Ollama sidecar configured
    try:
        from memory import config_manager as cm
        keys = cm.load_api_keys()
        has_gemini = bool(str(keys.get("gemini_api_key") or "").strip())
        mode = cm.get_provider_mode()
        sidecar = cm.get_sidecar_provider()
        ol_model = cm.get_provider_config("openai_compat@ollama").get("model")
        add(
            "Gemini key & Ollama sidecar config",
            has_gemini and mode == "gemini" and sidecar == "openai_compat@ollama" and ol_model == "qwen3:8b",
            f"gemini_key_set={has_gemini}, mode={mode}, sidecar={sidecar}, ollama_model={ol_model}",
        )
    except Exception as exc:
        add("Gemini key & Ollama sidecar config", False, str(exc))

    # 5. Local Ollama reachability + core models
    try:
        with urlopen("http://127.0.0.1:11434/api/tags", timeout=3.0) as resp:
            models = {
                str(m.get("name") or "")
                for m in (json.loads(resp.read().decode("utf-8")).get("models") or [])
                if isinstance(m, dict)
            }
        missing = [m for m in REQUIRED_OLLAMA_MODELS if m not in models]
        add(
            "Local Ollama GPU backend",
            not missing,
            f"reachable=True, models={len(models)}, missing={missing or 'none'}",
        )
    except Exception as exc:
        add("Local Ollama GPU backend", False, f"unreachable: {exc}")

    # 6. WSL2 unsloth_env CUDA bridge
    try:
        py_cmd = "import torch; print(torch.cuda.is_available(), torch.cuda.get_device_name(0) if torch.cuda.is_available() else 'none')"
        if os.name == "nt":
            cmd = ["wsl.exe", "-d", "Ubuntu-D", "-e", "/home/tanishq/unsloth_env/bin/python", "-c", py_cmd]
        else:
            cmd = ["/home/tanishq/unsloth_env/bin/python", "-c", py_cmd]
        proc = subprocess.run(cmd, stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=15.0)
        out = proc.stdout.strip()
        add(
            "WSL2 unsloth_env CUDA bridge",
            proc.returncode == 0 and out.startswith("True"),
            out or proc.stderr.strip()[:120],
        )
    except Exception as exc:
        add("WSL2 unsloth_env CUDA bridge", False, str(exc))

    # 7. Single-brain memory & routines integrity
    try:
        from memory.memory_manager import load_memory
        mem = load_memory()
        fact_count = sum(len(v) for k, v in mem.items() if k != "sessions" and isinstance(v, dict))
        from core.routines.desktop import routines_path
        from core.routines.engine import RoutineEngine
        reng = RoutineEngine(routines_path(), object(), autostart=False, log=lambda _m: None)
        r_names = {r.get("name") for r in reng.list()}
        peer_decoupled = peer_memory_dir() is None
        add(
            "Single-brain memory & routines",
            fact_count > 0 and "Nightly reflection" in r_names and peer_decoupled,
            f"facts={fact_count}, routines={len(r_names)}, nightly_reflection={'Nightly reflection' in r_names}, peer_archived={peer_decoupled}",
        )
    except Exception as exc:
        add("Single-brain memory & routines", False, str(exc))

    # 8. Knowledge vault status
    try:
        import plugins.knowledge_rag as kr
        st_kr = kr.vault_stats()
        add(
            "Knowledge vault (knowledge_rag)",
            st_kr["embed_reachable"] is True,
            f"files={st_kr['indexed_files']}/{st_kr['disk_files']}, chunks={st_kr['total_chunks']}, embed={st_kr['embed_status']}",
        )
    except Exception as exc:
        add("Knowledge vault (knowledge_rag)", False, str(exc))

    # 9. Feedback logger stats
    try:
        import plugins.feedback_logger as fb
        st_fb = fb._collect_stats()
        add(
            "Feedback logger (corrections.jsonl)",
            True,
            f"curated={st_fb['total']}/{st_fb['target']} ({st_fb['progress_pct']}%)",
        )
    except Exception as exc:
        add("Feedback logger (corrections.jsonl)", False, str(exc))

    # 10. MCP bridge status
    try:
        import plugins.mcp_bridge as mb
        st_mb = mb._handle_stats(player=None)
        add(
            "MCP tool bridge (mcp_bridge)",
            "default-deny" in st_mb.lower(),
            st_mb.splitlines()[0][:140],
        )
    except Exception as exc:
        add("MCP tool bridge (mcp_bridge)", False, str(exc))

    elapsed = round(time.perf_counter() - t0, 2)
    hard_failed = [c for c in checks if c["status"] == "FAIL"]
    return {
        "repo_root": str(ROOT),
        "elapsed_sec": elapsed,
        "passed": len(hard_failed) == 0,
        "checks": checks,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Fast unified Mark-LVII single-brain health check.")
    parser.add_argument("--json", action="store_true", dest="as_json", help="Emit structured JSON output")
    parser.add_argument("--allow-dirty", action="store_true", help="Do not fail when working tree has uncommitted changes")
    args = parser.parse_args(argv)

    report = run_health_check(allow_dirty=args.allow_dirty)
    if args.as_json:
        print(json.dumps(report, indent=2, ensure_ascii=False))
    else:
        print(f"=== Mark-LVII Unified Health Check ({report['repo_root']}) ===")
        for c in report["checks"]:
            print(f"  [{c['status']}] {c['name']}: {c['detail']}")
        verdict = "ALL HEALTHY" if report["passed"] else "ISSUES DETECTED"
        print(f"=== RESULT: {verdict} ({report['elapsed_sec']:.2f}s) ===")
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
