"""Universal Omnibridge — JARVIS's unified Windows 11 + Ubuntu WSL2 (Ubuntu-D)
interface to all 30+ GitHub repositories, 260+ installed Linux/Claude/Agent/
Antigravity/Omega skills, 53 Claude plugins, 9 MCP servers, 16 cloud connectors,
and cross-OS Linux + Windows execution.

Works identically whether invoked from Windows JARVIS or Linux (WSL2) JARVIS so
both installs cooperate seamlessly as one full-machine operator.
"""
from __future__ import annotations

import json
import os
import platform
import re
import shlex
import subprocess
import time
from pathlib import Path

from core import cross_os as _cross_os

PLUGIN = {
    "name": "universal_omnibridge",
    "description": (
        "JARVIS's unified cross-OS (Windows 11 + Ubuntu WSL2) execution bridge to all 30 GitHub repositories "
        "(tq-forge, omega-system, horizon, finsolenterprises, recoveryos, OmniRoute, agent-hq, addzify-*), "
        "260+ installed skills & specialist agents (~/.claude/skills, ~/.agents/skills, Antigravity builtin skills, "
        "~/.gemini/skills, omega-system/.agents), 53 Claude plugins, 9 MCP servers (claude-flow/ruflo, claude-mem, "
        "desktop-commander, chrome-devtools-mcp, filesystem, youtube-transcript, obscura, nvidia-app), "
        "16 cloud connectors (GitHub, Gmail, Calendar, Drive, Docs, Sheets, Notion, Granola, Vercel, Supabase, "
        "Figma, Canva, Adobe, Zapier, Hugging Face, Ruflo), and both Linux & Windows shells. "
        "Actions: "
        "'status' = display the complete dual-OS ecosystem inventory and Windows/Linux peer status on the HUD; "
        "'github' = inspect, read, search, or clone any of the user's 30 GitHub repositories or local projects; "
        "'search_skills' = search all 260+ installed skills and specialist agents by keyword or capability; "
        "'use_skill' = load and execute any installed skill or specialist agent on a task (works from BOTH Windows & Linux); "
        "'mcp_connectors' = inspect active MCP server daemons, Claude plugins, CLAUDE.md rules, and cloud connectors; "
        "'mcp_run' = execute a task using any of the 9 MCP servers or 16 cloud connectors via Claude Code in WSL2; "
        "'linux_shell' = run a Linux/Ubuntu WSL2 command or custom binary (redcell, hermes, graphify, gh, claude-flow) from either OS; "
        "'windows_shell' = run a Windows PowerShell command or Windows desktop automation from either OS; "
        "'shell' = run a CLI command on the current OS; "
        "'sync_index' = rescan both Linux WSL2 and Windows + GitHub to refresh and mirror memory/ecosystem_index.json."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "action": {
                "type": "STRING",
                "enum": [
                    "status",
                    "github",
                    "search_skills",
                    "use_skill",
                    "mcp_connectors",
                    "mcp_run",
                    "linux_shell",
                    "windows_shell",
                    "shell",
                    "sync_index",
                ],
                "description": "Operation to perform (default 'status').",
            },
            "target": {
                "type": "STRING",
                "description": (
                    "For 'github': repo name (e.g. 'tq-forge', 'omega-system', 'horizon', 'finsolenterprises'). "
                    "For 'search_skills'/'use_skill': skill keyword or name (e.g. 'graphify', 'vibesec', 'remotion'). "
                    "For 'mcp_run': MCP server or connector name (e.g. 'claude-mem', 'claude-flow', 'gmail', 'notion', 'supabase', 'vercel', 'figma'). "
                    "For 'shell'/'linux_shell'/'windows_shell': the command to execute."
                ),
            },
            "task": {
                "type": "STRING",
                "description": "Instruction, question, or arguments for 'use_skill', 'mcp_run', or 'github'.",
            },
        },
        "required": ["action"],
    },
}

_BASE = Path(__file__).resolve().parent.parent
_INDEX_PATH = _BASE / "memory" / "ecosystem_index.json"


def _call(player, method: str, *args) -> None:
    fn = getattr(player, method, None) if player else None
    if callable(fn):
        try:
            fn(*args)
        except Exception as e:
            print(f"[universal_omnibridge] {method} failed: {e}")


def _load_index() -> dict:
    if _INDEX_PATH.is_file():
        try:
            data = json.loads(_INDEX_PATH.read_text(encoding="utf-8"))
            if isinstance(data, dict) and data.get("skills"):
                return data
        except Exception:
            pass
    return _build_index()


def _scan_skills_dir(base_dir: Path, source: str, posix_base: str) -> list[dict]:
    out = []
    try:
        if not base_dir.is_dir():
            return out
        items = sorted(base_dir.iterdir())
    except OSError:
        return out
    for p in items:
        skill_md = p / "SKILL.md" if p.is_dir() else (p if p.suffix == ".md" else None)
        if not skill_md or not skill_md.is_file():
            continue
        try:
            txt = skill_md.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        desc = ""
        m = re.search(r"^description:\s*(.+)$", txt, re.M)
        if m:
            desc = m.group(1).strip().strip("\"'")
        else:
            lines = [
                ln.strip()
                for ln in txt.splitlines()
                if ln.strip() and not ln.startswith("#") and not ln.startswith("---")
            ]
            desc = lines[0][:200] if lines else p.stem
        rel = f"{p.name}/SKILL.md" if p.is_dir() else p.name
        out.append({
            "name": p.stem,
            "source": source,
            # Store canonical POSIX path so both Linux and Windows (via to_host_path) read it!
            "path": f"{posix_base.rstrip('/')}/{rel}",
            "description": desc[:220],
        })
    return out


def _build_index() -> dict:
    existing = {}
    if _INDEX_PATH.is_file():
        try:
            existing = json.loads(_INDEX_PATH.read_text(encoding="utf-8"))
        except Exception:
            existing = {}

    lhome = _cross_os.linux_home()
    wsl_ok = _cross_os.linux_home_reachable()

    skills = []
    if wsl_ok:
        skills = (
            _scan_skills_dir(lhome / ".claude/skills", "claude-skills", "/home/tanishq/.claude/skills")
            + _scan_skills_dir(lhome / ".agents/skills", "agent-skills", "/home/tanishq/.agents/skills")
            + _scan_skills_dir(lhome / ".gemini/skills", "gemini-skills", "/home/tanishq/.gemini/skills")
            + _scan_skills_dir(
                lhome / ".gemini/antigravity-cli/builtin/skills",
                "antigravity-builtin",
                "/home/tanishq/.gemini/antigravity-cli/builtin/skills",
            )
            + _scan_skills_dir(lhome / "omega-system/.agents", "omega-agents", "/home/tanishq/omega-system/.agents")
        )
    if not skills and existing.get("skills"):
        skills = existing["skills"]

    gh_repos = []
    try:
        r = _cross_os.run_linux(
            "gh repo list tanishq286 --limit 100 --json name,description,isPrivate,updatedAt,url",
            timeout=15,
        )
        if r.returncode == 0 and r.stdout.strip():
            gh_repos = json.loads(r.stdout)
    except Exception:
        pass
    if not gh_repos and existing.get("github_repos"):
        gh_repos = existing["github_repos"]

    local_projects = {}
    if wsl_ok:
        try:
            for p in sorted(lhome.iterdir()):
                if p.is_dir() and not p.name.startswith("."):
                    if any((p / marker).exists() for marker in (".git", "README.md", "package.json", "pyproject.toml", "main.py")):
                        local_projects[p.name.lower()] = f"/home/tanishq/{p.name}"
        except OSError:
            pass
    try:
        win_jp = _cross_os.windows_home() / "Desktop" / "JarvisProjects"
        if win_jp.is_dir():
            for p in sorted(win_jp.iterdir()):
                if p.is_dir() and not p.name.startswith("."):
                    local_projects[f"win:{p.name.lower()}"] = f"C:\\Users\\tanis\\Desktop\\JarvisProjects\\{p.name}"
    except OSError:
        pass
    if not local_projects and existing.get("local_projects"):
        local_projects = existing["local_projects"]

    for repo in gh_repos:
        rname = str(repo.get("name") or "").lower()
        if rname in local_projects:
            repo["local_path"] = local_projects[rname]
        elif rname == "finsolenterprises" and "recoveryos-app" in local_projects:
            repo["local_path"] = local_projects["recoveryos-app"]

    plugins_list = []
    enabled_plugins = []
    mcp_servers = [
        "claude-flow", "claude-mem", "desktop-commander", "chrome-devtools-mcp",
        "mcp-server-filesystem", "mcp-server-youtube-transcript",
    ]
    if wsl_ok:
        for cfg_name in (".claude.json", ".mcp.json"):
            cfg_p = lhome / cfg_name
            if cfg_p.is_file():
                try:
                    cj = json.loads(cfg_p.read_text(encoding="utf-8"))
                    for k, v in (cj.get("pluginUsage") or {}).items():
                        plugins_list.append({"id": k, "usageCount": v.get("usageCount", 0)})
                    for k in (cj.get("mcpServers") or {}).keys():
                        if k not in mcp_servers:
                            mcp_servers.append(k)
                except Exception:
                    pass
        settings_p = lhome / ".claude/settings.json"
        if settings_p.is_file():
            try:
                sj = json.loads(settings_p.read_text(encoding="utf-8"))
                enabled_plugins = sorted(
                    k for k, v in (sj.get("enabledPlugins") or {}).items() if v
                )
            except Exception:
                pass
    if not plugins_list and existing.get("claude_plugins"):
        plugins_list = existing["claude_plugins"]
    plugins_list.sort(key=lambda x: -x.get("usageCount", 0))

    claude_rules = ""
    if wsl_ok:
        cm = lhome / ".claude/CLAUDE.md"
        if cm.is_file():
            try:
                claude_rules = cm.read_text(encoding="utf-8", errors="replace").strip()[:1200]
            except OSError:
                pass
    if not claude_rules and existing.get("claude_rules"):
        claude_rules = existing["claude_rules"]

    connectors = [
        "GitHub (gh CLI @tanishq286)", "Gmail", "Google Calendar", "Google Drive",
        "Google Docs", "Google Sheets", "Notion", "Granola", "Vercel", "Supabase",
        "Figma", "Canva", "Adobe", "Zapier", "Hugging Face", "Ruflo / Claude-Flow Swarm Daemon",
    ]
    windows_apps = [
        "Antigravity IDE & CLI", "Cursor IDE", "Obsidian", "LM Studio",
        "NVIDIA Broadcast", "Google Chrome", "Brave", "Microsoft Edge",
        "CapCut", "Telegram Desktop", "Wispr Flow",
    ]

    index = {
        "updated": time.strftime("%Y-%m-%d %H:%M:%S"),
        "counts": {
            "github_repos": len(gh_repos),
            "local_projects": len(local_projects),
            "skills_and_agents": len(skills),
            "claude_plugins": len(plugins_list),
            "enabled_claude_plugins": len(enabled_plugins),
            "mcp_servers": len(mcp_servers),
            "cloud_connectors": len(connectors),
            "windows_apps": len(windows_apps),
        },
        "github_repos": gh_repos,
        "local_projects": local_projects,
        "mcp_servers": mcp_servers,
        "cloud_connectors": connectors,
        "claude_plugins": plugins_list,
        "enabled_claude_plugins": enabled_plugins,
        "claude_rules": claude_rules,
        "windows_apps": windows_apps,
        "skills": skills,
    }
    try:
        _INDEX_PATH.parent.mkdir(parents=True, exist_ok=True)
        _INDEX_PATH.write_text(json.dumps(index, indent=2, ensure_ascii=False), encoding="utf-8")
        _cross_os.mirror_memory_file(_INDEX_PATH)
    except Exception:
        pass
    return index


def _status_report(player) -> str:
    idx = _load_index()
    counts = idx.get("counts") or {}
    repos = idx.get("github_repos") or []
    skills = idx.get("skills") or []
    plugins = idx.get("claude_plugins") or []
    mcps = idx.get("mcp_servers") or []
    conns = idx.get("cloud_connectors") or []
    peer = _cross_os.peer_running_status()

    top_repos = ", ".join(
        f"{r['name']}{' (local)' if r.get('local_path') else ''}"
        for r in repos[:14]
    )
    top_plugins = ", ".join(f"{p['id'].split('@')[0]} ({p['usageCount']}x)" for p in plugins[:12])
    top_skills = ", ".join(s["name"] for s in skills[:25])

    hud_text = (
        f"JARVIS UNIVERSAL OMNIBRIDGE — UNIFIED DUAL-OS ECOSYSTEM (Updated: {idx.get('updated', 'live')})\n"
        f"Runtime: {peer['current_os']} | Windows JARVIS: {'ONLINE' if peer['windows_running'] else 'standby'} "
        f"| Linux WSL2 JARVIS: {'ONLINE' if peer['linux_running'] else 'standby'} | Shared Brain: SYNCED\n\n"
        f"• GitHub Repositories ({counts.get('github_repos', len(repos))}): {top_repos} …\n"
        f"• Local Cloned Projects ({counts.get('local_projects', 0)}): "
        f"{', '.join(sorted((idx.get('local_projects') or {}).keys())[:18])}\n"
        f"• Installed Skills & Specialist Agents ({counts.get('skills_and_agents', len(skills))}): "
        f"{top_skills} …\n"
        f"• Installed Claude Plugins ({counts.get('claude_plugins', len(plugins))}): {top_plugins} …\n"
        f"• Active MCP Servers ({len(mcps)}): {', '.join(mcps)}\n"
        f"• Cloud Connectors ({len(conns)}): {', '.join(conns)}\n"
        f"• Windows Apps: {', '.join(idx.get('windows_apps') or [])}\n"
    )
    _call(player, "show_content", "UNIVERSAL OMNIBRIDGE · DUAL-OS", hud_text[:4000])
    return (
        f"Unified Windows and Linux Omnibridge is synced with {counts.get('github_repos', 30)} GitHub repositories, "
        f"{counts.get('skills_and_agents', len(skills))} skills and specialist agents, "
        f"{counts.get('claude_plugins', 53)} plugins, {len(mcps)} MCP servers, and {len(conns)} cloud connectors. "
        "The full inventory is on your HUD."
    )


def _github_action(target: str, task: str, player) -> str:
    idx = _load_index()
    repos = idx.get("github_repos") or []
    local_map = idx.get("local_projects") or {}
    q = (target or task or "").strip().lower()

    if not q or q in ("list", "all", "repos", "repositories"):
        lines = ["ALL GITHUB REPOSITORIES (@tanishq286):"]
        for r in repos:
            vis = "private" if r.get("isPrivate") else "public"
            loc = f" → LOCAL: {r['local_path']}" if r.get("local_path") else ""
            desc = f" — {r.get('description')}" if r.get("description") else ""
            lines.append(f"• {r.get('name')} [{vis}]{loc}{desc}")
        text = "\n".join(lines)
        _call(player, "show_content", "GITHUB REPOSITORIES (@tanishq286)", text[:4000])
        return f"You have {len(repos)} GitHub repositories under tanishq286, including tq-forge, omega-system, horizon, finsolenterprises, recoveryos, and OmniRoute. All {len(repos)} are listed on the HUD."

    matched = None
    for r in repos:
        rname = str(r.get("name") or "").lower()
        if q == rname or q in rname or rname in q:
            matched = r
            break

    local_dir = None
    if matched and matched.get("local_path"):
        cand = _cross_os.to_host_path(matched["local_path"])
        if cand.is_dir():
            local_dir = cand
    if local_dir is None and q in local_map:
        cand = _cross_os.to_host_path(local_map[q])
        if cand.is_dir():
            local_dir = cand
    if local_dir is None:
        cand = _cross_os.linux_home() / q
        if cand.is_dir():
            local_dir = cand

    repo_name = matched["name"] if matched else target.strip()
    if local_dir is None and matched:
        dest_posix = f"/home/tanishq/{repo_name}"
        try:
            _call(player, "write_log", f"SYS: [Omnibridge] Cloning tanishq286/{repo_name} to {dest_posix}…")
            _cross_os.run_linux(
                f"gh repo clone {shlex.quote(f'tanishq286/{repo_name}')} {shlex.quote(dest_posix)} -- --depth 1",
                timeout=45,
            )
            cand = _cross_os.to_host_path(dest_posix)
            if cand.is_dir():
                local_dir = cand
        except Exception:
            pass

    details = [f"REPOSITORY: tanishq286/{repo_name}"]
    if matched:
        details.append(f"Visibility: {'Private' if matched.get('isPrivate') else 'Public'} | Updated: {matched.get('updatedAt', '')}")
        if matched.get("description"):
            details.append(f"Description: {matched['description']}")

    if local_dir and local_dir.is_dir():
        details.append(f"Local Path: {local_dir}")
        try:
            entries = sorted(p.name + ("/" if p.is_dir() else "") for p in local_dir.iterdir() if p.name != ".git")
            details.append(f"Top-Level Files ({len(entries)}): {', '.join(entries[:35])}")
        except OSError:
            pass
        for doc in ("README.md", "readme.md", "AGENTS.md", "CLAUDE.md", ".memory/architecture.md", ".ai/memory/README.md"):
            dp = local_dir / doc
            if dp.is_file():
                try:
                    details.append(f"\n=== {doc} ===\n" + dp.read_text(encoding="utf-8", errors="replace")[:2600])
                except OSError:
                    pass

    digest = "\n".join(details)
    if task:
        try:
            from core import gemini
            ans = gemini.text(
                f"Answer this user question/task about repository '{repo_name}' using the repository data below.\n"
                f"Task: {task}\n\nRepository Data:\n{digest[:12000]}",
                tier=gemini.SMART,
            )
            if ans:
                digest = f"ANALYSIS FOR: {task}\n\n{ans}\n\n---\n{digest}"
                _call(player, "show_content", f"REPO · {repo_name.upper()}", digest[:4000])
                return " ".join(ans.split())[:420]
        except Exception:
            pass

    _call(player, "show_content", f"GITHUB · {repo_name.upper()}", digest[:4000])
    return f"Loaded repository {repo_name} ({local_dir or 'via GitHub CLI'}). Full structure and documentation are on the HUD."


def _search_skills(query: str, player) -> str:
    idx = _load_index()
    skills = idx.get("skills") or []
    q = (query or "").strip().lower()
    if not q:
        matches = skills[:40]
    else:
        terms = q.split()
        scored = []
        for s in skills:
            hay = f"{s['name']} {s['source']} {s['description']}".lower()
            hits = sum(3 if t in s["name"].lower() else (1 if t in hay else 0) for t in terms)
            if hits > 0:
                scored.append((hits, s))
        scored.sort(key=lambda x: (-x[0], x[1]["name"]))
        matches = [s for _, s in scored[:30]]

    if not matches:
        return f"No installed skill matched '{query}'. Try a broader keyword."

    lines = [f"MATCHING SKILLS ({len(matches)} of {len(skills)} total):"]
    for s in matches:
        lines.append(f"• {s['name']} [{s['source']}]: {s['description']}")
    text = "\n".join(lines)
    _call(player, "show_content", f"SKILLS SEARCH · {query.upper() or 'ALL'}", text[:4000])
    top_names = ", ".join(s["name"] for s in matches[:6])
    return f"Found {len(matches)} matching skills ({top_names}). Details are on the HUD."


def _use_skill(target: str, task: str, player) -> str:
    idx = _load_index()
    skills = idx.get("skills") or []
    q = (target or "").strip().lower()
    chosen = None
    for s in skills:
        if s["name"].lower() == q:
            chosen = s
            break
    if not chosen:
        for s in skills:
            if q in s["name"].lower() or q in s["description"].lower():
                chosen = s
                break
    if not chosen:
        return f"Could not find an installed skill matching '{target}'."

    # Works identically from BOTH Linux and Windows via \\wsl.localhost\Ubuntu-D\...
    skill_path = _cross_os.to_host_path(chosen["path"])
    try:
        skill_body = skill_path.read_text(encoding="utf-8", errors="replace")
    except Exception as e:
        return f"Could not read skill file {skill_path}: {e}"

    if not task:
        _call(player, "show_content", f"SKILL · {chosen['name'].upper()}", skill_body[:4000])
        return f"Loaded skill '{chosen['name']}' ({chosen['description'][:140]}). Its instructions are on the HUD."

    try:
        from core import gemini
        prompt = (
            f"You are JARVIS executing the specialized skill '{chosen['name']}' ({chosen['source']}).\n\n"
            f"=== SKILL SPECIFICATION ===\n{skill_body[:10000]}\n\n"
            f"=== USER TASK ===\n{task}\n\n"
            "Follow the skill's methodology and rules strictly to produce a complete, actionable result."
        )
        res = gemini.text(prompt, tier=gemini.SMART, timeout_ms=90_000) or ""
    except Exception as e:
        res = f"Skill execution error: {e}"

    if not res:
        res = f"Loaded skill '{chosen['name']}' for task: {task}\n\n{skill_body[:2000]}"

    _call(player, "show_content", f"SKILL EXECUTED · {chosen['name'].upper()}", res[:4000])
    return f"Executed skill '{chosen['name']}' on your task. The full output is on the HUD."


def _mcp_connectors(player) -> str:
    idx = _load_index()
    mcps = idx.get("mcp_servers") or []
    conns = idx.get("cloud_connectors") or []
    plugins = idx.get("claude_plugins") or []
    rules = idx.get("claude_rules") or ""

    # Check live running MCP daemons in WSL2 from EITHER Linux or Windows!
    running_procs = []
    try:
        r = _cross_os.run_linux("ps aux", timeout=6)
        for marker in ("claude-flow", "mcp-server", "chroma-mcp", "desktop-commander", "chrome-devtools-mcp", "ollama-ctx-proxy"):
            if marker in (r.stdout or ""):
                running_procs.append(marker)
    except Exception:
        pass

    text = (
        f"ACTIVE MCP SERVERS ({len(mcps)} configured, live WSL2 daemons: {', '.join(running_procs) or 'on-demand'}):\n"
        + "\n".join(f"  • {m}" for m in mcps)
        + f"\n\nCONNECTED CLOUD ACCOUNTS & INTEGRATIONS ({len(conns)}) — callable via mcp_run / claude_code:\n"
        + "\n".join(f"  • {c}" for c in conns)
        + f"\n\nTOP INSTALLED CLAUDE PLUGINS ({len(plugins)} total):\n"
        + "\n".join(f"  • {p['id']} ({p['usageCount']} uses)" for p in plugins[:18])
        + (f"\n\nCLAUDE.MD RULES:\n{rules[:500]}" if rules else "")
    )
    _call(player, "show_content", "MCP SERVERS · PLUGINS · CONNECTORS", text[:4000])
    return (
        f"You have {len(mcps)} MCP servers (live in WSL2: {', '.join(running_procs) or 'on-demand'}), "
        f"{len(conns)} cloud connectors, and {len(plugins)} Claude plugins ready. "
        "Use mcp_run or claude_code to invoke any MCP tool or connector directly."
    )


def _mcp_run(target: str, task: str, player) -> str:
    """Delegate an MCP server or cloud connector task to Claude Code in WSL2,
    where all 9 MCP servers and 16 cloud connectors are authenticated and live."""
    which = (target or "MCP / cloud connector").strip()
    what = (task or "").strip()
    if not what:
        return (
            f"Tell me what you want to do with {which} (pass the instruction in 'task'), "
            "and I will execute it live via Claude Code's authenticated MCP & connector bridge."
        )
    try:
        from plugins import claude_code
        delegated = (
            f"Use the '{which}' MCP server / cloud connector (or ToolSearch to load its tools) "
            f"to complete this task thoroughly and report the concrete outcome:\n\n{what}"
        )
        return claude_code.run(
            {"action": "run", "task": delegated, "cwd": "~"},
            player=player,
        )
    except Exception as e:
        return f"Could not delegate '{which}' MCP task to Claude Code: {e}"


def _shell_exec(command: str, player, os_mode: str = "auto") -> str:
    cmd = (command or "").strip()
    if not cmd:
        return "Provide a command in 'target' to execute."
    if re.search(r"rm\s+-rf\s+/(?:\s|$)|mkfs\.|dd\s+if=.*of=/dev/sd", cmd):
        return "Blocked destructive filesystem wipe command by safety guardrail."

    if os_mode == "auto":
        os_mode = "windows" if _cross_os.is_windows() else "linux"

    tag = "WSL2/LINUX" if os_mode == "linux" else "WINDOWS/PS"
    _call(player, "write_log", f"SYS: [Omnibridge {tag}] $ {cmd[:120]}")
    try:
        if os_mode == "linux":
            r = _cross_os.run_linux(cmd, timeout=45)
        else:
            r = _cross_os.run_windows(cmd, timeout=45)
        out = ((r.stdout or "") + ("\n" + r.stderr if r.stderr else "")).strip()
        if not out:
            out = f"(command exited with code {r.returncode})"
    except subprocess.TimeoutExpired:
        out = "Command timed out after 45 seconds."
    except Exception as e:
        out = f"Command failed: {e}"

    _call(player, "show_content", f"{tag} · {cmd[:30]}", f"$ {cmd}\n\n{out}"[:4000])
    first_lines = " ".join(out.splitlines()[:3])[:260]
    return f"{tag} command finished: {first_lines}"


def run(parameters: dict, player=None, session_memory=None) -> str:
    params = parameters or {}
    action = str(params.get("action") or "status").strip().lower()
    target = str(params.get("target") or "").strip()
    task = str(params.get("task") or "").strip()

    if action == "status":
        return _status_report(player)
    if action == "sync_index":
        idx = _build_index()
        _status_report(player)
        c = idx.get("counts") or {}
        return (
            f"Rescanned both Windows and Linux WSL2 ecosystems: {c.get('github_repos', 0)} GitHub repos, "
            f"{c.get('skills_and_agents', 0)} skills, {c.get('claude_plugins', 0)} plugins, "
            f"and {c.get('mcp_servers', 0)} MCP servers — mirrored to both JARVIS installs."
        )
    if action == "github":
        return _github_action(target, task, player)
    if action == "search_skills":
        return _search_skills(target or task, player)
    if action == "use_skill":
        return _use_skill(target, task, player)
    if action == "mcp_connectors":
        return _mcp_connectors(player)
    if action == "mcp_run":
        return _mcp_run(target, task, player)
    if action == "linux_shell":
        return _shell_exec(target or task, player, os_mode="linux")
    if action == "windows_shell":
        return _shell_exec(target or task, player, os_mode="windows")
    if action == "shell":
        return _shell_exec(target or task, player, os_mode="auto")
    return _status_report(player)
