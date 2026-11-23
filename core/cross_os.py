"""core/cross_os.py — Unified Windows 11 <-> Ubuntu WSL2 (Ubuntu-D) Bridge.

Lets BOTH the Windows J.A.R.V.I.S install and the Linux (WSL2) J.A.R.V.I.S
install cooperate as one unified operator across the full machine:

  * Translates Linux paths (`/home/tanishq/...`, `/mnt/c/...`) <-> Windows
    paths (`\\\\wsl.localhost\\Ubuntu-D\\home\\tanishq\\...`, `C:\\...`).
  * Runs Linux commands natively on Linux or via `wsl.exe -d Ubuntu-D` on
    Windows (always non-interactive, full tool PATH, zero console flash).
  * Runs Windows PowerShell commands natively on Windows or via
    `powershell.exe` from WSL2.
  * Keeps shared knowledge (`long_term.json`, `ecosystem_index.json`,
    `linux_wsl_deep_knowledge.json`, `heuristics_chain.json`) mirrored across
    both installs so anything learned on one side is immediately known on the
    other.
"""
from __future__ import annotations

import json
import os
import platform
import shutil
import subprocess
from pathlib import Path

WSL_DISTRO = "Ubuntu-D"
LINUX_HOME_STR = "/home/tanishq"
LINUX_MARK_DIR = "/home/tanishq/Mark-LVII"
WIN_HOME_STR = r"C:\Users\tanis"
WIN_MARK_DIR = r"C:\Users\tanis\Downloads\Mark-LVII-main\Mark-LVII-main"
_WSL_UNC_PREFIX = f"\\\\wsl.localhost\\{WSL_DISTRO}"

_IS_WIN = platform.system() == "Windows"


def is_windows() -> bool:
    return _IS_WIN


def is_wsl() -> bool:
    if _IS_WIN:
        return False
    return bool(
        os.environ.get("WSL_DISTRO_NAME")
        or Path("/mnt/c/Windows/System32/powershell.exe").is_file()
    )


def linux_home() -> Path:
    """Path to `/home/tanishq` readable from the CURRENT OS (native on Linux,
    UNC `\\\\wsl.localhost\\Ubuntu-D\\home\\tanishq` on Windows)."""
    if not _IS_WIN:
        p = Path(LINUX_HOME_STR)
        return p if p.is_dir() else Path.home()
    unc = Path(f"{_WSL_UNC_PREFIX}\\home\\tanishq")
    try:
        if unc.is_dir():
            return unc
    except OSError:
        pass
    return Path.home()


def linux_home_reachable() -> bool:
    """True if the real Ubuntu-D `/home/tanishq` filesystem is currently mounted."""
    if not _IS_WIN:
        return Path(LINUX_HOME_STR).is_dir()
    try:
        return Path(f"{_WSL_UNC_PREFIX}\\home\\tanishq").is_dir()
    except OSError:
        return False


def windows_home() -> Path:
    """Path to `C:\\Users\\tanis` readable from the CURRENT OS."""
    if _IS_WIN:
        p = Path(WIN_HOME_STR)
        return p if p.is_dir() else Path.home()
    p = Path("/mnt/c/Users/tanis")
    return p if p.is_dir() else Path.home()


def to_host_path(raw: str | Path) -> Path:
    """Convert any Linux, WSL, or Windows path into a Path readable by the
    CURRENT Python runtime."""
    s = str(raw or "").strip().strip('"')
    if not s:
        return Path(".")

    if _IS_WIN:
        # /mnt/c/foo -> C:\foo
        if s.startswith("/mnt/") and len(s) >= 7 and s[6] == "/":
            drive = s[5].upper()
            rest = s[7:].replace("/", "\\")
            return Path(f"{drive}:\\{rest}")
        # /home/tanishq/... or other POSIX absolute path -> \\wsl.localhost\Ubuntu-D\...
        if s.startswith("/"):
            rest = s.lstrip("/").replace("/", "\\")
            return Path(f"{_WSL_UNC_PREFIX}\\{rest}")
        return Path(s)

    # On Linux / WSL:
    low = s.lower()
    unc_low = _WSL_UNC_PREFIX.lower()
    if low.startswith(unc_low):
        rest = s[len(_WSL_UNC_PREFIX):].replace("\\", "/")
        return Path(rest if rest.startswith("/") else "/" + rest)
    if len(s) >= 3 and s[1] == ":" and s[2] in ("\\", "/"):
        drive = s[0].lower()
        rest = s[3:].replace("\\", "/")
        return Path(f"/mnt/{drive}/{rest}")
    return Path(s)


def to_wsl_path(raw: str | Path) -> str:
    """Convert a Windows or UNC path into a Linux/WSL POSIX path string."""
    s = str(raw or "").strip().strip('"')
    if not s:
        return "~"
    low = s.lower()
    unc_low = _WSL_UNC_PREFIX.lower()
    if low.startswith(unc_low):
        rest = s[len(_WSL_UNC_PREFIX):].replace("\\", "/")
        return rest if rest.startswith("/") else "/" + rest
    if len(s) >= 2 and s[1] == ":":
        drive = s[0].lower()
        rest = s[2:].replace("\\", "/").lstrip("/")
        return f"/mnt/{drive}/{rest}"
    return s.replace("\\", "/")


def run_linux(cmd: str, cwd: str = "~", timeout: int = 45) -> subprocess.CompletedProcess:
    """Execute a bash command inside Ubuntu-D Linux from EITHER Linux or Windows."""
    path_export = (
        'export PATH="$HOME/.local/bin:$HOME/.local/opt/node/bin:'
        '$HOME/.local/go/bin:$HOME/bin:$PATH"; '
    )
    full_cmd = path_export + cmd
    wsl_cwd = to_wsl_path(cwd) if cwd and cwd != "~" else "~"

    if not _IS_WIN:
        actual_cwd = str(linux_home()) if wsl_cwd == "~" else wsl_cwd
        if not Path(actual_cwd).is_dir():
            actual_cwd = str(linux_home())
        return subprocess.run(
            ["bash", "-lc", full_cmd],
            cwd=actual_cwd,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
        )

    return subprocess.run(
        ["wsl.exe", "-d", WSL_DISTRO, "--cd", wsl_cwd, "--", "bash", "-lc", full_cmd],
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
    )


def run_windows(cmd: str, cwd: str | None = None, timeout: int = 45) -> subprocess.CompletedProcess:
    """Execute a PowerShell command on Windows from EITHER Windows or Linux WSL2."""
    ps_exe = "powershell.exe"
    if not _IS_WIN and Path("/mnt/c/Windows/System32/WindowsPowerShell/v1.0/powershell.exe").is_file():
        ps_exe = "/mnt/c/Windows/System32/WindowsPowerShell/v1.0/powershell.exe"
    run_cwd = None
    if cwd:
        p = to_host_path(cwd)
        if p.is_dir():
            run_cwd = str(p)
    return subprocess.run(
        [ps_exe, "-NoProfile", "-NonInteractive", "-Command", cmd],
        cwd=run_cwd,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
    )


def peer_memory_dir() -> Path | None:
    """Return the OTHER J.A.R.V.I.S install's `memory/` directory if reachable."""
    try:
        if _IS_WIN:
            p = Path(f"{_WSL_UNC_PREFIX}\\home\\tanishq\\Mark-LVII\\memory")
        else:
            p = Path("/mnt/c/Users/tanis/Downloads/Mark-LVII-main/Mark-LVII-main/memory")
        return p if p.is_dir() else None
    except OSError:
        return None


def mirror_memory_file(local_path: Path) -> bool:
    """Atomically mirror a memory JSON file to the peer J.A.R.V.I.S install so
    Windows and Linux J.A.R.V.I.S share one brain. Never raises."""
    try:
        src = Path(local_path)
        if not src.is_file():
            return False
        peer = peer_memory_dir()
        if peer is None:
            return False
        dst = peer / src.name
        if src.resolve() == dst.resolve():
            return False
        tmp = peer / f".{src.name}.sync.tmp"
        shutil.copyfile(src, tmp)
        os.replace(tmp, dst)
        return True
    except Exception:
        return False


def merge_peer_long_term(local_data: dict) -> tuple[dict, bool]:
    """Merge newer long_term.json entries from the peer J.A.R.V.I.S install into
    `local_data`. Returns `(merged_dict, did_change)`. Never raises."""
    changed = False
    try:
        peer = peer_memory_dir()
        if peer is None:
            return local_data, False
        peer_file = peer / "long_term.json"
        if not peer_file.is_file():
            return local_data, False
        peer_data = json.loads(peer_file.read_text(encoding="utf-8"))
        if not isinstance(peer_data, dict):
            return local_data, False

        for cat, items in peer_data.items():
            if cat == "sessions" or not isinstance(items, dict):
                continue
            local_cat = local_data.setdefault(cat, {})
            if not isinstance(local_cat, dict):
                continue
            for key, p_entry in items.items():
                if not isinstance(p_entry, dict) or "value" not in p_entry:
                    continue
                l_entry = local_cat.get(key)
                if not isinstance(l_entry, dict) or "value" not in l_entry:
                    local_cat[key] = dict(p_entry)
                    changed = True
                else:
                    p_upd = str(p_entry.get("updated", "") or "")
                    l_upd = str(l_entry.get("updated", "") or "")
                    if p_upd > l_upd and p_entry.get("value") != l_entry.get("value"):
                        local_cat[key] = dict(p_entry)
                        changed = True
    except Exception:
        pass
    return local_data, changed


def peer_running_status() -> dict:
    """Report whether the Windows and Linux J.A.R.V.I.S processes are currently running."""
    status = {"windows_running": False, "linux_running": False, "current_os": "Windows" if _IS_WIN else "Linux (WSL2)"}
    try:
        r_lin = run_linux("pgrep -f 'Mark-LVII.*main.py' || true", timeout=4)
        status["linux_running"] = bool(r_lin.stdout.strip())
    except Exception:
        pass
    try:
        r_win = run_windows(
            "(Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -match 'Mark-LVII.*main\\.py' } | Measure-Object).Count",
            timeout=6,
        )
        status["windows_running"] = int((r_win.stdout or "0").strip() or "0") > 0
    except Exception:
        pass
    return status
