"""Milestone 9 verification script — Unified Single-Brain Windows 11 + WSL2 Verification.

Designed to run natively on Windows Python 3.13.17 inside
`C:\\Users\\tanis\\Downloads\\Mark-LVII-main\\Mark-LVII-main` (and also works in WSL2).
"""
from __future__ import annotations

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


def main() -> int:
    all_ok = True
    print("=== Milestone 9: Unified Single-Brain (Windows 11 + WSL2) Verification ===")

    # 1. Pre-unification backups exist & archived Linux peer is decoupled (zero deletions)
    bkp_dirs = sorted((ROOT / "backups").glob("pre-unification-*")) if (ROOT / "backups").is_dir() else []
    latest_bkp = bkp_dirs[-1] if bkp_dirs else None
    bkp_files = sorted(p.name for p in latest_bkp.iterdir()) if latest_bkp else []
    from core.cross_os import peer_memory_dir, run_git, to_host_path
    archived_marker = to_host_path("/home/tanishq/Mark-LVII/.archived_single_brain")
    all_ok &= _check(
        "Pre-unification timestamped backup exists and Linux peer is archived read-only",
        latest_bkp is not None
        and len(bkp_files) >= 6
        and archived_marker.is_file()
        and peer_memory_dir() is None,
        f"backup={latest_bkp.name if latest_bkp else None} ({len(bkp_files)} files), peer_decoupled={peer_memory_dir() is None}",
    )

    # 2. git check-ignore -v across all 8 protected paths (via cross_os.run_git)
    targets = [
        "config/api_keys.json",
        "config/mcp_servers.json",
        "memory/long_term.json",
        "memory/routines.json",
        "training_data/eval_set.jsonl",
        "knowledge/.rag_index.sqlite",
        "backups/test.json",
        "UPGRADE_PROGRESS.md",
    ]
    unignored = [t for t in targets if run_git(ROOT, "check-ignore", "-v", t).returncode != 0]
    all_ok &= _check(
        "git check-ignore -v protects all 8 private/runtime paths in unified repo",
        not unignored,
        f"checked={len(targets)}, unignored={unignored or 'none'}",
    )

    # 3. scripts/health_check.py passes in <10s
    hc = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "health_check.py"), "--allow-dirty", "--json"],
        cwd=str(ROOT),
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        timeout=30.0,
    )
    hc_data = json.loads(hc.stdout) if hc.stdout.strip().startswith("{") else {}
    all_ok &= _check(
        "scripts/health_check.py passes all 10 checks in <10s",
        hc.returncode == 0 and hc_data.get("passed") is True and float(hc_data.get("elapsed_sec") or 99) < 10.0,
        f"elapsed={hc_data.get('elapsed_sec')}s, checks={len(hc_data.get('checks') or [])}",
    )

    # 4. scripts/wsl_train.py --dry-run invokes /home/tanishq/unsloth_env/bin/python on unified path
    wt = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "wsl_train.py"), "--dry-run", "--json"],
        cwd=str(ROOT),
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        timeout=30.0,
    )
    wt_data = json.loads(wt.stdout) if wt.stdout.strip().startswith("{") else {}
    wt_env = wt_data.get("environment") or {}
    wt_ds = wt_data.get("dataset") or {}
    all_ok &= _check(
        "scripts/wsl_train.py --dry-run bridges into WSL2 unsloth_env CUDA against unified Windows dataset",
        wt.returncode == 0
        and wt_env.get("cuda_available") is True
        and wt_env.get("using_unsloth_env") is True
        and "/mnt/c/Users/tanis/Downloads/Mark-LVII-main/Mark-LVII-main/training_data/corrections.jsonl"
        in str(wt_ds.get("path") or ""),
        f"gpu={wt_env.get('gpu_name')}, dataset={wt_ds.get('path')}",
    )

    # 5. plugins/linux_wsl_mastery.py + core.cross_os.run_linux bridge Linux/Docker from native Windows Python
    import plugins.linux_wsl_mastery as lwm
    from core.cross_os import run_linux
    lwm_res = lwm.run({"action": "services"}, player=None)
    wsl_cmd = run_linux("echo WSL_BRIDGE_OK && uname -s && docker --version", timeout=15)
    all_ok &= _check(
        "plugins/linux_wsl_mastery.py + cross_os.run_linux bridge Linux/Docker cleanly from Windows",
        bool(lwm_res and "WSL2" in lwm_res)
        and wsl_cmd.returncode == 0
        and "WSL_BRIDGE_OK" in wsl_cmd.stdout
        and "Linux" in wsl_cmd.stdout
        and "Docker" in wsl_cmd.stdout,
        f"lwm_ok={bool(lwm_res)}, docker={wsl_cmd.stdout.splitlines()[-1] if wsl_cmd.stdout.strip() else 'err'}",
    )

    # 6. plugins/_skill_gate.py works natively via WSL2 git fallback inside an isolated temp Git repo
    from plugins._skill_gate import list_skill_branches, stage_plugin_on_branch
    sample_code = (
        '"""Temp M9 skill gate verification plugin."""\n'
        "PLUGIN = {'name': 'm9_gate_smoke', 'description': 'Temporary M9 Windows WSL2 git smoke plugin.', "
        "'brief': 'M9 smoke test.', 'parameters': {'type': 'OBJECT', 'properties': {}}}\n"
        "def run(parameters, player=None, session_memory=None):\n"
        "    return 'm9_ok'\n"
    )
    with tempfile.TemporaryDirectory() as td:
        tmp_repo = Path(td) / "win_git_repo"
        tmp_repo.mkdir()
        (tmp_repo / "plugins").mkdir()
        (tmp_repo / "actions").mkdir()
        (tmp_repo / "plugins" / "README.txt").write_text("init\n", encoding="utf-8")
        run_git(tmp_repo, "init", "-b", "main")
        run_git(tmp_repo, "config", "core.fileMode", "false")
        run_git(tmp_repo, "config", "user.name", "M9 Test")
        run_git(tmp_repo, "config", "user.email", "m9@localhost")
        run_git(tmp_repo, "add", ".")
        run_git(tmp_repo, "commit", "-m", "baseline")
        staged = stage_plugin_on_branch(
            tmp_repo,
            "m9_gate_smoke",
            sample_code,
            summary="m9 windows wsl git gate check",
            validator_script=ROOT / "scripts" / "validate_plugin.py",
        )
        branches = list_skill_branches(tmp_repo)
        all_ok &= _check(
            "plugins/_skill_gate.py stages & validates skill/* branch natively via WSL2 git fallback",
            staged["ok"]
            and staged["branch"] in branches
            and not (tmp_repo / "plugins" / "m9_gate_smoke.py").exists(),
            f"branch={staged.get('branch')}, main_untouched=True",
        )

    # 7. Routines preserve BOTH StartupVirusScan and silent 01:00 Nightly reflection
    from core.routines.desktop import routines_path
    from core.routines.engine import RoutineEngine
    reng = RoutineEngine(routines_path(), object(), autostart=False, log=lambda _m: None)
    by_name = {r.get("name"): r for r in reng.list()}
    all_ok &= _check(
        "Unified memory/routines.json preserves StartupVirusScan + silent 01:00 Nightly reflection",
        "StartupVirusScan" in by_name
        and "Nightly reflection" in by_name
        and by_name["Nightly reflection"].get("deliver") == "silent"
        and by_name["Nightly reflection"]["trigger"]["rule"].get("time") == "01:00",
        f"routines={sorted(by_name.keys())}",
    )

    print("=== RESULT:", "ALL PASSED" if all_ok else "FAILED", "===")
    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main())
