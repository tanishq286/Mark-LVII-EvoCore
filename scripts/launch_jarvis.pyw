"""scripts/launch_jarvis.pyw — Single-Instance Silent Startup Launcher for Mark-LVII.

Designed to run via `pythonw.exe` on native Windows 11 with zero flashing
console window while preserving existing `pythonw.exe` Windows Firewall rules:

  1. Single-instance guard: detects if `main.py` in this repository is already
     running; if so, logs a brief note to `memory/startup.log` and exits cleanly.
  2. Local Ollama readiness check: probes `http://127.0.0.1:11434/api/tags`; if
     unreachable, attempts a silent background `ollama serve` start and waits
     up to 3 seconds (never blocking or aborting JARVIS launch if unavailable).
  3. Launches `main.py` from the unified repository root via `pythonw.exe` and
     records any startup errors in git-ignored `memory/startup.log`.

Supports `--dry-run` (and `--json`) for non-destructive verification.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
from urllib.error import URLError
from urllib.request import urlopen

ROOT: Path = Path(__file__).resolve().parent.parent
MAIN_SCRIPT: Path = ROOT / "main.py"
STARTUP_LOG: Path = ROOT / "memory" / "startup.log"
OLLAMA_TAGS_URL: str = "http://127.0.0.1:11434/api/tags"
OLLAMA_PS_URL: str = "http://127.0.0.1:11434/api/ps"
OLLAMA_GENERATE_URL: str = "http://127.0.0.1:11434/api/generate"
OLLAMA_WAIT_SEC: float = 3.0
WARMUP_MODEL: str = "qwen3:8b"
WARMUP_KEEP_ALIVE: str = "30m"


def _log(message: str) -> None:
    """Append a UTC-timestamped line to git-ignored `memory/startup.log`."""
    try:
        STARTUP_LOG.parent.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        with open(STARTUP_LOG, "a", encoding="utf-8") as fh:
            fh.write(f"[{stamp}] {message}\n")
    except OSError:
        pass


def resolve_pythonw() -> Path:
    """Prefer `pythonw.exe` alongside the active interpreter on Windows (zero console flash)."""
    exe = Path(sys.executable)
    if os.name == "nt":
        if exe.name.lower() == "pythonw.exe" and exe.is_file():
            return exe
        cand = exe.with_name("pythonw.exe")
        if cand.is_file():
            return cand
        fallback = Path(r"C:\Users\tanis\AppData\Local\Python\pythoncore-3.13-64\pythonw.exe")
        if fallback.is_file():
            return fallback
    return exe


def find_running_main_pids(repo_root: Path = ROOT) -> list[int]:
    """Return PIDs of any already-running Mark-LVII `main.py` processes in `repo_root`."""
    my_pid = os.getpid()
    target_main = str((repo_root / "main.py").resolve()).lower()
    target_root = str(repo_root.resolve()).lower()
    found: list[int] = []

    try:
        import psutil
        for proc in psutil.process_iter(["pid", "name", "cmdline", "cwd"]):
            try:
                pid = int(proc.info.get("pid") or 0)
                if pid <= 0 or pid == my_pid:
                    continue
                pname = str(proc.info.get("name") or "").lower()
                if "python" not in pname:
                    continue
                cmdline = proc.info.get("cmdline") or []
                joined = " ".join(str(x) for x in cmdline).lower()
                if "launch_jarvis.pyw" in joined:
                    continue
                if target_main in joined:
                    found.append(pid)
                    continue
                # Also match relative `pythonw.exe main.py` launched with cwd == repo_root
                has_main_arg = any(Path(str(a)).name.lower() == "main.py" for a in cmdline[1:])
                if has_main_arg:
                    proc_cwd = str(proc.info.get("cwd") or "").lower()
                    if proc_cwd == target_root:
                        found.append(pid)
            except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess, OSError):
                continue
        return sorted(set(found))
    except Exception:
        return []


def is_ollama_listening(url: str = OLLAMA_TAGS_URL, timeout: float = 1.5) -> bool:
    """Return True if local Ollama responds cleanly on `/api/tags`."""
    try:
        with urlopen(url, timeout=timeout) as resp:
            return resp.status == 200
    except (URLError, TimeoutError, OSError):
        return False


def _find_ollama_executable() -> str | None:
    found = shutil.which("ollama")
    if found:
        return found
    if os.name == "nt":
        local_app = os.environ.get("LOCALAPPDATA") or ""
        for rel in (
            Path(local_app) / "Programs" / "Ollama" / "ollama.exe",
            Path(r"C:\Program Files\Ollama\ollama.exe"),
        ):
            if rel.is_file():
                return str(rel)
    return None


def ensure_ollama_running(
    *,
    wait_sec: float = OLLAMA_WAIT_SEC,
    dry_run: bool = False,
) -> dict:
    """Check Ollama health; if offline and not dry-run, attempt a silent background start."""
    if is_ollama_listening():
        return {"reachable": True, "started_by_launcher": False, "note": "Ollama already listening on 127.0.0.1:11434"}

    exe = _find_ollama_executable()
    if dry_run:
        return {
            "reachable": False,
            "started_by_launcher": False,
            "ollama_exe": exe,
            "note": "Ollama not listening (dry-run skipped background start)",
        }

    if not exe:
        return {
            "reachable": False,
            "started_by_launcher": False,
            "note": "Ollama executable not found; continuing JARVIS startup without local Ollama",
        }

    creationflags = 0
    if os.name == "nt":
        creationflags = (
            getattr(subprocess, "CREATE_NO_WINDOW", 0)
            | getattr(subprocess, "DETACHED_PROCESS", 0)
            | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
        )
    try:
        subprocess.Popen(
            [exe, "serve"],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            shell=False,
            creationflags=creationflags,
        )
    except Exception as exc:
        return {
            "reachable": False,
            "started_by_launcher": False,
            "note": f"Failed to start Ollama silently: {exc}",
        }

    deadline = time.monotonic() + max(0.5, wait_sec)
    while time.monotonic() < deadline:
        if is_ollama_listening(timeout=0.8):
            return {
                "reachable": True,
                "started_by_launcher": True,
                "note": "Started Ollama silently in background and verified /api/tags",
            }
        time.sleep(0.25)

    return {
        "reachable": False,
        "started_by_launcher": True,
        "note": f"Triggered background Ollama start; proceeding with JARVIS after {wait_sec:.1f}s wait",
    }


def trigger_background_warmup(pythonw: Path, *, dry_run: bool = False) -> dict:
    """Optionally warm `qwen3:8b` (`keep_alive: '30m'`) in a detached background ping
    ONLY when Ollama is reachable and no LLM is already loaded in VRAM. Never blocks launch."""
    try:
        with urlopen(OLLAMA_PS_URL, timeout=1.2) as resp:
            active = json.loads(resp.read().decode("utf-8")).get("models") or []
    except Exception:
        return {"triggered": False, "reason": "ollama /api/ps unavailable"}

    active_names = [str(m.get("name") or "") for m in active if isinstance(m, dict)]
    if any(n.lower() == WARMUP_MODEL.lower() for n in active_names):
        return {"triggered": False, "reason": f"{WARMUP_MODEL} already warm in VRAM"}
    # Do not evict an active 14B/12B model if the user is already running one
    if any("embed" not in n.lower() for n in active_names):
        return {"triggered": False, "reason": f"another LLM already active ({active_names})"}
    if dry_run:
        return {"triggered": False, "reason": f"would warm {WARMUP_MODEL} ({WARMUP_KEEP_ALIVE}) in background"}

    ping_code = (
        "import json; from urllib.request import Request, urlopen; "
        f"req=Request({OLLAMA_GENERATE_URL!r}, "
        f"data=json.dumps({{'model':{WARMUP_MODEL!r},'keep_alive':{WARMUP_KEEP_ALIVE!r}}}).encode('utf-8'), "
        "headers={'Content-Type':'application/json'}, method='POST'); "
        "urlopen(req, timeout=30).read()"
    )
    creationflags = 0
    if os.name == "nt":
        creationflags = (
            getattr(subprocess, "CREATE_NO_WINDOW", 0)
            | getattr(subprocess, "DETACHED_PROCESS", 0)
        )
    try:
        subprocess.Popen(
            [str(pythonw), "-c", ping_code],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            shell=False,
            creationflags=creationflags,
        )
        return {"triggered": True, "reason": f"background warm-up sent for {WARMUP_MODEL} ({WARMUP_KEEP_ALIVE})"}
    except Exception as exc:
        return {"triggered": False, "reason": f"warm-up spawn skipped: {exc}"}


def launch_jarvis(*, dry_run: bool = False) -> dict:
    """Run single-instance guard, Ollama check, and launch `main.py` (or report plan in dry-run)."""
    pythonw = resolve_pythonw()
    running_pids = find_running_main_pids(ROOT)
    if running_pids:
        note = f"JARVIS main.py is already running (PID(s): {running_pids}); skipping duplicate launch."
        if not dry_run:
            _log(note)
        return {
            "action": "skipped_already_running",
            "running_pids": running_pids,
            "pythonw": str(pythonw),
            "main_script": str(MAIN_SCRIPT),
            "ollama": {"reachable": is_ollama_listening()},
            "note": note,
        }

    if not MAIN_SCRIPT.is_file():
        err = f"Cannot launch JARVIS: main.py not found at {MAIN_SCRIPT}"
        if not dry_run:
            _log(f"ERROR: {err}")
        return {
            "action": "error_missing_main",
            "error": err,
            "pythonw": str(pythonw),
            "main_script": str(MAIN_SCRIPT),
        }

    ollama_info = ensure_ollama_running(dry_run=dry_run)
    warmup_info = (
        trigger_background_warmup(pythonw, dry_run=dry_run)
        if ollama_info.get("reachable")
        else {"triggered": False, "reason": "ollama offline"}
    )
    if dry_run:
        return {
            "action": "dry_run_ready",
            "running_pids": [],
            "pythonw": str(pythonw),
            "main_script": str(MAIN_SCRIPT),
            "workdir": str(ROOT),
            "startup_log": str(STARTUP_LOG),
            "ollama": ollama_info,
            "warmup": warmup_info,
        }

    _log(f"Starting Mark-LVII via {pythonw.name} ({ollama_info['note']}; {warmup_info['reason']})")
    creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0
    try:
        STARTUP_LOG.parent.mkdir(parents=True, exist_ok=True)
        with open(STARTUP_LOG, "a", encoding="utf-8") as err_fh:
            proc = subprocess.Popen(
                [str(pythonw), str(MAIN_SCRIPT)],
                cwd=str(ROOT),
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=err_fh,
                shell=False,
                creationflags=creationflags,
            )
        _log(f"Launched main.py (PID {proc.pid}) from {ROOT}")
        return {
            "action": "launched",
            "pid": proc.pid,
            "pythonw": str(pythonw),
            "main_script": str(MAIN_SCRIPT),
            "ollama": ollama_info,
        }
    except Exception as exc:
        err = f"Failed to launch main.py: {exc}"
        _log(f"ERROR: {err}")
        return {"action": "error_launch_failed", "error": err}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Single-instance silent startup launcher for Mark-LVII.")
    parser.add_argument("--dry-run", action="store_true", help="Verify single-instance guard and Ollama without launching main.py")
    parser.add_argument("--json", action="store_true", dest="as_json", help="Emit JSON output")
    args = parser.parse_args(argv)

    result = launch_jarvis(dry_run=args.dry_run)
    if args.as_json:
        print(json.dumps(result, indent=2, ensure_ascii=False))
    elif args.dry_run:
        print("=== Mark-LVII Startup Launcher (DRY RUN) ===")
        for k, v in result.items():
            print(f"  {k}: {v}")
    return 0 if not str(result.get("action", "")).startswith("error_") else 1


if __name__ == "__main__":
    sys.exit(main())
