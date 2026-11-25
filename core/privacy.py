"""core/privacy.py — Strict Local Privacy Mode.

While active, nothing this process does may leave the computer. Three layers:

1. Network guard: outbound connects, datagrams and DNS lookups from this process
   are refused unless the target is loopback. This covers every SDK (Gemini,
   OpenAI, requests, httpx, websockets) without having to know about each one.
2. Tool gate: only an allowlist of tools runs, because a tool that starts a
   browser, a CLI agent or an MCP server leaves the process and the guard.
3. Routing: the conversation and one-shot calls go to local Ollama / Whisper /
   system voice, and Ollama's own ":cloud" models are refused.

Fails closed: if the local engine cannot start, there is no cloud fallback.
Inbound connections (the LAN phone dashboard) are not affected.
"""
from __future__ import annotations

import ipaddress
import os
import re
import socket
import threading
from typing import Any, Callable

CONFIG_KEY = "privacy_mode"

LOCAL_LLM_ID = "openai_compat@ollama"
LOCAL_STT_ID = "whisper@local"
LOCAL_TTS_ID = "system_tts"
LOCAL_OLLAMA_ORIGIN = "http://127.0.0.1:11434"
DEFAULT_LOCAL_MODEL = "qwen3:8b"

# Tools that work entirely on this computer. Anything not listed is refused.
# save_memory is deliberately absent: long-term memory is injected into later
# cloud sessions, so saving during a private conversation would leak it later.
ALLOWED_TOOLS = frozenset({
    "privacy_mode", "sleep_jarvis", "shutdown_jarvis", "system_status", "system_monitor",
    "recall_memory", "undo", "screen_process", "close_camera",
    "knowledge_rag", "file_controller", "file_processor", "reminder",
    "voice_lock", "persona_control", "emotion_soul", "avatar_control",
})

_PROXY_VARS = ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY",
               "http_proxy", "https_proxy", "all_proxy")

_PHRASE_OFF = re.compile(
    r"\b(?:(?:disable|deactivate|turn off|switch off|exit|stop|end)\s+(?:the\s+)?"
    r"(?:strict\s+)?(?:local\s+)?privacy mode"
    r"|(?:strict\s+)?(?:local\s+)?privacy mode\s+(?:off|disabled?))\b", re.I)
_PHRASE_ON = re.compile(
    r"\b(?:(?:enable|activate|turn on|switch on|enter|start|engage)\s+(?:the\s+)?"
    r"(?:strict\s+)?(?:local\s+)?privacy mode"
    r"|(?:strict\s+)?(?:local\s+)?privacy mode\s+(?:on|enabled?)"
    r"|^\W*strict local privacy mode\W*$)", re.I)


class PrivacyBlocked(ConnectionError):
    """Raised when privacy mode refuses to let something leave the computer."""


_lock = threading.RLock()
_active = False
_listeners: list[Callable[[bool], None]] = []
_originals: list[tuple[Any, str, Any]] = []
_saved_env: dict[str, str | None] = {}


def is_active() -> bool:
    return _active


def add_listener(fn: Callable[[bool], None]) -> None:
    """Call fn(active) after every change."""
    if fn not in _listeners:
        _listeners.append(fn)


# ── Network guard ────────────────────────────────────────────────────────────

def is_local_host(host: Any) -> bool:
    """True only for this computer."""
    if host is None:
        return True
    if isinstance(host, bytes):
        host = host.decode("ascii", "replace")
    name = str(host).strip().strip("[]").split("%", 1)[0].lower()
    if name in ("", "localhost", "localhost.localdomain", "0.0.0.0", "::"):
        return True
    try:
        ip = ipaddress.ip_address(name)
    except ValueError:
        return name == socket.gethostname().lower()
    mapped = getattr(ip, "ipv4_mapped", None)
    return (mapped or ip).is_loopback


def _check(address: Any) -> None:
    # AF_UNIX addresses are strings and never leave the machine.
    if isinstance(address, tuple) and address and not is_local_host(address[0]):
        raise PrivacyBlocked(f"Privacy mode blocked a connection to {address[0]}")


def _patch(owner: Any, attr: str, wrapper_factory: Callable[[Any], Any]) -> None:
    original = getattr(owner, attr, None)
    if original is None:
        return
    # Remember whether the attribute was the owner's own, so that restoring
    # does not leave a copy of an inherited method behind.
    _originals.append((owner, attr, original if attr in vars(owner) else None))
    setattr(owner, attr, wrapper_factory(original))


def _install_guard() -> None:
    if _originals:
        return

    def by_address(original):
        def guarded(self, address, *a, **kw):
            _check(address)
            return original(self, address, *a, **kw)
        return guarded

    def sendto(original):
        def guarded(self, data, *a):
            _check(a[-1] if a else None)
            return original(self, data, *a)
        return guarded

    def by_host(original):
        def guarded(host, *a, **kw):
            if not is_local_host(host):
                raise PrivacyBlocked(f"Privacy mode blocked a lookup of {host}")
            return original(host, *a, **kw)
        return guarded

    for name in ("connect", "connect_ex"):
        _patch(socket.socket, name, by_address)
    _patch(socket.socket, "sendto", sendto)
    for name in ("getaddrinfo", "gethostbyname", "gethostbyname_ex"):
        _patch(socket, name, by_host)

    # Windows asyncio connects through overlapped I/O, not socket.connect.
    try:
        from asyncio.windows_events import IocpProactor
    except ImportError:
        return

    def proactor_connect(original):
        def guarded(self, conn, address):
            _check(address)
            return original(self, conn, address)
        return guarded

    def proactor_sendto(original):
        def guarded(self, conn, buf, flags=0, addr=None):
            _check(addr)
            return original(self, conn, buf, flags, addr)
        return guarded

    _patch(IocpProactor, "connect", proactor_connect)
    _patch(IocpProactor, "sendto", proactor_sendto)


def _remove_guard() -> None:
    while _originals:
        owner, attr, original = _originals.pop()
        if original is None:
            delattr(owner, attr)
        else:
            setattr(owner, attr, original)


def _isolate_proxies(on: bool) -> None:
    """A proxy on localhost would carry traffic past the guard."""
    names = _PROXY_VARS + ("NO_PROXY", "no_proxy")
    if on:
        for name in names:
            _saved_env.setdefault(name, os.environ.get(name))
        for name in _PROXY_VARS:
            os.environ.pop(name, None)
        os.environ["NO_PROXY"] = os.environ["no_proxy"] = "*"
        return
    for name, value in _saved_env.items():
        if value is None:
            os.environ.pop(name, None)
        else:
            os.environ[name] = value
    _saved_env.clear()


# ── State ────────────────────────────────────────────────────────────────────

def set_active(on: bool, *, persist: bool = True) -> bool:
    """Switch the mode. Returns True when the state changed."""
    global _active
    on = bool(on)
    with _lock:
        changed = on != _active
        if on:
            _active = True                  # flag first, so nothing slips between
            _isolate_proxies(True)
            _install_guard()
        else:
            _remove_guard()
            _isolate_proxies(False)
            _active = False
        if persist:
            try:
                from memory.config_manager import update_config
                update_config(**{CONFIG_KEY: on})
            except Exception as exc:
                print(f"[Privacy] could not save the setting: {exc}")
    if changed:
        print(f"[Privacy] Strict Local Privacy Mode {'ON' if on else 'OFF'}")
        for fn in list(_listeners):
            try:
                fn(on)
            except Exception as exc:
                print(f"[Privacy] listener failed: {exc}")
    return changed


def load() -> bool:
    """Restore the saved state at startup, before any network use."""
    try:
        from memory.config_manager import load_api_keys
        saved = bool(load_api_keys().get(CONFIG_KEY))
    except Exception:
        saved = False
    if saved:
        set_active(True, persist=False)
    return saved


def phrase_intent(text: str) -> str | None:
    """'on', 'off' or None for a spoken or typed sentence."""
    text = (text or "").strip()
    if not text or re.search(r"\b(?:don't|do not|never|what|how|why|is|does)\b", text, re.I):
        return None
    if _PHRASE_OFF.search(text):
        return "off"
    if _PHRASE_ON.search(text):
        return "on"
    return None


def request_disable() -> str:
    """Leaving privacy mode needs a click on screen: a model, a document it is
    reading or a mis-heard sentence must not be able to reopen the cloud."""
    if not _active:
        return "Privacy mode is already off."
    from core import confirm

    def _run() -> str:
        set_active(False)
        return "Privacy mode is off. Cloud services are available again."

    return confirm.request(
        "privacy_off", "TURN OFF PRIVACY MODE?",
        "Cloud services (Gemini and others) will be used again.", _run)


# ── Tool gate and model filter ───────────────────────────────────────────────

def blocked_reason(tool_name: str) -> str | None:
    if not _active or tool_name in ALLOWED_TOOLS:
        return None
    return (f"Blocked: '{tool_name}' can reach outside this computer and Strict "
            f"Local Privacy Mode is on.")


def is_cloud_model(name: str, record: dict | None = None) -> bool:
    """Ollama can serve hosted models through localhost; those are not local."""
    if record and (record.get("remote_host") or record.get("remote_model")):
        return True
    tag = str(name or "").lower()
    return tag.endswith((":cloud", "-cloud"))


def local_model(configured: str = "") -> str:
    """A model that really runs on this machine."""
    name = (configured or "").strip()
    return name if name and not is_cloud_model(name) else DEFAULT_LOCAL_MODEL


# ── Local conversation engine ────────────────────────────────────────────────

def local_llm_config(saved: dict | None = None) -> dict:
    cfg = dict(saved or {})
    cfg.update(base_url=f"{LOCAL_OLLAMA_ORIGIN}/v1", api_key="",
               model=local_model(cfg.get("model", "")))
    return cfg


def local_provider(slot: str, log: Callable | None = None):
    """Build the local STT, LLM or TTS, ignoring any hosted choice in settings."""
    from memory.config_manager import get_provider_config
    from providers import registry

    pid = {"stt": LOCAL_STT_ID, "llm": LOCAL_LLM_ID, "tts": LOCAL_TTS_ID}[slot]
    cfg = dict(get_provider_config(pid))
    if slot == "llm":
        cfg = local_llm_config(cfg)
    elif slot == "stt":
        cfg.update(backend="local", base_url="", api_key="")
    if log:
        cfg["log"] = log
    return registry.build(pid, cfg)
