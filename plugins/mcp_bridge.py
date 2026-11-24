"""
Universal Safe MCP (Model Context Protocol) Client Bridge Plugin for J.A.R.V.I.S (Mark-LVII).

Dynamically discovers, mounts, and invokes external MCP tool servers (GitHub,
Google Workspace, Notion, PostgreSQL/SQLite Databases, Smart Home, Filesystem,
and Streamable HTTP/SSE servers) via `core.mcp_client` with automatic 3-tier
`core.evolution.safety_guard` risk wrapping (`SAFE`, `CONFIRM`, `LOCKED`),
Biometric Voice Lock enforcement, HUD confirmation, and `<<<UNTRUSTED>>>` isolation.
"""
from __future__ import annotations

from pathlib import Path
import sys
from typing import Any

from core import mcp_client

PLUGIN = {
    "name": "mcp_bridge",
    "description": (
        "Universal Model Context Protocol (MCP) JSON-RPC 2.0 client bridge for stdio and HTTP/SSE "
        "MCP servers (GitHub, Google Workspace, Notion, Databases, Smart Home). "
        "Modes: "
        "'list_servers' (default) = show configured MCP servers, enabled state, and allowlist counts; "
        "'list_tools' = query an enabled server (or all enabled servers) and show allowlisted tools with risk tiers; "
        "'discover' / 'mount' = dynamically discover & mount external MCP tools into Mark-LVII's SQLite MCP catalog; "
        "'mounted_tools' = list or invoke dynamically mounted `mcp__<server>__<tool>` descriptors; "
        "'call_tool' = invoke an allowlisted tool (`server`, `tool`, optional `arguments` dict) with automatic "
        "SAFE/CONFIRM/LOCKED `safety_guard` + `voice_lock` + `core/confirm.py` gating and untrusted-output wrapping; "
        "'presets' = inspect built-in server presets (GitHub, Google Workspace, Notion, Databases, Smart Home); "
        "'stats' = summarize bridge config, mounted tool count, and default-deny security posture."
    ),
    "brief": (
        "Discover, mount, or invoke external stdio/HTTP MCP tool servers with automatic safety_guard risk gating "
        "(mode='list_servers'|'list_tools'|'mount'|'mounted_tools'|'call_tool'|'presets'|'stats')."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "mode": {
                "type": "STRING",
                "enum": [
                    "list_servers",
                    "list_tools",
                    "discover",
                    "mount",
                    "mounted_tools",
                    "call_tool",
                    "presets",
                    "ecosystem",
                    "stats",
                ],
                "description": (
                    "'list_servers' (default), 'list_tools', 'discover', 'mount', "
                    "'mounted_tools', 'call_tool', 'presets', or 'stats'."
                ),
            },
            "server": {
                "type": "STRING",
                "description": "Configured MCP server name (for 'list_tools', 'mount', or 'call_tool').",
            },
            "tool": {
                "type": "STRING",
                "description": "Allowlisted MCP tool name or mounted `mcp__<server>__<tool>` name.",
            },
            "arguments": {
                "type": "OBJECT",
                "properties": {},
                "description": "Optional JSON object of arguments to pass to the MCP tool.",
            },
        },
        "required": [],
    },
}


def _get_base_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent.parent


BASE_DIR: Path = _get_base_dir()
EXAMPLE_TEMPLATE_NAME: str = "mcp_servers.example.json"


def _notify_hud(player, log_line: str, title: str = "", body: str = "") -> None:
    if player is None:
        return
    try:
        if hasattr(player, "write_log"):
            player.write_log(f"JARVIS: {log_line}")
    except Exception:
        pass
    if title and body:
        try:
            if hasattr(player, "show_content"):
                player.show_content(title, body[:4000])
        except Exception:
            pass


def load_servers_config(config_path: Path | None = None) -> tuple[dict[str, dict], str]:
    """Load MCP server definitions via `core.mcp_client`."""
    return mcp_client.load_servers_config(config_path)


def is_mutating_tool(tool_name: str, server_cfg: dict) -> bool:
    """Return True when `tool_name` requires `CONFIRM` or `LOCKED` gating."""
    return mcp_client.is_mutating_tool(tool_name, server_cfg)


def classify_tool_risk(
    tool_name: str,
    server_cfg: dict,
    *,
    arguments: dict[str, Any] | None = None,
    tool_schema: dict[str, Any] | None = None,
) -> str:
    """Return `SAFE`, `CONFIRM`, or `LOCKED` for an MCP tool."""
    return mcp_client.classify_mcp_risk(tool_name, server_cfg, arguments=arguments, tool_schema=tool_schema)


def _handle_list_servers(player=None, config_path: Path | None = None) -> str:
    servers, err = load_servers_config(config_path)
    if err:
        _notify_hud(player, err)
        return err
    if not servers:
        msg = f"No MCP servers are defined yet (see config/{EXAMPLE_TEMPLATE_NAME})."
        _notify_hud(player, msg)
        return msg

    lines = []
    enabled_cnt = 0
    for name, cfg in sorted(servers.items()):
        on = bool(cfg.get("enabled", False))
        if on:
            enabled_cnt += 1
        transport = str(cfg.get("transport") or "stdio")
        allowed = sorted(mcp_client.allowed_tools_set(cfg))
        t_sec = mcp_client.server_timeout(cfg)
        lines.append(
            f"- {name} [{'ENABLED' if on else 'disabled'}] ({transport}, timeout={t_sec:.0f}s, "
            f"allowed_tools={len(allowed)}: {', '.join(allowed) or 'none'})"
        )
    body = "\n".join(lines)
    reply = f"MCP bridge has {len(servers)} configured server(s) ({enabled_cnt} enabled)."
    _notify_hud(player, reply, "MCP SERVERS", body)
    return f"{reply}\n{body}"


def _handle_list_tools(server_name: str = "", player=None, config_path: Path | None = None) -> str:
    servers, err = load_servers_config(config_path)
    if err:
        _notify_hud(player, err)
        return err

    targets = (
        [server_name.strip()]
        if server_name.strip()
        else sorted(k for k, v in servers.items() if bool(v.get("enabled", False)))
    )
    if not targets:
        msg = "No MCP servers are currently enabled."
        _notify_hud(player, msg)
        return msg

    sections: list[str] = []
    total_exposed = 0
    for sname in targets:
        cfg = servers.get(sname)
        if cfg is None:
            sections.append(f"[{sname}] not found in MCP configuration.")
            continue
        if not bool(cfg.get("enabled", False)):
            sections.append(f"[{sname}] disabled — refusing to query tools.")
            continue
        allowed = mcp_client.allowed_tools_set(cfg)
        if not allowed:
            sections.append(f"[{sname}] enabled, but allowed_tools is empty (default-deny: 0 tools exposed).")
            continue
        try:
            res = mcp_client.mcp_request(cfg, "tools/list", {})
        except Exception as exc:
            sections.append(f"[{sname}] failed to list tools: {exc}")
            continue
        raw_tools = res.get("tools") if isinstance(res.get("tools"), list) else []
        filtered = []
        for t in raw_tools:
            if not isinstance(t, dict):
                continue
            tname = str(t.get("name") or "").strip()
            if tname and mcp_client.is_tool_allowed(tname, cfg):
                tier = classify_tool_risk(tname, cfg, tool_schema=t)
                if tier == "LOCKED":
                    tag = " [locked: voice_lock + confirm required]"
                elif tier == "CONFIRM":
                    tag = " [confirm required]"
                else:
                    tag = " [read-only]"
                desc = " ".join(str(t.get("description") or "").split())[:120]
                filtered.append(f"  - {tname}{tag}: {desc}")
        total_exposed += len(filtered)
        sections.append(
            f"[{sname}] {len(filtered)} allowlisted tool(s) exposed (of {len(raw_tools)} advertised):\n"
            + ("\n".join(filtered) if filtered else "  (none matched allowed_tools)")
        )

    body = "\n\n".join(sections)
    reply = f"Listed {total_exposed} allowlisted MCP tool(s) across {len(targets)} server(s)."
    _notify_hud(player, reply, "ALLOWLISTED MCP TOOLS", body)
    return f"{reply}\n{body}"


def _handle_mount(
    server_name: str = "",
    player=None,
    config_path: Path | None = None,
    registry_db: Path | None = None,
) -> str:
    res = mcp_client.discover_and_mount(
        server_name,
        config_path=config_path,
        registry_db=registry_db,
        persist=True,
    )
    if not res.get("ok"):
        err = str(res.get("error") or "MCP discovery failed.")
        _notify_hud(player, err)
        return err

    mounted = res.get("mounted") or []
    srv_errs = res.get("errors") or {}
    lines = [
        f"  - {m['mounted_name']} (tier={m['risk_tier']}): {m['description']}"
        for m in mounted
    ]
    if srv_errs:
        lines.append("Server warnings: " + ", ".join(f"{k}={v}" for k, v in srv_errs.items()))
    body = "\n".join(lines) if lines else "0 tools mounted (enable a server first)."
    reply = f"Dynamically discovered & mounted {len(mounted)} MCP tool(s) with safety_guard risk tiers."
    _notify_hud(player, reply, "MOUNTED MCP TOOLS", body)
    return f"{reply}\n{body}"


def _handle_mounted_tools(
    tool_name: str = "",
    arguments: dict[str, Any] | None = None,
    player=None,
    config_path: Path | None = None,
    registry_db: Path | None = None,
) -> str:
    if tool_name.strip():
        return mcp_client.invoke_mounted_tool(
            tool_name.strip(),
            arguments,
            player=player,
            config_path=config_path,
            registry_db=registry_db,
            notify_fn=_notify_hud,
        )
    mounted = mcp_client.list_mounted_tools(registry_db=registry_db)
    if not mounted:
        msg = "No MCP tools are currently mounted. Run mcp_bridge(mode='mount') on an enabled server."
        _notify_hud(player, msg)
        return msg
    lines = [
        f"  - {m['mounted_name']} [{m['risk_tier']}] -> {m['server']}:{m['tool']}"
        for m in mounted
    ]
    reply = f"{len(mounted)} mounted MCP tool(s) ready:"
    body = "\n".join(lines)
    _notify_hud(player, reply, "MOUNTED MCP CATALOG", body)
    return f"{reply}\n{body}"


def _handle_presets(player=None) -> str:
    lines = []
    for name, info in sorted(mcp_client.MCP_SERVER_PRESETS.items()):
        tpl = info["template"]
        lines.append(
            f"- {name} ({info['category']}, transport={tpl['transport']}): {info['description']}\n"
            f"    sample allowed_tools={tpl['allowed_tools']}"
        )
    body = "\n".join(lines)
    reply = f"Available Universal MCP presets ({len(lines)}): GitHub, Google Workspace, Notion, Databases, Smart Home."
    _notify_hud(player, reply, "UNIVERSAL MCP PRESETS", body)
    return f"{reply}\n{body}"


def _handle_call_tool(
    server_name: str,
    tool_name: str,
    arguments: dict | None = None,
    player=None,
    config_path: Path | None = None,
) -> str:
    return mcp_client.execute_mcp_tool_with_safety(
        server_name,
        tool_name,
        arguments,
        player=player,
        config_path=config_path,
        notify_fn=_notify_hud,
    )


def _handle_stats(
    player=None,
    config_path: Path | None = None,
    registry_db: Path | None = None,
) -> str:
    cfg_file = Path(config_path) if config_path is not None else mcp_client.DEFAULT_CONFIG_PATH
    servers, err = load_servers_config(cfg_file)
    mounted_cnt = len(mcp_client.list_mounted_tools(registry_db=registry_db))
    preset_cnt = len(mcp_client.MCP_SERVER_PRESETS)

    if err and not cfg_file.is_file():
        msg = (
            f"MCP bridge stats: config missing (example template available at config/{EXAMPLE_TEMPLATE_NAME}; "
            f"{preset_cnt} presets ready, {mounted_cnt} mounted tools). "
            "Default-deny allowlist, safety_guard SAFE/CONFIRM/LOCKED tiers, and core/confirm.py gate active."
        )
        _notify_hud(player, msg, "MCP BRIDGE STATS", msg)
        return msg
    if err:
        _notify_hud(player, err)
        return err

    enabled = [k for k, v in servers.items() if bool(v.get("enabled", False))]
    total_allowed = sum(len(mcp_client.allowed_tools_set(v)) for v in servers.values())
    reply = (
        f"MCP bridge stats: {len(servers)} configured server(s) ({len(enabled)} enabled), "
        f"{total_allowed} allowlisted tool(s), {mounted_cnt} dynamically mounted tool(s), "
        f"{preset_cnt} built-in presets, default-deny + safety_guard SAFE/CONFIRM/LOCKED gate active."
    )
    _notify_hud(player, reply, "MCP BRIDGE STATS", reply)
    return reply


def run(parameters: dict, player=None, session_memory=None) -> str:
    """Entry point discovered by `core.plugin_loader`. Never raises."""
    _ = session_memory
    try:
        params = parameters if isinstance(parameters, dict) else {}
        mode = str(params.get("mode") or "list_servers").strip().lower()
        server = str(params.get("server") or "").strip()
        tool = str(params.get("tool") or "").strip()
        arguments = params.get("arguments") if isinstance(params.get("arguments"), dict) else {}

        if mode == "list_servers":
            return _handle_list_servers(player=player)
        if mode == "list_tools":
            return _handle_list_tools(server, player=player)
        if mode in ("discover", "mount"):
            return _handle_mount(server, player=player)
        if mode == "mounted_tools":
            return _handle_mounted_tools(tool, arguments, player=player)
        if mode == "presets":
            return _handle_presets(player=player)
        if mode == "ecosystem":
            eco = mcp_client.discover_ecosystem_summary()
            msg = (
                f"OmniCore Ecosystem: {eco['indexed_skills']} indexed skill(s) across {len(eco['skill_domains'])} domain(s), "
                f"{len(eco['presets'])} MCP server presets ({', '.join(eco['presets'])}), "
                f"{eco['mounted_tools']} mounted MCP tool(s), {len(eco['active_daemons'])} active daemon(s)."
            )
            _notify_hud(player, msg, "OMNICORE ECOSYSTEM", str(eco))
            return msg
        if mode == "call_tool":
            if tool.startswith("mcp__") and not server:
                return _handle_mounted_tools(tool, arguments, player=player)
            return _handle_call_tool(server, tool, arguments, player=player)
        if mode == "stats":
            return _handle_stats(player=player)

        msg = (
            f"Sir, unknown mcp_bridge mode '{mode}'. "
            "Use 'list_servers', 'list_tools', 'discover', 'mount', 'mounted_tools', 'call_tool', 'presets', or 'stats'."
        )
        _notify_hud(player, msg)
        return msg
    except Exception as exc:
        err = f"Sir, mcp_bridge encountered an error: {exc}"
        _notify_hud(player, err)
        return err
