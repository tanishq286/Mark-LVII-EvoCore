"""
dashboard/server.py — JARVIS Local HTTPS & Mobile Companion Dashboard

Binds to 0.0.0.0:8000 with auto-generated self-signed HTTPS/TLS certificates in
git-ignored `memory/certs/` (SAN entries for real active LAN IPv4 + localhost +
127.0.0.1 + optional Tailscale IP), enabling Android Chrome microphone voice
input, PWA installation, Web Crypto AES-256-GCM encryption, and OkHttp TLS
SHA-256 certificate pinning in the native Kotlin APK.

Also runs an optional HTTP -> HTTPS redirector on port 8001 when free, supports
Tailscale + Cloudflare Quick Tunnel detection for 4G/5G anywhere access, and
serves live PC screen streaming, remote tap/type input, push-to-talk audio,
live mission logs, and 1-tap remote HUD CONFIRM / ChoiceBanner approvals.
"""

import asyncio
import atexit
import base64
import hashlib
import inspect
from io import BytesIO
import ipaddress
import json
import os
import re
import secrets
import shutil
import socket
import string
import subprocess
import sys
import threading
import time
from pathlib import Path
from urllib.parse import urlencode

_DEPS_OK = False
try:
    from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Request
    from fastapi.responses import HTMLResponse, JSONResponse, FileResponse, Response
    import uvicorn
    _DEPS_OK = True
except ImportError:
    pass

# python-multipart is required for file uploads — optional dependency
_UPLOAD_OK = False
try:
    from fastapi import UploadFile, File as FastAPIFile
    _UPLOAD_OK = True
except Exception:
    pass

BASE_DIR      = Path(__file__).resolve().parent.parent
STATIC_DIR    = Path(__file__).parent / "static"
CERTS_DIR     = BASE_DIR / "memory" / "certs"
LEGACY_CERTS  = BASE_DIR / "config" / "certs"

# Primary dashboard binds to 0.0.0.0:8000 over HTTPS/TLS.
# Optional plain-HTTP redirector on REDIRECT_PORT bounces accidental HTTP hits to HTTPS :8000.
PORT          = 8000
SECURE_PORT   = 8000
REDIRECT_PORT = 8001
MAX_UPLOAD_MB = 500

_VIRTUAL_ADAPTER_RE = re.compile(
    r"(wsl|vethernet|hyper-v|docker|vbox|virtualbox|vmware|bluetooth|loopback|"
    r"npcap|tap-|tun\b|wireguard|zerotier|cloudflare|proton|teredo|isatap|tailscale)",
    re.IGNORECASE,
)


def _make_uploads_dir() -> Path:
    """Return (and create) the cross-platform uploads folder."""
    for candidate in [
        Path.home() / "Downloads" / "JARVIS Uploads",
        Path.home() / "Documents" / "JARVIS Uploads",
        BASE_DIR / "uploads",
    ]:
        try:
            candidate.mkdir(parents=True, exist_ok=True)
            return candidate
        except Exception:
            pass
    return BASE_DIR / "uploads"


UPLOADS_DIR = _make_uploads_dir()


def _get_gemini_key() -> str | None:
    try:
        with open(BASE_DIR / "config" / "api_keys.json", "r", encoding="utf-8") as f:
            return json.load(f).get("gemini_api_key")
    except Exception:
        return None


_KEY_CHARS = [c for c in (string.ascii_uppercase + string.digits)
              if c not in ("O", "I", "L", "0", "1")]

# ── AES-256-GCM ───────────────────────────────────────────────────────────────
_AES_SALT = b"JARVIS-DASHBOARD-v1"
_KDF_ROUNDS = 200_000


def _derive_key(session_key: str) -> bytes:
    """Pairing key -> 32-byte AES-256 key via PBKDF2-HMAC-SHA256 (200k rounds)."""
    return hashlib.pbkdf2_hmac(
        "sha256", session_key.encode("utf-8"), _AES_SALT, _KDF_ROUNDS, dklen=32
    )


def _decrypt(aes_key: bytes, enc_b64: str) -> str:
    """Decrypt base64(nonce[12] || ciphertext || tag) with AES-256-GCM."""
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    raw = base64.b64decode(enc_b64)
    if len(raw) < 13:
        raise ValueError("ciphertext too short to contain a nonce")
    nonce, payload = raw[:12], raw[12:]
    return AESGCM(aes_key).decrypt(nonce, payload, None).decode("utf-8")


# ── Network Adapter, Tailscale & Cloudflare Quick Tunnel Detection ────────────

def _is_usable_lan_ipv4(ip_str: str) -> bool:
    """True for non-loopback, non-link-local, non-Tailscale-CGNAT unicast IPv4."""
    try:
        addr = ipaddress.IPv4Address(ip_str)
    except ValueError:
        return False
    if addr.is_loopback or addr.is_link_local or addr.is_multicast or addr.is_unspecified:
        return False
    # Tailscale uses 100.64.0.0/10 CGNAT — tracked separately by detect_tailscale()
    if addr in ipaddress.IPv4Network("100.64.0.0/10"):
        return False
    return True


def _parse_windows_ipconfig_lan_ip() -> str:
    """Parse Windows ipconfig.exe output (fast <100ms, works from both Win32 & WSL2)."""
    ipconfig_candidates = []
    if sys.platform == "win32":
        ipconfig_candidates.append("ipconfig")
    else:
        win_ipc = Path("/mnt/c/Windows/System32/ipconfig.exe")
        if win_ipc.is_file():
            ipconfig_candidates.append(str(win_ipc))
    for exe in ipconfig_candidates:
        try:
            r = subprocess.run(
                [exe],
                stdin=subprocess.DEVNULL,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=3.0,
            )
            adapter = ""
            ip_val = ""
            gw_val = ""
            with_gw: list[tuple[int, str]] = []
            without_gw: list[tuple[int, str]] = []

            def _commit(name: str, ip_s: str, gw_s: str) -> None:
                if not name or not ip_s or _VIRTUAL_ADAPTER_RE.search(name):
                    return
                if not _is_usable_lan_ipv4(ip_s):
                    return
                low = name.lower()
                prio = 0 if ("wi-fi" in low or "wlan" in low or "wireless" in low) else (
                    1 if "ethernet" in low else 2
                )
                if gw_s:
                    with_gw.append((prio, ip_s))
                else:
                    without_gw.append((prio, ip_s))

            for raw_line in r.stdout.splitlines():
                line = raw_line.rstrip()
                if line and not line.startswith(" ") and line.endswith(":"):
                    _commit(adapter, ip_val, gw_val)
                    adapter = line[:-1].strip()
                    ip_val = ""
                    gw_val = ""
                elif "IPv4" in line and ":" in line:
                    cand = line.split(":", 1)[1].strip().split("(")[0].strip()
                    if _is_usable_lan_ipv4(cand):
                        ip_val = cand
                elif "Default Gateway" in line and ":" in line:
                    cand_gw = line.split(":", 1)[1].strip()
                    if cand_gw:
                        gw_val = cand_gw
            _commit(adapter, ip_val, gw_val)
            if with_gw:
                with_gw.sort(key=lambda x: x[0])
                return with_gw[0][1]
            if without_gw:
                without_gw.sort(key=lambda x: x[0])
                return without_gw[0][1]
        except Exception:
            pass
    return ""


def detect_lan_ipv4() -> str:
    """Return the real active Wi-Fi/Ethernet IPv4 address, ignoring WSL/vEthernet/Hyper-V."""
    # On WSL2, Linux eth0 is NATed behind Hyper-V; prefer the real Windows Wi-Fi/Ethernet IPv4
    if sys.platform != "win32" and Path("/mnt/c/Windows/System32/ipconfig.exe").is_file():
        win_ip = _parse_windows_ipconfig_lan_ip()
        if win_ip:
            return win_ip

    # Map every local IPv4 -> interface name via psutil so we can filter out virtual switches
    ip_to_iface: dict[str, str] = {}
    up_ifaces: set[str] = set()
    try:
        import psutil
        stats = psutil.net_if_stats()
        for iface, st in stats.items():
            if getattr(st, "isup", True):
                up_ifaces.add(iface)
        for iface, addrs in psutil.net_if_addrs().items():
            for snic in addrs:
                if snic.family == socket.AF_INET and snic.address:
                    ip_to_iface[snic.address] = iface
    except Exception:
        pass

    # 1. OS route table probe — accept ONLY if the chosen interface is not a virtual switch
    for probe in ("8.8.8.8", "1.1.1.1", "192.168.1.1"):
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.settimeout(0.4)
            s.connect((probe, 80))
            ip = s.getsockname()[0]
            s.close()
            iface = ip_to_iface.get(ip, "")
            if _is_usable_lan_ipv4(ip) and not _VIRTUAL_ADAPTER_RE.search(iface):
                return ip
        except Exception:
            pass

    # 2. Windows ipconfig gateway-aware fallback
    if sys.platform == "win32":
        win_ip = _parse_windows_ipconfig_lan_ip()
        if win_ip:
            return win_ip

    # 3. Rank physical UP interfaces from psutil (Wi-Fi / WLAN / Ethernet first)
    ranked: list[tuple[int, str]] = []
    for ip, iface in ip_to_iface.items():
        if up_ifaces and iface not in up_ifaces:
            continue
        if _VIRTUAL_ADAPTER_RE.search(iface) or not _is_usable_lan_ipv4(ip):
            continue
        low = iface.lower()
        prio = 0 if ("wi-fi" in low or "wlan" in low or "wireless" in low or low.startswith("wl")) else (
            1 if ("ethernet" in low or low.startswith(("eth", "en"))) else 2
        )
        ranked.append((prio, ip))
    if ranked:
        ranked.sort(key=lambda item: item[0])
        return ranked[0][1]

    # 4. Last-resort stdlib resolution
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ip = info[4][0]
            iface = ip_to_iface.get(ip, "")
            if _is_usable_lan_ipv4(ip) and not _VIRTUAL_ADAPTER_RE.search(iface):
                return ip
    except Exception:
        pass

    return "127.0.0.1"


def _local_ip() -> str:
    """Backward-compatible alias returning the active non-virtual LAN IPv4."""
    return detect_lan_ipv4()


def detect_tailscale(*, port: int = PORT, https: bool = True) -> dict:
    """Detect active Tailscale 4G/5G mesh IPv4 / MagicDNS URL if available."""
    ts_ip = ""
    dns_name = ""
    try:
        import psutil
        for iface, addrs in psutil.net_if_addrs().items():
            for snic in addrs:
                if snic.family != socket.AF_INET or not snic.address:
                    continue
                try:
                    addr = ipaddress.IPv4Address(snic.address)
                    if "tailscale" in iface.lower() or addr in ipaddress.IPv4Network("100.64.0.0/10"):
                        ts_ip = snic.address
                        break
                except ValueError:
                    continue
            if ts_ip:
                break
    except Exception:
        pass

    ts_cli = shutil.which("tailscale")
    if not ts_cli and sys.platform == "win32":
        cand = Path(r"C:\Program Files\Tailscale\tailscale.exe")
        if cand.is_file():
            ts_cli = str(cand)
    if ts_cli:
        try:
            r = subprocess.run(
                [ts_cli, "status", "--json"],
                stdin=subprocess.DEVNULL,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=2.5,
            )
            if r.returncode == 0 and r.stdout.strip():
                st = json.loads(r.stdout)
                if st.get("BackendState") == "Running":
                    self_node = st.get("Self") or {}
                    ips = self_node.get("TailscaleIPs") or []
                    for cand_ip in ips:
                        if "." in str(cand_ip):
                            ts_ip = str(cand_ip)
                            break
                    dns_name = str(self_node.get("DNSName") or "").rstrip(".")
        except Exception:
            pass

    scheme = "https" if https else "http"
    host = dns_name or ts_ip
    return {
        "available": bool(ts_ip),
        "ip": ts_ip,
        "dns_name": dns_name,
        "url": f"{scheme}://{host}:{port}" if host else "",
        "pwa_url": f"{scheme}://{host}:{port}/pwa" if host else "",
    }


LOCAL_TUNNEL_PORT = 8766
PAIRING_PIN_TTL_SECONDS = 300
_TUNNEL_LOCK = threading.RLock()
_TUNNEL_START_LOCK = threading.RLock()       # one launch at a time (auto-start vs. API)
_TUNNEL_SUPERVISOR: threading.Thread | None = None
_TUNNEL_PROC: subprocess.Popen | None = None
_TUNNEL_URL: str = ""
_TUNNEL_PROVIDER: str = ""
_TUNNEL_ERR: str = ""
_TRYCF_RE = re.compile(r"https://(?!api\.)[a-zA-Z0-9]+(?:-[a-zA-Z0-9]+)+\.trycloudflare\.com", re.IGNORECASE)
_LHR_RE = re.compile(r"https://[a-zA-Z0-9-]+(?:\.[a-zA-Z0-9-]+)*\.lhr\.life", re.IGNORECASE)


def _find_cloudflared() -> str | None:
    exe = shutil.which("cloudflared")
    if exe:
        return exe
    for cand in (
        Path(r"C:\Program Files\cloudflared\cloudflared.exe"),
        Path(r"C:\Program Files (x86)\cloudflared\cloudflared.exe"),
        Path.home() / ".local" / "bin" / "cloudflared",
        Path("/usr/local/bin/cloudflared"),
    ):
        if cand.is_file():
            return str(cand)
    return None


def _find_ssh() -> str | None:
    if sys.platform == "win32":
        win_ssh = Path(r"C:\Windows\System32\OpenSSH\ssh.exe")
        if win_ssh.is_file():
            return str(win_ssh)
    return shutil.which("ssh")


def get_tunnel_status() -> dict:
    """Return current Quick Tunnel (`cloudflared` or `localhost.run` fallback) status & live HTTPS URL."""
    with _TUNNEL_LOCK:
        running = _TUNNEL_PROC is not None and _TUNNEL_PROC.poll() is None
        url = _TUNNEL_URL if running else ""
        provider = _TUNNEL_PROVIDER if running else ""
        err = _TUNNEL_ERR
    exe = _find_cloudflared() or _find_ssh()
    return {
        "installed": bool(exe),
        "binary": exe or "",
        "provider": provider,
        "running": running,
        "url": url,
        "pwa_url": f"{url}/pwa" if url else "",
        "error": err,
    }


def _spawn_tunnel_process(cmd: list[str], url_re: re.Pattern[str], provider: str, wait_s: float) -> bool:
    global _TUNNEL_PROC, _TUNNEL_URL, _TUNNEL_PROVIDER, _TUNNEL_ERR
    found_ev = threading.Event()
    last_lines: list[str] = []
    creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0

    def _watch_output(proc: subprocess.Popen) -> None:
        global _TUNNEL_URL, _TUNNEL_PROVIDER, _TUNNEL_ERR
        try:
            assert proc.stdout is not None
            for raw in iter(proc.stdout.readline, b""):
                line = raw.decode("utf-8", errors="replace").strip()
                if line:
                    last_lines.append(line)
                m = url_re.search(line)
                if m:
                    with _TUNNEL_LOCK:
                        _TUNNEL_URL = m.group(0).rstrip("/")
                        _TUNNEL_PROVIDER = provider
                        _TUNNEL_ERR = ""
                    found_ev.set()
        except Exception as exc:
            with _TUNNEL_LOCK:
                _TUNNEL_ERR = str(exc)
        finally:
            found_ev.set()

    try:
        proc = subprocess.Popen(
            cmd,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            shell=False,
            creationflags=creationflags,
        )
        with _TUNNEL_LOCK:
            _TUNNEL_PROC = proc
            _TUNNEL_URL = ""
            _TUNNEL_PROVIDER = provider
            _TUNNEL_ERR = ""
        threading.Thread(target=_watch_output, args=(proc,), daemon=True, name=f"jarvis-tunnel-{provider}").start()
        found_ev.wait(timeout=max(1.0, float(wait_s)))
        with _TUNNEL_LOCK:
            if _TUNNEL_URL and proc.poll() is None:
                return True
            _TUNNEL_ERR = (last_lines[-1] if last_lines else f"{provider} exited without tunnel URL")[:240]
    except Exception as exc:
        with _TUNNEL_LOCK:
            _TUNNEL_ERR = f"Failed to start {provider}: {exc}"
    stop_quick_tunnel()
    return False


def start_quick_tunnel(
    *,
    local_port: int = PORT,
    https: bool = True,
    tunnel_http_port: int = LOCAL_TUNNEL_PORT,
    wait_s: float = 25.0,
) -> dict:
    """Launch a public HTTPS tunnel (`cloudflared`, with automatic `localhost.run` SSH fallback if DPI-blocked)."""
    global _TUNNEL_ERR
    from core import privacy
    if privacy.is_active():
        with _TUNNEL_LOCK:
            _TUNNEL_ERR = "Public tunnel is disabled while Strict Local Privacy Mode is on."
        return get_tunnel_status()
    with _TUNNEL_START_LOCK:
        _start_tunnel_locked(local_port=local_port, https=https,
                             tunnel_http_port=tunnel_http_port, wait_s=wait_s)
    if privacy.is_active():                 # switched on while the tunnel was opening
        stop_quick_tunnel()
    return get_tunnel_status()


def _start_tunnel_locked(*, local_port: int, https: bool, tunnel_http_port: int, wait_s: float) -> dict:
    already_running = False
    with _TUNNEL_LOCK:
        already_running = bool(_TUNNEL_PROC is not None and _TUNNEL_PROC.poll() is None and _TUNNEL_URL)
    if already_running:
        return get_tunnel_status()

    cf_exe = _find_cloudflared()
    if cf_exe:
        scheme = "https" if https else "http"
        cf_cmd = [cf_exe, "tunnel", "--url", f"{scheme}://127.0.0.1:{local_port}"]
        if https:
            cf_cmd.append("--no-tls-verify")
        if _spawn_tunnel_process(cf_cmd, _TRYCF_RE, "cloudflared", float(wait_s)):
            return get_tunnel_status()

    # Automatic fallback when campus/corporate DPI resets api.trycloudflare.com:
    # OpenSSH reverse tunnel to localhost.run (terminates real CA-trusted HTTPS + WSS -> 127.0.0.1:LOCAL_TUNNEL_PORT)
    from core import privacy
    ssh_exe = _find_ssh()
    if ssh_exe and not privacy.is_active():
        null_kh = "NUL" if os.name == "nt" else "/dev/null"
        ssh_cmd = [
            ssh_exe,
            "-T",
            "-o", "StrictHostKeyChecking=no",
            "-o", f"UserKnownHostsFile={null_kh}",
            "-o", "ServerAliveInterval=30",
            "-o", "ExitOnForwardFailure=yes",
            "-R", f"80:127.0.0.1:{tunnel_http_port}",
            "nokey@localhost.run",
        ]
        if _spawn_tunnel_process(ssh_cmd, _LHR_RE, "localhost.run", max(6.0, float(wait_s))):
            return get_tunnel_status()

    return get_tunnel_status()


def stop_quick_tunnel() -> dict:
    """Stop any active Quick Tunnel process cleanly."""
    global _TUNNEL_PROC, _TUNNEL_URL, _TUNNEL_PROVIDER
    with _TUNNEL_LOCK:
        proc = _TUNNEL_PROC
        _TUNNEL_PROC = None
        _TUNNEL_URL = ""
        _TUNNEL_PROVIDER = ""
    if proc is not None and proc.poll() is None:
        try:
            proc.terminate()
            proc.wait(timeout=2.0)
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass
    return get_tunnel_status()


def tunnel_autostart_enabled() -> bool:
    """`"tunnel_autostart": true` in config/api_keys.json."""
    try:
        cfg = json.loads((BASE_DIR / "config" / "api_keys.json").read_text(encoding="utf-8"))
        return cfg.get("tunnel_autostart") is True
    except Exception:
        return False


def start_tunnel_supervisor(*, https: bool, interval_s: float = 60.0) -> bool:
    """Open the tunnel at launch and reopen it if it dies. Off unless configured."""
    global _TUNNEL_SUPERVISOR
    from core import privacy
    if not tunnel_autostart_enabled():
        return False
    if _TUNNEL_SUPERVISOR is not None and _TUNNEL_SUPERVISOR.is_alive():
        return True

    def _run() -> None:
        time.sleep(3.0)             # let uvicorn bind first
        while tunnel_autostart_enabled():
            if not privacy.is_active() and not get_tunnel_status()["running"]:
                st = start_quick_tunnel(local_port=PORT, https=https)
                print(f"[Dashboard] Tunnel: {st['pwa_url'] or st['error']}")
            time.sleep(interval_s)

    _TUNNEL_SUPERVISOR = threading.Thread(target=_run, daemon=True, name="jarvis-tunnel-supervisor")
    _TUNNEL_SUPERVISOR.start()
    return True


atexit.register(stop_quick_tunnel)          # Windows does not end child processes for us


# ── Firewall Inspection & Setup ───────────────────────────────────────────────

_BLOCK_CACHE = None


def blocking_firewall_rules() -> list:
    """Inbound Windows BLOCK rules aimed at the interpreter running this app."""
    if sys.platform != "win32":
        return []

    global _BLOCK_CACHE
    if _BLOCK_CACHE is not None:
        return _BLOCK_CACHE

    py_exe = sys.executable
    sibling = os.path.join(
        os.path.dirname(py_exe),
        "pythonw.exe" if py_exe.lower().endswith("python.exe") else "python.exe",
    )
    wanted = {py_exe.lower()}
    if os.path.exists(sibling):
        wanted.add(sibling.lower())

    found: list = []
    try:
        r = subprocess.run(
            ["netsh", "advfirewall", "firewall", "show", "rule",
             "name=all", "dir=in", "verbose"],
            capture_output=True, text=True, encoding="utf-8",
            errors="replace", timeout=20,
        )
        name = program = None
        for line in r.stdout.splitlines():
            if line.startswith("Rule Name:"):
                name = line.split(":", 1)[1].strip()
                program = None
            elif line.startswith("Program:"):
                program = line.split(":", 1)[1].strip().lower()
            elif line.startswith("Action:"):
                if (
                    name and program and program in wanted
                    and line.split(":", 1)[1].strip().lower() == "block"
                    and (name, program) not in found
                ):
                    found.append((name, program))
    except Exception as exc:
        print(f"[Dashboard] could not read the firewall rules: {exc}")
    _BLOCK_CACHE = found
    return found


_ACCESS_ASKED = False


def report_network_access(*ports: int) -> bool:
    """Read-only startup check: warns if Windows Firewall has explicit Python block rules."""
    _ = ports
    if sys.platform != "win32":
        return True
    try:
        blocked = blocking_firewall_rules()
    except Exception:
        return True
    if blocked:
        print(
            "[Dashboard] Windows Firewall is blocking this Python from the network — "
            "open REMOTE CONTROL or run scripts/fix_phone_remote.ps1 to fix it."
        )
        return False
    return True


def fix_network_access(*ports: int) -> None:
    """Ensure Private/Domain inbound TCP rules exist for the dashboard ports."""
    global _ACCESS_ASKED
    if _ACCESS_ASKED:
        return
    _ACCESS_ASKED = True
    unique_ports = tuple(dict.fromkeys(int(p) for p in (ports or (PORT, REDIRECT_PORT)) if p))
    _ensure_network_access(*unique_ports)


def _ensure_network_access(*ports: int) -> None:
    """Cross-platform best-effort inbound LAN firewall setup on Private/Domain profiles."""
    import tempfile

    if sys.platform == "win32":
        import ctypes

        port_rules = {pt: f"JARVIS Dashboard Port {pt}" for pt in ports}
        prog_rule = "JARVIS Dashboard Python"

        py_exe = sys.executable
        _sibling = os.path.join(
            os.path.dirname(py_exe),
            "pythonw.exe" if py_exe.lower().endswith("python.exe") else "python.exe",
        )
        py_exes = [py_exe] + ([_sibling] if os.path.exists(_sibling) else [])

        def _netsh_rule_exists(name: str) -> bool:
            try:
                r = subprocess.run(
                    ["netsh", "advfirewall", "firewall", "show", "rule", f"name={name}"],
                    capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=5,
                )
                return r.returncode == 0 and "No rules match" not in r.stdout
            except Exception:
                return False

        def _network_is_public() -> bool:
            try:
                r = subprocess.run(
                    ["powershell", "-NoProfile", "-NonInteractive", "-Command",
                     "(Get-NetConnectionProfile | "
                     "Where-Object {$_.NetworkCategory -eq 'Public'} | "
                     "Measure-Object).Count"],
                    capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=6,
                )
                return r.stdout.strip() not in ("", "0")
            except Exception:
                return False

        blocked = blocking_firewall_rules()
        missing_ports = [pt for pt, nm in port_rules.items() if not _netsh_rule_exists(nm)]
        need_prog = not _netsh_rule_exists(prog_rule)
        if _network_is_public():
            print(
                "[Dashboard] Active Windows network profile is Public. Switch your trusted "
                "Wi-Fi to Private in Settings > Network & internet (or run scripts/fix_phone_remote.ps1 -SetPrivateProfile)."
            )

        if not missing_ports and not need_prog and not blocked:
            return

        bat_lines = ["@echo off"]
        for pt in missing_ports:
            bat_lines.append(
                f'netsh advfirewall firewall add rule '
                f'name="{port_rules[pt]}" protocol=TCP dir=in '
                f'localport={pt} profile=private,domain action=allow'
            )
        for rule_name, _prog in blocked:
            bat_lines.append(
                f'netsh advfirewall firewall delete rule name="{rule_name}" dir=in'
            )
        if need_prog or blocked:
            bat_lines.append(
                f'netsh advfirewall firewall delete rule name="{prog_rule}" dir=in'
            )
            for exe in py_exes:
                bat_lines.append(
                    f'netsh advfirewall firewall add rule '
                    f'name="{prog_rule}" dir=in profile=private,domain action=allow '
                    f'program="{exe}" enable=yes'
                )

        bat_body = "\r\n".join(bat_lines) + "\r\n"
        fd, bat_path = tempfile.mkstemp(suffix=".bat", prefix="jarvis_fw_")
        try:
            os.write(fd, bat_body.encode("mbcs"))
            os.close(fd)
        except Exception:
            try:
                os.close(fd)
            except Exception:
                pass
            return

        try:
            r = subprocess.run([bat_path], capture_output=True, timeout=8, shell=False)
            if r.returncode == 0:
                try:
                    os.unlink(bat_path)
                except Exception:
                    pass
                return
        except Exception:
            pass

        try:
            ret = ctypes.windll.shell32.ShellExecuteW(None, "runas", bat_path, None, None, 0)
            if int(ret) > 32:
                time.sleep(1.5)
        except Exception as exc:
            print(f"[Dashboard] Firewall setup error: {exc}")
        finally:
            def _cleanup(path: str) -> None:
                time.sleep(5)
                try:
                    os.unlink(path)
                except Exception:
                    pass
            threading.Thread(target=_cleanup, args=(bat_path,), daemon=True).start()
        return

    if sys.platform == "darwin":
        return

    target_port = ports[0] if ports else PORT

    def _privileged(cmd: list[str]) -> bool:
        for prefix in (["pkexec"], ["sudo", "-n"]):
            try:
                r = subprocess.run(prefix + cmd, capture_output=True, timeout=15)
                if r.returncode == 0:
                    return True
            except Exception:
                pass
        return False

    try:
        r = subprocess.run(["ufw", "status"], capture_output=True, text=True, timeout=5)
        if "active" in r.stdout.lower():
            _privileged(["ufw", "allow", f"{target_port}/tcp"])
    except FileNotFoundError:
        pass


# ── Self-Signed SAN TLS Certificate Management (memory/certs/) ────────────────

def _resolve_cert_files(certs_dir: Path | None = None) -> tuple[Path, Path]:
    d = Path(certs_dir) if certs_dir is not None else CERTS_DIR
    return d / "jarvis.key", d / "jarvis.crt"


def cert_san_entries(cert_path: Path | None = None) -> list[str]:
    """Return human-readable SAN strings (`DNS:localhost`, `IP:172.16.6.143`) for `cert_path`."""
    p = Path(cert_path) if cert_path else (CERTS_DIR / "jarvis.crt")
    if not p.is_file():
        return []
    try:
        from cryptography import x509
        cert = x509.load_pem_x509_certificate(p.read_bytes())
        san = cert.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
        out = [f"DNS:{d}" for d in san.get_values_for_type(x509.DNSName)]
        out.extend(f"IP:{ip}" for ip in san.get_values_for_type(x509.IPAddress))
        return out
    except Exception:
        return []


def _cert_covers_targets(cert_path: Path, key_path: Path, required_ips: set[str]) -> bool:
    if not (cert_path.is_file() and key_path.is_file()):
        return False
    try:
        import datetime
        from cryptography import x509
        cert = x509.load_pem_x509_certificate(cert_path.read_bytes())
        exp = getattr(cert, "not_valid_after_utc", None)
        now = datetime.datetime.now(datetime.timezone.utc)
        if exp is not None and exp <= (now + datetime.timedelta(days=7)):
            return False
        san = cert.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
        dns_vals = set(san.get_values_for_type(x509.DNSName))
        ip_vals = {str(ip) for ip in san.get_values_for_type(x509.IPAddress)}
        if "localhost" not in dns_vals:
            return False
        return required_ips.issubset(ip_vals)
    except Exception:
        return False


def _ensure_certs(
    *,
    certs_dir: Path | None = None,
    extra_ips: list[str] | None = None,
) -> bool:
    """Auto-generate or refresh self-signed TLS cert + key in git-ignored `memory/certs/`.

    Ensures `SubjectAlternativeName` always covers `localhost`, `127.0.0.1`, the
    real active Wi-Fi/Ethernet LAN IPv4, and any active Tailscale IPv4.
    """
    key_p, crt_p = _resolve_cert_files(certs_dir)
    target_ips: set[str] = {"127.0.0.1"}
    lan = detect_lan_ipv4()
    if lan:
        target_ips.add(lan)
    ts_info = detect_tailscale()
    if ts_info.get("ip"):
        target_ips.add(str(ts_info["ip"]))
    for extra in (extra_ips or []):
        if extra:
            target_ips.add(str(extra).strip())

    valid_ips: list[ipaddress.IPv4Address] = []
    for raw_ip in sorted(target_ips):
        try:
            valid_ips.append(ipaddress.IPv4Address(raw_ip))
        except ValueError:
            continue

    if _cert_covers_targets(crt_p, key_p, {str(ip) for ip in valid_ips}):
        return True

    try:
        import datetime
        from cryptography import x509
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import rsa
        from cryptography.x509.oid import NameOID
    except ImportError:
        print("[Dashboard] cryptography not installed — serving over plain HTTP.")
        return False

    try:
        key_p.parent.mkdir(parents=True, exist_ok=True)
        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)

        cn = lan if (lan and lan != "127.0.0.1") else "JARVIS Mark-LVII"
        who = x509.Name([
            x509.NameAttribute(NameOID.COMMON_NAME, cn),
            x509.NameAttribute(NameOID.ORGANIZATION_NAME, "JARVIS Mark-LVII"),
        ])

        alt: list[x509.GeneralName] = [x509.DNSName("localhost")]
        ts_dns = str(ts_info.get("dns_name") or "").strip()
        if ts_dns:
            alt.append(x509.DNSName(ts_dns))
        for ip_obj in valid_ips:
            alt.append(x509.IPAddress(ip_obj))

        now = datetime.datetime.now(datetime.timezone.utc)
        cert = (
            x509.CertificateBuilder()
            .subject_name(who)
            .issuer_name(who)
            .public_key(key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(now - datetime.timedelta(days=1))
            .not_valid_after(now + datetime.timedelta(days=3650))
            .add_extension(x509.SubjectAlternativeName(alt), critical=False)
            .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
            .sign(key, hashes.SHA256())
        )

        key_p.write_bytes(key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.TraditionalOpenSSL,
            encryption_algorithm=serialization.NoEncryption(),
        ))
        crt_p.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
        try:
            os.chmod(key_p, 0o600)
        except Exception:
            pass
        print(f"[Dashboard] TLS certificate ready in {key_p.parent} (SAN IPs: {[str(i) for i in valid_ips]})")
        return True
    except Exception as exc:
        print(f"[Dashboard] Certificate generation failed ({exc}) — serving over plain HTTP.")
        return False


def cert_fingerprint_sha256(cert_path: Path | None = None) -> str:
    """Return lowercase SHA-256 hex digest of the dashboard TLS certificate, or ''."""
    candidates = [Path(cert_path)] if cert_path else [CERTS_DIR / "jarvis.crt", LEGACY_CERTS / "jarvis.crt"]
    for p in candidates:
        if p.is_file():
            try:
                from cryptography import x509
                from cryptography.hazmat.primitives import hashes
                cert = x509.load_pem_x509_certificate(p.read_bytes())
                return cert.fingerprint(hashes.SHA256()).hex()
            except Exception:
                continue
    return ""


def _render_qr_png_base64(data: str) -> str:
    """Render `data` into a base64 PNG QR code if `qrcode` is installed, else ''."""
    if not data:
        return ""
    try:
        import qrcode as _qrmod
        qr = _qrmod.QRCode(
            box_size=6,
            border=2,
            error_correction=_qrmod.constants.ERROR_CORRECT_M,
        )
        qr.add_data(data)
        qr.make(fit=True)
        img = qr.make_image(fill_color="black", back_color="white")
        buf = BytesIO()
        img.save(buf, format="PNG")
        return base64.b64encode(buf.getvalue()).decode("ascii")
    except Exception:
        return ""


def _synthetic_screen_jpeg() -> bytes:
    """Minimal valid JPEG fallback when headless or OS screen capture is unavailable."""
    try:
        from PIL import Image, ImageDraw
        img = Image.new("RGB", (640, 360), color=(7, 9, 15))
        draw = ImageDraw.Draw(img)
        draw.rectangle([16, 16, 624, 344], outline=(0, 212, 255), width=2)
        draw.text((36, 160), "MARK-LVII LIVE DESKTOP STREAM", fill=(0, 212, 255))
        buf = BytesIO()
        img.save(buf, format="JPEG", quality=75)
        return buf.getvalue()
    except Exception:
        # 1x1 valid JPEG bytes
        return base64.b64decode(
            "/9j/4AAQSkZJRgABAQEASABIAAD/2wBDAP//////////////////////////////////////////"
            "////////////////////////////////////////////wgALCAABAAEBAREA/8QAFBABAAAAAAAAAAAAAAAAAAAAAP/aAAgBAQABPxA="
        )


def _read(name: str) -> str:
    return (STATIC_DIR / name).read_text(encoding="utf-8")


# ── HUD CONFIRM / ChoiceBanner Bridge Helpers (core/confirm.py stays IMMUTABLE) ──

def get_pending_hud_prompts() -> dict:
    """Read active HUD `CONFIRM` and `ChoiceBanner` prompts without mutating `core/confirm.py`."""
    confirm_info = None
    choice_info = None
    try:
        from core import confirm as _cf
        with _cf._lock:
            p = _cf._pending
            if p is not None:
                rem = _cf.TIMEOUT_SECONDS - (time.monotonic() - p.at)
                if rem > 0:
                    confirm_info = {
                        "key": p.key,
                        "title": p.title,
                        "detail": p.detail,
                        "expires_in": round(rem, 1),
                    }
        choice_info = _cf.pending_choice_info()
    except Exception:
        pass
    return {"confirm": confirm_info, "choice": choice_info}


# ── Active Server & Cross-Thread Broadcast Helpers ────────────────────────────

_TOKEN_TTL_SECONDS = 12 * 3600
_LOGIN_MAX_TRIES   = 6
_LOGIN_WINDOW      = 300.0
_LOGIN_LOCKOUT     = 300.0

ANDROID_DEVICES_FILE = BASE_DIR / "memory" / "android_devices.json"
_ACTIVE_SERVER = None


def set_active_server(srv) -> None:
    global _ACTIVE_SERVER
    _ACTIVE_SERVER = srv


def get_active_server():
    return _ACTIVE_SERVER


def broadcast_mission_log(text: str, *, source: str = "brain", status: str = "") -> bool:
    """Thread-safe helper allowing Brain/Swarm/MissionRunner to stream live logs to phones."""
    srv = get_active_server()
    if srv is None:
        return False
    return srv.push_mission_log(text, source=source, status=status)


def broadcast_hud_prompt(event_type: str, payload: dict | None = None) -> bool:
    """Thread-safe helper pushing `confirm_prompt` / `choice_prompt` updates to phones."""
    srv = get_active_server()
    if srv is None:
        return False
    return srv.push_ws_event({"type": event_type, **(payload or {}), "ts": time.time()})


def dispatch_android_command_sync(
    action: str,
    params: dict | None = None,
    *,
    device_id: str | None = None,
    timeout: float = 8.0,
) -> dict:
    """Thread-safe sync helper for plugins/android_bridge.py to call into active server."""
    srv = get_active_server()
    if srv is None:
        return {"ok": False, "error": "DashboardServer is not active."}
    loop = getattr(srv, "_loop", None)
    if loop is None or loop.is_closed():
        return {"ok": False, "error": "DashboardServer WebSocket event loop is offline."}
    try:
        fut = asyncio.run_coroutine_threadsafe(
            srv.send_to_android(device_id, action, params or {}, timeout=timeout),
            loop,
        )
        return fut.result(timeout=float(timeout) + 2.0)
    except Exception as exc:
        return {"ok": False, "error": f"Android dispatch failed ({type(exc).__name__}: {exc})"}


class _Throttle:
    """Failed-attempt counter, per client address."""

    def __init__(self):
        self._fails: dict[str, list[float]] = {}
        self._lock = threading.Lock()

    def blocked_for(self, who: str) -> float:
        now = time.time()
        with self._lock:
            tries = [t for t in self._fails.get(who, []) if now - t < _LOGIN_WINDOW]
            self._fails[who] = tries
            if len(tries) < _LOGIN_MAX_TRIES:
                return 0.0
            return max(0.0, _LOGIN_LOCKOUT - (now - tries[-1]))

    def record_failure(self, who: str) -> None:
        with self._lock:
            self._fails.setdefault(who, []).append(time.time())

    def clear(self, who: str) -> None:
        with self._lock:
            self._fails.pop(who, None)


class DashboardServer:

    def __init__(self, *, certs_dir: Path | None = None):
        self._certs_dir                   = Path(certs_dir) if certs_dir else CERTS_DIR
        self._ip                          = detect_lan_ipv4()
        _ensure_certs(certs_dir=self._certs_dir, extra_ips=[self._ip] if self._ip != "127.0.0.1" else [])
        self._tokens: dict[str, float]    = {}
        self._token_keys: dict[str, str]  = {}
        self._aes_cache: dict[str, bytes] = {}
        self._clients: set[WebSocket]     = set()
        self._history: list[dict]         = []
        self._mission_logs: list[dict]    = []
        self._command_queue               = asyncio.Queue()
        self._wake_callback               = None
        self._connect_callback            = None
        self._pending_keys: dict[str, float] = {}
        self._last_qr_key: str            = ""
        self._throttle                    = _Throttle()
        self._device_sessions: dict[str, dict] = {}
        self._phone_audio_queue: asyncio.Queue = asyncio.Queue(maxsize=200)
        self._uploads_dir                 = UPLOADS_DIR
        self._devices_file: Path          = ANDROID_DEVICES_FILE
        self._android_devices: dict[str, dict] = {}
        self._pending_android_reqs: dict[str, asyncio.Future] = {}
        self._android_events: list[dict]  = []
        self._tool_executor               = None
        self._event_callback              = None
        self._screen_capture_fn           = None
        self._screen_bounds_fn            = None
        self._screen_input_fn             = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._bind_error: str = ""
        self._login_html                  = _read("login.html")
        self._app_html                    = _read("app.html")
        self._pwa_html                    = _read("pwa.html")
        self._load_known_devices()
        set_active_server(self)
        self.app                          = self._build_app()

    # ── Key & TLS helpers ────────────────────────────────────────────────────

    def new_key(self, expiry_secs: int = 600) -> str:
        now = time.time()
        self._pending_keys = {k: v for k, v in self._pending_keys.items() if v > now}
        key = "".join(secrets.choice(_KEY_CHARS) for _ in range(8))
        self._pending_keys[key] = now + expiry_secs
        self._last_qr_key = key
        return key

    def active_or_new_key(self, expiry_secs: int = 600) -> str:
        now = time.time()
        self._pending_keys = {k: v for k, v in self._pending_keys.items() if v > now}
        if self._last_qr_key and self._pending_keys.get(self._last_qr_key, 0.0) > now + 30.0:
            return self._last_qr_key
        return self.new_key(expiry_secs=expiry_secs)

    def _ssl_enabled(self) -> bool:
        """HTTPS enabled by default using `memory/certs/` unless explicitly set false in config."""
        try:
            cfg_p = BASE_DIR / "config" / "api_keys.json"
            if cfg_p.is_file():
                cfg = json.loads(cfg_p.read_text(encoding="utf-8"))
                if cfg.get("dashboard_https") is False:
                    return False
        except Exception:
            pass
        key_p, crt_p = _resolve_cert_files(self._certs_dir)
        if not (key_p.is_file() and crt_p.is_file()):
            _ensure_certs(certs_dir=self._certs_dir)
        return key_p.is_file() and crt_p.is_file()

    def cert_sha256(self) -> str:
        _, crt_p = _resolve_cert_files(self._certs_dir)
        return cert_fingerprint_sha256(crt_p)

    def get_url(self, *, mode: str = "lan") -> str:
        """Primary URL (HTTPS on port 8000 by default, or active Tailscale / Cloudflare Tunnel)."""
        ssl_on = self._ssl_enabled()
        scheme = "https" if ssl_on else "http"
        m = (mode or "lan").strip().lower()
        if m == "tunnel":
            tun = get_tunnel_status()
            if tun.get("running") and tun.get("url"):
                return str(tun["url"]).rstrip("/")
        if m == "tailscale":
            ts = detect_tailscale(port=PORT, https=ssl_on)
            if ts.get("available") and ts.get("url"):
                return str(ts["url"]).rstrip("/")
        self._ip = detect_lan_ipv4()
        return f"{scheme}://{self._ip}:{PORT}"

    def get_manual_url(self) -> str:
        return f"{self.get_url()}/pwa"

    def build_qr_pairing_info(
        self,
        key: str | None = None,
        *,
        include_pin: bool = True,
        mode: str = "lan",
        fresh: bool = False,
    ) -> dict:
        """Build 1-tap phone pairing metadata (URL + PIN + TLS SHA-256 + PNG QR)."""
        ssl_on = self._ssl_enabled()
        base_url = self.get_url(mode=mode)
        pwa_url = f"{base_url}/pwa"
        fp = self.cert_sha256()
        if include_pin and fresh and not key:
            key = self.new_key(expiry_secs=PAIRING_PIN_TTL_SECONDS)
        pin = (key or self.active_or_new_key()) if include_pin else ""

        q_params: dict[str, str] = {}
        if pin:
            q_params["pin"] = pin
        if fp:
            q_params["fp"] = fp
        pairing_url = f"{pwa_url}?{urlencode(q_params)}" if q_params else pwa_url
        auto_login_url = f"{base_url}/auto-login?key={pin}" if pin else base_url

        qr_payload = {
            "v": 2,
            "url": base_url,
            "pwa_url": pwa_url,
            "pairing_url": pairing_url,
            "pin": pin,
            "cert_sha256": fp,
            "ws_path": "/ws",
            "https": ssl_on,
        }
        return {
            "ok": True,
            "https": ssl_on,
            "port": PORT,
            "lan_ip": self._ip,
            "url": base_url,
            "pwa_url": pwa_url,
            "pairing_url": pairing_url,
            "auto_login_url": auto_login_url,
            "pin": pin,
            "auth_required_for_pin": not include_pin,
            "cert_sha256": fp,
            "san": cert_san_entries(_resolve_cert_files(self._certs_dir)[1]),
            "qr_payload": qr_payload,
            "qr_png_base64": _render_qr_png_base64(pairing_url),
            "tailscale": detect_tailscale(port=PORT, https=ssl_on),
            "cloudflare_tunnel": get_tunnel_status(),
        }

    def _aes_key(self, session_key: str) -> bytes:
        if session_key not in self._aes_cache:
            self._aes_cache[session_key] = _derive_key(session_key)
        return self._aes_cache[session_key]

    def _decrypt(self, token: str, enc_b64: str) -> str | None:
        sk = self._token_keys.get(token)
        if not sk:
            return None
        try:
            return _decrypt(self._aes_key(sk), enc_b64)
        except Exception:
            return None

    # ── Callbacks & Injectables ──────────────────────────────────────────────

    def set_wake_callback(self, fn) -> None:
        self._wake_callback = fn

    def set_connect_callback(self, fn) -> None:
        self._connect_callback = fn

    def set_tool_executor(self, fn) -> None:
        self._tool_executor = fn

    def set_event_callback(self, fn) -> None:
        self._event_callback = fn

    def set_screen_handlers(self, *, capture_fn=None, bounds_fn=None, input_fn=None) -> None:
        if capture_fn is not None:
            self._screen_capture_fn = capture_fn
        if bounds_fn is not None:
            self._screen_bounds_fn = bounds_fn
        if input_fn is not None:
            self._screen_input_fn = input_fn

    # ── Screen Capture & Remote Tap/Type Execution ───────────────────────────

    def _get_screen_bounds(self) -> tuple[int, int, int, int]:
        if callable(self._screen_bounds_fn):
            try:
                b = self._screen_bounds_fn()
                if isinstance(b, (tuple, list)) and len(b) == 4:
                    return int(b[0]), int(b[1]), max(1, int(b[2])), max(1, int(b[3]))
            except Exception:
                pass
        try:
            import pyautogui
            sz = pyautogui.size()
            return 0, 0, max(1, int(sz.width)), max(1, int(sz.height))
        except Exception:
            return 0, 0, 1920, 1080

    def capture_screen_frame(self) -> tuple[bytes, str, int, int]:
        """Capture current PC desktop frame as `(bytes, mime_type, width, height)`."""
        _, _, w, h = self._get_screen_bounds()
        img_bytes: bytes | None = None
        mime = "image/jpeg"
        try:
            if callable(self._screen_capture_fn):
                raw = self._screen_capture_fn()
            else:
                from actions.screen_processor import _capture_screen
                raw = _capture_screen()
            if isinstance(raw, tuple) and len(raw) >= 2:
                img_bytes = bytes(raw[0]) if raw[0] else None
                mime = str(raw[1] or "image/jpeg")
            elif isinstance(raw, (bytes, bytearray)):
                img_bytes = bytes(raw)
        except Exception:
            img_bytes = None

        if not img_bytes:
            img_bytes = _synthetic_screen_jpeg()
            mime = "image/jpeg"
        return img_bytes, mime, w, h

    def execute_screen_input(self, payload: dict) -> dict:
        """Execute remote tap/click/type/key/scroll from phone with safety guards."""
        action = str(payload.get("action") or "tap").strip().lower()
        left, top, width, height = self._get_screen_bounds()

        x = payload.get("x")
        y = payload.get("y")
        raw_nx = payload.get("norm_x", payload.get("nx"))
        raw_ny = payload.get("norm_y", payload.get("ny"))
        if (raw_nx is not None and raw_ny is not None) and (x is None or y is None):
            nx = max(0.0, min(1.0, float(raw_nx or 0.0)))
            ny = max(0.0, min(1.0, float(raw_ny or 0.0)))
            x = left + int(round(nx * max(0, width - 1)))
            y = top + int(round(ny * max(0, height - 1)))
        elif x is not None and y is not None:
            x = max(left, min(left + width - 1, int(x)))
            y = max(top, min(top + height - 1, int(y)))

        keys_raw = str(payload.get("keys") or "").strip()
        text_raw = str(payload.get("text") or "")

        # Safety gate for destructive/power key combos sent remotely
        dangerous_keys = {"shift+delete", "shift+del", "ctrl+alt+del", "ctrl+alt+delete"}
        if action in ("key", "press") and keys_raw.lower().replace(" ", "") in dangerous_keys:
            from core.evolution import safety_guard
            msg = safety_guard.enforce_gate(
                "autonomous_screen_agent",
                "irreversible_act",
                f"Remote Destructive Key: {keys_raw}",
                f"Phone requested destructive shortcut {keys_raw}",
                lambda: self._run_raw_screen_input(action, x, y, text_raw, keys_raw, payload),
                key=f"phone_key_{keys_raw}",
            )
            return {"ok": False, "gated": True, "message": msg}

        if callable(self._screen_input_fn):
            res = self._screen_input_fn({
                "action": action,
                "x": x,
                "y": y,
                "text": text_raw,
                "keys": keys_raw,
                "direction": str(payload.get("direction") or "down"),
                "amount": int(payload.get("amount") or 3),
            })
            if isinstance(res, dict):
                return {"ok": bool(res.get("ok", True)), "x": x, "y": y, **res}
            return {"ok": True, "action": action, "x": x, "y": y, "detail": str(res)}

        detail = self._run_raw_screen_input(action, x, y, text_raw, keys_raw, payload)
        ok = not detail.lower().startswith("error:")
        return {"ok": ok, "action": action, "x": x, "y": y, "detail": detail}

    @staticmethod
    def _run_raw_screen_input(action: str, x: int | None, y: int | None, text: str, keys: str, payload: dict) -> str:
        try:
            from core import screenact
            if action in ("tap", "click", "double_click", "right_click"):
                if x is None or y is None:
                    return "Error: missing coordinates for screen tap."
                pt = screenact.Point(int(x), int(y), origin="phone_remote", label="phone_tap")
                if action == "double_click":
                    act = screenact.double_click(pt)
                elif action == "right_click":
                    act = screenact.right_click(pt)
                else:
                    act = screenact.click(pt)
                return act.detail or ("ok" if act.ok else f"Error: {act.error}")
            if action in ("type", "type_text"):
                act = screenact.type_text(text, clear_first=bool(payload.get("clear_first", False)))
                return act.detail or ("typed" if act.ok else f"Error: {act.error}")
            if action in ("key", "press"):
                act = screenact.press(keys)
                return act.detail or ("pressed" if act.ok else f"Error: {act.error}")
            if action == "scroll":
                direction = str(payload.get("direction") or "down").lower()
                amount = int(payload.get("amount") or 3)
                pt = screenact.Point(int(x), int(y), origin="phone_remote", label="scroll") if (x is not None and y is not None) else None
                act = screenact.scroll(direction, amount, target=pt)
                return act.detail or ("scrolled" if act.ok else f"Error: {act.error}")
            return f"Error: unsupported screen input action {action!r}"
        except Exception as exc:
            return f"Error: {type(exc).__name__}: {exc}"

    # ── Device Persistence & RPC ─────────────────────────────────────────────

    def _load_known_devices(self) -> None:
        p = Path(self._devices_file)
        if not p.is_file():
            return
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                for dev_id, meta in data.items():
                    if isinstance(meta, dict):
                        rec = dict(meta)
                        rec["connected"] = False
                        rec["ws"] = None
                        self._android_devices[str(dev_id)] = rec
        except Exception:
            pass

    def _save_known_devices(self) -> None:
        p = Path(self._devices_file)
        try:
            p.parent.mkdir(parents=True, exist_ok=True)
            serializable = {
                dev_id: {k: v for k, v in meta.items() if k != "ws"}
                for dev_id, meta in self._android_devices.items()
            }
            p.write_text(json.dumps(serializable, indent=2), encoding="utf-8")
        except Exception:
            pass

    def list_android_devices(self) -> list[dict]:
        return [
            {k: v for k, v in meta.items() if k != "ws"}
            for meta in self._android_devices.values()
        ]

    def list_connected_device_ids(self) -> list[str]:
        return [
            dev_id for dev_id, meta in self._android_devices.items()
            if meta.get("connected") and meta.get("ws") is not None
        ]

    async def send_to_android(
        self,
        device_id: str | None,
        action: str,
        params: dict | None = None,
        *,
        timeout: float = 8.0,
    ) -> dict:
        target_id = device_id
        if not target_id:
            connected = self.list_connected_device_ids()
            target_id = connected[-1] if connected else None
        if not target_id or target_id not in self._android_devices:
            return {"ok": False, "error": "No paired Android companion is currently connected."}
        dev = self._android_devices[target_id]
        ws = dev.get("ws")
        if not dev.get("connected") or ws is None:
            return {"ok": False, "error": f"Android device '{target_id}' is disconnected."}
        if dev.get("privacy_mode") and action not in ("ping", "status"):
            return {"ok": False, "error": f"Device '{target_id}' is in 100% On-Device Privacy Mode."}

        req_id = f"req_{secrets.token_hex(6)}"
        loop = asyncio.get_running_loop()
        fut: asyncio.Future = loop.create_future()
        self._pending_android_reqs[req_id] = fut
        try:
            await ws.send_json({
                "type": "pc_to_android",
                "request_id": req_id,
                "action": action,
                "params": params or {},
                "ts": time.time(),
            })
            return await asyncio.wait_for(fut, timeout=float(timeout))
        except asyncio.TimeoutError:
            return {"ok": False, "error": f"Android command '{action}' timed out after {timeout}s."}
        except Exception as exc:
            return {"ok": False, "error": f"Android WebSocket error: {exc}"}
        finally:
            self._pending_android_reqs.pop(req_id, None)

    # ── Broadcast & Mission Log Stream ───────────────────────────────────────

    async def broadcast(self, msg: dict) -> None:
        self._history.append(msg)
        if len(self._history) > 300:
            self._history = self._history[-300:]
        dead: set[WebSocket] = set()
        for ws in list(self._clients):
            try:
                await ws.send_json(msg)
            except Exception:
                dead.add(ws)
        self._clients -= dead

    def push_ws_event(self, msg: dict) -> bool:
        loop = self._loop
        if loop is not None and loop.is_running():
            try:
                asyncio.run_coroutine_threadsafe(self.broadcast(msg), loop)
                return True
            except Exception:
                return False
        self._history.append(msg)
        if len(self._history) > 300:
            self._history = self._history[-300:]
        return True

    def push_mission_log(self, text: str, *, source: str = "brain", status: str = "") -> bool:
        entry = {
            "type": "mission_log",
            "source": str(source or "brain"),
            "status": str(status or ""),
            "text": str(text or "")[:600],
            "ts": time.time(),
        }
        self._mission_logs.append(entry)
        if len(self._mission_logs) > 250:
            self._mission_logs = self._mission_logs[-250:]
        return self.push_ws_event(entry)

    # ── FastAPI App ──────────────────────────────────────────────────────────

    def _build_app(self) -> "FastAPI":
        app = FastAPI(docs_url=None, redoc_url=None)

        def _valid(tok: str) -> bool:
            if not tok:
                return False
            issued = self._tokens.get(tok)
            if issued is None:
                return False
            if time.time() - issued > _TOKEN_TTL_SECONDS:
                self._tokens.pop(tok, None)
                self._token_keys.pop(tok, None)
                return False
            return True

        def _auth(req: Request) -> bool:
            bearer = req.headers.get("authorization", "").removeprefix("Bearer ").strip()
            if _valid(bearer):
                return True
            q_tok = str(req.query_params.get("token") or "").strip()
            return _valid(q_tok)

        def _is_proxied_request(req: Request) -> bool:
            if req.headers.get("x-forwarded-for") or req.headers.get("cf-connecting-ip") or req.headers.get("x-real-ip"):
                return True
            hdr_host = (req.headers.get("host") or "").lower().split(":", 1)[0]
            return hdr_host.endswith((".lhr.life", ".trycloudflare.com"))

        def _who(req: Request) -> str:
            xff = (req.headers.get("cf-connecting-ip") or req.headers.get("x-forwarded-for") or "").split(",", 1)[0].strip()
            if xff:
                return xff
            return (getattr(getattr(req, "client", None), "host", "") or "unknown")

        def _is_trusted_local_caller(req: Request) -> bool:
            if _auth(req):
                return True
            q_pin = str(req.query_params.get("pin") or req.query_params.get("key") or "").strip().upper()
            now = time.time()
            if q_pin and self._pending_keys.get(q_pin, 0.0) > now:
                return True
            if _is_proxied_request(req):
                return False
            host = (getattr(getattr(req, "client", None), "host", "") or "").lower()
            if host in ("127.0.0.1", "::1", "localhost", "testclient"):
                return True
            if self._ip and host == self._ip.lower():
                return True
            return False

        @app.get("/login", response_class=HTMLResponse)
        async def login_page():
            return HTMLResponse(self._login_html)

        @app.get("/", response_class=HTMLResponse)
        async def index():
            html = (
                self._app_html
                .replace("__IP__", self._ip)
                .replace("__PORT__", str(PORT))
            )
            return HTMLResponse(html)

        @app.get("/pwa", response_class=HTMLResponse)
        async def pwa_page():
            html = (
                self._pwa_html
                .replace("__IP__", self._ip)
                .replace("__PORT__", str(PORT))
            )
            return HTMLResponse(html)

        @app.get("/manifest.webmanifest")
        async def pwa_manifest():
            return FileResponse(str(STATIC_DIR / "manifest.webmanifest"), media_type="application/manifest+json")

        @app.get("/sw.js")
        async def pwa_sw():
            return FileResponse(str(STATIC_DIR / "sw.js"), media_type="application/javascript")

        @app.get("/pwa-icon.svg")
        async def pwa_icon():
            return FileResponse(str(STATIC_DIR / "pwa-icon.svg"), media_type="image/svg+xml")

        @app.get("/api/cert-fingerprint")
        async def get_cert_fingerprint():
            return JSONResponse({
                "https": self._ssl_enabled(),
                "sha256": self.cert_sha256(),
                "lan_ip": self._ip,
                "port": PORT,
                "san": cert_san_entries(_resolve_cert_files(self._certs_dir)[1]),
            })

        @app.get("/api/android/qr")
        async def android_qr_ep(req: Request, mode: str = "lan", fresh: bool = False):
            include_pin = _is_trusted_local_caller(req)
            info = self.build_qr_pairing_info(include_pin=include_pin, mode=mode, fresh=fresh)
            return JSONResponse(info)

        @app.get("/api/android/tunnel")
        async def tunnel_status_ep(req: Request):
            if not _is_trusted_local_caller(req):
                return JSONResponse({"error": "Unauthorized"}, status_code=401)
            return JSONResponse({
                "ok": True,
                "tailscale": detect_tailscale(port=PORT, https=self._ssl_enabled()),
                "cloudflare_tunnel": get_tunnel_status(),
            })

        @app.post("/api/android/tunnel")
        async def tunnel_control_ep(req: Request):
            if not _is_trusted_local_caller(req):
                return JSONResponse({"error": "Unauthorized"}, status_code=401)
            try:
                body = await req.json()
                if not isinstance(body, dict):
                    body = {}
            except Exception:
                body = {}
            action = str(body.get("action") or "start").strip().lower()
            if action == "start":
                st = await asyncio.to_thread(start_quick_tunnel, local_port=PORT, https=self._ssl_enabled())
                payload = {"ok": bool(st.get("running")), "cloudflare_tunnel": st}
                if body.get("include_pairing") and st.get("running"):
                    payload["pairing"] = self.build_qr_pairing_info(
                        include_pin=True,
                        mode="tunnel",
                        fresh=bool(body.get("fresh", True)),
                    )
                return JSONResponse(payload)
            if action == "stop":
                st = await asyncio.to_thread(stop_quick_tunnel)
                return JSONResponse({"ok": True, "cloudflare_tunnel": st})
            return JSONResponse({"ok": True, "cloudflare_tunnel": get_tunnel_status()})

        @app.post("/api/android/pair")
        async def android_pair(req: Request):
            who = _who(req)
            wait = self._throttle.blocked_for(who)
            if wait > 0:
                return JSONResponse(
                    {"ok": False, "error": f"Too many attempts. Try again in {int(wait) + 1} seconds."},
                    status_code=429,
                )
            body = await req.json()
            entered = str(body.get("pin", "")).strip().upper()
            now = time.time()
            if entered in self._pending_keys and self._pending_keys[entered] > now:
                self._throttle.clear(who)
                del self._pending_keys[entered]
                if self._last_qr_key == entered:
                    self._last_qr_key = ""
                tok = secrets.token_urlsafe(32)
                dev_tok = secrets.token_urlsafe(32)
                dev_id = str(body.get("device_id") or f"android-{secrets.token_hex(3)}")
                self._tokens[tok] = now
                self._token_keys[tok] = entered
                self._aes_key(entered)
                self._device_sessions[dev_tok] = {"session_key": entered, "device_id": dev_id}
                self._android_devices[dev_id] = {
                    "device_id": dev_id,
                    "name": str(body.get("name") or "Android Companion"),
                    "model": str(body.get("model") or "Android"),
                    "android_version": str(body.get("android_version") or ""),
                    "capabilities": list(body.get("capabilities") or []),
                    "privacy_mode": False,
                    "connected": False,
                    "last_seen": now,
                    "ws": None,
                }
                self._save_known_devices()
                if self._connect_callback:
                    self._connect_callback()
                return JSONResponse({
                    "ok": True,
                    "token": tok,
                    "device_token": dev_tok,
                    "device_id": dev_id,
                    "session_key": entered,
                    "ws_path": "/ws",
                    "cert_sha256": self.cert_sha256(),
                    "protocol_version": 2,
                })
            self._throttle.record_failure(who)
            return JSONResponse({"ok": False, "error": "Invalid or expired pairing PIN"}, status_code=401)

        @app.get("/api/android/devices")
        async def android_devices_ep(req: Request):
            if not _auth(req):
                return JSONResponse({"error": "Unauthorized"}, status_code=401)
            return JSONResponse({
                "ok": True,
                "devices": self.list_android_devices(),
                "recent_events": self._android_events[-20:],
            })

        @app.post("/api/android/command")
        async def android_command_ep(req: Request):
            if not _auth(req):
                return JSONResponse({"error": "Unauthorized"}, status_code=401)
            body = await req.json()
            action = str(body.get("action") or "").strip().lower()
            device_id = str(body.get("device_id") or "").strip() or None
            params = body.get("params") if isinstance(body.get("params"), dict) else {}
            if not action:
                return JSONResponse({"ok": False, "error": "Missing action"}, status_code=400)
            from core.evolution import safety_guard
            tier = safety_guard.classify_risk("android_bridge", action)
            if tier != safety_guard.SAFE:
                msg = safety_guard.enforce_gate(
                    "android_bridge",
                    action,
                    f"Remote Android Command: {action}",
                    f"Device: {device_id or 'active'} | Params: {json.dumps(params)[:120]}",
                    lambda: json.dumps(dispatch_android_command_sync(action, params, device_id=device_id)),
                    key=f"android_http_{action}",
                )
                return JSONResponse({"ok": False, "gated": True, "tier": tier, "message": msg})
            res = await self.send_to_android(device_id, action, params)
            return JSONResponse(res)

        # ── Live PC Screen Stream + Remote Tap/Type Endpoints ────────────────

        @app.get("/api/screen/frame")
        async def screen_frame_ep(req: Request, format: str = "binary"):
            if not _auth(req):
                return JSONResponse({"error": "Unauthorized"}, status_code=401)
            img_bytes, mime, w, h = await asyncio.to_thread(self.capture_screen_frame)
            if str(format or "").lower() == "json":
                return JSONResponse({
                    "ok": True,
                    "mime_type": mime,
                    "width": w,
                    "height": h,
                    "image_b64": base64.b64encode(img_bytes).decode("ascii"),
                    "ts": time.time(),
                })
            return Response(
                content=img_bytes,
                media_type=mime,
                headers={
                    "Cache-Control": "no-store, no-cache, must-revalidate",
                    "X-Screen-Width": str(w),
                    "X-Screen-Height": str(h),
                },
            )

        @app.post("/api/screen/input")
        async def screen_input_ep(req: Request):
            if not _auth(req):
                return JSONResponse({"error": "Unauthorized"}, status_code=401)
            body = await req.json()
            if not isinstance(body, dict):
                return JSONResponse({"ok": False, "error": "Invalid JSON object"}, status_code=400)
            out = await asyncio.to_thread(self.execute_screen_input, body)
            return JSONResponse(out)

        # ── Live Mission Log & 1-Tap Remote HUD Approval Endpoints ───────────

        @app.get("/api/missions/live")
        async def missions_live_ep(req: Request):
            if not _auth(req):
                return JSONResponse({"error": "Unauthorized"}, status_code=401)
            return JSONResponse({
                "ok": True,
                "logs": self._mission_logs[-80:],
                "pending_prompts": get_pending_hud_prompts(),
            })

        @app.get("/api/confirm/pending")
        async def confirm_pending_ep(req: Request):
            if not _auth(req):
                return JSONResponse({"error": "Unauthorized"}, status_code=401)
            return JSONResponse({"ok": True, **get_pending_hud_prompts()})

        @app.post("/api/confirm/resolve")
        async def confirm_resolve_ep(req: Request):
            if not _auth(req):
                return JSONResponse({"error": "Unauthorized"}, status_code=401)
            body = await req.json()
            kind = str(body.get("kind") or "confirm").strip().lower()
            from core import confirm as _cf
            if kind == "choice":
                sel = body.get("selection")
                sync = bool(body.get("sync", False))
                msg = _cf.resolve_choice(sel, sync=sync)
                await self.broadcast({
                    "type": "confirm_resolved",
                    "kind": "choice",
                    "selection": sel,
                    "message": msg,
                    "ts": time.time(),
                })
                return JSONResponse({"ok": True, "kind": "choice", "message": msg})

            accepted = bool(body.get("accepted", False))
            _cf.resolve(accepted)
            await self.broadcast({
                "type": "confirm_resolved",
                "kind": "confirm",
                "accepted": accepted,
                "ts": time.time(),
            })
            return JSONResponse({"ok": True, "kind": "confirm", "accepted": accepted})

        # ── Web Browser Login & Device Session Endpoints ─────────────────────

        @app.post("/login")
        async def login(req: Request):
            who  = _who(req)
            wait = self._throttle.blocked_for(who)
            if wait > 0:
                return JSONResponse(
                    {"ok": False, "error": f"Too many attempts. Try again in {int(wait) + 1} seconds."},
                    status_code=429,
                )
            body    = await req.json()
            entered = str(body.get("pin", "")).strip().upper()
            now     = time.time()
            if entered in self._pending_keys and self._pending_keys[entered] > now:
                self._throttle.clear(who)
                del self._pending_keys[entered]
                if self._last_qr_key == entered:
                    self._last_qr_key = ""
                tok = secrets.token_urlsafe(32)
                self._tokens[tok] = time.time()
                self._token_keys[tok] = entered
                self._aes_key(entered)
                if self._connect_callback:
                    self._connect_callback()
                asyncio.create_task(self.broadcast({"type": "sys", "text": "Remote connection established."}))
                return JSONResponse({"ok": True, "token": tok, "session_key": entered, "cert_sha256": self.cert_sha256()})
            self._throttle.record_failure(who)
            return JSONResponse({"ok": False, "error": "Invalid or expired key"}, status_code=401)

        @app.get("/auto-login")
        async def auto_login(req: Request, key: str = ""):
            now = time.time()
            who = _who(req)
            blocked = self._throttle.blocked_for(who) > 0
            if blocked or not key or key not in self._pending_keys or self._pending_keys[key] <= now:
                if key and not blocked:
                    self._throttle.record_failure(who)
                return HTMLResponse("""<!DOCTYPE html>
<html><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width">
<style>
  body{background:#07090f;color:#dde3ed;font-family:sans-serif;
       display:flex;align-items:center;justify-content:center;height:100vh;margin:0;text-align:center}
  h2{color:#f87171;margin-bottom:12px}p{color:#5e6a7e;font-size:14px}
</style></head>
<body><div><h2>Link Expired</h2>
<p>Press <strong style="color:#dde3ed">Remote Control</strong> in JARVIS to get a new QR code.</p>
</div></body></html>""")

            del self._pending_keys[key]
            if self._last_qr_key == key:
                self._last_qr_key = ""
            tok     = secrets.token_urlsafe(32)
            dev_tok = secrets.token_urlsafe(32)
            self._tokens[tok] = time.time()
            self._token_keys[tok] = key
            self._aes_key(key)
            self._device_sessions[dev_tok] = {"session_key": key}

            if self._connect_callback:
                self._connect_callback()
            asyncio.create_task(self.broadcast({"type": "sys", "text": "Remote connection established via QR code."}))

            return HTMLResponse(f"""<!DOCTYPE html>
<html><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width">
<style>
  body{{background:#07090f;color:#dde3ed;font-family:sans-serif;
       display:flex;align-items:center;justify-content:center;height:100vh;margin:0;text-align:center}}
  p{{color:#5e6a7e;font-size:14px}}
</style></head>
<body>
<script>
  sessionStorage.setItem('jarvis_token','{tok}');
  sessionStorage.setItem('jarvis_key','{key}');
  localStorage.setItem('jarvis_device_token','{dev_tok}');
  setTimeout(function(){{location.replace('/pwa')}},250);
</script>
<p>Connecting to JARVIS…</p>
</body></html>""")

        @app.post("/api/device-login")
        async def device_login_ep(req: Request):
            try:
                body = await req.json()
            except Exception:
                return JSONResponse({"ok": False}, status_code=400)
            dev_tok = (body.get("device_token") or "").strip()
            if not dev_tok or dev_tok not in self._device_sessions:
                return JSONResponse({"ok": False}, status_code=401)
            session_key = self._device_sessions[dev_tok]["session_key"]
            tok = secrets.token_urlsafe(32)
            self._tokens[tok] = time.time()
            self._token_keys[tok] = session_key
            self._aes_key(session_key)
            if self._connect_callback:
                self._connect_callback()
            asyncio.create_task(self.broadcast({"type": "sys", "text": "Known device reconnected automatically."}))
            return JSONResponse({"ok": True, "token": tok, "key": session_key})

        @app.post("/api/revoke-devices")
        async def revoke_devices(req: Request):
            if not _auth(req):
                return JSONResponse({"error": "Unauthorized"}, status_code=401)
            mine  = req.headers.get("authorization", "").removeprefix("Bearer ").strip()
            count = len(self._device_sessions)
            live  = len(self._tokens) - (1 if mine in self._tokens else 0)
            self._device_sessions.clear()
            keep = self._tokens.get(mine)
            self._tokens.clear()
            self._token_keys = {k: v for k, v in self._token_keys.items() if k == mine}
            if keep is not None:
                self._tokens[mine] = keep
            return JSONResponse({"ok": True, "revoked": count, "sessions_ended": live})

        @app.post("/api/command")
        async def command(req: Request):
            if not _auth(req):
                return JSONResponse({"error": "Unauthorized"}, status_code=401)
            body  = await req.json()
            token = req.headers.get("authorization", "").removeprefix("Bearer ").strip()
            enc   = body.get("enc", "")
            if enc:
                text = self._decrypt(token, enc)
                if text is None:
                    return JSONResponse({"error": "Decryption failed"}, status_code=400)
            else:
                text = (body.get("text") or "").strip()
            if text:
                await self._command_queue.put(text)
                if self._wake_callback:
                    self._wake_callback()
            return JSONResponse({"ok": True})

        @app.post("/api/wake")
        async def wake_ep(req: Request):
            if not _auth(req):
                return JSONResponse({"error": "Unauthorized"}, status_code=401)
            if self._wake_callback:
                self._wake_callback()
            return JSONResponse({"ok": True})

        # ── Phone Mic Real-Time Audio -> Gemini Live ─────────────────────────

        @app.websocket("/ws/phone-audio")
        async def phone_audio_ws(websocket: WebSocket, token: str = ""):
            tok = token.strip()
            if not _valid(tok):
                await websocket.close(code=4001)
                return
            await websocket.accept()
            if self._wake_callback:
                try:
                    self._wake_callback()
                except Exception:
                    pass
            asyncio.create_task(self.broadcast({"type": "sys", "text": "Phone microphone live."}))
            try:
                while True:
                    data = await websocket.receive_bytes()
                    try:
                        self._phone_audio_queue.put_nowait({"data": data, "mime_type": "audio/pcm"})
                    except asyncio.QueueFull:
                        pass
            except WebSocketDisconnect:
                pass
            finally:
                asyncio.create_task(self.broadcast({"type": "sys", "text": "Phone microphone stopped."}))

        # ── File Sharing ─────────────────────────────────────────────────────

        def _safe_filename(raw: str) -> str:
            name = Path(raw).name
            name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", name).strip(". ")
            return name or "upload"

        if _UPLOAD_OK:
            @app.post("/api/upload")
            async def upload_file(req: Request, file: UploadFile = FastAPIFile(...)):
                if not _auth(req):
                    return JSONResponse({"error": "Unauthorized"}, status_code=401)

                safe = _safe_filename(file.filename or "upload")
                dest = self._uploads_dir / safe
                stem, suffix = Path(safe).stem, Path(safe).suffix
                counter = 1
                while dest.exists():
                    dest = self._uploads_dir / f"{stem}_{counter}{suffix}"
                    counter += 1

                size = 0
                max_bytes = MAX_UPLOAD_MB * 1024 * 1024
                try:
                    with open(dest, "wb") as fout:
                        while True:
                            chunk = await file.read(65536)
                            if not chunk:
                                break
                            size += len(chunk)
                            if size > max_bytes:
                                fout.close()
                                dest.unlink(missing_ok=True)
                                return JSONResponse(
                                    {"error": f"File too large (max {MAX_UPLOAD_MB} MB)"},
                                    status_code=413,
                                )
                            fout.write(chunk)
                except Exception as exc:
                    try:
                        dest.unlink(missing_ok=True)
                    except Exception:
                        pass
                    return JSONResponse({"error": str(exc)}, status_code=500)

                asyncio.create_task(self.broadcast({
                    "type": "file_received",
                    "name": dest.name,
                    "size": size,
                    "saved_to": str(self._uploads_dir),
                }))
                return JSONResponse({"ok": True, "name": dest.name, "size": size})
        else:
            @app.post("/api/upload")
            async def upload_unavailable(req: Request):
                _ = req
                return JSONResponse(
                    {"error": "File uploads require: pip install python-multipart"},
                    status_code=503,
                )

        @app.get("/api/files")
        async def list_files(req: Request):
            if not _auth(req):
                return JSONResponse({"error": "Unauthorized"}, status_code=401)
            files = []
            try:
                for f in sorted(
                    (p for p in self._uploads_dir.iterdir() if p.is_file()),
                    key=lambda p: p.stat().st_mtime,
                    reverse=True,
                ):
                    files.append({"name": f.name, "size": f.stat().st_size})
            except Exception:
                pass
            return JSONResponse({"files": files})

        @app.get("/uploads/{filename}")
        async def download_file(filename: str, token: str = ""):
            tok = token.strip()
            if not _valid(tok):
                return JSONResponse({"error": "Unauthorized"}, status_code=401)
            safe = re.sub(r"[/\\]", "", filename)
            path = self._uploads_dir / safe
            if not path.exists() or not path.is_file():
                return JSONResponse({"error": "Not found"}, status_code=404)
            return FileResponse(str(path), filename=safe)

        # ── Bidirectional Authenticated WebSocket (`/ws`) ────────────────────

        @app.websocket("/ws")
        async def ws_ep(websocket: WebSocket, token: str = ""):
            tok = token.strip()
            if not _valid(tok):
                await websocket.close(code=4001)
                return
            await websocket.accept()
            self._loop = asyncio.get_running_loop()
            self._clients.add(websocket)
            registered_dev_id: str | None = None
            for entry in self._history[-50:]:
                try:
                    await websocket.send_json(entry)
                except Exception:
                    break
            # Immediately push any active HUD CONFIRM or ChoiceBanner prompt to newly connected phone
            pending_now = get_pending_hud_prompts()
            if pending_now.get("confirm"):
                try:
                    await websocket.send_json({"type": "confirm_prompt", **pending_now["confirm"]})
                except Exception:
                    pass
            if pending_now.get("choice"):
                try:
                    await websocket.send_json({"type": "choice_prompt", **pending_now["choice"]})
                except Exception:
                    pass

            try:
                while True:
                    data = await websocket.receive_json()
                    if not isinstance(data, dict):
                        continue
                    msg_type = str(data.get("type") or "").strip().lower()

                    if msg_type == "command":
                        enc = data.get("enc", "")
                        t   = self._decrypt(tok, enc) if enc else (data.get("text") or "").strip()
                        if t:
                            await self._command_queue.put(t)
                            if self._wake_callback:
                                self._wake_callback()

                    elif msg_type == "device_register":
                        dev_id = str(data.get("device_id") or f"android-{secrets.token_hex(3)}").strip()
                        registered_dev_id = dev_id
                        rec = self._android_devices.get(dev_id, {})
                        rec.update({
                            "device_id": dev_id,
                            "name": str(data.get("name") or rec.get("name") or "Android Companion"),
                            "model": str(data.get("model") or rec.get("model") or "Android"),
                            "android_version": str(data.get("android_version") or rec.get("android_version") or ""),
                            "capabilities": list(data.get("capabilities") or rec.get("capabilities") or []),
                            "privacy_mode": bool(data.get("privacy_mode", False)),
                            "battery": data.get("battery", rec.get("battery")),
                            "charging": data.get("charging", rec.get("charging")),
                            "location": data.get("location", rec.get("location")),
                            "last_seen": time.time(),
                            "connected": True,
                            "ws": websocket,
                        })
                        self._android_devices[dev_id] = rec
                        self._save_known_devices()
                        await websocket.send_json({
                            "type": "registered",
                            "ok": True,
                            "device_id": dev_id,
                            "protocol_version": 2,
                            "server_time": time.time(),
                        })
                        asyncio.create_task(self.broadcast({
                            "type": "android_connected",
                            "device_id": dev_id,
                            "name": rec["name"],
                            "model": rec["model"],
                        }))

                    elif msg_type == "heartbeat":
                        dev_id = str(data.get("device_id") or registered_dev_id or "").strip()
                        if dev_id and dev_id in self._android_devices:
                            rec = self._android_devices[dev_id]
                            for field in ("battery", "charging", "privacy_mode", "location", "network"):
                                if field in data:
                                    rec[field] = data[field]
                            rec["last_seen"] = time.time()
                            rec["connected"] = True
                            rec["ws"] = websocket
                        await websocket.send_json({
                            "type": "heartbeat_ack",
                            "device_id": dev_id,
                            "ts": time.time(),
                        })

                    elif msg_type == "android_result":
                        req_id = str(data.get("request_id") or "")
                        fut = self._pending_android_reqs.get(req_id)
                        if fut is not None and not fut.done():
                            fut.set_result({
                                "ok": bool(data.get("ok", True)),
                                "device_id": registered_dev_id or str(data.get("device_id") or ""),
                                "data": data.get("data"),
                                "error": str(data.get("error") or ""),
                            })

                    elif msg_type == "screen_frame_req":
                        img_bytes, mime, w, h = await asyncio.to_thread(self.capture_screen_frame)
                        await websocket.send_json({
                            "type": "screen_frame",
                            "ok": True,
                            "mime_type": mime,
                            "width": w,
                            "height": h,
                            "image_b64": base64.b64encode(img_bytes).decode("ascii"),
                            "ts": time.time(),
                        })

                    elif msg_type == "screen_input":
                        out = await asyncio.to_thread(self.execute_screen_input, data)
                        await websocket.send_json({
                            "type": "screen_input_ack",
                            **out,
                        })

                    elif msg_type == "confirm_pending_req":
                        await websocket.send_json({
                            "type": "confirm_pending",
                            **get_pending_hud_prompts(),
                        })

                    elif msg_type == "confirm_resolve":
                        from core import confirm as _cf
                        kind = str(data.get("kind") or "confirm").strip().lower()
                        if kind == "choice":
                            sel = data.get("selection")
                            msg = _cf.resolve_choice(sel, sync=bool(data.get("sync", False)))
                            ev_out = {"type": "confirm_resolved", "kind": "choice", "selection": sel, "message": msg, "ts": time.time()}
                        else:
                            acc = bool(data.get("accepted", False))
                            _cf.resolve(acc)
                            ev_out = {"type": "confirm_resolved", "kind": "confirm", "accepted": acc, "ts": time.time()}
                        await websocket.send_json(ev_out)
                        asyncio.create_task(self.broadcast(ev_out))

                    elif msg_type == "tool_invoke":
                        req_id = str(data.get("request_id") or secrets.token_hex(6))
                        tool_name = str(data.get("tool") or "").strip()
                        params = data.get("parameters") if isinstance(data.get("parameters"), dict) else {}
                        if not tool_name:
                            await websocket.send_json({
                                "type": "tool_result", "request_id": req_id, "ok": False, "error": "Missing tool name.",
                            })
                        elif tool_name == "assistant_prompt":
                            prompt_txt = str(params.get("text") or "").strip()
                            if prompt_txt:
                                await self._command_queue.put(prompt_txt)
                                if self._wake_callback:
                                    self._wake_callback()
                            await websocket.send_json({
                                "type": "tool_result", "request_id": req_id, "ok": True, "result": "enqueued",
                            })
                        elif self._tool_executor is None:
                            await websocket.send_json({
                                "type": "tool_result", "request_id": req_id, "ok": False,
                                "error": "tool_executor_unavailable",
                            })
                        else:
                            from core.evolution import safety_guard
                            tier = safety_guard.classify_risk(tool_name, str(params.get("action") or ""))
                            try:
                                if inspect.iscoroutinefunction(self._tool_executor):
                                    res = await self._tool_executor(tool_name, params)
                                else:
                                    res = await asyncio.to_thread(self._tool_executor, tool_name, params)
                                await websocket.send_json({
                                    "type": "tool_result",
                                    "request_id": req_id,
                                    "ok": True,
                                    "risk_tier": tier,
                                    "result": str(res),
                                })
                            except Exception as exc:
                                await websocket.send_json({
                                    "type": "tool_result",
                                    "request_id": req_id,
                                    "ok": False,
                                    "risk_tier": tier,
                                    "error": f"{type(exc).__name__}: {exc}",
                                })

                    elif msg_type == "android_event":
                        ev = {
                            "event": str(data.get("event") or "unknown"),
                            "device_id": registered_dev_id or str(data.get("device_id") or "unknown"),
                            "payload": data.get("payload") if isinstance(data.get("payload"), dict) else {},
                            "ts": time.time(),
                        }
                        self._android_events.append(ev)
                        if len(self._android_events) > 100:
                            self._android_events = self._android_events[-100:]
                        asyncio.create_task(self.broadcast({"type": "android_event", **ev}))
                        if self._event_callback:
                            try:
                                if inspect.iscoroutinefunction(self._event_callback):
                                    asyncio.create_task(self._event_callback(ev))
                                else:
                                    self._event_callback(ev)
                            except Exception:
                                pass
                        await websocket.send_json({"type": "event_ack", "event": ev["event"], "ts": ev["ts"]})

            except WebSocketDisconnect:
                pass
            finally:
                self._clients.discard(websocket)
                if registered_dev_id and registered_dev_id in self._android_devices:
                    rec = self._android_devices[registered_dev_id]
                    if rec.get("ws") is websocket:
                        rec["connected"] = False
                        rec["ws"] = None
                        self._save_known_devices()

        return app

    # ── Serve (0.0.0.0:8000 HTTPS + Optional :8001 HTTP Redirector) ──────────

    async def _serve_redirector(self) -> None:
        """Optional plain-HTTP listener on port 8001 redirecting browsers to HTTPS :8000."""
        from fastapi.responses import RedirectResponse

        probe = socket.socket()
        try:
            probe.bind(("0.0.0.0", REDIRECT_PORT))
        except OSError:
            probe.close()
            return
        probe.close()

        bounce = FastAPI(docs_url=None, redoc_url=None)

        @bounce.get("/{path:path}")
        async def _to_https(path: str, request: Request):
            query = f"?{request.url.query}" if request.url.query else ""
            return RedirectResponse(f"https://{self._ip}:{PORT}/{path}{query}", status_code=307)

        cfg = uvicorn.Config(bounce, host="0.0.0.0", port=REDIRECT_PORT, log_level="warning")
        await uvicorn.Server(cfg).serve()

    async def _serve_local_tunnel_loopback(self) -> None:
        """Loopback-only (`127.0.0.1:8766`) HTTP/WS listener backing SSH TLS-terminating tunnels."""
        probe = socket.socket()
        try:
            probe.bind(("127.0.0.1", LOCAL_TUNNEL_PORT))
        except OSError:
            probe.close()
            return
        probe.close()
        cfg = uvicorn.Config(self.app, host="127.0.0.1", port=LOCAL_TUNNEL_PORT, log_level="warning")
        await uvicorn.Server(cfg).serve()

    async def serve(self) -> None:
        self._loop = asyncio.get_running_loop()
        set_active_server(self)
        if not _DEPS_OK:
            print("[Dashboard] fastapi/uvicorn not installed — dashboard disabled.")
            return

        self._ip = detect_lan_ipv4()
        asyncio.get_running_loop().run_in_executor(None, report_network_access, PORT, REDIRECT_PORT)

        _ensure_certs(certs_dir=self._certs_dir, extra_ips=[self._ip] if self._ip != "127.0.0.1" else [])
        use_ssl = self._ssl_enabled()
        ssl_key, ssl_cert = _resolve_cert_files(self._certs_dir)

        if use_ssl:
            asyncio.create_task(self._serve_redirector())
        asyncio.create_task(self._serve_local_tunnel_loopback())
        start_tunnel_supervisor(https=use_ssl)

        cfg = uvicorn.Config(
            self.app,
            host="0.0.0.0",
            port=PORT,
            log_level="warning",
            **({"ssl_keyfile": str(ssl_key), "ssl_certfile": str(ssl_cert)} if use_ssl else {}),
        )

        _probe = socket.socket()
        try:
            _probe.bind(("0.0.0.0", PORT))
        except OSError as exc:
            _probe.close()
            self._bind_error = (
                f"port {PORT} is already in use — another copy of JARVIS may be running "
                f"({exc.__class__.__name__})"
            )
            print(f"[Dashboard] {self._bind_error}")
            return
        _probe.close()

        print(f"[Dashboard] Serving on {self.get_url()} (0.0.0.0:{PORT}, TLS={use_ssl})")
        print(f"[Dashboard] Mobile Companion PWA: {self.get_url()}/pwa")
        await uvicorn.Server(cfg).serve()
