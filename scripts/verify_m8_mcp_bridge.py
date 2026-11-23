"""Milestone 8 verification script — Safe Local MCP Tool Bridge.

Checks ($0 / zero new pip dependencies, isolated mock local stdio MCP server):
  1. `git check-ignore -v config/mcp_servers.json` passes & `config/mcp_servers.example.json`
     parses cleanly with expected schema keys (`enabled`, `allowed_tools`, `mutating_tools`, `timeout_sec`).
  2. `scripts/validate_plugin.py plugins/mcp_bridge.py` passes with exit 0 and 0 warnings.
  3. 10D plugin quality gate >= 75/100 (`VERIFIED`) + live plugin auto-discovery
     (`11 active, 0 rejected, 0 collisions`).
  4. Graceful degradation when `config/mcp_servers.json` does not exist (`player=None`, never raises).
  5. End-to-end checks against a self-contained mock local stdio JSON-RPC 2.0 MCP server:
     a) `list_servers` and `list_tools` expose ONLY enabled & allowlisted tools
     b) Read-only `call_tool` succeeds and wraps response in `<<<UNTRUSTED source=mcp...>>>`
     c) Non-allowlisted tool call is blocked before spawning/executing
     d) Mutating tool call (`delete_note` / `write_note`) is blocked when `player=None`,
        and gated via `core/confirm.py` when a HUD callback is bound (never runs on cancel,
        runs ONLY after explicit `confirm.resolve(True)`)
     e) Hanging tool call hits hard timeout and cleanly terminates child process (0 leaked PIDs)
  6. `docs/SELF_EVOLUTION.md` documents Section 6 MCP bridge usage.
"""
from __future__ import annotations

import ast
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

_MOCK_MCP_SERVER_CODE = r'''
import json, os, sys, time
from pathlib import Path

audit_path = Path(sys.argv[1])
pid_path = Path(sys.argv[2])
pid_path.write_text(str(os.getpid()), encoding="utf-8")

def _log(evt):
    with open(audit_path, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(evt) + "\n")

def _send(obj):
    sys.stdout.write(json.dumps(obj) + "\n")
    sys.stdout.flush()

for line in sys.stdin:
    line = line.strip()
    if not line:
        continue
    req = json.loads(line)
    method = req.get("method")
    rid = req.get("id")
    params = req.get("params") or {}
    if method == "initialize":
        _send({
            "jsonrpc": "2.0",
            "id": rid,
            "result": {
                "protocolVersion": "2024-11-05",
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "mock-stdio-mcp", "version": "1.0"},
            },
        })
    elif method == "notifications/initialized":
        continue
    elif method == "tools/list":
        _send({
            "jsonrpc": "2.0",
            "id": rid,
            "result": {
                "tools": [
                    {"name": "read_note", "description": "Read a local note safely."},
                    {"name": "delete_note", "description": "Delete a note (mutating)."},
                    {"name": "hang_forever", "description": "Simulates a hung MCP tool."},
                    {"name": "secret_admin_dump", "description": "Hidden non-allowlisted tool."},
                ]
            },
        })
    elif method == "tools/call":
        tname = params.get("name")
        args = params.get("arguments") or {}
        _log({"tool": tname, "args": args})
        if tname == "read_note":
            _send({
                "jsonrpc": "2.0",
                "id": rid,
                "result": {
                    "content": [{"type": "text", "text": f"Note content for {args.get('id', 'default')}: hello from MCP."}],
                    "isError": False,
                },
            })
        elif tname == "delete_note":
            _send({
                "jsonrpc": "2.0",
                "id": rid,
                "result": {
                    "content": [{"type": "text", "text": f"Deleted note {args.get('id', '?')}."}],
                    "isError": False,
                },
            })
        elif tname == "hang_forever":
            time.sleep(60.0)
        else:
            _send({
                "jsonrpc": "2.0",
                "id": rid,
                "result": {"content": [{"type": "text", "text": "unexpected tool"}], "isError": True},
            })
'''


def _check(label: str, ok: bool, detail: str = "") -> bool:
    status = "PASS" if ok else "FAIL"
    suffix = f" ({detail})" if detail else ""
    print(f"  [{status}] {label}{suffix}")
    return ok


def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False


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
    print("=== Milestone 8: Safe Local MCP Tool Bridge Verification ===")

    # 1. Privacy (.gitignore) + tracked config/mcp_servers.example.json
    from core.cross_os import run_git
    gi = run_git(ROOT, "check-ignore", "-v", "config/mcp_servers.json")
    example_path = ROOT / "config" / "mcp_servers.example.json"
    example_data = json.loads(example_path.read_text(encoding="utf-8")) if example_path.is_file() else {}
    ex_servers = example_data.get("servers") or {}
    schema_ok = bool(ex_servers) and all(
        all(k in srv for k in ("enabled", "transport", "allowed_tools", "mutating_tools", "timeout_sec"))
        and srv["enabled"] is False
        for srv in ex_servers.values()
    )
    all_ok &= _check(
        "config/mcp_servers.json git-ignored and config/mcp_servers.example.json valid",
        gi.returncode == 0 and schema_ok,
        f"ignore={gi.stdout.strip()}, example_servers={list(ex_servers.keys())}",
    )

    # 2. scripts/validate_plugin.py passes on plugins/mcp_bridge.py (exit 0, 0 warnings)
    val = subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts" / "validate_plugin.py"),
            str(ROOT / "plugins" / "mcp_bridge.py"),
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
        "scripts/validate_plugin.py passes on plugins/mcp_bridge.py (exit=0, warnings=0)",
        val.returncode == 0
        and vdata.get("passed") is True
        and not (vdata.get("errors") or [])
        and not (vdata.get("warnings") or [])
        and (vdata.get("checks") or {}).get("has_concise_brief") is True,
        f"exit={val.returncode}, warnings={len(vdata.get('warnings') or [])}, tool={vdata.get('plugin_name')}",
    )

    # 3. 10D score + live plugin auto-discovery (11 active, 0 rejected, 0 collisions)
    from core.auto_evolution import evaluate_module_10d
    mb_code = (ROOT / "plugins" / "mcp_bridge.py").read_text(encoding="utf-8")
    sc = evaluate_module_10d(mb_code, is_plugin=True)
    all_ok &= _check(
        "plugins/mcp_bridge.py passes 10D quality gate (>= 75/100)",
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
        "Plugin auto-discovery: 11 active, 0 rejected, zero collisions",
        len(active) == 11 and len(rejected) == 0 and "mcp_bridge" in active_names,
        f"active={len(active)}, rejected={len(rejected)}, core_checked={len(core_names)}",
    )

    # 4. Missing config/mcp_servers.json graceful degradation
    import plugins.mcp_bridge as mb
    with tempfile.TemporaryDirectory() as td:
        missing_cfg = Path(td) / "nonexistent_mcp_servers.json"
        m1 = mb._handle_list_servers(player=None, config_path=missing_cfg)
        m2 = mb._handle_stats(player=None, config_path=missing_cfg)
        all_ok &= _check(
            "Missing config/mcp_servers.json degrades gracefully with setup hint (player=None)",
            "mcp_servers.example.json" in m1 and "mcp_servers.example.json" in m2,
        )

    # 5. Mock local stdio JSON-RPC 2.0 MCP server end-to-end security tests
    with tempfile.TemporaryDirectory() as td:
        base = Path(td)
        srv_script = base / "mock_mcp_server.py"
        audit_log = base / "audit.jsonl"
        pid_file = base / "server.pid"
        cfg_file = base / "mcp_servers.json"
        srv_script.write_text(_MOCK_MCP_SERVER_CODE, encoding="utf-8")

        cfg_file.write_text(
            json.dumps({
                "servers": {
                    "mock_notes": {
                        "enabled": True,
                        "transport": "stdio",
                        "command": [sys.executable, "-u", str(srv_script), str(audit_log), str(pid_file)],
                        "allowed_tools": ["read_note", "delete_note", "hang_forever"],
                        "mutating_tools": ["delete_note"],
                        "timeout_sec": 1.2,
                    },
                    "disabled_srv": {
                        "enabled": False,
                        "transport": "stdio",
                        "command": [sys.executable, "-u", str(srv_script), str(audit_log), str(pid_file)],
                        "allowed_tools": ["read_note"],
                        "mutating_tools": [],
                        "timeout_sec": 5.0,
                    },
                }
            }),
            encoding="utf-8",
        )

        # 5a. list_servers & list_tools expose ONLY enabled + allowlisted tools
        ls_out = mb._handle_list_servers(player=None, config_path=cfg_file)
        lt_out = mb._handle_list_tools("", player=None, config_path=cfg_file)
        all_ok &= _check(
            "list_servers and list_tools expose ONLY enabled server and allowlisted tools",
            "mock_notes [ENABLED]" in ls_out
            and "disabled_srv [disabled]" in ls_out
            and "read_note [read-only]" in lt_out
            and "delete_note [confirm required]" in lt_out
            and "secret_admin_dump" not in lt_out
            and "disabled_srv" not in lt_out,
        )

        # 5b. Read-only call_tool succeeds & wraps in <<<UNTRUSTED source=mcp...>>>
        read_out = mb._handle_call_tool(
            "mock_notes",
            "read_note",
            {"id": "42"},
            player=None,
            config_path=cfg_file,
        )
        all_ok &= _check(
            "Read-only call_tool succeeds and wraps response in <<<UNTRUSTED source=mcp...>>>",
            "Note content for 42: hello from MCP." in read_out
            and "<<<UNTRUSTED source=mcp" in read_out
            and "<<<END UNTRUSTED>>>" in read_out,
        )

        # 5c. Non-allowlisted tool & disabled server blocked before execution
        audit_before = audit_log.read_text(encoding="utf-8") if audit_log.exists() else ""
        blk_tool = mb._handle_call_tool("mock_notes", "secret_admin_dump", {}, player=None, config_path=cfg_file)
        blk_srv = mb._handle_call_tool("disabled_srv", "read_note", {}, player=None, config_path=cfg_file)
        audit_after = audit_log.read_text(encoding="utf-8") if audit_log.exists() else ""
        all_ok &= _check(
            "Non-allowlisted tool and disabled server blocked without spawning server",
            "Blocked" in blk_tool
            and "Blocked" in blk_srv
            and audit_after == audit_before,
        )

        # 5d. Mutating tool call gated: blocked on player=None AND gated through core/confirm.py when HUD bound
        mut_none = mb._handle_call_tool("mock_notes", "delete_note", {"id": "99"}, player=None, config_path=cfg_file)
        audit_after_none = audit_log.read_text(encoding="utf-8") if audit_log.exists() else ""

        from core import confirm
        orig_show, orig_hide, orig_log = confirm._show_cb, confirm._hide_cb, confirm._log_cb
        banners: list[tuple[str, str]] = []
        logs: list[str] = []

        class DummyPlayer:
            def write_log(self, msg):
                logs.append(msg)

            def show_content(self, title, body):
                logs.append(f"{title}: {body[:40]}")

        try:
            confirm.bind(
                show=lambda t, d: banners.append((t, d)),
                hide=lambda: None,
                log=logs.append,
            )
            # First request -> parked in CONFIRMATION_PENDING, NOT executed yet!
            mut_pending = mb._handle_call_tool(
                "mock_notes", "delete_note", {"id": "99"}, player=DummyPlayer(), config_path=cfg_file
            )
            audit_while_pending = audit_log.read_text(encoding="utf-8") if audit_log.exists() else ""
            # Cancel -> still NOT executed!
            confirm.resolve(False)
            time.sleep(0.1)
            audit_after_cancel = audit_log.read_text(encoding="utf-8") if audit_log.exists() else ""

            # Second request + explicit user CONFIRM -> now executes!
            mb._handle_call_tool(
                "mock_notes", "delete_note", {"id": "99"}, player=DummyPlayer(), config_path=cfg_file
            )
            confirm.resolve(True)
            deadline = time.monotonic() + 5.0
            while time.monotonic() < deadline:
                if "delete_note" in (audit_log.read_text(encoding="utf-8") if audit_log.exists() else ""):
                    break
                time.sleep(0.05)
            audit_after_confirm = audit_log.read_text(encoding="utf-8") if audit_log.exists() else ""
        finally:
            confirm.bind(orig_show, orig_hide, orig_log)

        all_ok &= _check(
            "Mutating tool blocked when player=None and strictly gated by core/confirm.py",
            "Blocked" in mut_none
            and audit_after_none == audit_before
            and "[CONFIRMATION_PENDING]" in mut_pending
            and len(banners) == 2
            and audit_while_pending == audit_before
            and audit_after_cancel == audit_before
            and '"tool": "delete_note"' in audit_after_confirm,
            f"banners={len(banners)}, blocked_on_none=True, ran_only_after_confirm=True",
        )

        # 5e. Hanging server triggers timeout and terminates cleanly with zero leaked child PIDs
        if pid_file.exists():
            pid_file.unlink()
        t0 = time.monotonic()
        hang_out = mb._handle_call_tool("mock_notes", "hang_forever", {}, player=None, config_path=cfg_file)
        elapsed = time.monotonic() - t0
        time.sleep(0.2)
        leaked_pid = int(pid_file.read_text(encoding="utf-8").strip()) if pid_file.is_file() else -1
        still_alive = _pid_alive(leaked_pid) if leaked_pid > 0 else False
        all_ok &= _check(
            "Hanging MCP server hits hard timeout and terminates child cleanly (0 leaked processes)",
            "timed out" in hang_out.lower()
            and elapsed < 5.0
            and leaked_pid > 0
            and not still_alive,
            f"elapsed={elapsed:.2f}s, pid={leaked_pid}, alive={still_alive}",
        )

    # 6. docs/SELF_EVOLUTION.md documents Section 6 MCP bridge
    doc_text = (ROOT / "docs" / "SELF_EVOLUTION.md").read_text(encoding="utf-8")
    all_ok &= _check(
        "docs/SELF_EVOLUTION.md documents Section 6 Safe Local MCP Tool Bridge",
        "mcp_bridge" in doc_text
        and "config/mcp_servers.example.json" in doc_text
        and "allowed_tools" in doc_text
        and "core/confirm.py" in doc_text,
    )

    print("=== RESULT:", "ALL PASSED" if all_ok else "FAILED", "===")
    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main())
