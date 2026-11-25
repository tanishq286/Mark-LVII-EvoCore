"""scripts/setup_integrations.py — Interactive `.env` & MCP integration setup helper.

Safely configures optional integration credentials in git-ignored `.env`
(Spotify, SMTP/IMAP Email, optional cloud image generation) and generates
git-ignored `config/mcp_servers.json` from `config/mcp_servers.example.json`.

Usage:
    python scripts/setup_integrations.py                 # interactive wizard
    python scripts/setup_integrations.py --status        # inspect configured state (masked)
    python scripts/setup_integrations.py --init-mcp      # create config/mcp_servers.json non-interactively
"""
from __future__ import annotations

import argparse
from getpass import getpass
import json
import os
from pathlib import Path
import sys
from typing import Callable

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.env_loader import _read_dotenv, has_env

DEFAULT_ENV_PATH = ROOT / ".env"
DEFAULT_MCP_EXAMPLE = ROOT / "config" / "mcp_servers.example.json"
DEFAULT_MCP_CONFIG = ROOT / "config" / "mcp_servers.json"

INTEGRATION_GROUPS: tuple[dict, ...] = (
    {
        "id": "spotify",
        "title": "Spotify Playback & Search (plugins/spotify_control.py)",
        "fields": (
            ("SPOTIPY_CLIENT_ID", "Spotify Client ID", False, ""),
            ("SPOTIPY_CLIENT_SECRET", "Spotify Client Secret", True, ""),
            ("SPOTIPY_REDIRECT_URI", "Spotify Redirect URI", False, "http://127.0.0.1:8888/callback"),
        ),
    },
    {
        "id": "email",
        "title": "SMTP / IMAP Email Automation (plugins/email_automator.py)",
        "fields": (
            ("EMAIL_ADDRESS", "Email Address", False, ""),
            ("EMAIL_PASSWORD", "Email App Password", True, ""),
            ("SMTP_HOST", "SMTP Host", False, "smtp.gmail.com"),
            ("SMTP_PORT", "SMTP Port", False, "587"),
            ("IMAP_HOST", "IMAP Host", False, "imap.gmail.com"),
            ("IMAP_PORT", "IMAP Port", False, "993"),
        ),
    },
    {
        "id": "image_gen",
        "title": "Optional Cloud Image Generation (plugins/image_generator.py)",
        "fields": (
            ("OPENAI_API_KEY", "OpenAI API Key (optional; local PIL/ComfyUI works offline)", True, ""),
            ("GEMINI_API_KEY", "Gemini API Key override (optional; leave blank to use config/api_keys.json)", True, ""),
        ),
    },
)


def _format_env_value(value: str) -> str:
    val = str(value).strip()
    if not val:
        return ""
    if any(ch in val for ch in (" ", "#", "'", '"', "$")):
        escaped = val.replace("\\", "\\\\").replace('"', '\\"')
        return f'"{escaped}"'
    return val


def write_dotenv_updates(env_path: Path, updates: dict[str, str]) -> Path:
    """Merge `updates` into `env_path` while preserving existing keys and comments."""
    clean_updates = {
        str(k).strip(): str(v).strip()
        for k, v in (updates or {}).items()
        if str(k).strip() and str(v).strip()
    }
    existing_lines: list[str] = []
    if env_path.is_file():
        existing_lines = env_path.read_text(encoding="utf-8", errors="ignore").splitlines()

    seen: set[str] = set()
    out_lines: list[str] = []
    for raw in existing_lines:
        stripped = raw.strip()
        probe = stripped[len("export "):].strip() if stripped.startswith("export ") else stripped
        if not stripped or stripped.startswith("#") or "=" not in probe:
            out_lines.append(raw)
            continue
        key = probe.split("=", 1)[0].strip()
        if key in clean_updates:
            out_lines.append(f"{key}={_format_env_value(clean_updates[key])}")
            seen.add(key)
        else:
            out_lines.append(raw)

    if not out_lines:
        out_lines.append("# Mark-LVII optional plugin credentials (git-ignored; never commit)")

    for key, val in clean_updates.items():
        if key not in seen:
            out_lines.append(f"{key}={_format_env_value(val)}")

    env_path.parent.mkdir(parents=True, exist_ok=True)
    env_path.write_text("\n".join(out_lines) + "\n", encoding="utf-8")
    if os.name != "nt":
        try:
            os.chmod(env_path, 0o600)
        except OSError:
            pass
    return env_path


def verify_gitignore_protects(repo_root: Path = ROOT) -> dict[str, bool]:
    """Check `.gitignore` patterns cover `.env` and `config/mcp_servers.json`."""
    gi_path = repo_root / ".gitignore"
    patterns: set[str] = set()
    if gi_path.is_file():
        for ln in gi_path.read_text(encoding="utf-8", errors="ignore").splitlines():
            s = ln.strip()
            if s and not s.startswith("#"):
                patterns.add(s)
    return {
        ".env": ".env" in patterns,
        "config/mcp_servers.json": "config/mcp_servers.json" in patterns,
    }


def generate_mcp_config(
    *,
    example_path: Path = DEFAULT_MCP_EXAMPLE,
    output_path: Path = DEFAULT_MCP_CONFIG,
    enable_servers: list[str] | None = None,
    overwrite: bool = False,
    local_fs_root: Path | None = None,
) -> dict:
    """Generate `config/mcp_servers.json` from `config/mcp_servers.example.json` safely."""
    if not example_path.is_file():
        raise FileNotFoundError(f"MCP template not found: {example_path}")

    template = json.loads(example_path.read_text(encoding="utf-8"))
    if output_path.is_file() and not overwrite:
        data = json.loads(output_path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            data = {}
        servers = data.setdefault("servers", {})
        for name, spec in (template.get("servers") or {}).items():
            if name not in servers:
                servers[name] = dict(spec)
    else:
        data = dict(template)
        servers = data.setdefault("servers", {})

    data.pop("_comment", None)

    fs_dir = str((local_fs_root or (ROOT / "knowledge")).resolve())
    fs_srv = servers.get("local_filesystem")
    if isinstance(fs_srv, dict) and isinstance(fs_srv.get("command"), list) and len(fs_srv["command"]) >= 4:
        if "/home/tanishq/Mark-LVII/knowledge" in fs_srv["command"][3]:
            fs_srv["command"] = [*fs_srv["command"][:3], fs_dir]

    enabled_set = {s.strip() for s in (enable_servers or []) if s.strip()}
    for name in enabled_set:
        if name in servers and isinstance(servers[name], dict):
            servers[name]["enabled"] = True

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    if os.name != "nt":
        try:
            os.chmod(output_path, 0o600)
        except OSError:
            pass
    return data


def integration_status(
    *,
    env_path: Path = DEFAULT_ENV_PATH,
    mcp_path: Path = DEFAULT_MCP_CONFIG,
    repo_root: Path = ROOT,
) -> dict:
    """Return masked readiness summary without leaking any secret value."""
    dotenv_vals = _read_dotenv(env_path)

    def _is_set(key: str) -> bool:
        if env_path == DEFAULT_ENV_PATH:
            return has_env(key)
        return bool(dotenv_vals.get(key, "").strip())

    groups_status: dict[str, dict[str, bool]] = {}
    for grp in INTEGRATION_GROUPS:
        groups_status[grp["id"]] = {
            key: _is_set(key) for key, _label, _sec, _default in grp["fields"]
        }

    mcp_enabled: list[str] = []
    mcp_total = 0
    if mcp_path.is_file():
        try:
            cfg = json.loads(mcp_path.read_text(encoding="utf-8"))
            srvs = cfg.get("servers") if isinstance(cfg, dict) else {}
            if isinstance(srvs, dict):
                mcp_total = len(srvs)
                mcp_enabled = sorted(
                    k for k, v in srvs.items()
                    if isinstance(v, dict) and v.get("enabled") is True
                )
        except Exception:
            pass

    return {
        "env_file_exists": env_path.is_file(),
        "env_path": str(env_path),
        "mcp_file_exists": mcp_path.is_file(),
        "mcp_path": str(mcp_path),
        "mcp_servers_total": mcp_total,
        "mcp_servers_enabled": mcp_enabled,
        "gitignore_protected": verify_gitignore_protects(repo_root),
        "integrations": groups_status,
    }


def run_interactive_wizard(
    *,
    env_path: Path = DEFAULT_ENV_PATH,
    example_path: Path = DEFAULT_MCP_EXAMPLE,
    mcp_path: Path = DEFAULT_MCP_CONFIG,
    input_fn: Callable[[str], str] = input,
    secret_fn: Callable[[str], str] = getpass,
) -> dict:
    """Interactive CLI wizard for `.env` credentials and `config/mcp_servers.json`."""
    print("=== Mark-LVII Optional Integrations & MCP Setup Wizard ===")
    print("Leave any prompt blank to skip or keep its current value.")
    gi = verify_gitignore_protects()
    if not all(gi.values()):
        print(f"[WARN] Check .gitignore secret rules: {gi}")

    current_env = _read_dotenv(env_path)
    updates: dict[str, str] = {}

    for grp in INTEGRATION_GROUPS:
        ans = input_fn(f"\nConfigure {grp['title']}? [y/N]: ").strip().lower()
        if ans not in ("y", "yes"):
            continue
        for key, label, is_secret, default in grp["fields"]:
            already = bool(current_env.get(key) or os.environ.get(key))
            tag = " [currently set]" if already else (f" [default: {default}]" if default else "")
            prompt = f"  {label} ({key}){tag}: "
            val = (secret_fn(prompt) if is_secret else input_fn(prompt)).strip()
            if val:
                updates[key] = val
            elif default and not already:
                updates[key] = default

    if updates:
        write_dotenv_updates(env_path, updates)
        print(f"\nSaved {len(updates)} setting(s) cleanly to {env_path} (git-ignored).")
    else:
        print("\nNo `.env` changes written.")

    mcp_ans = input_fn(f"\nGenerate/update {mcp_path.name} from {example_path.name}? [Y/n]: ").strip().lower()
    if mcp_ans in ("", "y", "yes"):
        tmpl = json.loads(example_path.read_text(encoding="utf-8"))
        avail = sorted((tmpl.get("servers") or {}).keys())
        print(f"Available MCP server templates: {', '.join(avail)}")
        raw_enable = input_fn("Comma-separated server names to enable now (blank = keep disabled): ").strip()
        enable_list = [s.strip() for s in raw_enable.split(",") if s.strip()]
        generate_mcp_config(
            example_path=example_path,
            output_path=mcp_path,
            enable_servers=enable_list,
            overwrite=False,
        )
        print(f"Wrote {mcp_path} (git-ignored; enabled={enable_list or 'none'}).")

    return integration_status(env_path=env_path, mcp_path=mcp_path)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Configure optional Mark-LVII `.env` keys and MCP servers.")
    parser.add_argument("--status", action="store_true", help="Print masked integration & MCP status")
    parser.add_argument("--json", action="store_true", dest="as_json", help="Emit JSON summary")
    parser.add_argument("--init-mcp", action="store_true", help="Non-interactively generate config/mcp_servers.json")
    parser.add_argument("--enable-mcp", default="", help="Comma-separated MCP server names to enable with --init-mcp")
    parser.add_argument("--overwrite-mcp", action="store_true", help="Overwrite config/mcp_servers.json from example")
    parser.add_argument("--env-file", default=str(DEFAULT_ENV_PATH), help="Target .env path")
    parser.add_argument("--mcp-file", default=str(DEFAULT_MCP_CONFIG), help="Target mcp_servers.json path")
    args = parser.parse_args(argv)

    env_path = Path(args.env_file)
    mcp_path = Path(args.mcp_file)

    if args.init_mcp:
        enable_list = [s.strip() for s in args.enable_mcp.split(",") if s.strip()]
        generate_mcp_config(
            example_path=DEFAULT_MCP_EXAMPLE,
            output_path=mcp_path,
            enable_servers=enable_list,
            overwrite=args.overwrite_mcp,
        )
        st = integration_status(env_path=env_path, mcp_path=mcp_path)
    elif args.status:
        st = integration_status(env_path=env_path, mcp_path=mcp_path)
    else:
        st = run_interactive_wizard(env_path=env_path, mcp_path=mcp_path)

    if args.as_json:
        print(json.dumps(st, indent=2))
    else:
        print("\n=== Integration & MCP Status ===")
        print(f"  .env present            : {st['env_file_exists']}")
        print(f"  mcp_servers.json present: {st['mcp_file_exists']} (enabled={st['mcp_servers_enabled'] or 'none'})")
        print(f"  .gitignore protected    : {st['gitignore_protected']}")
        for grp, keys in st["integrations"].items():
            configured = [k for k, v in keys.items() if v]
            print(f"  {grp:10s}              : {len(configured)}/{len(keys)} set ({', '.join(configured) or 'none'})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
