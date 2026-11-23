"""Milestone 7 verification script — 100% Free Local Semantic Knowledge Vault (RAG).

Checks (zero paid APIs, zero new pip deps, isolated temp knowledge folder):
  1. `git check-ignore -v knowledge/.rag_index.sqlite` passes (privacy guarantee).
  2. `scripts/validate_plugin.py plugins/knowledge_rag.py` passes with exit 0 and 0 warnings.
  3. 10D plugin quality gate >= 75/100 (`VERIFIED`) + live plugin auto-discovery
     (`10 active, 0 rejected, 0 collisions`).
  4. Isolated temp knowledge vault end-to-end checks with LIVE `nomic-embed-text:latest`:
     - Empty-folder graceful handling (`mode='stats'`, `mode='index'`, `mode='search'`, `player=None`)
     - Initial indexing of `.md`, `.py`, `.json` files + symlink escape blocked (points outside vault)
     - Incremental no-op re-index (unchanged files skipped via SHA-256, `unchanged == 3`, `indexed_new == 0`)
     - Incremental update on file edit (`updated == 1`, `unchanged == 2`)
     - Pruning on file delete (`pruned == 1`, `total_files == 2`)
     - Live semantic retrieval returns the exact right file + `knowledge/<rel>:L<start>-L<end>`
       citation and wraps excerpts inside `<<<UNTRUSTED source=knowledge_rag>>>`
  5. `docs/SELF_EVOLUTION.md` documents `knowledge/` vault usage.
"""
from __future__ import annotations

import ast
import json
from pathlib import Path
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def _check(label: str, ok: bool, detail: str = "") -> bool:
    status = "PASS" if ok else "FAIL"
    suffix = f" ({detail})" if detail else ""
    print(f"  [{status}] {label}{suffix}")
    return ok


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


def main() -> int:
    all_ok = True
    print("=== Milestone 7: Free Local Semantic Knowledge Vault (knowledge_rag) Verification ===")

    # 1. Privacy / gitignore check
    from core.cross_os import run_git
    gi = run_git(ROOT, "check-ignore", "-v", "knowledge/.rag_index.sqlite")
    all_ok &= _check(
        "git check-ignore -v knowledge/.rag_index.sqlite passes",
        gi.returncode == 0 and "knowledge/" in gi.stdout,
        gi.stdout.strip(),
    )

    # 2. scripts/validate_plugin.py passes with exit 0 and 0 warnings
    val = subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts" / "validate_plugin.py"),
            str(ROOT / "plugins" / "knowledge_rag.py"),
            "--repo-root", str(ROOT),
            "--json",
        ],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        timeout=25.0,
    )
    vdata = json.loads(val.stdout) if val.stdout.strip().startswith("{") else {}
    all_ok &= _check(
        "scripts/validate_plugin.py passes on plugins/knowledge_rag.py (exit=0, warnings=0)",
        val.returncode == 0
        and vdata.get("passed") is True
        and not (vdata.get("errors") or [])
        and not (vdata.get("warnings") or [])
        and (vdata.get("checks") or {}).get("has_concise_brief") is True,
        f"exit={val.returncode}, warnings={len(vdata.get('warnings') or [])}, tool={vdata.get('plugin_name')}",
    )

    # 3. 10D score + live plugin auto-discovery (10 active, 0 rejected, 0 collisions)
    from core.auto_evolution import evaluate_module_10d
    kr_code = (ROOT / "plugins" / "knowledge_rag.py").read_text(encoding="utf-8")
    sc = evaluate_module_10d(kr_code, is_plugin=True)
    all_ok &= _check(
        "plugins/knowledge_rag.py passes 10D quality gate (>= 75/100)",
        sc["passed"],
        f"combined={sc['combined']}, tq={sc['tq_score']}, omega={sc['omega_score']}",
    )

    core_names = _collect_core_names()
    from core.plugin_loader import discover_plugins
    reg = discover_plugins(ROOT / "plugins", core_names, logger=lambda _m: None)
    ui_list = reg.list_for_ui()
    active = [p for p in ui_list if p["valid"]]
    rejected = [p for p in ui_list if not p["valid"]]
    active_names = sorted(p["name"] for p in active)
    all_ok &= _check(
        "Plugin auto-discovery: >=10 active, 0 rejected, zero collisions",
        len(active) >= 10 and len(rejected) == 0 and "knowledge_rag" in active_names,
        f"active={len(active)}, rejected={len(rejected)}, core_checked={len(core_names)}",
    )

    # 4. Isolated temp knowledge vault end-to-end checks with LIVE nomic-embed-text:latest
    import plugins.knowledge_rag as kr

    with tempfile.TemporaryDirectory() as td:
        base = Path(td)
        vault = base / "knowledge"
        vault.mkdir()
        outside_secret = base / "outside_secret.txt"
        outside_secret.write_text("TOP_SECRET_API_TOKEN=DO_NOT_INDEX_THIS", encoding="utf-8")

        # 4a. Empty folder graceful handling (player=None)
        empty_stats = kr._handle_stats(player=None, vault_dir=vault)
        empty_idx = kr._handle_index(player=None, vault_dir=vault)
        empty_search = kr._handle_search("quantum gravity", 3, player=None, vault_dir=vault)
        all_ok &= _check(
            "Empty knowledge vault handles stats/index/search gracefully with player=None",
            "0 indexed" in empty_stats
            and "empty" in empty_idx.lower()
            and "No indexed knowledge chunks" in empty_search,
        )

        # 4b. Seed 3 diverse docs + 1 symlink pointing outside vault (must be blocked!)
        doc_gpu = vault / "hardware_notes.md"
        doc_gpu.write_text(
            "# RTX 5070 Ti Tuning Guide\n\n"
            "The laptop GPU has 12 GB VRAM.\n"
            "For QLoRA fine-tuning, use 4-bit NF4 quantization with rank 16 and Unsloth gradient checkpointing.\n"
            "Always keep at least 2 GB of VRAM headroom for Windows and the desktop HUD.\n",
            encoding="utf-8",
        )
        doc_deploy = vault / "projects" / "finsol_deploy.md"
        doc_deploy.parent.mkdir(parents=True, exist_ok=True)
        doc_deploy.write_text(
            "# Finsol Enterprises Deployment Notes\n\n"
            "Vercel deployment requires setting the Root Directory to apps/web.\n"
            "Database tables use partitioned PostgreSQL schemas for high-throughput loan recovery search.\n",
            encoding="utf-8",
        )
        doc_py = vault / "snippets.py"
        doc_py.write_text(
            "\"\"\"Helper utilities.\"\"\"\n"
            "def safe_backoff(attempt: int) -> float:\n"
            "    return min(30.0, 2.0 ** attempt)\n",
            encoding="utf-8",
        )
        sym_escape = vault / "escape_link.txt"
        try:
            sym_escape.symlink_to(outside_secret)
            sym_created = True
        except OSError:
            sym_created = False

        # Initial index
        r1 = kr.index_vault(vault_dir=vault)
        all_ok &= _check(
            "Initial index embeds all 3 safe docs via nomic-embed-text:latest and blocks symlink escape",
            r1["indexed_new"] == 3
            and r1["total_files"] == 3
            and r1["total_chunks"] >= 3
            and (not sym_created or r1["rejected_symlinks"] == 1),
            f"new={r1['indexed_new']}, chunks={r1['total_chunks']}, rejected_symlinks={r1['rejected_symlinks']}",
        )

        # Incremental no-op re-index (SHA-256 unchanged -> 0 re-embeds!)
        r2 = kr.index_vault(vault_dir=vault)
        all_ok &= _check(
            "Incremental re-index skips unchanged files via SHA-256 (0 re-embeds)",
            r2["indexed_new"] == 0 and r2["updated"] == 0 and r2["unchanged"] == 3 and r2["pruned"] == 0,
            f"unchanged={r2['unchanged']}, updated={r2['updated']}",
        )

        # Edit one file -> only 1 file updated!
        doc_deploy.write_text(
            doc_deploy.read_text(encoding="utf-8")
            + "\nEmergency rollback procedure: git checkout baseline-before-upgrade.\n",
            encoding="utf-8",
        )
        r3 = kr.index_vault(vault_dir=vault)
        all_ok &= _check(
            "Modified file triggers targeted SHA-256 re-embedding (1 updated, 2 unchanged)",
            r3["updated"] == 1 and r3["unchanged"] == 2 and r3["indexed_new"] == 0,
            f"updated={r3['updated']}, unchanged={r3['unchanged']}",
        )

        # Delete one file -> pruned cleanly!
        doc_py.unlink()
        r4 = kr.index_vault(vault_dir=vault)
        all_ok &= _check(
            "Deleted file is pruned cleanly from SQLite index",
            r4["pruned"] == 1 and r4["total_files"] == 2,
            f"pruned={r4['pruned']}, total_files={r4['total_files']}",
        )

        # Live semantic search + citation + <<<UNTRUSTED source=knowledge_rag>>> wrapping
        hits = kr.search_vault("What Vercel root directory does Finsol Enterprises need?", top_k=2, vault_dir=vault)
        search_out = kr._handle_search(
            "What Vercel root directory does Finsol Enterprises need?",
            top_k=2,
            player=None,
            vault_dir=vault,
        )
        top_hit = hits[0] if hits else {}
        all_ok &= _check(
            "Live semantic search retrieves exact file + line range citation & wraps in <<<UNTRUSTED>>>",
            bool(hits)
            and top_hit.get("rel_path") == "projects/finsol_deploy.md"
            and top_hit.get("citation", "").startswith("knowledge/projects/finsol_deploy.md:L")
            and "apps/web" in top_hit.get("text", "")
            and "TOP_SECRET_API_TOKEN" not in search_out
            and "<<<UNTRUSTED source=knowledge_rag>>>" in search_out
            and "<<<END UNTRUSTED>>>" in search_out,
            f"top={top_hit.get('citation')}, score={top_hit.get('score')}",
        )

        # Stats readout on populated vault
        st = kr.vault_stats(vault_dir=vault)
        all_ok &= _check(
            "vault_stats() reports accurate file/chunk counts, DB size, and reachable nomic-embed-text",
            st["indexed_files"] == 2
            and st["total_chunks"] >= 2
            and st["index_size_bytes"] > 0
            and st["embed_reachable"] is True,
            f"files={st['indexed_files']}, chunks={st['total_chunks']}, db_bytes={st['index_size_bytes']}, embed={st['embed_status']}",
        )

    # 5. docs/SELF_EVOLUTION.md documents knowledge/ vault
    doc_text = (ROOT / "docs" / "SELF_EVOLUTION.md").read_text(encoding="utf-8")
    all_ok &= _check(
        "docs/SELF_EVOLUTION.md documents knowledge/ vault indexing and querying",
        "knowledge_rag" in doc_text and "nomic-embed-text" in doc_text and "knowledge/" in doc_text,
    )

    print("=== RESULT:", "ALL PASSED" if all_ok else "FAILED", "===")
    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main())
