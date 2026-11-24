"""plugins/cybersec_defender.py — Defensive Cybersecurity & Authorized Audit Plugin.

Provides strictly read-only, authorized, loopback-only / local-workspace
cybersecurity checks ($0 cost, zero external scanners, zero mutations):

Modes:
  - status        : Fast readiness and guardrail check (default on empty call).
  - audit_ports   : Read-only local listening TCP/UDP socket & process audit.
  - audit_posture : Read-only host firewall, Defender, and UAC posture check.
  - audit_code    : Read-only local source SAST + redacted secret scan (symlinks blocked).
  - audit_headers : Loopback-only HTTP security header audit (127.0.0.1 / localhost / ::1).
  - cve_lookup    : Free OSV.dev advisory/package lookup wrapped as UNTRUSTED data.

Safety & Privacy Guarantees:
  - Hard authorization guardrail: refuses non-loopback network targets and
    offensive exploitation/scanning requests.
  - Strictly read-only: never mutates firewall, Defender, UAC, or user files.
  - Never reads or leaks protected credential/memory files such as
    config/api_keys.json or memory/long_term.json.
  - All discovered secrets are redacted before returning or logging.
  - Zero Qt imports; subprocess calls strictly use shell=False.
"""

from __future__ import annotations

import ast
import ipaddress
import os
import re
import socket
import subprocess
import urllib.parse
from pathlib import Path
from typing import Any

import httpx

try:
    import psutil
except ImportError:  # pragma: no cover - optional fallback handled cleanly
    psutil = None  # type: ignore[assignment]

try:
    from core.outcome import untrusted as _wrap_untrusted
except Exception:  # pragma: no cover - isolated smoke fallback

    def _wrap_untrusted(source: str, content: str) -> str:
        safe = re.sub(r"[^a-zA-Z0-9_-]", "_", source)[:24] or "external"
        return f"<<<UNTRUSTED source={safe}>>>\n{content}\n<<<END UNTRUSTED>>>"


PLUGIN = {
    "name": "cybersec_defender",
    "brief": (
        "Strictly read-only defensive security checks: local listening ports, "
        "host posture, local code audit, loopback HTTP headers, and OSV.dev CVE lookup."
    ),
    "description": (
        "Authorized defensive cybersecurity inspector for the local machine and "
        "local source folders only (no remote scanning or offensive exploitation). "
        "Supports 8 modes: 'status' (fast guardrail readiness), 'audit_ports' "
        "(read-only listening TCP/UDP sockets & owning processes, flagging 0.0.0.0 "
        "exposure), 'audit_posture' (read-only Windows Firewall, Defender, and UAC "
        "check), 'audit_code' (local folder/file secret detection with redaction + "
        "AST/regex CWE audit, blocking symlinks outside target), 'audit_headers' "
        "(loopback-only 127.0.0.1/localhost HTTP security header audit), 'cve_lookup' "
        "(free OSV.dev advisory or package lookup wrapped as UNTRUSTED data), "
        "'defender_scan_file' (targeted Windows Defender MpCmdRun.exe file/folder malware scan), "
        "and 'defender_quick_scan' (CONFIRM-gated Windows Defender system quick scan)."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "mode": {
                "type": "STRING",
                "enum": [
                    "status",
                    "audit_ports",
                    "audit_posture",
                    "audit_code",
                    "audit_headers",
                    "cve_lookup",
                    "defender_scan_file",
                    "defender_quick_scan",
                ],
                "description": (
                    "Defensive check mode: 'status', 'audit_ports', 'audit_posture', "
                    "'audit_code', 'audit_headers', 'cve_lookup', 'defender_scan_file', "
                    "or 'defender_quick_scan'."
                ),
            },
            "target": {
                "type": "STRING",
                "description": (
                    "Mode-specific target: local directory/file for 'audit_code', "
                    "loopback URL (http://127.0.0.1:PORT/...) for 'audit_headers', "
                    "or CVE/GHSA/PYSEC ID or package name for 'cve_lookup'."
                ),
            },
            "ecosystem": {
                "type": "STRING",
                "description": (
                    "Optional OSV package ecosystem for 'cve_lookup' when target is a "
                    "package name (e.g. 'PyPI', 'npm', 'Go')."
                ),
            },
            "version": {
                "type": "STRING",
                "description": "Optional package version for 'cve_lookup' (e.g. '2.31.0').",
            },
            "max_files": {
                "type": "INTEGER",
                "description": "Maximum number of files to inspect in 'audit_code' (default 200).",
            },
        },
        "required": [],
    },
}

_BASE_DIR = Path(__file__).resolve().parent.parent
OSV_API_BASE = "https://api.osv.dev/v1"

_SKIP_DIRS = frozenset(
    {
        ".git",
        ".venv",
        "venv",
        "env",
        "node_modules",
        "__pycache__",
        ".pytest_cache",
        ".mypy_cache",
        ".ruff_cache",
        "backups",
        "dist",
        "build",
    }
)

_SCANNABLE_EXTS = frozenset(
    {
        ".py",
        ".js",
        ".ts",
        ".tsx",
        ".jsx",
        ".ps1",
        ".sh",
        ".bash",
        ".sql",
        ".yaml",
        ".yml",
        ".toml",
        ".json",
        ".ini",
        ".cfg",
        ".conf",
        ".html",
        ".htm",
        ".txt",
    }
)

_PROTECTED_NAME_PREFIXES = ("api_keys.", "long_term.", ".env")
_PROTECTED_EXTS = frozenset({".pem", ".key", ".pfx", ".p12", ".kdbx"})

_OFFENSIVE_PATTERNS = (
    re.compile(r"\b(exploit|metasploit|sqlmap|masscan|hydra|nikto|gobuster)\b", re.I),
    re.compile(r"\b(reverse shell|brute[- ]?force|ddos|ransomware|phishing)\b", re.I),
    re.compile(r"\b(attack|scan)\s+(?:external|remote|target)\s+(?:ip|host|domain)\b", re.I),
)

_SECRET_PATTERNS: list[tuple[str, str, re.Pattern[str], str]] = [
    (
        "CWE-798",
        "High",
        re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH |DSA |PRIVATE )+KEY-----"),
        "Private key material detected",
    ),
    (
        "CWE-798",
        "High",
        re.compile(r"\b(AIza[0-9A-Za-z_-]{35})\b"),
        "Google API key pattern detected",
    ),
    (
        "CWE-798",
        "High",
        re.compile(r"\b(sk-(?:proj-|live-|test-)?[A-Za-z0-9_-]{20,})\b"),
        "OpenAI/Stripe-style secret key detected",
    ),
    (
        "CWE-798",
        "High",
        re.compile(r"\b(gh[pousr]_[A-Za-z0-9]{20,})\b"),
        "GitHub token pattern detected",
    ),
    (
        "CWE-798",
        "High",
        re.compile(r"\b(AKIA[0-9A-Z]{16})\b"),
        "AWS Access Key ID pattern detected",
    ),
    (
        "CWE-798",
        "Medium",
        re.compile(
            r"""(?i)\b(?:api_?key|secret_?key|access_?token|auth_?token|password|passwd|private_?key)\s*[:=]\s*["']([^"'\s]{8,})["']"""
        ),
        "Hardcoded credential assignment detected",
    ),
]

_SCRUB_PATTERNS = (
    re.compile(r"AIza[0-9A-Za-z_-]{35}"),
    re.compile(r"sk-(?:proj-|live-|test-)?[A-Za-z0-9_-]{20,}"),
    re.compile(r"gh[pousr]_[A-Za-z0-9]{20,}"),
    re.compile(r"AKIA[0-9A-Z]{16}"),
)

_REGEX_CODE_PATTERNS: list[tuple[str, str, re.Pattern[str], str, str]] = [
    (
        "CWE-78",
        "High",
        re.compile(r"\bInvoke-Expression\b|\biex\s+[\$\(\"]", re.I),
        "PowerShell dynamic expression execution (Invoke-Expression)",
        "Use parameterized commands with '&' or explicit cmdlets instead of Invoke-Expression.",
    ),
    (
        "CWE-295",
        "High",
        re.compile(r"\bverify\s*=\s*False\b|ssl\._create_unverified_context\b"),
        "TLS certificate verification disabled",
        "Keep TLS verification enabled and pass explicit CA bundles if testing internal certs.",
    ),
    (
        "CWE-79",
        "Medium",
        re.compile(r"\bdangerouslySetInnerHTML\b|\.innerHTML\s*="),
        "Potential DOM XSS sink (innerHTML / dangerouslySetInnerHTML)",
        "Use textContent or sanitize untrusted HTML with an allowlist sanitizer.",
    ),
    (
        "CWE-89",
        "High",
        re.compile(
            r"""\b(?:SELECT|INSERT|UPDATE|DELETE)\b[^;\n]{0,120}(?:\+\s*[A-Za-z_][A-Za-z0-9_]*|\{[^}]+\}|%\s*\()""",
            re.I,
        ),
        "Dynamic SQL string formatting/concatenation",
        "Use parameterized SQL queries with bind placeholders instead of string interpolation.",
    ),
    (
        "CWE-502",
        "High",
        re.compile(r"\byaml\.load\s*\((?![^)]*SafeLoader|[^)]*CSafeLoader)"),
        "Unsafe yaml.load() without SafeLoader",
        "Use yaml.safe_load() or Loader=yaml.SafeLoader.",
    ),
]

_SENSITIVE_PORTS: dict[int, str] = {
    21: "FTP (cleartext)",
    22: "SSH",
    23: "Telnet (cleartext)",
    135: "Windows RPC",
    139: "NetBIOS Session",
    445: "SMB File Sharing",
    1433: "MSSQL",
    3306: "MySQL",
    3389: "RDP Remote Desktop",
    5432: "PostgreSQL",
    5900: "VNC",
    6379: "Redis",
    11434: "Ollama API",
    27017: "MongoDB",
}

_SECURITY_HEADERS: list[tuple[str, str, bool]] = [
    ("Content-Security-Policy", "Restricts script/resource origins against XSS", False),
    ("X-Content-Type-Options", "Prevents MIME-sniffing (expect 'nosniff')", False),
    ("X-Frame-Options", "Prevents clickjacking (expect 'DENY' or 'SAMEORIGIN')", False),
    ("Referrer-Policy", "Limits URL leakage in Referer headers", False),
    ("Permissions-Policy", "Restricts powerful browser APIs", False),
    ("Strict-Transport-Security", "Enforces HTTPS on TLS origins (HSTS)", True),
]


def _log(player: Any, message: str) -> None:
    if player is None:
        return
    fn = getattr(player, "write_log", None)
    if callable(fn):
        try:
            fn(message)
        except Exception:
            pass


def _show(player: Any, title: str, content: str) -> None:
    if player is None:
        return
    fn = getattr(player, "show_content", None)
    if callable(fn):
        try:
            fn(str(title)[:48], str(content)[:4000])
        except Exception:
            pass


def _redact_secret(raw: str) -> str:
    cleaned = raw.strip()
    if len(cleaned) <= 8:
        return "[REDACTED]"
    return f"****{cleaned[-4:]}"


def _scrub_output(text: str) -> str:
    scrubbed = text
    for pat in _SCRUB_PATTERNS:
        scrubbed = pat.sub(lambda m: _redact_secret(m.group(0)), scrubbed)
    return scrubbed


def _protected_paths() -> set[Path]:
    protected: set[Path] = set()
    for mod_name, attr in (
        ("memory.config_manager", "CONFIG_FILE"),
        ("memory.memory_manager", "MEMORY_PATH"),
    ):
        try:
            mod = __import_safe_config_mod(mod_name)
            val = getattr(mod, attr, None)
            if val:
                protected.add(Path(val).resolve())
        except Exception:
            pass
    for subdir in (_BASE_DIR / "config", _BASE_DIR / "memory"):
        if subdir.is_dir():
            try:
                for entry in subdir.iterdir():
                    low = entry.name.lower()
                    if any(low.startswith(p) for p in _PROTECTED_NAME_PREFIXES):
                        protected.add(entry.resolve())
            except OSError:
                pass
    return protected


def __import_safe_config_mod(mod_name: str) -> Any:
    import importlib

    return importlib.import_module(mod_name)


def _is_protected_file(path: Path, protected: set[Path]) -> bool:
    low = path.name.lower()
    if any(low.startswith(pref) for pref in _PROTECTED_NAME_PREFIXES):
        return True
    if path.suffix.lower() in _PROTECTED_EXTS:
        return True
    try:
        resolved = path.resolve()
        if resolved in protected:
            return True
    except OSError:
        return True
    return False


def _is_path_escaped(path: Path, root: Path) -> bool:
    """Return True if *path* is a symlink or resolves outside *root*."""
    try:
        root_resolved = root.resolve()
        if path.is_symlink():
            target_resolved = path.resolve()
            if not target_resolved.is_relative_to(root_resolved):
                return True
            return True
        path_resolved = path.resolve()
        return not path_resolved.is_relative_to(root_resolved)
    except OSError:
        return True


def _is_loopback_host(host: str) -> bool:
    h = (host or "").strip().lower().strip("[]")
    if not h:
        return False
    if h == "localhost" or h.endswith(".localhost"):
        return True
    try:
        return ipaddress.ip_address(h).is_loopback
    except ValueError:
        return False


def _is_loopback_url(target: str) -> tuple[bool, str, str]:
    raw = (target or "").strip()
    if not raw:
        return False, "", "Target URL is empty. Provide http://127.0.0.1:<port>/..."
    if "://" not in raw:
        raw = f"http://{raw}"
    parsed = urllib.parse.urlparse(raw)
    if parsed.scheme not in {"http", "https"}:
        return False, "", f"Unsupported URL scheme '{parsed.scheme}'; only http/https allowed."
    host = parsed.hostname or ""
    if not _is_loopback_host(host):
        return (
            False,
            "",
            (
                f"AUTHORIZATION BLOCKED: '{host}' is not a loopback address. "
                "cybersec_defender restricts active HTTP header audits strictly to "
                "127.0.0.1, localhost, or ::1."
            ),
        )
    return True, raw, ""


def _check_authorization(mode: str, target: str) -> str | None:
    combined = f"{mode} {target}".strip()
    for pat in _OFFENSIVE_PATTERNS:
        if pat.search(combined):
            return (
                "AUTHORIZATION BLOCKED: cybersec_defender is strictly defensive and "
                "local-only. Offensive exploitation, password cracking, and unauthorized "
                "remote scanning are refused."
            )
    if mode == "audit_ports" and target.strip() and not _is_loopback_host(target.strip()):
        return (
            f"AUTHORIZATION BLOCKED: remote host '{target.strip()}' cannot be scanned. "
            "audit_ports inspects listening sockets on this local PC only."
        )
    return None


def _cmd_status() -> str:
    return (
        "CyberSec Defender ready (strictly read-only & authorized local-only). "
        "Active guardrails: loopback-only HTTP checks (127.0.0.1/localhost/::1), "
        "local socket/posture inspection only, symlink-escape blocking in audit_code, "
        "automatic secret redaction, protected data files excluded, and untrusted "
        "wrapping on OSV.dev responses. Modes: status, audit_ports, audit_posture, "
        "audit_code, audit_headers, cve_lookup."
    )


def _proc_name(pid: int | None) -> str:
    if not pid or psutil is None:
        return "-"
    try:
        return psutil.Process(pid).name()
    except Exception:
        return f"pid:{pid}"


def _audit_ports() -> str:
    if psutil is None:
        return _audit_ports_netstat()

    rows: list[tuple[str, int, str, str, int | None, str]] = []
    try:
        conns = psutil.net_connections(kind="inet")
    except Exception:
        return _audit_ports_netstat()

    seen: set[tuple[str, str, int, int | None]] = set()
    for conn in conns:
        proto = "TCP" if conn.type == socket.SOCK_STREAM else "UDP"
        if proto == "TCP" and getattr(conn, "status", "") != "LISTEN":
            continue
        laddr = getattr(conn, "laddr", None)
        if not laddr:
            continue
        ip = getattr(laddr, "ip", "") or ""
        port = int(getattr(laddr, "port", 0) or 0)
        pid = getattr(conn, "pid", None)
        key = (proto, ip, port, pid)
        if key in seen:
            continue
        seen.add(key)
        is_wildcard = ip in {"0.0.0.0", "::", "*"}
        exposure = "EXPOSED_WILDCARD" if is_wildcard else ("LOOPBACK" if _is_loopback_host(ip) else "SPECIFIC_IF")
        proc = _proc_name(pid)
        rows.append((proto, port, ip, exposure, pid, proc))

    rows.sort(key=lambda r: (0 if r[3] == "EXPOSED_WILDCARD" else 1, r[1], r[0]))
    exposed = [r for r in rows if r[3] == "EXPOSED_WILDCARD"]
    loopback = [r for r in rows if r[3] == "LOOPBACK"]

    lines = [
        f"Local Listening Sockets Audit: {len(rows)} listeners "
        f"({len(exposed)} wildcard 0.0.0.0/:: exposed, {len(loopback)} loopback-only)."
    ]
    if exposed:
        lines.append("Wildcard (all-interface) listeners to review:")
        for proto, port, ip, _, pid, proc in exposed[:20]:
            note = f" [{_SENSITIVE_PORTS[port]}]" if port in _SENSITIVE_PORTS else ""
            lines.append(f"  - {proto} {ip}:{port} (pid={pid or '-'}, proc={proc}){note}")
    else:
        lines.append("No wildcard 0.0.0.0/:: listeners found.")

    if loopback:
        lines.append("Sample loopback-bound listeners (safe localhost scope):")
        for proto, port, ip, _, pid, proc in loopback[:10]:
            lines.append(f"  - {proto} {ip}:{port} (pid={pid or '-'}, proc={proc})")
    return "\n".join(lines)


def _audit_ports_netstat() -> str:
    try:
        proc = subprocess.run(
            ["netstat", "-ano"],
            capture_output=True,
            text=True,
            timeout=8,
            shell=False,
            stdin=subprocess.DEVNULL,
        )
    except Exception as exc:
        return f"Unable to inspect local listening ports: {exc}"
    if proc.returncode != 0:
        return f"netstat failed (rc={proc.returncode}): {proc.stderr.strip()[:200]}"
    listening = [
        line.strip()
        for line in proc.stdout.splitlines()
        if "LISTENING" in line or line.strip().startswith("UDP")
    ]
    exposed = [ln for ln in listening if "0.0.0.0:" in ln or "[::]:" in ln]
    header = f"Local netstat fallback: {len(listening)} entries, {len(exposed)} wildcard 0.0.0.0/[::] listeners."
    sample = "\n".join(f"  - {ln}" for ln in (exposed or listening)[:15])
    return f"{header}\n{sample}" if sample else header


def _audit_posture() -> str:
    """Strictly read-only Windows Firewall, Defender, and UAC posture check."""
    ps_script = (
        "$ErrorActionPreference='SilentlyContinue'; "
        "$fw = Get-NetFirewallProfile | ForEach-Object { \"$($_.Name)=$($_.Enabled)\" }; "
        "$uac = (Get-ItemProperty 'HKLM:\\SOFTWARE\\Microsoft\\Windows\\CurrentVersion\\Policies\\System').EnableLUA; "
        "$mp = Get-MpComputerStatus; "
        "Write-Output ('FIREWALL:' + ($fw -join ',')); "
        "Write-Output ('UAC_ENABLELUA:' + $uac); "
        "if ($mp) { "
        "  Write-Output ('DEFENDER_AV:' + $mp.AntivirusEnabled + '|RTP:' + $mp.RealTimeProtectionEnabled + '|AM:' + $mp.AMServiceEnabled) "
        "} else { Write-Output 'DEFENDER_AV:unavailable' }"
    )
    cmd = [
        "powershell.exe",
        "-NoProfile",
        "-NonInteractive",
        "-ExecutionPolicy",
        "Bypass",
        "-Command",
        ps_script,
    ]
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=12,
            shell=False,
            stdin=subprocess.DEVNULL,
        )
    except FileNotFoundError:
        return (
            "Host Posture Audit (Linux/WSL2 without powershell.exe): "
            f"uid={os.getuid() if hasattr(os, 'getuid') else 'unknown'}, "
            "check host firewall with ufw/nftables manually (read-only)."
        )
    except subprocess.TimeoutExpired:
        return "Host Posture Audit timed out querying Windows Defender/Firewall (read-only)."
    except Exception as exc:
        return f"Host Posture Audit error: {exc}"

    if proc.returncode != 0 and not proc.stdout.strip():
        return f"Host Posture Audit failed (rc={proc.returncode}): {proc.stderr.strip()[:250]}"

    parsed: dict[str, str] = {}
    for line in proc.stdout.splitlines():
        if ":" in line:
            k, v = line.split(":", 1)
            parsed[k.strip()] = v.strip()

    fw_raw = parsed.get("FIREWALL", "unavailable")
    uac_raw = parsed.get("UAC_ENABLELUA", "unknown")
    def_raw = parsed.get("DEFENDER_AV", "unavailable")

    warnings: list[str] = []
    if "False" in fw_raw or "=0" in fw_raw:
        warnings.append(f"One or more Windows Firewall profiles disabled ({fw_raw})")
    if uac_raw not in {"1", "True", "true"}:
        warnings.append(f"UAC EnableLUA is not enabled ({uac_raw})")
    if "False" in def_raw:
        warnings.append(f"Windows Defender protection degraded ({def_raw})")

    status_label = "GOOD" if not warnings else f"ATTENTION ({len(warnings)} issue(s))"
    lines = [
        f"Host Security Posture (Read-Only): {status_label}",
        f"  - Firewall profiles: {fw_raw}",
        f"  - UAC EnableLUA    : {uac_raw}",
        f"  - Windows Defender : {def_raw}",
    ]
    for w in warnings:
        lines.append(f"  - Warning: {w}")
    return "\n".join(lines)


class _PythonAstAuditor(ast.NodeVisitor):
    """Lightweight read-only AST visitor for high-signal Python CWE patterns."""

    def __init__(self) -> None:
        self.findings: list[tuple[int, str, str, str, str]] = []

    def _call_name(self, node: ast.AST) -> str:
        if isinstance(node, ast.Name):
            return node.id
        if isinstance(node, ast.Attribute):
            parent = self._call_name(node.value)
            return f"{parent}.{node.attr}" if parent else node.attr
        return ""

    def visit_Call(self, node: ast.Call) -> None:  # noqa: N802
        name = self._call_name(node.func)
        line = getattr(node, "lineno", 1)

        if name in {"eval", "exec"}:
            self.findings.append(
                (
                    line,
                    "CWE-95",
                    "High",
                    f"Dynamic code execution via {name}()",
                    "Avoid eval/exec on untrusted data; use ast.literal_eval or explicit dispatch.",
                )
            )
        elif name in {"os.system", "os.popen"}:
            self.findings.append(
                (
                    line,
                    "CWE-78",
                    "High",
                    f"OS shell execution via {name}()",
                    "Use subprocess.run([...], shell=False) with an explicit argument list.",
                )
            )
        elif name.startswith("subprocess."):
            for kw in node.keywords:
                if (
                    kw.arg == "shell"
                    and isinstance(kw.value, ast.Constant)
                    and kw.value.value is True
                ):
                    self.findings.append(
                        (
                            line,
                            "CWE-78",
                            "High",
                            f"{name}(..., shell=True) enables shell injection",
                            "Pass a list of arguments and set shell=False.",
                        )
                    )
        elif name in {"pickle.loads", "pickle.load", "marshal.loads", "marshal.load"}:
            self.findings.append(
                (
                    line,
                    "CWE-502",
                    "High",
                    f"Unsafe binary deserialization via {name}()",
                    "Use JSON or another data-only schema instead of pickle/marshal.",
                )
            )
        elif name in {"hashlib.md5", "hashlib.sha1"}:
            used_for_sec_false = any(
                kw.arg == "usedforsecurity"
                and isinstance(kw.value, ast.Constant)
                and kw.value.value is False
                for kw in node.keywords
            )
            if not used_for_sec_false:
                self.findings.append(
                    (
                        line,
                        "CWE-328",
                        "Low",
                        f"Weak hash algorithm {name}()",
                        "Use hashlib.sha256() for integrity/security or pass usedforsecurity=False for non-crypto IDs.",
                    )
                )
        elif name.endswith(".execute") and node.args:
            arg0 = node.args[0]
            if isinstance(arg0, (ast.JoinedStr, ast.BinOp, ast.Call)):
                self.findings.append(
                    (
                        line,
                        "CWE-89",
                        "High",
                        "Dynamic SQL expression passed to .execute()",
                        "Use bound parameters: cursor.execute(sql, (param1, ...)).",
                    )
                )
        self.generic_visit(node)


def _scan_single_file(path: Path, rel: str) -> list[dict[str, str]]:
    try:
        raw = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    if len(raw) > 512_000:
        return []

    findings: list[dict[str, str]] = []
    lines = raw.splitlines()

    if path.suffix.lower() == ".py":
        try:
            tree = ast.parse(raw, filename=rel)
            auditor = _PythonAstAuditor()
            auditor.visit(tree)
            for ln, cwe, sev, msg, rem in auditor.findings:
                findings.append(
                    {
                        "file": rel,
                        "line": str(ln),
                        "cwe": cwe,
                        "severity": sev,
                        "detail": msg,
                        "remediation": rem,
                    }
                )
        except SyntaxError:
            pass

    for idx, line in enumerate(lines, start=1):
        for cwe, sev, pat, desc in _SECRET_PATTERNS:
            m = pat.search(line)
            if m:
                token = m.group(1) if m.lastindex else m.group(0)
                if token.upper() in {"REDACTED", "CHANGEME", "YOUR_KEY_HERE"}:
                    continue
                findings.append(
                    {
                        "file": rel,
                        "line": str(idx),
                        "cwe": cwe,
                        "severity": sev,
                        "detail": f"{desc} (redacted={_redact_secret(token)})",
                        "remediation": "Move secrets into environment variables or git-ignored config files.",
                    }
                )
        for cwe, sev, pat, desc, rem in _REGEX_CODE_PATTERNS:
            if pat.search(line):
                findings.append(
                    {
                        "file": rel,
                        "line": str(idx),
                        "cwe": cwe,
                        "severity": sev,
                        "detail": desc,
                        "remediation": rem,
                    }
                )
    return findings


def _collect_audit_files(root: Path, max_files: int, protected: set[Path]) -> tuple[list[Path], int, int]:
    files: list[Path] = []
    skipped_symlinks = 0
    skipped_protected = 0

    if root.is_file():
        if _is_path_escaped(root, root.parent):
            return [], 1, 0
        if _is_protected_file(root, protected):
            return [], 0, 1
        return [root], 0, 0

    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        dp = Path(dirpath)
        kept_dirs: list[str] = []
        for d in sorted(dirnames):
            if d in _SKIP_DIRS:
                continue
            cand_dir = dp / d
            if _is_path_escaped(cand_dir, root):
                skipped_symlinks += 1
                continue
            kept_dirs.append(d)
        dirnames[:] = kept_dirs

        for fname in sorted(filenames):
            fpath = dp / fname
            if _is_path_escaped(fpath, root):
                skipped_symlinks += 1
                continue
            if _is_protected_file(fpath, protected):
                skipped_protected += 1
                continue
            if fpath.suffix.lower() not in _SCANNABLE_EXTS:
                continue
            files.append(fpath)
            if len(files) >= max_files:
                return files, skipped_symlinks, skipped_protected
    return files, skipped_symlinks, skipped_protected


def _audit_code(target: str, max_files: int = 200) -> str:
    if not target or not target.strip():
        return (
            "audit_code requires a local folder or file path in 'target' "
            "(e.g. target='plugins'). No remote URLs allowed."
        )
    raw_target = target.strip()
    if "://" in raw_target and not raw_target.startswith("file://"):
        return "AUTHORIZATION BLOCKED: audit_code inspects local paths only, not remote URLs."

    path = Path(raw_target)
    if not path.is_absolute():
        path = _BASE_DIR / path
    if not path.exists() and not path.is_symlink():
        return f"Target path not found: {raw_target}"

    root = path.parent if path.is_file() else path
    if path.is_symlink() and _is_path_escaped(path, root):
        return f"Symlink blocked outside audit target boundary: {raw_target}"

    limit = max(1, min(int(max_files or 200), 1000))
    protected = _protected_paths()
    files, skipped_symlinks, skipped_protected = _collect_audit_files(path, limit, protected)

    all_findings: list[dict[str, str]] = []
    for fpath in files:
        try:
            rel = str(fpath.relative_to(root))
        except ValueError:
            rel = fpath.name
        all_findings.extend(_scan_single_file(fpath, rel))

    sev_rank = {"High": 0, "Medium": 1, "Low": 2}
    all_findings.sort(key=lambda item: (sev_rank.get(item["severity"], 9), item["file"], int(item["line"])))

    counts = {"High": 0, "Medium": 0, "Low": 0}
    for item in all_findings:
        counts[item["severity"]] = counts.get(item["severity"], 0) + 1

    lines = [
        f"Local Code Audit ({path}): scanned {len(files)} file(s), "
        f"found {len(all_findings)} issue(s) "
        f"[High={counts['High']}, Medium={counts['Medium']}, Low={counts['Low']}]; "
        f"blocked_symlinks={skipped_symlinks}, protected_skipped={skipped_protected}."
    ]
    for item in all_findings[:30]:
        lines.append(
            f"  - [{item['severity']}] {item['cwe']} {item['file']}:{item['line']} — "
            f"{item['detail']} | Fix: {item['remediation']}"
        )
    if len(all_findings) > 30:
        lines.append(f"  ... and {len(all_findings) - 30} additional finding(s).")
    if not all_findings:
        lines.append("  No issues detected by static rules in scanned files.")
    return _scrub_output("\n".join(lines))


def _audit_headers(target: str) -> str:
    ok, url, err = _is_loopback_url(target)
    if not ok:
        return err

    parsed = urllib.parse.urlparse(url)
    is_https = parsed.scheme == "https"
    try:
        with httpx.Client(timeout=6.0, follow_redirects=False) as client:
            resp = client.get(url)
    except Exception as exc:
        return f"Loopback HTTP header check failed for {url}: {exc}"

    if 300 <= resp.status_code < 400 and "location" in resp.headers:
        loc = resp.headers["location"]
        loc_parsed = urllib.parse.urlparse(loc)
        if loc_parsed.netloc and not _is_loopback_host(loc_parsed.hostname or ""):
            return (
                f"Redirect to non-loopback origin blocked ({loc}). "
                f"Initial status={resp.status_code} at {url}."
            )

    present: list[str] = []
    missing: list[str] = []
    notes: list[str] = []

    for header, guidance, tls_only in _SECURITY_HEADERS:
        val = resp.headers.get(header)
        if val is not None:
            present.append(f"{header}: {val[:120]}")
        else:
            if tls_only and not is_https:
                notes.append(f"  - Info: {header} omitted on plaintext http://localhost (required only on HTTPS).")
            else:
                missing.append(f"  - Missing {header}: {guidance}")

    acao = resp.headers.get("access-control-allow-origin")
    if acao and acao.strip() == "*":
        notes.append("  - Warning: Access-Control-Allow-Origin is wildcard '*' — restrict if endpoint serves private local data.")

    set_cookies = resp.headers.get_list("set-cookie") if hasattr(resp.headers, "get_list") else []
    for ck in set_cookies[:5]:
        low_ck = ck.lower()
        gaps = [flag for flag in ("httponly", "samesite") if flag not in low_ck]
        if is_https and "secure" not in low_ck:
            gaps.append("secure")
        if gaps:
            cname = ck.split("=", 1)[0].strip()
            notes.append(f"  - Cookie '{cname}' missing flag(s): {', '.join(gaps)}")

    lines = [
        f"Loopback Security Headers Audit ({url}) -> HTTP {resp.status_code}",
        f"  Present ({len(present)}): " + (", ".join(h.split(":", 1)[0] for h in present) if present else "none"),
    ]
    lines.extend(missing)
    lines.extend(notes)
    if not missing and not notes:
        lines.append("  All checked HTTP security headers are configured.")
    return "\n".join(lines)


def _format_osv_vuln(vuln: dict[str, Any]) -> list[str]:
    vid = str(vuln.get("id") or "unknown")
    summary = str(vuln.get("summary") or "(no summary)").strip()
    details = str(vuln.get("details") or "").strip().replace("\n", " ")
    if len(details) > 260:
        details = details[:257] + "..."
    aliases = [str(a) for a in (vuln.get("aliases") or [])[:6]]
    sev_items = vuln.get("severity") or []
    sev_str = ", ".join(
        f"{s.get('type', '?')}:{s.get('score', '?')}" for s in sev_items[:2] if isinstance(s, dict)
    )
    refs = [
        str(r.get("url"))
        for r in (vuln.get("references") or [])[:4]
        if isinstance(r, dict) and r.get("url")
    ]
    lines = [f"- ID: {vid}"]
    if aliases:
        lines.append(f"  Aliases: {', '.join(aliases)}")
    if sev_str:
        lines.append(f"  Severity: {sev_str}")
    lines.append(f"  Summary: {summary}")
    if details:
        lines.append(f"  Details: {details}")
    if refs:
        lines.append(f"  References: {', '.join(refs)}")
    return lines


def _cve_lookup(target: str, ecosystem: str = "", version: str = "") -> str:
    query = (target or "").strip()
    if not query:
        return "cve_lookup requires a CVE/GHSA/PYSEC ID or package name in 'target'."

    advisory_re = re.compile(r"^(?:CVE|GHSA|PYSEC|OSV|RUSTSEC|GO)-[A-Za-z0-9-]+$", re.I)
    base = OSV_API_BASE.rstrip("/")

    try:
        with httpx.Client(timeout=6.0) as client:
            if advisory_re.match(query):
                resp = client.get(f"{base}/vulns/{urllib.parse.quote(query, safe='-')}")
                if resp.status_code == 404:
                    body = f"No OSV advisory record found for ID '{query}'."
                    return _wrap_untrusted("cybersec_cve", body)
                resp.raise_for_status()
                vuln = resp.json()
                body = "OSV.dev Advisory Record (data-only):\n" + "\n".join(_format_osv_vuln(vuln))
                return _wrap_untrusted("cybersec_cve", body)

            payload: dict[str, Any] = {"package": {"name": query}}
            if ecosystem.strip():
                payload["package"]["ecosystem"] = ecosystem.strip()
            if version.strip():
                payload["version"] = version.strip()
            resp = client.post(f"{base}/query", json=payload)
            resp.raise_for_status()
            data = resp.json()
            vulns = data.get("vulns") or []
            if not vulns:
                body = f"No known OSV vulnerabilities matched package='{query}' ecosystem='{ecosystem or 'any'}' version='{version or 'any'}'."
                return _wrap_untrusted("cybersec_cve", body)
            lines = [
                f"OSV.dev Query Results for package='{query}' ({len(vulns)} advisories, showing top 8):"
            ]
            for v in vulns[:8]:
                lines.extend(_format_osv_vuln(v))
            return _wrap_untrusted("cybersec_cve", "\n".join(lines))
    except Exception as exc:
        fallback = (
            f"OSV.dev offline/lookup fallback for '{query}': {exc}. "
            "Verify manually offline via cached security notes in knowledge/cybersec/ "
            "or retry when network access to https://api.osv.dev is available."
        )
        return _wrap_untrusted("cybersec_cve", fallback)


def _find_mpcmdrun() -> Path | None:
    """Locate Windows Defender's official CLI scanner (`MpCmdRun.exe`)."""
    candidates: list[Path] = []
    pf = os.environ.get("ProgramFiles") or r"C:\Program Files"
    candidates.append(Path(pf) / "Windows Defender" / "MpCmdRun.exe")
    prog_data = os.environ.get("ProgramData") or r"C:\ProgramData"
    platform_dir = Path(prog_data) / "Microsoft" / "Windows Defender" / "Platform"
    if platform_dir.is_dir():
        try:
            for sub in sorted(platform_dir.iterdir(), reverse=True):
                if sub.is_dir():
                    candidates.append(sub / "MpCmdRun.exe")
        except OSError:
            pass
    for c in candidates:
        try:
            if c.is_file():
                return c
        except OSError:
            continue
    return None


def _defender_scan_file(target: str) -> str:
    raw = (target or "").strip()
    if not raw:
        return "defender_scan_file requires a local file or directory path in 'target'."
    p = Path(raw)
    if p.is_symlink() or not p.exists():
        return f"AUTHORIZATION BLOCKED: target does not exist or symlink refused: {raw}"

    mp = _find_mpcmdrun()
    if mp is None:
        return "Windows Defender MpCmdRun.exe is not available on this OS/machine."

    try:
        proc = subprocess.run(
            [str(mp), "-Scan", "-ScanType", "3", "-File", str(p.resolve())],
            capture_output=True,
            text=True,
            timeout=90.0,
            shell=False,
            stdin=subprocess.DEVNULL,
        )
    except subprocess.TimeoutExpired:
        return f"Windows Defender file scan timed out after 90s for '{p}'."
    except Exception as exc:
        return f"Windows Defender file scan failed: {exc}"

    out = ((proc.stdout or "") + "\n" + (proc.stderr or "")).strip()
    if proc.returncode == 0:
        return f"Windows Defender custom scan CLEAN (exit=0) for '{p.resolve()}'.\n{out[:1200]}"
    if proc.returncode == 2:
        return f"ALERT: Windows Defender detected a threat (exit=2) in '{p.resolve()}':\n{out[:1600]}"
    return f"Windows Defender scan finished (exit={proc.returncode}) for '{p.resolve()}':\n{out[:1200]}"


def _defender_quick_scan(player: Any = None) -> str:
    mp = _find_mpcmdrun()
    if mp is None:
        return "Windows Defender MpCmdRun.exe is not available on this OS/machine."

    def _run_quick() -> str:
        try:
            proc = subprocess.run(
                [str(mp), "-Scan", "-ScanType", "1"],
                capture_output=True,
                text=True,
                timeout=300.0,
                shell=False,
                stdin=subprocess.DEVNULL,
            )
            out = ((proc.stdout or "") + "\n" + (proc.stderr or "")).strip()
            msg = (
                f"Windows Defender Quick Scan complete (exit={proc.returncode}): "
                + ("no threats found." if proc.returncode == 0 else out[:300])
            )
        except Exception as exc:
            msg = f"Windows Defender Quick Scan failed: {exc}"
        _log(player, msg)
        _show(player, "Windows Defender Quick Scan", msg)
        return msg

    def _launch_thread() -> str:
        import threading
        threading.Thread(target=_run_quick, daemon=True, name="defender-quick-scan").start()
        return "Windows Defender Quick Scan started in the background; results will appear on the HUD."

    try:
        from core.evolution import safety_guard
        return safety_guard.enforce_gate(
            "cybersec_defender",
            "defender_quick_scan",
            "Run Windows Defender Quick Scan",
            "Executes `MpCmdRun.exe -Scan -ScanType 1` across system memory and startup locations.",
            _launch_thread,
            key="defender_quick_scan",
        )
    except Exception:
        from core import confirm
        return confirm.request(
            key="defender_quick_scan",
            title="[CONFIRM] Run Windows Defender Quick Scan",
            detail="Executes `MpCmdRun.exe -Scan -ScanType 1`.",
            run=_launch_thread,
        )


def run(parameters: dict, player=None, session_memory=None) -> str:
    """Execute a strictly defensive, read-only cybersecurity check cleanly."""
    try:
        params = parameters if isinstance(parameters, dict) else {}
        mode = str(params.get("mode") or "status").strip().lower()
        target = str(params.get("target") or "").strip()
        ecosystem = str(params.get("ecosystem") or "").strip()
        version = str(params.get("version") or "").strip()
        max_files = int(params.get("max_files") or 200)

        blocked = _check_authorization(mode, target)
        if blocked:
            _log(player, f"cybersec_defender blocked: {blocked}")
            return blocked

        if mode == "status":
            result = _cmd_status()
            _log(player, "cybersec_defender: status OK")
            return result
        if mode == "audit_ports":
            result = _audit_ports()
            _log(player, "cybersec_defender: audit_ports complete")
            _show(player, "Local Listening Ports Audit", result)
            return result
        if mode == "audit_posture":
            result = _audit_posture()
            _log(player, "cybersec_defender: audit_posture complete")
            _show(player, "Host Security Posture", result)
            return result
        if mode == "audit_code":
            result = _audit_code(target, max_files=max_files)
            _log(player, "cybersec_defender: audit_code complete")
            _show(player, f"Code Security Audit ({target})", result)
            return result
        if mode == "audit_headers":
            result = _audit_headers(target)
            _log(player, "cybersec_defender: audit_headers complete")
            _show(player, "Loopback HTTP Headers Audit", result)
            return result
        if mode == "cve_lookup":
            result = _cve_lookup(target, ecosystem=ecosystem, version=version)
            _log(player, f"cybersec_defender: cve_lookup complete ({target})")
            _show(player, f"OSV Advisory Lookup ({target})", result)
            return result
        if mode == "defender_scan_file":
            result = _defender_scan_file(target)
            _log(player, "cybersec_defender: defender_scan_file complete")
            _show(player, "Windows Defender File Scan", result)
            return result
        if mode == "defender_quick_scan":
            return _defender_quick_scan(player=player)

        return (
            f"Unknown mode '{mode}'. Supported modes: status, audit_ports, "
            "audit_posture, audit_code, audit_headers, cve_lookup, "
            "defender_scan_file, defender_quick_scan."
        )
    except Exception as exc:
        _log(player, f"cybersec_defender error: {exc}")
        return f"CyberSec Defender encountered a safe error: {exc}"
