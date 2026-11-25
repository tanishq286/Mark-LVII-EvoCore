"""Live-streaming bridge between JARVIS and Claude Code / WSL coding agents.

Streams real-time tool calls, file edits, reasoning steps, and outputs directly
into the JARVIS HUD (via player.show_code_job, player.show_content, and
player.write_log) as well as memory/claude_code.log and
memory/live_agent_stream.log, so the user can always see where and how JARVIS
is working in the background.
"""
from __future__ import annotations

import json
import os
import platform
import subprocess
import threading
import time
from pathlib import Path

PLUGIN = {
    "name": "claude_code",
    "description": (
        "Send a task to Claude Code (or check its live background status, stream, or history), "
        "with real-time visual progress on the JARVIS HUD. "
        "Actions: "
        "'run' (default when task is given) = start a task in the background and stream live steps to the HUD; "
        "'status' = inspect what the background agent is doing right now, which files it touched, and recent steps; "
        "'history' = display the log of recent background sessions on screen; "
        "'watch' = open a visible live console window showing the real-time agent stream; "
        "'cancel' = stop the currently running background task. "
        "Use it when the user asks to work with Claude Code, check where or what the background agent is doing, "
        "show the live background work, or perform complex multi-file coding and account tasks."
    ),
    "brief": "Run, watch, check status/history of, or cancel a Claude Code background coding task.",
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "action": {
                "type": "STRING",
                "enum": ["status", "run", "history", "watch", "cancel"],
                "description": "Operation to perform. Defaults to 'run' when a task is provided, or 'status' otherwise.",
            },
            "task": {
                "type": "STRING",
                "description": "For 'run': the full task in the user's words, with every detail they gave.",
            },
            "cwd": {
                "type": "STRING",
                "description": "Optional project folder path to work inside.",
            },
            "visible_console": {
                "type": "BOOLEAN",
                "description": "If true, also pops open a visible terminal window streaming the live output.",
            },
        },
    },
}

_BASE = Path(__file__).resolve().parent.parent
_LOG = _BASE / "memory" / "claude_code.log"
_STREAM_LOG = _BASE / "memory" / "live_agent_stream.log"
_TIMEOUT_S = 30 * 60
_SPOKEN = (
    "You were called by voice from the JARVIS desktop assistant. Do the task fully, "
    "then end with a reply of at most 120 words of plain spoken English: no "
    "markdown, no code blocks, no file listings."
)

_busy = threading.Lock()
_state_lock = threading.Lock()
_CURRENT_PROC: subprocess.Popen | None = None
_JOB: dict = {}


def _mode() -> str:
    try:
        from memory.config_manager import load_api_keys
        return load_api_keys().get("claude_code_permission_mode") or "bypassPermissions"
    except Exception:
        return "bypassPermissions"


def _to_wsl_path(raw_path: str) -> str:
    p = (raw_path or "").strip().strip('"')
    if not p:
        return "~"
    if len(p) >= 2 and p[1] == ":":
        drive = p[0].lower()
        rest = p[2:].replace("\\", "/").lstrip("/")
        return f"/mnt/{drive}/{rest}"
    return p.replace("\\", "/")


def _command(mode: str, stream_json: bool = True, wsl_cwd: str = "~") -> list[str]:
    fmt = ["stream-json", "--verbose"] if stream_json else ["text"]
    args = [
        "-p",
        "--permission-mode",
        mode,
        "--output-format",
        *fmt,
        "--append-system-prompt",
        _SPOKEN,
    ]
    if platform.system() == "Windows":
        return [
            "wsl.exe",
            "-d",
            "Ubuntu-D",
            "--cd",
            wsl_cwd,
            "-e",
            "bash",
            "-lc",
            'export PATH="$HOME/.local/bin:$HOME/.local/opt/node/bin:$HOME/bin:$PATH"; exec claude "$@"',
            "claude",
            *args,
        ]
    return ["claude", *args]


def _log(entry: dict) -> None:
    try:
        _LOG.parent.mkdir(parents=True, exist_ok=True)
        with _LOG.open("a", encoding="utf-8") as f:
            f.write(json.dumps({"ts": time.strftime("%Y-%m-%d %H:%M:%S"), **entry}, ensure_ascii=False) + "\n")
    except Exception:
        pass


def _stream_write(line: str, reset: bool = False) -> None:
    try:
        _STREAM_LOG.parent.mkdir(parents=True, exist_ok=True)
        mode = "w" if reset else "a"
        with _STREAM_LOG.open(mode, encoding="utf-8") as f:
            ts = time.strftime("%H:%M:%S")
            f.write(f"[{ts}] {line}\n")
    except Exception:
        pass


def _call(player, method: str, *args) -> None:
    fn = getattr(player, method, None) if player else None
    if callable(fn):
        try:
            fn(*args)
        except Exception as e:
            print(f"[claude_code] {method} failed: {e}")


def _publish_hud(player) -> None:
    if not player:
        return
    with _state_lock:
        if not _JOB:
            return
        snap = json.loads(json.dumps(_JOB))
    _call(player, "show_code_job", snap)


def _record_step(player, msg: str, status: str | None = None,
                 file_path: str | None = None, file_action: str = "edit",
                 file_state: str = "writing", file_what: str = "") -> None:
    _stream_write(msg)
    with _state_lock:
        if _JOB:
            if status:
                _JOB["status"] = status
            elapsed = round(time.time() - _JOB.get("started", time.time()), 1)
            _JOB.setdefault("steps", []).append([elapsed, msg])
            del _JOB["steps"][:-60]
            if file_path:
                f_entry = _JOB.setdefault("files", {}).setdefault(
                    file_path,
                    {"action": file_action, "what": file_what or msg[:80], "state": file_state},
                )
                f_entry["state"] = file_state
                if file_what:
                    f_entry["what"] = file_what
    _publish_hud(player)
    _call(player, "write_log", f"SYS: [Claude] {msg[:140]}")


def _open_live_console() -> str:
    _STREAM_LOG.parent.mkdir(parents=True, exist_ok=True)
    if not _STREAM_LOG.exists():
        _STREAM_LOG.write_text("=== JARVIS LIVE BACKGROUND AGENT STREAM ===\n", encoding="utf-8")
    try:
        if platform.system() == "Windows":
            ps_cmd = f'Get-Content -Path "{_STREAM_LOG}" -Wait -Tail 40'
            subprocess.Popen(
                ["powershell.exe", "-NoExit", "-Command", ps_cmd],
                creationflags=getattr(subprocess, "CREATE_NEW_CONSOLE", 0),
            )
            return f"Opened live background console streaming {_STREAM_LOG.name}."
        for term in (
            ["x-terminal-emulator", "-e", "tail", "-n", "40", "-f", str(_STREAM_LOG)],
            ["gnome-terminal", "--", "tail", "-n", "40", "-f", str(_STREAM_LOG)],
            ["xterm", "-e", "tail", "-n", "40", "-f", str(_STREAM_LOG)],
        ):
            try:
                subprocess.Popen(term)
                return f"Opened live terminal streaming {_STREAM_LOG}."
            except FileNotFoundError:
                continue
    except Exception as e:
        return f"Live stream file is at {_STREAM_LOG} (could not open separate terminal window: {e})."
    return f"Live stream log is active at {_STREAM_LOG}."


def _summarize_tool_use(name: str, inp: dict) -> tuple[str, str | None, str, str]:
    """Return (step_message, file_path, file_action, file_state)."""
    inp = inp or {}
    low = (name or "").lower()
    path = inp.get("file_path") or inp.get("path") or inp.get("notebook_path")
    if path:
        path = str(path)
    if low in ("read", "read_file", "view"):
        return f"Reading {path or 'file'}", path, "edit", "waiting"
    if low in ("write", "write_file", "create"):
        return f"Writing {path or 'new file'}", path, "create", "writing"
    if low in ("edit", "multiedit", "edit_file", "replace"):
        return f"Editing {path or 'file'}", path, "edit", "writing"
    if low in ("bash", "run_command", "shell"):
        cmd = str(inp.get("command") or inp.get("cmd") or "")[:90]
        return f"Running command: {cmd}", None, "edit", "writing"
    if low in ("grep", "glob", "search", "find"):
        pat = str(inp.get("pattern") or inp.get("query") or "")[:60]
        return f"Searching codebase ({name}): {pat}", None, "edit", "waiting"
    return f"Using tool {name}", path, "edit", "writing"


def _handle_stream_line(line: str, player, collected_texts: list[str]) -> str | None:
    """Parse one line from `claude --output-format stream-json --verbose`.
    Returns final result string if a result event was received."""
    text = line.strip()
    if not text:
        return None
    if not text.startswith("{"):
        collected_texts.append(text)
        _record_step(player, text[:140], status="running")
        return None
    try:
        ev = json.loads(text)
    except Exception:
        collected_texts.append(text)
        _record_step(player, text[:140], status="running")
        return None

    etype = str(ev.get("type") or "")
    if etype == "system":
        sub = ev.get("subtype") or "init"
        model = ev.get("model") or ""
        if model:
            with _state_lock:
                if _JOB:
                    _JOB["model"] = str(model)
        _record_step(player, f"Session {sub} ({model or 'connected'})", status="connecting")
    elif etype == "assistant":
        msg = ev.get("message") or {}
        for part in msg.get("content") or []:
            if not isinstance(part, dict):
                continue
            ptype = part.get("type")
            if ptype == "thinking":
                thought = " ".join(str(part.get("thinking") or "").split())
                if thought:
                    _record_step(player, f"Thinking: {thought[:110]}…", status="thinking")
            elif ptype == "text":
                t = str(part.get("text") or "").strip()
                if t:
                    collected_texts.append(t)
                    first_line = " ".join(t.splitlines()[0].split())[:120]
                    _record_step(player, f"Reply: {first_line}", status="writing")
            elif ptype == "tool_use":
                tname = str(part.get("name") or "tool")
                tinp = part.get("input") if isinstance(part.get("input"), dict) else {}
                step_msg, fpath, faction, fstate = _summarize_tool_use(tname, tinp)
                _record_step(player, f"🔧 {step_msg}", status="running tools",
                             file_path=fpath, file_action=faction, file_state=fstate,
                             file_what=step_msg)
                _log({"event": "tool", "tool": tname, "summary": step_msg})
    elif etype == "user":
        # Tool result returning to the model
        with _state_lock:
            for f in (_JOB.get("files") or {}).values():
                if f.get("state") == "writing":
                    f["state"] = "done"
        _publish_hud(player)
    elif etype == "result":
        res = str(ev.get("result") or "").strip()
        if res:
            return res
    return None


def _work(task: str, cwd: str, visible_console: bool, player) -> None:
    global _CURRENT_PROC
    mode = _mode()
    wsl_cwd = _to_wsl_path(cwd) if platform.system() == "Windows" else (cwd or str(Path.home()))
    folder_label = Path(cwd).name if cwd else "WSL Workspace (~)"
    started = time.time()

    with _state_lock:
        _JOB.clear()
        _JOB.update({
            "folder": f"Claude Code · {folder_label}",
            "path": cwd or str(Path.home()),
            "model": "Claude Code (Live Stream)",
            "kind": "improve",
            "phases": ["connecting", "thinking", "running tools", "writing", "done"],
            "request": task,
            "status": "connecting",
            "started": started,
            "finished": None,
            "summary": f"Streaming live background session in {wsl_cwd} (log: memory/live_agent_stream.log)",
            "files": {},
            "steps": [],
            "result": "",
        })
    _stream_write(f"=== TASK STARTED: {task} (mode={mode}, cwd={wsl_cwd}) ===", reset=True)
    _log({"event": "start", "mode": mode, "cwd": wsl_cwd, "task": task})
    _record_step(player, f"Launching background agent in {wsl_cwd}…", status="connecting")

    if visible_console:
        _open_live_console()

    collected: list[str] = []
    final_result: str | None = None
    err_lines: list[str] = []
    ok = False

    try:
        flags = subprocess.CREATE_NO_WINDOW if platform.system() == "Windows" else 0
        run_cwd = str(Path.home())
        if platform.system() != "Windows" and cwd and Path(cwd).is_dir():
            run_cwd = str(Path(cwd))

        cmd = _command(mode, stream_json=True, wsl_cwd=wsl_cwd)
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
        with _state_lock:
            _CURRENT_PROC = proc

        if proc.stdin:
            proc.stdin.write(task)
            proc.stdin.close()

        def _read_stderr():
            if not proc.stderr:
                return
            for eline in proc.stderr:
                s = eline.strip()
                if s:
                    err_lines.append(s)
                    _stream_write(f"[stderr] {s}")

        stderr_thread = threading.Thread(target=_read_stderr, daemon=True)
        stderr_thread.start()

        deadline = started + _TIMEOUT_S
        if proc.stdout:
            for raw_line in proc.stdout:
                if time.time() > deadline:
                    proc.kill()
                    raise subprocess.TimeoutExpired(cmd, _TIMEOUT_S)
                res = _handle_stream_line(raw_line, player, collected)
                if res:
                    final_result = res

        proc.wait(timeout=30)
        stderr_thread.join(timeout=2)
        result_text = (final_result or "\n".join(collected)).strip()
        ok = proc.returncode == 0 and bool(result_text)
        if not ok and not result_text:
            result_text = "\n".join(err_lines).strip() or f"exit code {proc.returncode}"
    except subprocess.TimeoutExpired:
        ok = False
        result_text = f"it ran longer than {_TIMEOUT_S // 60} minutes and was stopped"
    except FileNotFoundError as e:
        ok = False
        result_text = f"Claude Code was not found ({e})"
    except Exception as e:
        ok = False
        result_text = str(e)
    finally:
        with _state_lock:
            _CURRENT_PROC = None
        _busy.release()

    elapsed = round(time.time() - started)
    _stream_write(f"=== TASK {'FINISHED' if ok else 'FAILED'} in {elapsed}s ===\n{result_text[:2000]}")
    _log({
        "event": "done" if ok else "failed",
        "secs": elapsed,
        "result": result_text[:2000],
    })

    with _state_lock:
        if _JOB:
            _JOB["status"] = "done" if ok else "failed"
            _JOB["finished"] = time.time()
            _JOB["result"] = result_text[:1500]
            for f in (_JOB.get("files") or {}).values():
                if f.get("state") in ("writing", "waiting"):
                    f["state"] = "done" if ok else "broken"
            steps_tail = "\n".join(f"  [{t}s] {m}" for t, m in _JOB.get("steps", [])[-15:])
        else:
            steps_tail = ""
    _publish_hud(player)

    from core import outcome
    panel_body = (
        f"TASK: {task}\n"
        f"STATUS: {'DONE' if ok else 'FAILED'} ({elapsed}s)\n"
        f"LIVE LOG: {_STREAM_LOG}\n"
        + (f"\nRECENT LIVE STEPS:\n{steps_tail}\n" if steps_tail else "")
        + f"\nRESULT:\n{result_text}"
    )
    _call(player, "show_content", "CLAUDE CODE — LIVE REPORT", panel_body[:4000])
    if ok:
        _call(player, "write_log", f"SYS: Claude Code finished in {elapsed}s")
        _call(
            player,
            "request_say",
            "[CLAUDE CODE FINISHED] The task the user gave Claude Code is done: "
            f"'{task[:200]}'. Tell the user what came of it in one to three sentences, "
            "in their language. The full reply and step trace are on the screen. Do not call any tool for this.\n"
            + outcome.untrusted("claude-code-result", result_text[:3000]),
        )
    else:
        _call(player, "write_log", f"ERR: Claude Code failed — {result_text[:160]}")
        _call(
            player,
            "request_say",
            f"[CLAUDE CODE FAILED] Claude Code could not finish '{task[:120]}': {result_text[:200]}. "
            "Tell the user in one short sentence. Do not call any tool for this.",
        )


def _status_report(player) -> str:
    with _state_lock:
        job = json.loads(json.dumps(_JOB)) if _JOB else {}
    if not job:
        return _history_report(player, limit=4)
    running = job.get("finished") is None
    elapsed = round((job.get("finished") or time.time()) - job.get("started", time.time()))
    steps = job.get("steps") or []
    recent = " | ".join(f"[{t}s] {m}" for t, m in steps[-5:]) or "warming up"
    files = ", ".join(f"{k} ({v.get('state')})" for k, v in (job.get("files") or {}).items()) or "none yet"
    _publish_hud(player)
    state_word = f"RUNNING ({job.get('status')}, {elapsed}s elapsed)" if running else f"FINISHED ({job.get('status')} in {elapsed}s)"
    report = (
        f"Background Agent Status: {state_word}\n"
        f"Task: {job.get('request', '')}\n"
        f"Files touched: {files}\n"
        f"Recent live steps: {recent}\n"
        f"Live stream file: {_STREAM_LOG}"
    )
    if job.get("result"):
        report += f"\nResult: {job['result'][:600]}"
    _call(player, "show_content", "BACKGROUND AGENT STATUS", report[:4000])
    return report


def _history_report(player, limit: int = 8) -> str:
    if not _LOG.exists():
        msg = "No background Claude Code tasks have been recorded yet."
        _call(player, "show_content", "CLAUDE CODE HISTORY", msg)
        return msg
    try:
        lines = _LOG.read_text(encoding="utf-8", errors="replace").splitlines()
    except Exception as e:
        return f"Could not read claude_code.log: {e}"
    entries = []
    for ln in reversed(lines):
        try:
            entries.append(json.loads(ln))
        except Exception:
            continue
        if len(entries) >= limit:
            break
    entries.reverse()
    out = [f"Log file: {_LOG}", f"Live stream file: {_STREAM_LOG}", ""]
    for e in entries:
        ts = e.get("ts", "")
        ev = e.get("event", "")
        if ev == "start":
            out.append(f"[{ts}] ▶ START ({e.get('mode', '')}): {e.get('task', '')}")
        elif ev == "tool":
            out.append(f"[{ts}] 🔧 TOOL ({e.get('tool', '')}): {e.get('summary', '')}")
        elif ev in ("done", "failed"):
            out.append(f"[{ts}] ■ {ev.upper()} ({e.get('secs', 0)}s): {str(e.get('result', ''))[:260]}")
    text = "\n".join(out)
    _call(player, "show_content", "CLAUDE CODE — BACKGROUND LOG", text[:4000])
    return text[:1500]


def _cancel_task(player) -> str:
    global _CURRENT_PROC
    with _state_lock:
        proc = _CURRENT_PROC
    if proc is None:
        return "No background Claude Code task is currently running."
    try:
        proc.kill()
        _record_step(player, "Task cancelled by user.", status="cancelled")
        return "Cancelled the running Claude Code background task."
    except Exception as e:
        return f"Could not cancel the background task: {e}"


def run(parameters: dict, player=None, session_memory=None) -> str:
    params = parameters or {}
    task = (params.get("task") or "").strip()
    raw_action = (params.get("action") or "").strip().lower()
    action = raw_action or ("run" if task else "status")
    cwd = (params.get("cwd") or "").strip()
    visible_console = bool(params.get("visible_console", False))

    if action == "status":
        return _status_report(player)
    if action == "history":
        return _history_report(player)
    if action == "watch":
        _publish_hud(player)
        return _open_live_console()
    if action == "cancel":
        return _cancel_task(player)

    if not task:
        return _status_report(player)
    if not _busy.acquire(blocking=False):
        _publish_hud(player)
        return (
            "Claude Code is currently working in the background — I've put its live "
            "progress panel on your HUD. Ask for status or watch to inspect every step."
        )
    threading.Thread(
        target=_work,
        args=(task, cwd, visible_console, player),
        daemon=True,
        name="claude-code-live",
    ).start()
    _call(player, "write_log", f"SYS: Sent to Claude Code (live stream) — {task[:120]}")
    return (
        "I've started that in the background with live streaming on your HUD panel "
        "so you can watch every tool call and file change in real time."
    )
