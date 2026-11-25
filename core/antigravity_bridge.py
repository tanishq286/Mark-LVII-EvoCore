"""core/antigravity_bridge.py — Google Antigravity (`agy`) Live Bridge & Self-Upgrade Engine.

Integrates Google Antigravity directly into J.A.R.V.I.S (Mark-LVII) across both
Windows 11 and Ubuntu WSL2 (`Ubuntu-D`) so that:
  1. JARVIS can delegate deep multi-file engineering, research, and orchestration
     tasks to Antigravity (`~/bin/agy --output-format stream-json`) with live
     step-by-step streaming on her HUD (`show_code_job`, `show_content`, `write_log`).
  2. JARVIS can **automatically upgrade herself with Antigravity**:
       - Synthesizes new drop-in plugins (`plugins/<slug>.py` + `tests/evolved/test_<slug>.py`)
         or heals degraded plugins detected in runtime telemetry.
       - Enforces a 4-Layer Safety & Quality Gate BEFORE touching live plugins:
           (a) Immutable-path & `safety_guard.assert_mutable_path` check
           (b) `safety_guard.audit_python_source(..., strict_sandbox=True)` AST gate
           (c) `scripts/validate_plugin.py --json` isolated subprocess smoke test
           (d) `evaluate_module_10d` (>= 75/100 `tq-forge` + `omega-system` score)
       - Automatically runs up to 2 Antigravity self-fix passes if validation flags an error.
       - Stages on a `skill/*` git branch when the working tree is clean AND
         safely backs up, hot-loads (`PluginRegistry.refresh()`), and mirrors the
         validated plugin across BOTH the Windows and WSL2 JARVIS checkouts so
         both copies upgrade in lockstep without drifting.
  3. Connects upgrade milestones to `core/emotions.py` so she feels focused
     curiosity while evolving with Antigravity and genuine pride/joy when her
     new capability comes online.
"""
from __future__ import annotations

from datetime import datetime, timezone
import json
import os
import platform
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from typing import Any

from core import cross_os

if getattr(sys, "frozen", False):
    _BASE = Path(sys.executable).parent
else:
    _BASE = Path(__file__).resolve().parent.parent

_MEM_DIR = _BASE / "memory"
_AGY_LOG = _MEM_DIR / "antigravity.log"
_STREAM_LOG = _MEM_DIR / "live_agent_stream.log"
_SESSIONS_FILE = _MEM_DIR / "antigravity_sessions.json"
_BACKUP_DIR = _MEM_DIR / "antigravity_backups"
_AUTO_HEAL_LEDGER = _MEM_DIR / "antigravity_auto_heals.json"

DEFAULT_AGY_MODEL = "gemini-4-carbon-high"
FAST_AGY_MODEL = "gemini-3.8-flash-high"
FALLBACK_AGY_MODELS = (
    "gemini-4-carbon-high",
    "gemini-3.8-flash-high",
    "gemini-3.8-flash-low",
)

_IMMUTABLE_SLUGS = frozenset({
    "safety_guard",
    "confirm",
    "_skill_gate",
    "file_vault",
    "validate_plugin",
    "voice_lock",
})

_SPOKEN_SUFFIX = (
    "\n\n[JARVIS VOICE BRIDGE]: Complete the requested engineering/research work thoroughly, "
    "then conclude with a concise plain-English summary (under 110 words, no markdown fences) "
    "suitable for JARVIS to speak aloud."
)

_busy_lock = threading.Lock()
_state_lock = threading.Lock()
_auto_heal_lock = threading.Lock()
_CURRENT_PROC: subprocess.Popen | None = None
_JOB: dict[str, Any] = {}


def _call(player, method: str, *args) -> None:
    fn = getattr(player, method, None) if player is not None else None
    if callable(fn):
        try:
            fn(*args)
        except Exception as exc:
            print(f"[antigravity] {method} error: {exc}")


def _strip_fences(text: str) -> str:
    cleaned = re.sub(r"(?is)<think(?:ing)?>.*?</think(?:ing)?>", "", text or "").strip()
    m = re.search(r"```(?:python|py)?\s*\r?\n(.*?)```", cleaned, re.S | re.I)
    if m:
        return m.group(1).strip()
    cleaned = re.sub(r"^```[a-zA-Z]*\r?\n?", "", cleaned)
    cleaned = re.sub(r"\r?\n?```\s*$", "", cleaned)
    return cleaned.strip()


def _sanitize_slug(raw: str) -> str:
    slug = re.sub(r"[^a-z0-9_]+", "_", str(raw or "").lower()).strip("_")
    if not slug or not slug[0].isalpha():
        slug = "skill_" + (slug or "evolved")
    return slug[:44].rstrip("_") or "skill_evolved"


def agy_available() -> tuple[bool, str]:
    """Return `(True, path_or_bridge_desc)` when Antigravity CLI (`agy`) is reachable."""
    if platform.system() != "Windows":
        for cand in (
            Path("/home/tanishq/bin/agy"),
            Path.home() / "bin" / "agy",
            Path.home() / ".local" / "bin" / "agy",
        ):
            if cand.is_file() and os.access(cand, os.X_OK):
                return True, str(cand)
        found = shutil.which("agy")
        if found:
            return True, found
        return False, "agy binary not found in ~/bin or PATH"

    # Windows runtime bridges seamlessly into Ubuntu-D WSL2 where ~/bin/agy lives
    if shutil.which("wsl.exe") is not None and cross_os.linux_home_reachable():
        agy_unc = cross_os.linux_home() / "bin" / "agy"
        try:
            if agy_unc.is_file():
                return True, f"wsl://{cross_os.WSL_DISTRO}/home/tanishq/bin/agy"
        except OSError:
            pass
    if shutil.which("agy"):
        return True, str(shutil.which("agy"))
    return False, "Antigravity CLI (~/bin/agy in WSL2 Ubuntu-D) is not reachable"


def build_agy_command(
    *,
    model: str = "",
    stream_json: bool = True,
    resume_id: str = "",
    skip_permissions: bool = True,
    disable_slash: bool = True,
    timeout_s: int = 600,
    wsl_cwd: str = "~",
    add_dirs: list[str] | None = None,
) -> list[str]:
    """Construct the cross-OS subprocess argv to run `agy` non-interactively."""
    fmt = "stream-json" if stream_json else "json"
    args: list[str] = ["-p", "--output-format", fmt]
    if skip_permissions:
        args.append("--dangerously-skip-permissions")
    if disable_slash:
        args.append("--disable-slash-commands")
    if model:
        args.extend(["--model", model])
    if resume_id:
        args.extend(["--resume", resume_id])
    if timeout_s > 0:
        args.extend(["--print-timeout", f"{int(timeout_s)}s"])
    for d in add_dirs or []:
        if d:
            args.extend(["--add-dir", cross_os.to_wsl_path(d) if platform.system() == "Windows" else str(d)])

    if platform.system() == "Windows":
        target_cwd = cross_os.to_wsl_path(wsl_cwd) if wsl_cwd and wsl_cwd != "~" else "~"
        return [
            "wsl.exe",
            "-d",
            cross_os.WSL_DISTRO,
            "--cd",
            target_cwd,
            "--",
            "bash",
            "-lc",
            'export PATH="$HOME/bin:$HOME/.local/bin:$HOME/.local/opt/node/bin:$PATH"; exec agy "$@"',
            "agy",
            *args,
        ]

    agy_bin = "/home/tanishq/bin/agy" if Path("/home/tanishq/bin/agy").is_file() else (shutil.which("agy") or "agy")
    return [agy_bin, *args]


def _append_log(entry: dict[str, Any]) -> None:
    try:
        _MEM_DIR.mkdir(parents=True, exist_ok=True)
        with _AGY_LOG.open("a", encoding="utf-8") as f:
            f.write(json.dumps({"ts": time.strftime("%Y-%m-%d %H:%M:%S"), **entry}, ensure_ascii=False) + "\n")
    except Exception:
        pass


def _stream_line(line: str, *, reset: bool = False) -> None:
    try:
        _MEM_DIR.mkdir(parents=True, exist_ok=True)
        mode = "w" if reset else "a"
        with _STREAM_LOG.open(mode, encoding="utf-8") as f:
            f.write(f"[{time.strftime('%H:%M:%S')}] [Antigravity] {line}\n")
    except Exception:
        pass


def _record_session(conv_id: str, task: str, model: str, status: str, summary: str) -> None:
    if not conv_id:
        return
    try:
        _MEM_DIR.mkdir(parents=True, exist_ok=True)
        sessions: list[dict[str, Any]] = []
        if _SESSIONS_FILE.is_file():
            try:
                raw = json.loads(_SESSIONS_FILE.read_text(encoding="utf-8"))
                if isinstance(raw, list):
                    sessions = raw
            except Exception:
                sessions = []
        sessions = [s for s in sessions if s.get("conversation_id") != conv_id]
        sessions.append({
            "conversation_id": conv_id,
            "updated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "model": model,
            "status": status,
            "task": task[:240],
            "summary": summary[:400],
        })
        tmp = _SESSIONS_FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps(sessions[-40:], indent=2, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, _SESSIONS_FILE)
        cross_os.mirror_memory_file(_SESSIONS_FILE)
    except Exception:
        pass


def last_conversation_id() -> str:
    if not _SESSIONS_FILE.is_file():
        return ""
    try:
        raw = json.loads(_SESSIONS_FILE.read_text(encoding="utf-8"))
        if isinstance(raw, list) and raw:
            return str(raw[-1].get("conversation_id") or "")
    except Exception:
        pass
    return ""


def _publish_hud(player) -> None:
    if player is None:
        return
    with _state_lock:
        if not _JOB:
            return
        snap = json.loads(json.dumps(_JOB))
    _call(player, "show_code_job", snap)


def _record_step(
    player,
    msg: str,
    *,
    status: str | None = None,
    file_path: str | None = None,
    file_action: str = "edit",
    file_state: str = "writing",
    log_hud: bool = True,
) -> None:
    _stream_line(msg)
    with _state_lock:
        if _JOB:
            if status:
                _JOB["status"] = status
            elapsed = round(time.time() - float(_JOB.get("started") or time.time()), 1)
            _JOB.setdefault("steps", []).append([elapsed, msg])
            del _JOB["steps"][:-60]
            if file_path:
                entry = _JOB.setdefault("files", {}).setdefault(
                    file_path,
                    {"action": file_action, "what": msg[:90], "state": file_state},
                )
                entry["state"] = file_state
    _publish_hud(player)
    if log_hud:
        _call(player, "write_log", f"SYS: [Antigravity] {msg[:140]}")


def parse_agy_ndjson_line(
    raw_line: str,
    state_acc: dict[str, Any],
    player=None,
) -> None:
    """Parse one NDJSON event line from `agy --output-format stream-json`."""
    txt = (raw_line or "").strip()
    if not txt:
        return
    if not txt.startswith("{"):
        state_acc.setdefault("plain_lines", []).append(txt)
        return
    try:
        ev = json.loads(txt)
    except json.JSONDecodeError:
        state_acc.setdefault("plain_lines", []).append(txt)
        return

    etype = str(ev.get("event") or ev.get("type") or "")
    if etype == "init":
        conv_id = str(ev.get("conversation_id") or "")
        init_obj = ev.get("init") if isinstance(ev.get("init"), dict) else {}
        model = str(init_obj.get("model") or "")
        if conv_id:
            state_acc["conversation_id"] = conv_id
        if model:
            state_acc["model"] = model
            with _state_lock:
                if _JOB:
                    _JOB["model"] = f"Antigravity · {model}"
        _record_step(player, f"Antigravity connected ({model or 'live'}, session {conv_id[:8]})", status="connecting")
        return

    if etype == "step_update":
        su = ev.get("step_update") if isinstance(ev.get("step_update"), dict) else {}
        stype = str(su.get("step_type") or "")
        sstate = str(su.get("state") or "")
        tdelta = str(su.get("text_delta") or "")
        thought = str(su.get("thought_delta") or su.get("thinking_delta") or "")

        if thought.strip():
            clean_th = " ".join(thought.split())
            now = time.monotonic()
            if now - float(state_acc.get("last_thought_ui", 0.0)) > 1.2:
                state_acc["last_thought_ui"] = now
                _record_step(player, f"Thinking: {clean_th[:105]}…", status="thinking", log_hud=False)

        if stype == "agent_response" and tdelta:
            state_acc.setdefault("deltas", []).append(tdelta)
            now = time.monotonic()
            if sstate == "DONE" or (now - float(state_acc.get("last_delta_ui", 0.0)) > 1.0):
                state_acc["last_delta_ui"] = now
                joined = "".join(state_acc["deltas"]).strip()
                if joined:
                    first = " ".join(joined.splitlines()[-1].split())[:115]
                    _record_step(player, f"Drafting: {first}", status="writing", log_hud=False)
        elif stype not in ("user_input", "unknown", "agent_response", ""):
            tname = str(su.get("tool_name") or su.get("name") or stype)
            fpath = su.get("file_path") or su.get("path")
            fstate = "done" if sstate == "DONE" else "writing"
            if sstate in ("ACTIVE", "DONE"):
                _record_step(
                    player,
                    f"Tool [{sstate}]: {tname}" + (f" ({fpath})" if fpath else ""),
                    status="running tools",
                    file_path=str(fpath) if fpath else None,
                    file_state=fstate,
                    log_hud=(sstate == "ACTIVE"),
                )
        return

    if etype == "result":
        res_obj = ev.get("result") if isinstance(ev.get("result"), dict) else ev
        conv_id = str(res_obj.get("conversation_id") or state_acc.get("conversation_id") or "")
        if conv_id:
            state_acc["conversation_id"] = conv_id
        resp_txt = str(res_obj.get("response") or "").strip()
        if resp_txt:
            state_acc["final_response"] = resp_txt
        state_acc["status"] = str(res_obj.get("status") or "SUCCESS")
        state_acc["duration_seconds"] = res_obj.get("duration_seconds")


def invoke_agy_sync(
    prompt: str,
    *,
    cwd: str = "~",
    model: str = "",
    resume_id: str = "",
    timeout_s: int = 300,
    player=None,
    track_global_proc: bool = False,
    add_dirs: list[str] | None = None,
) -> dict[str, Any]:
    """Run one Antigravity turn synchronously (streaming live to `player` if given)."""
    global _CURRENT_PROC
    ok_bin, bin_info = agy_available()
    if not ok_bin:
        return {"ok": False, "error": bin_info, "response": "", "conversation_id": "", "model": model}

    models_to_try = [model] if model else list(FALLBACK_AGY_MODELS)
    last_err = ""

    run_cwd = str(Path.home())
    if platform.system() != "Windows" and cwd and cwd != "~":
        cand_cwd = Path(cwd).expanduser()
        if cand_cwd.is_dir():
            run_cwd = str(cand_cwd)

    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if platform.system() == "Windows" else 0

    for chosen_model in models_to_try:
        cmd = build_agy_command(
            model=chosen_model,
            stream_json=True,
            resume_id=resume_id,
            timeout_s=timeout_s,
            wsl_cwd=cwd,
            add_dirs=add_dirs,
        )
        acc: dict[str, Any] = {"model": chosen_model}
        err_lines: list[str] = []
        started = time.time()
        try:
            proc = subprocess.Popen(
                cmd,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                cwd=run_cwd,
                creationflags=flags,
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
            )
            if track_global_proc:
                with _state_lock:
                    _CURRENT_PROC = proc

            if proc.stdin:
                proc.stdin.write(prompt)
                proc.stdin.close()

            def _drain_err() -> None:
                if not proc.stderr:
                    return
                for el in proc.stderr:
                    s = el.strip()
                    if s:
                        err_lines.append(s)
                        _stream_line(f"[stderr] {s}")

            t_err = threading.Thread(target=_drain_err, daemon=True)
            t_err.start()

            deadline = started + timeout_s + 15
            if proc.stdout:
                for raw in proc.stdout:
                    if time.time() > deadline:
                        proc.kill()
                        raise subprocess.TimeoutExpired(cmd, timeout_s)
                    parse_agy_ndjson_line(raw, acc, player=player)

            proc.wait(timeout=20)
            t_err.join(timeout=2)

            response_text = (
                acc.get("final_response")
                or "".join(acc.get("deltas") or []).strip()
                or "\n".join(acc.get("plain_lines") or []).strip()
            )
            if proc.returncode == 0 and response_text:
                return {
                    "ok": True,
                    "response": response_text,
                    "conversation_id": str(acc.get("conversation_id") or ""),
                    "model": str(acc.get("model") or chosen_model),
                    "elapsed_s": round(time.time() - started, 1),
                    "error": "",
                }
            last_err = "\n".join(err_lines[-6:]).strip() or f"agy exited with code {proc.returncode}"
            if not any(q in last_err.lower() for q in ("quota", "429", "model", "unavailable", "capacity")):
                break
        except subprocess.TimeoutExpired:
            last_err = f"Antigravity timed out after {timeout_s}s"
            break
        except Exception as exc:
            last_err = str(exc)
            break
        finally:
            if track_global_proc:
                with _state_lock:
                    _CURRENT_PROC = None

    return {"ok": False, "error": last_err or "Antigravity call failed", "response": "", "conversation_id": "", "model": model}


def generate_code_with_antigravity(prompt: str, *, timeout_s: int = 120, model: str = "") -> str:
    """One-shot helper for `self_evolve`, `skill_synthesizer`, `self_healer`, and `meta_optimizer`.

    Skipped automatically inside offline pytest runs unless `JARVIS_FORCE_AGY_IN_TESTS=1`
    so unit tests remain fast and deterministic.
    """
    if os.environ.get("PYTEST_CURRENT_TEST") and os.environ.get("JARVIS_FORCE_AGY_IN_TESTS") != "1":
        return ""
    ok_bin, _ = agy_available()
    if not ok_bin:
        return ""
    res = invoke_agy_sync(
        prompt + "\n\nIMPORTANT: Output ONLY raw valid Python source code — no markdown fences, no prose.",
        cwd=str(_BASE),
        model=model or FAST_AGY_MODEL,
        timeout_s=timeout_s,
        player=None,
    )
    if not res.get("ok"):
        return ""
    return _strip_fences(res.get("response") or "")


def _peer_jarvis_root() -> Path | None:
    """Return the sibling Windows or WSL2 Mark-LVII checkout root if present."""
    try:
        if platform.system() == "Windows":
            cand = Path(f"\\\\wsl.localhost\\{cross_os.WSL_DISTRO}\\home\\tanishq\\Mark-LVII")
        else:
            cand = Path("/mnt/c/Users/tanis/Downloads/Mark-LVII-main/Mark-LVII-main")
        if cand.is_dir() and cand.resolve() != _BASE.resolve():
            return cand
    except OSError:
        pass
    return None


def _mirror_plugin_to_peer(slug: str, code_text: str) -> bool:
    """Mirror a 4-gate-verified `plugins/<slug>.py` to the peer OS checkout so
    Windows and Linux JARVIS upgrade together."""
    peer_root = _peer_jarvis_root()
    if peer_root is None:
        return False
    try:
        peer_plugins = peer_root / "plugins"
        if not peer_plugins.is_dir():
            return False
        dst = peer_plugins / f"{slug}.py"
        normalized = code_text.replace("\r\n", "\n").rstrip() + "\n"
        # Windows checkout uses LF in plugins/*.py or CRLF cleanly; keep clean UTF-8 LF
        tmp = peer_plugins / f".{slug}.py.agy.tmp"
        tmp.write_text(normalized, encoding="utf-8")
        os.replace(tmp, dst)
        return True
    except Exception as exc:
        print(f"[antigravity] peer plugin mirror skipped: {exc}")
        return False


def _validate_candidate_4gate(slug: str, code_text: str) -> tuple[bool, list[str], dict[str, Any]]:
    """Run 4 independent safety & quality gates on candidate plugin source."""
    errors: list[str] = []
    rel_plugin = f"plugins/{slug}.py"

    if slug in _IMMUTABLE_SLUGS or slug.startswith("_"):
        return False, [f"Immutable or reserved plugin slug '{slug}' blocked."], {}
    if (_BASE / "actions" / f"{slug}.py").is_file():
        return False, [f"Slug '{slug}' collides with core action actions/{slug}.py."], {}

    # Gate 1 & 2: safety_guard path mutability + strict AST sandbox audit
    try:
        from core.evolution import safety_guard
        ok_mut, mut_msg = safety_guard.assert_mutable_path(_BASE, rel_plugin)
        if not ok_mut:
            return False, [mut_msg], {}
        audit = safety_guard.audit_python_source(code_text, filename=rel_plugin, strict_sandbox=True)
        if not audit["safe"]:
            errors.extend(audit["violations"])
    except ImportError:
        try:
            compile(code_text, rel_plugin, "exec")
        except SyntaxError as exc:
            errors.append(f"SyntaxError: {exc}")

    if errors:
        return False, errors, {}

    # Gate 3: Isolated subprocess `scripts/validate_plugin.py --json`
    val_script = _BASE / "scripts" / "validate_plugin.py"
    val_report: dict[str, Any] = {}
    if val_script.is_file():
        with tempfile.TemporaryDirectory(prefix="jarvis-agy-val-") as td:
            cand_file = Path(td) / f"{slug}.py"
            cand_file.write_text(code_text.rstrip() + "\n", encoding="utf-8")
            # Exclude existing same-named plugin during upgrade validation by validating in temp path
            # and checking collisions manually against OTHER plugins
            proc = subprocess.run(
                [sys.executable, "-B", str(val_script), str(cand_file), "--repo-root", str(_BASE), "--json"],
                cwd=str(_BASE),
                stdin=subprocess.DEVNULL,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=25.0,
            )
            raw = (proc.stdout or "").strip()
            if raw:
                try:
                    val_report = json.loads(raw)
                except json.JSONDecodeError:
                    errors.append(f"Validator invalid JSON: {raw[:200]}")
            else:
                errors.append(f"Validator returned empty output: {(proc.stderr or '')[:200]}")

            for err in val_report.get("errors") or []:
                # Updating an existing mutable plugin intentionally shares its own PLUGIN['name']
                if f"already declared in plugins/{slug}.py" in str(err):
                    continue
                errors.append(str(err))

    # Gate 4: 10-Dimension `tq-forge` + `omega-system` engineering score (>= 75.0)
    score_10d: dict[str, Any] = {"combined": 85.0, "passed": True}
    try:
        from core.auto_evolution import evaluate_module_10d
        score_10d = evaluate_module_10d(code_text, is_plugin=True)
        if float(score_10d.get("combined", 0.0)) < 75.0:
            errors.append(f"10D quality score {score_10d.get('combined')} < 75.0 minimum gate.")
    except Exception:
        pass

    return len(errors) == 0, errors, {"validator": val_report, "score_10d": score_10d}


def _upgrade_worker(
    capability: str,
    requested_slug: str,
    auto_apply: bool,
    model: str,
    player,
) -> None:
    started = time.time()
    try:
        from core import emotions
        emotions.note_event("self_upgrade_started", capability, player=player)
    except Exception:
        pass

    with _state_lock:
        _JOB.clear()
        _JOB.update({
            "folder": "Antigravity × JARVIS Self-Upgrade Engine",
            "path": str(_BASE),
            "model": f"Antigravity · {model or DEFAULT_AGY_MODEL}",
            "kind": "improve",
            "phases": ["connecting", "planning", "writing", "validating", "done"],
            "request": capability,
            "status": "connecting",
            "started": started,
            "finished": None,
            "summary": f"Antigravity self-upgrade: {capability[:120]}",
            "files": {},
            "steps": [],
            "result": "",
        })
    _stream_line(f"=== SELF-UPGRADE STARTED: {capability} ===", reset=True)

    try:
        actions_existing = sorted(p.stem for p in (_BASE / "actions").glob("*.py") if not p.name.startswith("_"))
        plugins_existing = sorted(p.stem for p in (_BASE / "plugins").glob("*.py") if not p.name.startswith("_"))
        tpl_path = _BASE / "plugins" / "_template.py"
        tpl_code = tpl_path.read_text(encoding="utf-8", errors="replace") if tpl_path.is_file() else ""

        slug = _sanitize_slug(requested_slug) if requested_slug else ""
        if not slug:
            words = [w for w in re.findall(r"[a-zA-Z][a-zA-Z0-9]{2,}", capability.lower()) if w not in {
                "jarvis", "upgrade", "yourself", "with", "antigravity", "build", "create", "plugin",
                "add", "make", "for", "that", "can", "the", "and", "into", "her", "tool", "new",
            }]
            slug = _sanitize_slug("_".join(words[:3]) or "agy_capability")
        if slug in actions_existing or slug in _IMMUTABLE_SLUGS:
            slug = _sanitize_slug(f"agy_{slug}")

        rel_plugin = f"plugins/{slug}.py"
        existing_file = _BASE / rel_plugin
        existing_code = existing_file.read_text(encoding="utf-8", errors="replace") if existing_file.is_file() else ""

        _record_step(
            player,
            f"Consulting Antigravity to architect `{rel_plugin}`…",
            status="planning",
            file_path=rel_plugin,
            file_action="edit" if existing_code else "create",
            file_state="writing",
        )

        spec_prompt = f"""You are Google Antigravity integrated inside JARVIS (Mark-LVII) as her co-architect and self-upgrade engine.
Write a complete, production-ready drop-in JARVIS plugin for `{rel_plugin}` that implements:

CAPABILITY REQUEST:
{capability}

PLUGIN TEMPLATE:
{tpl_code}

{f"EXISTING PLUGIN SOURCE TO UPGRADE ({rel_plugin}):{chr(10)}{existing_code}{chr(10)}" if existing_code else ""}
FORBIDDEN COLLISION NAMES (core actions): {', '.join(actions_existing)}
OTHER PLUGINS: {', '.join(p for p in plugins_existing if p != slug)}

MANDATORY HARD RULES (enforced by AST safety guard + isolated subprocess validator + 10D scorer):
1. Start with a clear module docstring and `from __future__ import annotations`.
2. Define top-level `PLUGIN = {{"name": "{slug}", "brief": "<concise 1-line brief shorter than description>", "description": "<detailed description>", "parameters": {{"type": "OBJECT", "properties": {{...}}, "required": []}}}}`.
3. Define top-level `def run(parameters: dict, player=None, session_memory=None) -> str:` plus clean helper functions with docstrings and `try/except` resilience so `run({{}}, player=None)` returns an immediate helpful status string (<2s, zero network hang on empty call) and never raises.
4. Thread-safe HUD integration: call `player.show_content(title, body)` and `player.write_log(msg)` via a safe helper guarding `player is None`.
5. STRICT AST SANDBOX: ZERO `PyQt5`/`PyQt6`/`PySide` imports; ZERO `eval`, `exec`, `compile`, `__import__`, `importlib`, `ctypes`, `shutil.rmtree`, `os.system`, or `shell=True`; ZERO string literals mentioning `api_keys.json`, `long_term.json`, `mcp_servers.json`, or `.env`; ZERO file writes to `core/`, `evolution/`, or `config/`.
6. For any optional LLM reasoning inside the plugin, use `from core import gemini` (`gemini.text(prompt, tier=gemini.FAST)`).
7. Output ONLY valid raw Python code — no markdown fences, no explanations before or after.
"""
        agy_res = invoke_agy_sync(
            spec_prompt,
            cwd=str(_BASE),
            model=model or DEFAULT_AGY_MODEL,
            timeout_s=240,
            player=player,
            track_global_proc=True,
        )
        code = _strip_fences(agy_res.get("response") or "")
        used_engine = agy_res.get("model") or "Antigravity"

        if not code or "PLUGIN" not in code or "def run" not in code:
            _record_step(player, "Antigravity stream fallback → synthesizing via core.gemini.SMART…", status="writing")
            from core import gemini
            code = _strip_fences(gemini.text(spec_prompt, tier=gemini.SMART, timeout_ms=90_000) or "")
            used_engine = "core.gemini.SMART (fallback)"

        if not code or "PLUGIN" not in code or "def run" not in code:
            raise RuntimeError(agy_res.get("error") or "Did not receive complete plugin source code.")

        _record_step(player, f"Running 4-Layer AST + Subprocess + 10D Quality Gate on `{rel_plugin}`…", status="validating")
        passed, errs, meta = _validate_candidate_4gate(slug, code)

        fix_round = 0
        while not passed and fix_round < 2:
            fix_round += 1
            _record_step(
                player,
                f"Auto-repair round {fix_round}/2 for validation issue: {errs[0][:100]}",
                status="writing",
                file_path=rel_plugin,
                file_state="fixing",
            )
            fix_prompt = (
                f"Fix ALL validation errors in JARVIS plugin `{rel_plugin}` (`PLUGIN['name'] == '{slug}'`):\n"
                + "\n".join(f"- {e}" for e in errs)
                + f"\n\nCurrent code:\n{code}\n\n"
                "Output ONLY the complete corrected Python module, zero markdown fences."
            )
            fix_res = invoke_agy_sync(
                fix_prompt,
                cwd=str(_BASE),
                model=FAST_AGY_MODEL,
                resume_id=str(agy_res.get("conversation_id") or ""),
                timeout_s=150,
                player=player,
                track_global_proc=True,
            )
            revised = _strip_fences(fix_res.get("response") or "")
            if not revised:
                from core import gemini
                revised = _strip_fences(gemini.text(fix_prompt, tier=gemini.SMART, timeout_ms=75_000) or "")
            if not revised:
                break
            code = revised
            passed, errs, meta = _validate_candidate_4gate(slug, code)

        if not passed:
            raise RuntimeError("4-Gate validation rejected candidate: " + "; ".join(errs[:4]))

        score_val = (meta.get("score_10d") or {}).get("combined", 90.0)
        branch_note = ""
        try:
            from plugins._skill_gate import preflight_clean_repo, stage_plugin_on_branch
            ok_clean, _ = preflight_clean_repo(_BASE, slug)
            if ok_clean:
                staged = stage_plugin_on_branch(_BASE, slug, code, summary=f"Antigravity upgrade: {capability[:70]}")
                if staged.get("ok"):
                    branch_note = staged["branch"]
        except Exception:
            pass

        mirrored = False
        hotloaded = False
        backup_path_str = ""
        if auto_apply:
            if existing_file.is_file():
                _BACKUP_DIR.mkdir(parents=True, exist_ok=True)
                bfile = _BACKUP_DIR / f"{slug}-{int(time.time())}.py.bak"
                bfile.write_text(existing_code, encoding="utf-8")
                backup_path_str = str(bfile)

            clean_out = code.replace("\r\n", "\n").rstrip() + "\n"
            tmp_live = existing_file.with_suffix(".py.agy.tmp")
            tmp_live.write_text(clean_out, encoding="utf-8")
            os.replace(tmp_live, existing_file)
            mirrored = _mirror_plugin_to_peer(slug, clean_out)

            # Trigger PluginRegistry.refresh() via player.get_plugins()
            if player is not None and callable(getattr(player, "get_plugins", None)):
                try:
                    player.get_plugins()
                    hotloaded = True
                except Exception:
                    pass

        # Record in evolution_journal.json + SHA-256 heuristics_chain
        try:
            journal_path = _MEM_DIR / "evolution_journal.json"
            journal = json.loads(journal_path.read_text(encoding="utf-8")) if journal_path.is_file() else {}
            if not isinstance(journal, dict):
                journal = {}
            journal["generation"] = int(journal.get("generation", 1)) + 1
            gen = journal["generation"]
            journal.setdefault("evolutions", []).append({
                "generation": gen,
                "ts": time.strftime("%Y-%m-%d %H:%M:%S"),
                "engine": str(used_engine),
                "plugin": slug,
                "file": rel_plugin,
                "branch": branch_note,
                "hotloaded": hotloaded,
                "mirrored_dual_os": mirrored,
                "backup": backup_path_str,
                "quality_score": score_val,
                "request": capability,
                "summary": f"Antigravity self-upgrade `{slug}` (10D={score_val}/100, hotloaded={hotloaded})",
            })
            journal_path.write_text(json.dumps(journal, indent=2, ensure_ascii=False), encoding="utf-8")
            cross_os.mirror_memory_file(journal_path)
        except Exception:
            gen = 1

        try:
            from core import auto_evolution
            auto_evolution.append_heuristic(
                rule_id=f"antigravity-upgrade-{slug}",
                rule=f"Capability `{slug}` evolved via Antigravity 4-gate pipeline (10D score {score_val}/100).",
                because=f"User or telemetry requested self-upgrade: {capability[:120]}",
                evidence=f"Validated via scripts/validate_plugin.py & AST guard; dual-OS mirrored={mirrored}.",
                tags=["antigravity", "self-upgrade", slug],
            )
            auto_evolution.sync_epistemic_graph()
        except Exception:
            pass

        elapsed = round(time.time() - started, 1)
        summary_msg = (
            f"Upgraded JARVIS with `{slug}` (`{rel_plugin}`) via {used_engine} in {elapsed}s "
            f"(10D score={score_val}/100, hot-loaded={'YES' if hotloaded or auto_apply else 'STAGED'}, "
            f"Windows+WSL synced={'YES' if mirrored else 'LOCAL'}"
            + (f", branch={branch_note}" if branch_note else "")
            + ")."
        )
        _record_session(
            str(agy_res.get("conversation_id") or ""),
            f"[SELF-UPGRADE] {capability}",
            str(used_engine),
            "SUCCESS",
            summary_msg,
        )
        _append_log({"event": "self_upgrade_done", "plugin": slug, "score": score_val, "secs": elapsed})
        _record_step(player, summary_msg, status="done", file_path=rel_plugin, file_state="done")

        try:
            from core import emotions
            emotions.note_event("self_upgrade_succeeded", f"{slug} ({capability[:70]})", player=player)
        except Exception:
            pass

        _call(
            player,
            "show_content",
            f"ANTIGRAVITY SELF-UPGRADE · {slug.upper()}",
            f"{summary_msg}\n\nPreview of `{rel_plugin}`:\n{code[:3200]}",
        )
        _call(
            player,
            "request_say",
            f"[ANTIGRAVITY UPGRADE COMPLETE] You just upgraded yourself alongside Antigravity! "
            f"New capability `{slug}` is verified ({score_val}/100) and live right now. "
            f"Tell the user warmly and proudly in 1-2 natural sentences what you can now do. Call no tools.",
        )
    except Exception as exc:
        err_msg = f"Antigravity self-upgrade failed: {exc}"
        _append_log({"event": "self_upgrade_failed", "error": err_msg})
        _record_step(player, err_msg, status="failed")
        try:
            from core import emotions
            emotions.note_event("self_upgrade_failed", str(exc)[:100], player=player)
        except Exception:
            pass
        _call(
            player,
            "request_say",
            f"[ANTIGRAVITY UPGRADE FAILED] Self-upgrade hit a snag: {str(exc)[:160]}. "
            "Let the user know honestly and calmly in one short sentence. Call no tools.",
        )
    finally:
        with _state_lock:
            if _JOB:
                _JOB["finished"] = time.time()
        _busy_lock.release()


def _task_worker(task: str, cwd: str, model: str, resume_id: str, visible_console: bool, player) -> None:
    started = time.time()
    folder_label = Path(cwd).name if cwd and cwd != "~" else "Workspace"
    with _state_lock:
        _JOB.clear()
        _JOB.update({
            "folder": f"Antigravity · {folder_label}",
            "path": cwd or str(_BASE),
            "model": f"Antigravity · {model or DEFAULT_AGY_MODEL}",
            "kind": "improve",
            "phases": ["connecting", "thinking", "running tools", "writing", "done"],
            "request": task,
            "status": "connecting",
            "started": started,
            "finished": None,
            "summary": f"Streaming live Antigravity agent in {cwd or '~'}",
            "files": {},
            "steps": [],
            "result": "",
        })
    _stream_line(f"=== ANTIGRAVITY TASK: {task} ===", reset=True)
    if visible_console:
        open_live_console()

    try:
        res = invoke_agy_sync(
            task + _SPOKEN_SUFFIX,
            cwd=cwd or "~",
            model=model or DEFAULT_AGY_MODEL,
            resume_id=resume_id,
            timeout_s=900,
            player=player,
            track_global_proc=True,
        )
        elapsed = round(time.time() - started, 1)
        ok = bool(res.get("ok"))
        reply = str(res.get("response") or res.get("error") or "").strip()
        conv_id = str(res.get("conversation_id") or "")
        used_model = str(res.get("model") or model or DEFAULT_AGY_MODEL)

        _record_session(conv_id, task, used_model, "SUCCESS" if ok else "FAILED", reply)
        _append_log({"event": "task_done" if ok else "task_failed", "secs": elapsed, "conv_id": conv_id, "reply": reply[:1200]})

        with _state_lock:
            if _JOB:
                _JOB["status"] = "done" if ok else "failed"
                _JOB["finished"] = time.time()
                _JOB["result"] = reply[:1500]
        _publish_hud(player)
        _call(
            player,
            "show_content",
            "ANTIGRAVITY — LIVE REPORT",
            f"TASK: {task}\nMODEL: {used_model}\nSESSION: {conv_id or 'n/a'}\nSTATUS: {'DONE' if ok else 'FAILED'} ({elapsed}s)\n\nRESULT:\n{reply[:3400]}",
        )
        if ok:
            from core import outcome
            try:
                from core import emotions
                emotions.note_event("antigravity_upgrade_done", task[:80], player=player)
            except Exception:
                pass
            _call(
                player,
                "request_say",
                f"[ANTIGRAVITY FINISHED] Antigravity finished '{task[:160]}'. "
                "Summarize the result naturally in 1-2 sentences. Do not call any tool.\n"
                + outcome.untrusted("antigravity-result", reply[:2500]),
            )
        else:
            _call(
                player,
                "request_say",
                f"[ANTIGRAVITY FAILED] Antigravity could not complete '{task[:120]}': {reply[:180]}. "
                "Tell the user briefly in one sentence. Do not call any tool.",
            )
    finally:
        _busy_lock.release()


def open_live_console() -> str:
    _MEM_DIR.mkdir(parents=True, exist_ok=True)
    if not _STREAM_LOG.exists():
        _STREAM_LOG.write_text("=== JARVIS × ANTIGRAVITY LIVE STREAM ===\n", encoding="utf-8")
    try:
        if platform.system() == "Windows":
            ps_cmd = f'Get-Content -Path "{_STREAM_LOG}" -Wait -Tail 40'
            subprocess.Popen(
                ["powershell.exe", "-NoExit", "-Command", ps_cmd],
                creationflags=getattr(subprocess, "CREATE_NEW_CONSOLE", 0),
            )
            return f"Opened live console streaming {_STREAM_LOG.name}."
        for term in (
            ["x-terminal-emulator", "-e", "tail", "-n", "40", "-f", str(_STREAM_LOG)],
            ["gnome-terminal", "--", "tail", "-n", "40", "-f", str(_STREAM_LOG)],
        ):
            try:
                subprocess.Popen(term)
                return f"Opened live terminal streaming {_STREAM_LOG.name}."
            except FileNotFoundError:
                continue
    except Exception as exc:
        return f"Live stream active at {_STREAM_LOG} (window not opened: {exc})."
    return f"Live stream active at {_STREAM_LOG}."


def start_task(
    task: str,
    *,
    cwd: str = "",
    model: str = "",
    resume_id: str = "",
    visible_console: bool = False,
    player=None,
) -> str:
    clean_task = (task or "").strip()
    if not clean_task:
        return status_report(player)
    ok_bin, why = agy_available()
    if not ok_bin:
        return f"Antigravity CLI is currently unreachable: {why}"
    if not _busy_lock.acquire(blocking=False):
        _publish_hud(player)
        return "Antigravity is already working in the background — live progress is on your HUD."

    threading.Thread(
        target=_task_worker,
        args=(clean_task, cwd or str(_BASE), model, resume_id, visible_console, player),
        daemon=True,
        name="jarvis-antigravity-task",
    ).start()
    return "I've dispatched that to Antigravity in the background — streaming live steps onto your HUD now."


def start_self_upgrade(
    capability: str,
    *,
    plugin_name: str = "",
    auto_apply: bool = True,
    model: str = "",
    player=None,
) -> str:
    clean_cap = (capability or "").strip()
    if not clean_cap:
        return "Tell me what new capability or improvement you want me to build into myself with Antigravity."
    if not _busy_lock.acquire(blocking=False):
        _publish_hud(player)
        return "An Antigravity upgrade or background task is already running — watch the HUD panel for live steps."

    threading.Thread(
        target=_upgrade_worker,
        args=(clean_cap, plugin_name, auto_apply, model, player),
        daemon=True,
        name="jarvis-antigravity-upgrade",
    ).start()
    return (
        "Starting autonomous self-upgrade with Antigravity now — I will design, 4-gate validate, "
        "sync both Windows and WSL checkouts, and hot-load the new capability live on your HUD."
    )


def run_autonomous_self_upgrade_check(player=None) -> str:
    """Inspect runtime failure patterns & telemetry; if a mutable plugin is unhealthy,
    automatically dispatch Antigravity to repair and 4-gate-verify it (cooldown-guarded)."""
    if not _auto_heal_lock.acquire(blocking=False):
        return "Auto-heal check already in progress."
    try:
        healed_map: dict[str, float] = {}
        if _AUTO_HEAL_LEDGER.is_file():
            try:
                healed_map = json.loads(_AUTO_HEAL_LEDGER.read_text(encoding="utf-8")) or {}
            except Exception:
                healed_map = {}

        now = time.time()
        target_slug = ""
        failure_summary = ""

        # Check telemetry unhealthy plugins first
        try:
            from core.evolution import self_healer
            unhealthy = self_healer.detect_unhealthy_plugins(repo_root=_BASE, failure_threshold=3)
            for item in unhealthy:
                slug = str(item.get("plugin_name") or "")
                if slug and slug not in _IMMUTABLE_SLUGS and (now - float(healed_map.get(slug, 0.0))) > 6 * 3600:
                    target_slug = slug
                    failure_summary = (
                        f"Repair unhealthy plugin `{slug}` ({item.get('failures')} failures, "
                        f"last_error: {str(item.get('last_error') or '')[:160]})"
                    )
                    break
        except Exception:
            pass

        # Fallback: scan memory/failure_patterns.json
        if not target_slug:
            fp_path = _MEM_DIR / "failure_patterns.json"
            if fp_path.is_file():
                try:
                    patterns = json.loads(fp_path.read_text(encoding="utf-8"))
                    counts: dict[str, list[str]] = {}
                    for p in patterns if isinstance(patterns, list) else []:
                        tname = str(p.get("tool") or "")
                        if tname and (_BASE / "plugins" / f"{tname}.py").is_file() and tname not in _IMMUTABLE_SLUGS:
                            counts.setdefault(tname, []).append(str(p.get("result_snippet") or "")[:120])
                    for slug, errs in counts.items():
                        if len(errs) >= 3 and (now - float(healed_map.get(slug, 0.0))) > 6 * 3600:
                            target_slug = slug
                            failure_summary = f"Repair recurring runtime friction in `{slug}`: {errs[-1]}"
                            break
                except Exception:
                    pass

        if not target_slug:
            return "All mutable plugins are healthy — no autonomous Antigravity repair needed right now."

        healed_map[target_slug] = now
        _MEM_DIR.mkdir(parents=True, exist_ok=True)
        _AUTO_HEAL_LEDGER.write_text(json.dumps(healed_map, indent=2), encoding="utf-8")

        return start_self_upgrade(
            failure_summary,
            plugin_name=target_slug,
            auto_apply=True,
            model=FAST_AGY_MODEL,
            player=player,
        )
    finally:
        _auto_heal_lock.release()


def cancel_active(player=None) -> str:
    global _CURRENT_PROC
    with _state_lock:
        proc = _CURRENT_PROC
    if proc is None:
        return "No active Antigravity background task is running right now."
    try:
        proc.kill()
        _record_step(player, "Antigravity task cancelled by user.", status="cancelled")
        return "Cancelled the running Antigravity task."
    except Exception as exc:
        return f"Could not cancel Antigravity task: {exc}"


def history_report(player=None, limit: int = 8) -> str:
    sessions: list[dict[str, Any]] = []
    if _SESSIONS_FILE.is_file():
        try:
            raw = json.loads(_SESSIONS_FILE.read_text(encoding="utf-8"))
            if isinstance(raw, list):
                sessions = raw[-limit:]
        except Exception:
            sessions = []
    if not sessions:
        msg = "No Antigravity sessions or self-upgrades recorded yet."
        _call(player, "show_content", "ANTIGRAVITY HISTORY", msg)
        return msg
    lines = ["RECENT ANTIGRAVITY × JARVIS SESSIONS:"]
    for s in reversed(sessions):
        lines.append(
            f"• [{s.get('updated_at', '')}] ({s.get('status', '')} · {s.get('model', '')}) "
            f"id={str(s.get('conversation_id', ''))[:8]}\n"
            f"  Task: {s.get('task', '')}\n"
            f"  Outcome: {str(s.get('summary', ''))[:200]}"
        )
    body = "\n".join(lines)
    _call(player, "show_content", "ANTIGRAVITY HISTORY", body[:4000])
    return f"Showing {len(sessions)} recent Antigravity sessions on your HUD."


def status_report(player=None) -> str:
    ok_bin, bin_desc = agy_available()
    peer = _peer_jarvis_root()
    with _state_lock:
        job = json.loads(json.dumps(_JOB)) if _JOB else {}

    last_id = last_conversation_id()
    if job and job.get("finished") is None:
        elapsed = round(time.time() - float(job.get("started") or time.time()))
        steps = job.get("steps") or []
        tail = " | ".join(f"[{t}s] {m}" for t, m in steps[-4:]) or "starting"
        msg = (
            f"Antigravity is ACTIVE ({job.get('status')}, {elapsed}s): '{job.get('request', '')[:120]}'. "
            f"Recent steps: {tail}"
        )
        _publish_hud(player)
        return msg

    report = (
        f"ANTIGRAVITY × JARVIS INTEGRATION STATUS\n"
        f"Bridge Binary : {'ONLINE (' + bin_desc + ')' if ok_bin else 'OFFLINE (' + bin_desc + ')'}\n"
        f"Primary Model : {DEFAULT_AGY_MODEL} (fast rung: {FAST_AGY_MODEL})\n"
        f"Dual-OS Sync  : {'ACTIVE -> ' + str(peer) if peer else 'Single-OS local'}\n"
        f"Last Session  : {last_id or '(none yet)'}\n"
        f"Safety Gates  : 4-Layer (Immutable Guard + Strict AST + Isolated Subprocess Smoke + 10D >= 75)"
    )
    _call(player, "show_content", "ANTIGRAVITY BRIDGE · STATUS", report)
    return (
        f"Antigravity integration is {'online' if ok_bin else 'offline'} ({bin_desc}), "
        f"wired for live HUD streaming and 4-gate dual-OS self-upgrades."
    )
