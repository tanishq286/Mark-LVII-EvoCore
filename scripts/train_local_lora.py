"""scripts/train_local_lora.py — Isolated 4-bit QLoRA Fine-Tuning & GGUF Export for Mark-LVII.

Designed specifically for the user's NVIDIA RTX 5070 Ti (12 GB VRAM) and isolated
`/home/tanishq/unsloth_env` environment (never touching `Mark-LVII/.venv` or
`requirements.txt`).

Safety guarantees:
  1. Deduplicates and validates `training_data/corrections.jsonl` (ChatML `messages`).
  2. Enforces a 200-example minimum safety floor by default (preventing overfitting
     and model collapse on tiny datasets unless `--force-small-dataset` is passed).
  3. Tuned for 12 GB VRAM with >= 2 GB headroom (`load_in_4bit=True`, LoRA `r=16`,
     `lora_alpha=16`, `gradient_checkpointing="unsloth"`, micro-batch `1`, grad-accum `8`,
     `max_seq_length=2048`, `adamw_8bit`).
  4. Exports quantized GGUF (`q4_k_m`) to `training_data/exports/<run_tag>/` and writes
     a versioned Ollama `Modelfile` (`jarvis-custom`) — NEVER overwrites `qwen3:8b`.
  5. `--dry-run` verifies dataset health, GPU/CUDA & Unsloth availability, VRAM budget,
     and previews the generated `Modelfile` without mutating weights or disk.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import importlib.util
import json
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_TRAIN_PYTHON = Path("/home/tanishq/unsloth_env/bin/python")
FALLBACK_TRAIN_PYTHON = ROOT / ".venv-train" / "bin" / "python"

DEFAULT_DATASET = ROOT / "training_data" / "corrections.jsonl"
DEFAULT_EXPORT_ROOT = ROOT / "training_data" / "exports"
DEFAULT_BASE_MODEL = "unsloth/Qwen3-8B-unsloth-bnb-4bit"
DEFAULT_OLLAMA_NAME = "jarvis-custom"
DEFAULT_MIN_EXAMPLES = 200

# RTX 5070 Ti (12 GB VRAM) safe QLoRA defaults (~8.2-8.8 GB peak, >= 2.5 GB headroom)
RTX_5070_TI_PLAN = {
    "load_in_4bit": True,
    "max_seq_length": 2048,
    "lora_r": 16,
    "lora_alpha": 16,
    "lora_dropout": 0.0,
    "gradient_checkpointing": "unsloth",
    "per_device_train_batch_size": 1,
    "gradient_accumulation_steps": 8,
    "num_train_epochs": 2,
    "learning_rate": 2e-4,
    "warmup_ratio": 0.05,
    "optim": "adamw_8bit",
    "quantization_method": "q4_k_m",
    "estimated_peak_vram_gb": 8.6,
    "min_headroom_gb": 2.0,
    "target_modules": [
        "q_proj",
        "k_proj",
        "v_proj",
        "o_proj",
        "gate_proj",
        "up_proj",
        "down_proj",
    ],
}

_PROTECTED_BASE_NAMES = {
    "qwen3",
    "qwen3:8b",
    "qwen2.5-coder",
    "qwen2.5-coder:14b",
    "deepseek-r1",
    "llama3",
    "llama3.1",
}


def validate_ollama_target_name(name: str) -> tuple[bool, str]:
    """Refuse overwriting base Ollama models such as `qwen3:8b`."""
    cleaned = str(name or "").strip().lower()
    if not cleaned:
        return False, "Ollama model name cannot be empty."
    if not re.match(r"^[a-z0-9][a-z0-9._:-]{1,63}$", cleaned):
        return False, f"Invalid Ollama model name: {name!r}"
    base_stem = cleaned.split(":", 1)[0]
    if cleaned in _PROTECTED_BASE_NAMES or base_stem in _PROTECTED_BASE_NAMES:
        return (
            False,
            f"Refused: '{name}' is a protected base model name. Use a separate candidate "
            f"name such as '{DEFAULT_OLLAMA_NAME}' so qwen3:8b is never overwritten.",
        )
    return True, cleaned


def load_and_dedupe_corrections(dataset_path: Path) -> dict:
    """Parse, validate, and deduplicate ChatML examples from `corrections.jsonl`."""
    stats = {
        "path": str(dataset_path),
        "exists": dataset_path.is_file(),
        "total_lines": 0,
        "valid_raw": 0,
        "malformed": 0,
        "duplicates_removed": 0,
        "unique_valid": 0,
        "categories": {},
        "examples": [],
    }
    if not dataset_path.is_file():
        return stats

    deduped: dict[tuple[str, str], dict] = {}
    with open(dataset_path, "r", encoding="utf-8", errors="replace") as fh:
        for raw in fh:
            stripped = raw.strip()
            if not stripped:
                continue
            stats["total_lines"] += 1
            try:
                obj = json.loads(stripped)
            except json.JSONDecodeError:
                stats["malformed"] += 1
                continue
            if not isinstance(obj, dict) or not isinstance(obj.get("messages"), list):
                stats["malformed"] += 1
                continue

            msgs = []
            user_txt = ""
            asst_txt = ""
            for m in obj["messages"]:
                if not isinstance(m, dict):
                    continue
                role = str(m.get("role") or "").strip().lower()
                content = str(m.get("content") or "").strip()
                if role in ("system", "user", "assistant") and content:
                    msgs.append({"role": role, "content": content})
                    if role == "user" and not user_txt:
                        user_txt = content
                    elif role == "assistant" and not asst_txt:
                        asst_txt = content

            if not user_txt or not asst_txt:
                stats["malformed"] += 1
                continue

            stats["valid_raw"] += 1
            key = (" ".join(user_txt.lower().split()), " ".join(asst_txt.lower().split()))
            if key in deduped:
                stats["duplicates_removed"] += 1
            cat = str(obj.get("category") or "general").strip().lower() or "general"
            # Later duplicate replaces earlier so refined corrections take precedence
            deduped[key] = {
                "messages": msgs,
                "category": cat,
                "lesson": str(obj.get("lesson") or "").strip(),
                "timestamp": str(obj.get("timestamp") or "").strip(),
            }

    examples = list(deduped.values())
    cats: dict[str, int] = {}
    for ex in examples:
        c = ex["category"]
        cats[c] = cats.get(c, 0) + 1

    stats["unique_valid"] = len(examples)
    stats["categories"] = cats
    stats["examples"] = examples
    return stats


def probe_training_environment() -> dict:
    """Inspect active Python interpreter, CUDA GPU/VRAM, and Unsloth stack availability."""
    info = {
        "python": sys.executable,
        "using_unsloth_env": (
            str(DEFAULT_TRAIN_PYTHON.parent.parent) in str(Path(sys.prefix))
            or str(DEFAULT_TRAIN_PYTHON.parent.parent) in str(sys.executable)
        ),
        "recommended_python": (
            str(DEFAULT_TRAIN_PYTHON)
            if DEFAULT_TRAIN_PYTHON.is_file()
            else str(FALLBACK_TRAIN_PYTHON)
        ),
        "packages": {},
        "cuda_available": False,
        "gpu_name": "none",
        "vram_total_gb": 0.0,
        "vram_free_gb": 0.0,
        "headroom_ok": False,
    }
    for pkg in ("torch", "unsloth", "transformers", "trl", "peft", "accelerate", "bitsandbytes", "datasets"):
        info["packages"][pkg] = importlib.util.find_spec(pkg) is not None

    if info["packages"].get("torch"):
        try:
            import torch  # lazy import — never touches Mark-LVII/.venv
            info["torch_version"] = torch.__version__
            info["cuda_available"] = bool(torch.cuda.is_available())
            if info["cuda_available"]:
                info["gpu_name"] = torch.cuda.get_device_name(0)
                free_b, total_b = torch.cuda.mem_get_info(0)
                info["vram_total_gb"] = round(total_b / (1024 ** 3), 2)
                info["vram_free_gb"] = round(free_b / (1024 ** 3), 2)
                headroom = info["vram_total_gb"] - RTX_5070_TI_PLAN["estimated_peak_vram_gb"]
                info["estimated_headroom_gb"] = round(headroom, 2)
                info["headroom_ok"] = headroom >= RTX_5070_TI_PLAN["min_headroom_gb"]
        except Exception as exc:
            info["torch_error"] = str(exc)
    return info


def build_modelfile(gguf_filename: str, ollama_name: str, base_model: str, run_tag: str) -> str:
    """Generate a clean Ollama Modelfile for the quantized GGUF candidate."""
    return (
        f"# Auto-generated by Mark-LVII scripts/train_local_lora.py ({run_tag})\n"
        f"# Candidate model: {ollama_name} (base: {base_model})\n"
        f"# Never overwrites qwen3:8b — register with:\n"
        f"#   ollama create {ollama_name} -f <this-Modelfile>\n"
        f"FROM ./{gguf_filename}\n\n"
        "PARAMETER temperature 0.3\n"
        "PARAMETER top_p 0.9\n"
        "PARAMETER num_ctx 8192\n"
        'PARAMETER stop "<|im_end|>"\n'
        'PARAMETER stop "<|endoftext|>"\n\n'
        'SYSTEM """You are J.A.R.V.I.S., a precise, concise, tool-capable AI assistant running locally for Tanishq. '
        "Follow tool schemas strictly, emit valid JSON arguments, and never fabricate verified actions.\"\"\"\n"
    )


def _run_training(args: argparse.Namespace, dataset_stats: dict, run_tag: str) -> int:
    """Execute isolated 4-bit Unsloth QLoRA fine-tuning and GGUF export."""
    env = probe_training_environment()
    missing = [k for k, present in env["packages"].items() if not present]
    if missing:
        print(f"[ERROR] Missing training packages in {sys.executable}: {', '.join(missing)}", file=sys.stderr)
        print(f"[HINT] Run using your isolated training venv:\n  {env['recommended_python']} {' '.join(sys.argv)}", file=sys.stderr)
        return 1
    if not env["cuda_available"]:
        print("[ERROR] CUDA GPU not detected; refusing CPU QLoRA training.", file=sys.stderr)
        return 1

    # Lazy imports — only loaded during real training inside unsloth_env
    from datasets import Dataset
    import torch
    from trl import SFTConfig, SFTTrainer
    from unsloth import FastLanguageModel

    export_dir = Path(args.export_dir) / run_tag
    adapter_dir = export_dir / "lora_adapter"
    gguf_dir = export_dir / "gguf"
    export_dir.mkdir(parents=True, exist_ok=True)

    print(f"[Train] Loading 4-bit base model '{args.base_model}' on {env['gpu_name']} ({env['vram_total_gb']} GB VRAM)...")
    model, tokenizer = FastLanguageModel.from_pretrained(
        model_name=args.base_model,
        max_seq_length=args.max_seq_length,
        dtype=None,
        load_in_4bit=True,
    )

    model = FastLanguageModel.get_peft_model(
        model,
        r=args.lora_r,
        target_modules=RTX_5070_TI_PLAN["target_modules"],
        lora_alpha=args.lora_alpha,
        lora_dropout=RTX_5070_TI_PLAN["lora_dropout"],
        bias="none",
        use_gradient_checkpointing=RTX_5070_TI_PLAN["gradient_checkpointing"],
        random_state=42,
    )

    def _format_row(row: dict) -> dict:
        text = tokenizer.apply_chat_template(
            row["messages"],
            tokenize=False,
            add_generation_prompt=False,
        )
        return {"text": text}

    ds = Dataset.from_list([{"messages": ex["messages"]} for ex in dataset_stats["examples"]])
    ds = ds.map(_format_row, remove_columns=["messages"])

    bf16_ok = bool(torch.cuda.is_bf16_supported())
    sft_cfg = SFTConfig(
        output_dir=str(export_dir / "checkpoints"),
        dataset_text_field="text",
        max_seq_length=args.max_seq_length,
        per_device_train_batch_size=args.batch_size,
        gradient_accumulation_steps=args.grad_accum,
        num_train_epochs=args.epochs,
        learning_rate=args.learning_rate,
        warmup_ratio=RTX_5070_TI_PLAN["warmup_ratio"],
        optim=RTX_5070_TI_PLAN["optim"],
        fp16=not bf16_ok,
        bf16=bf16_ok,
        logging_steps=5,
        save_strategy="no",
        seed=42,
        report_to="none",
    )

    trainer = SFTTrainer(
        model=model,
        tokenizer=tokenizer,
        train_dataset=ds,
        args=sft_cfg,
    )
    print(f"[Train] Starting QLoRA fine-tuning on {len(ds)} deduplicated examples...")
    trainer.train()

    adapter_dir.mkdir(parents=True, exist_ok=True)
    model.save_pretrained(str(adapter_dir))
    tokenizer.save_pretrained(str(adapter_dir))
    print(f"[Train] Saved LoRA adapter to {adapter_dir}")

    gguf_dir.mkdir(parents=True, exist_ok=True)
    print(f"[Train] Exporting quantized GGUF ({args.quantization}) to {gguf_dir}...")
    model.save_pretrained_gguf(
        str(gguf_dir),
        tokenizer,
        quantization_method=args.quantization,
    )

    gguf_files = sorted(gguf_dir.glob("*.gguf"))
    gguf_name = gguf_files[-1].name if gguf_files else f"{args.ollama_name}-{args.quantization}.gguf"
    modelfile_path = gguf_dir / "Modelfile"
    modelfile_text = build_modelfile(gguf_name, args.ollama_name, args.base_model, run_tag)
    modelfile_path.write_text(modelfile_text, encoding="utf-8")

    print("\n=== Export Complete ===")
    print(f"  GGUF folder : {gguf_dir}")
    print(f"  Modelfile   : {modelfile_path}")
    print("\nNext steps (safe promotion gate — qwen3:8b remains untouched):")
    print(f"  1. Register candidate: ollama create {args.ollama_name} -f {modelfile_path}")
    print(f"  2. Run eval gate     : .venv/bin/python scripts/eval_local_model.py --base qwen3:8b --candidate {args.ollama_name}")
    print("  3. Promote ONLY if eval_local_model.py prints PROMOTE (exit 0).")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Isolated 4-bit Unsloth QLoRA fine-tuning & GGUF export for Mark-LVII (RTX 5070 Ti 12 GB)."
    )
    parser.add_argument("--dataset", default=str(DEFAULT_DATASET), help="Path to corrections.jsonl")
    parser.add_argument("--export-dir", default=str(DEFAULT_EXPORT_ROOT), help="Directory for GGUF + Modelfile exports")
    parser.add_argument("--base-model", default=DEFAULT_BASE_MODEL, help="4-bit HuggingFace/Unsloth base model")
    parser.add_argument("--ollama-name", default=DEFAULT_OLLAMA_NAME, help="Versioned Ollama candidate name (never qwen3:8b)")
    parser.add_argument("--min-examples", type=int, default=DEFAULT_MIN_EXAMPLES, help="Minimum unique examples floor (default 200)")
    parser.add_argument("--force-small-dataset", action="store_true", help="Explicitly override the 200-example safety floor")
    parser.add_argument("--max-seq-length", type=int, default=RTX_5070_TI_PLAN["max_seq_length"])
    parser.add_argument("--lora-r", type=int, default=RTX_5070_TI_PLAN["lora_r"])
    parser.add_argument("--lora-alpha", type=int, default=RTX_5070_TI_PLAN["lora_alpha"])
    parser.add_argument("--batch-size", type=int, default=RTX_5070_TI_PLAN["per_device_train_batch_size"])
    parser.add_argument("--grad-accum", type=int, default=RTX_5070_TI_PLAN["gradient_accumulation_steps"])
    parser.add_argument("--epochs", type=int, default=RTX_5070_TI_PLAN["num_train_epochs"])
    parser.add_argument("--learning-rate", type=float, default=RTX_5070_TI_PLAN["learning_rate"])
    parser.add_argument("--quantization", default=RTX_5070_TI_PLAN["quantization_method"])
    parser.add_argument("--dry-run", action="store_true", help="Validate dataset, GPU/VRAM, and preview Modelfile without training")
    parser.add_argument("--json", action="store_true", dest="as_json", help="Emit JSON summary during --dry-run")
    args = parser.parse_args(argv)

    ok_name, name_or_err = validate_ollama_target_name(args.ollama_name)
    if not ok_name:
        print(f"[ERROR] {name_or_err}", file=sys.stderr)
        return 1

    dataset_path = Path(args.dataset)
    ds_stats = load_and_dedupe_corrections(dataset_path)
    run_tag = f"{name_or_err.replace(':', '-')}-{datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')}"
    preview_gguf = f"{run_tag}-{args.quantization}.gguf"
    modelfile_preview = build_modelfile(preview_gguf, name_or_err, args.base_model, run_tag)

    meets_floor = ds_stats["unique_valid"] >= args.min_examples
    if args.dry_run:
        env = probe_training_environment()
        summary = {
            "mode": "dry_run",
            "environment": env,
            "dataset": {k: v for k, v in ds_stats.items() if k != "examples"},
            "min_examples_floor": args.min_examples,
            "meets_floor": meets_floor,
            "plan": {
                **RTX_5070_TI_PLAN,
                "base_model": args.base_model,
                "ollama_candidate": name_or_err,
                "max_seq_length": args.max_seq_length,
                "lora_r": args.lora_r,
                "lora_alpha": args.lora_alpha,
                "per_device_train_batch_size": args.batch_size,
                "gradient_accumulation_steps": args.grad_accum,
                "num_train_epochs": args.epochs,
                "learning_rate": args.learning_rate,
                "quantization_method": args.quantization,
                "export_path": str(Path(args.export_dir) / run_tag),
            },
            "modelfile_preview": modelfile_preview,
        }
        if args.as_json:
            print(json.dumps(summary, indent=2, ensure_ascii=False))
        else:
            print("=== Mark-LVII Local QLoRA Training Plan (DRY RUN) ===")
            print(f"  Interpreter : {env['python']} (unsloth_env={env['using_unsloth_env']})")
            print(f"  GPU / CUDA  : {env['gpu_name']} (CUDA={env['cuda_available']}, VRAM={env['vram_free_gb']}/{env['vram_total_gb']} GB free)")
            print(f"  VRAM Budget : est. peak {RTX_5070_TI_PLAN['estimated_peak_vram_gb']} GB, headroom={env.get('estimated_headroom_gb', 0.0)} GB (min {RTX_5070_TI_PLAN['min_headroom_gb']} GB, ok={env['headroom_ok']})")
            print(f"  Packages    : {', '.join(f'{k}={'OK' if v else 'MISS'}' for k, v in env['packages'].items())}")
            print(f"  Dataset     : {ds_stats['path']} (exists={ds_stats['exists']}, unique_valid={ds_stats['unique_valid']}/{args.min_examples}, dupes={ds_stats['duplicates_removed']}, malformed={ds_stats['malformed']})")
            print(f"  Safety Floor: {'READY' if meets_floor else f'BLOCKED FOR REAL RUN ({ds_stats['unique_valid']}/{args.min_examples} — need {args.min_examples - ds_stats['unique_valid']} more or --force-small-dataset)'}")
            print(f"  Hyperparams : 4-bit NF4, r={args.lora_r}, alpha={args.lora_alpha}, seq={args.max_seq_length}, gc={RTX_5070_TI_PLAN['gradient_checkpointing']}, bs={args.batch_size}x{args.grad_accum}, epochs={args.epochs}, lr={args.learning_rate}, quant={args.quantization}")
            print(f"  Candidate   : {name_or_err} (protected base qwen3:8b untouched)")
            print("\n--- Modelfile Preview ---")
            print(modelfile_preview.strip())
        return 0

    if not meets_floor and not args.force_small_dataset:
        print(
            f"[BLOCKED] Safety floor: only {ds_stats['unique_valid']}/{args.min_examples} unique valid examples "
            f"in {dataset_path}. Fine-tuning on fewer than {args.min_examples} curated examples risks overfitting "
            "and model collapse. Keep logging corrections via feedback_logger or pass --force-small-dataset explicitly.",
            file=sys.stderr,
        )
        return 1

    if ds_stats["unique_valid"] == 0:
        print(f"[ERROR] No valid training examples found in {dataset_path}.", file=sys.stderr)
        return 1

    return _run_training(args, ds_stats, run_tag)


if __name__ == "__main__":
    sys.exit(main())
