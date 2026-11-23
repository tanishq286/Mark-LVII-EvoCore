"""scripts/wsl_train.py — One-Command Windows -> WSL2 CUDA QLoRA Training Bridge.

Invokes `/home/tanishq/unsloth_env/bin/python scripts/train_local_lora.py` inside
WSL2 (`Ubuntu-D`) directly against the unified Mark-LVII repository's
`training_data/corrections.jsonl` and exports quantized GGUF + `Modelfile` back
into the unified `training_data/exports/` folder.

Works identically whether called from native Windows 11 `python.exe` or inside WSL2.
Zero PyTorch/Unsloth packages are ever needed on Windows.
"""
from __future__ import annotations

import os
from pathlib import Path
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

WSL_DISTRO = "Ubuntu-D"
WSL_UNSLOTH_PYTHON = "/home/tanishq/unsloth_env/bin/python"


def _to_wsl(path_str: str) -> str:
    from core.cross_os import to_wsl_path
    p = Path(path_str)
    if not p.is_absolute() and not path_str.startswith("/"):
        p = (ROOT / p).resolve()
    return to_wsl_path(str(p)) if not path_str.startswith("/") else path_str


def translate_cli_args(argv: list[str]) -> list[str]:
    """Translate any Windows `--dataset` or `--export-dir` paths to WSL `/mnt/...` paths."""
    out: list[str] = []
    has_dataset = False
    has_export = False
    i = 0
    while i < len(argv):
        tok = argv[i]
        if tok == "--wsl":
            i += 1
            continue
        if tok in ("--dataset", "--export-dir") and i + 1 < len(argv):
            if tok == "--dataset":
                has_dataset = True
            else:
                has_export = True
            out.append(tok)
            out.append(_to_wsl(argv[i + 1]))
            i += 2
            continue
        if tok.startswith("--dataset="):
            has_dataset = True
            out.append("--dataset=" + _to_wsl(tok.split("=", 1)[1]))
            i += 1
            continue
        if tok.startswith("--export-dir="):
            has_export = True
            out.append("--export-dir=" + _to_wsl(tok.split("=", 1)[1]))
            i += 1
            continue
        out.append(tok)
        i += 1

    if not has_dataset:
        out.extend(["--dataset", _to_wsl(str(ROOT / "training_data" / "corrections.jsonl"))])
    if not has_export:
        out.extend(["--export-dir", _to_wsl(str(ROOT / "training_data" / "exports"))])
    return out


def run_wsl_train(argv: list[str]) -> int:
    """Launch `scripts/train_local_lora.py` in `/home/tanishq/unsloth_env` and return exit code."""
    forwarded = translate_cli_args(argv)
    if os.name == "nt":
        if shutil.which("wsl.exe") is None:
            print("[ERROR] wsl.exe not found on PATH; WSL2 Ubuntu-D is required for CUDA QLoRA training.", file=sys.stderr)
            return 1
        wsl_root = _to_wsl(str(ROOT))
        cmd = [
            "wsl.exe",
            "-d",
            WSL_DISTRO,
            "--cd",
            wsl_root,
            "-e",
            WSL_UNSLOTH_PYTHON,
            "-u",
            "scripts/train_local_lora.py",
            *forwarded,
        ]
        return subprocess.run(cmd).returncode

    # Already running inside Linux / WSL2
    train_py = WSL_UNSLOTH_PYTHON if Path(WSL_UNSLOTH_PYTHON).is_file() else sys.executable
    cmd = [train_py, "-u", str(ROOT / "scripts" / "train_local_lora.py"), *forwarded]
    return subprocess.run(cmd, cwd=str(ROOT)).returncode


def main(argv: list[str] | None = None) -> int:
    return run_wsl_train(sys.argv[1:] if argv is None else list(argv))


if __name__ == "__main__":
    sys.exit(main())
