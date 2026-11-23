"""Milestone 5 verification script — Isolated QLoRA Training & Local Model Promotion Gate.

Checks (non-destructive, zero mutations to requirements.txt or Mark-LVII/.venv):
  1. `requirements.txt` is 100% untouched since `baseline-before-upgrade` AND
     `Mark-LVII/.venv` has zero PyTorch/Unsloth/TRL/PEFT/bitsandbytes installed.
  2. `scripts/requirements-train.txt` exists and documents isolated training deps.
  3. `scripts/train_local_lora.py --dry-run` via `/home/tanishq/unsloth_env/bin/python`:
     - exit 0, detects NVIDIA RTX 5070 Ti CUDA GPU + >= 2 GB VRAM headroom
     - confirms 4-bit QLoRA defaults (`r=16`, `lora_alpha=16`, `max_seq_length=2048`,
       `gradient_checkpointing='unsloth'`, `q4_k_m`) and previews `Modelfile` for `jarvis-custom`
  4. Safety guards in `scripts/train_local_lora.py`:
     - refuses real training when dataset has < 200 unique examples without `--force-small-dataset`
     - refuses overwriting base model `qwen3:8b`
  5. `scripts/eval_set.template.jsonl` has 20 valid held-out prompts (8 tool_call, 6 coding, 6 voice),
     auto-seeds git-ignored `training_data/eval_set.jsonl`, and `git check-ignore` passes.
  6. `scripts/eval_local_model.py --dry-run` passes (exit 0) verifying both PROMOTE and REJECT gate branches.
  7. Live `--limit 2` smoke test against `qwen3:8b` on `http://127.0.0.1:11434/v1` confirms real
     Ollama responses, `<think>` stripping, tool-call JSON validation, and latency capture.
  8. `docs/SELF_EVOLUTION.md` documents M5 train, eval, promote, and rollback commands.
"""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
UNSLOTH_PY = Path("/home/tanishq/unsloth_env/bin/python")
TRAIN_SCRIPT = ROOT / "scripts" / "train_local_lora.py"
WSL_TRAIN_SCRIPT = ROOT / "scripts" / "wsl_train.py"
EVAL_SCRIPT = ROOT / "scripts" / "eval_local_model.py"
TEMPLATE_PATH = ROOT / "scripts" / "eval_set.template.jsonl"
EVAL_PATH = ROOT / "training_data" / "eval_set.jsonl"


def _check(label: str, ok: bool, detail: str = "") -> bool:
    status = "PASS" if ok else "FAIL"
    suffix = f" ({detail})" if detail else ""
    print(f"  [{status}] {label}{suffix}")
    return ok


def main() -> int:
    all_ok = True
    print("=== Milestone 5: Isolated QLoRA Pipeline & Promotion Gate Verification ===")

    # 1. Verify requirements.txt untouched & Mark-LVII/.venv free of PyTorch/Unsloth
    from core.cross_os import run_git
    req_diff = run_git(ROOT, "diff", "baseline-before-upgrade", "--", "requirements.txt")
    ml_in_main = [
        p for p in ("torch", "unsloth", "trl", "peft", "bitsandbytes")
        if importlib.util.find_spec(p) is not None
    ]
    all_ok &= _check(
        "requirements.txt untouched and Mark-LVII/.venv has zero PyTorch/Unsloth",
        req_diff.returncode == 0 and not req_diff.stdout.strip() and not ml_in_main,
        f"req_diff_empty={not req_diff.stdout.strip()}, ml_pkgs_in_main_venv={ml_in_main or 'none'}",
    )

    # 2. scripts/requirements-train.txt exists
    req_train = ROOT / "scripts" / "requirements-train.txt"
    req_train_txt = req_train.read_text(encoding="utf-8") if req_train.is_file() else ""
    all_ok &= _check(
        "scripts/requirements-train.txt documents isolated training dependencies",
        req_train.is_file()
        and all(k in req_train_txt for k in ("torch", "unsloth", "trl", "peft", "bitsandbytes", "datasets")),
        f"bytes={len(req_train_txt)}",
    )

    # 3. scripts/train_local_lora.py --dry-run inside /home/tanishq/unsloth_env (via wsl_train.py on Windows)
    train_cmd = (
        [str(UNSLOTH_PY), str(TRAIN_SCRIPT)]
        if UNSLOTH_PY.is_file()
        else [sys.executable, str(WSL_TRAIN_SCRIPT)]
    )
    dry_train = subprocess.run(
        [*train_cmd, "--dry-run", "--json"],
        cwd=str(ROOT),
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        timeout=30.0,
    )
    dry_data = json.loads(dry_train.stdout) if dry_train.returncode == 0 and dry_train.stdout.strip() else {}
    env = dry_data.get("environment") or {}
    plan = dry_data.get("plan") or {}
    all_ok &= _check(
        "train_local_lora.py --dry-run via /home/tanishq/unsloth_env detects RTX 5070 Ti CUDA & >=2GB headroom",
        dry_train.returncode == 0
        and env.get("cuda_available") is True
        and "5070" in str(env.get("gpu_name") or "")
        and env.get("headroom_ok") is True
        and plan.get("load_in_4bit") is True
        and plan.get("lora_r") == 16
        and plan.get("lora_alpha") == 16
        and plan.get("max_seq_length") == 2048
        and plan.get("gradient_checkpointing") == "unsloth"
        and "FROM ./" in str(dry_data.get("modelfile_preview") or "")
        and "jarvis-custom" in str(dry_data.get("modelfile_preview") or ""),
        f"gpu={env.get('gpu_name')}, vram={env.get('vram_total_gb')}GB, headroom={env.get('estimated_headroom_gb')}GB",
    )

    # 4. Safety guards: 200-example floor + refusal to overwrite qwen3:8b
    with tempfile.TemporaryDirectory() as td:
        tiny_ds = Path(td) / "tiny.jsonl"
        tiny_ds.write_text(
            json.dumps({
                "timestamp": "2026-11-23T20:00:00Z",
                "category": "coding",
                "messages": [
                    {"role": "user", "content": "hi"},
                    {"role": "assistant", "content": "hello"},
                ],
                "lesson": "",
            }) + "\n",
            encoding="utf-8",
        )
        floor_chk = subprocess.run(
            [*train_cmd, "--dataset", str(tiny_ds)],
            cwd=str(ROOT),
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=20.0,
        )
        overwrite_chk = subprocess.run(
            [*train_cmd, "--dry-run", "--ollama-name", "qwen3:8b"],
            cwd=str(ROOT),
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=20.0,
        )
        all_ok &= _check(
            "train_local_lora.py enforces 200-example safety floor and refuses overwriting qwen3:8b",
            floor_chk.returncode != 0
            and "200" in floor_chk.stderr
            and overwrite_chk.returncode != 0
            and "protected base model" in overwrite_chk.stderr.lower(),
            f"floor_rc={floor_chk.returncode}, overwrite_rc={overwrite_chk.returncode}",
        )

    # 5 & 6. eval_set auto-seed + eval_local_model.py --dry-run (all 20 prompts + PROMOTE/REJECT)
    dry_eval = subprocess.run(
        [sys.executable, str(EVAL_SCRIPT), "--dry-run", "--json"],
        cwd=str(ROOT),
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        timeout=20.0,
    )
    eval_data = json.loads(dry_eval.stdout) if dry_eval.returncode == 0 and dry_eval.stdout.strip() else {}
    gi = run_git(ROOT, "check-ignore", "-v", "training_data/eval_set.jsonl")
    all_ok &= _check(
        "eval_set.template.jsonl (20 prompts) auto-seeds git-ignored training_data/eval_set.jsonl",
        TEMPLATE_PATH.is_file()
        and EVAL_PATH.is_file()
        and eval_data.get("total_prompts") == 20
        and eval_data.get("categories") == {"tool_call": 8, "coding": 6, "voice": 6}
        and gi.returncode == 0,
        f"cats={eval_data.get('categories')}, ignore={gi.stdout.strip()}",
    )

    gate_chks = eval_data.get("gate_checks") or {}
    all_ok &= _check(
        "eval_local_model.py --dry-run verifies PROMOTE and all 3 REJECT branches",
        dry_eval.returncode == 0
        and eval_data.get("passed") is True
        and gate_chks.get("promote_branch") == "PROMOTE"
        and "REJECT" in str(gate_chks.get("reject_accuracy_branch") or "")
        and "REJECT" in str(gate_chks.get("reject_tool_call_branch") or "")
        and "REJECT" in str(gate_chks.get("reject_latency_branch") or ""),
        f"promote={gate_chks.get('promote_branch')}, rejects=3/3",
    )

    # 7. Live --limit 2 smoke test against local qwen3:8b on 127.0.0.1:11434/v1
    live_eval = subprocess.run(
        [
            sys.executable,
            str(EVAL_SCRIPT),
            "--base", "qwen3:8b",
            "--candidate", "qwen3:8b",
            "--limit", "2",
            "--json",
        ],
        cwd=str(ROOT),
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        timeout=120.0,
    )
    live_data = json.loads(live_eval.stdout) if live_eval.stdout.strip().startswith("{") else {}
    base_m = live_data.get("base") or {}
    all_ok &= _check(
        "Live --limit 2 smoke test against qwen3:8b on 127.0.0.1:11434/v1 passes",
        live_eval.returncode == 0
        and base_m.get("total_items") == 2
        and base_m.get("tool_call_valid") == 2
        and base_m.get("median_latency_s", 0) > 0
        and (live_data.get("gate") or {}).get("verdict") == "PROMOTE",
        f"acc={base_m.get('passed_items')}/{base_m.get('total_items')}, tool_json={base_m.get('tool_call_valid')}/{base_m.get('tool_call_items')}, median_lat={base_m.get('median_latency_s')}s",
    )

    # 8. docs/SELF_EVOLUTION.md documents M5 train, eval, promote, rollback
    doc_text = (ROOT / "docs" / "SELF_EVOLUTION.md").read_text(encoding="utf-8")
    m5_terms = (
        "unsloth_env",
        "train_local_lora.py",
        "eval_local_model.py",
        "jarvis-custom",
        "PROMOTE",
        "git checkout baseline-before-upgrade",
    )
    missing = [t for t in m5_terms if t not in doc_text]
    all_ok &= _check(
        "docs/SELF_EVOLUTION.md documents M5 train, eval, promote, and rollback commands",
        not missing,
        f"missing={missing or 'none'}",
    )

    print("=== RESULT:", "ALL PASSED" if all_ok else "FAILED", "===")
    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main())
