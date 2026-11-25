"""Tests for Strict Local Privacy Mode, Cloudflare Quick Tunnel, Setup Wizard, and IP Audit."""
from __future__ import annotations

import io
import json
import os
import socket
import time
from pathlib import Path
from unittest.mock import patch

import pytest
from starlette.testclient import TestClient

REPO_ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture(autouse=True)
def _reset_privacy_after_test(monkeypatch: pytest.MonkeyPatch):
    from core import privacy
    from memory import config_manager as cm

    # Never mutate the user's real config/api_keys.json if persist=True is triggered.
    saved: dict = {}
    monkeypatch.setattr(cm, "update_config", lambda **kw: saved.update(kw))
    try:
        privacy.set_active(False, persist=False)
        yield saved
    finally:
        privacy.set_active(False, persist=False)


# ── Strict Local Privacy Mode ────────────────────────────────────────────────

def test_privacy_socket_guard_blocks_external_and_allows_loopback(monkeypatch: pytest.MonkeyPatch):
    from core import privacy

    monkeypatch.setenv("HTTP_PROXY", "http://127.0.0.1:8888")
    monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:8888")
    privacy.set_active(True, persist=False)

    try:
        assert "HTTP_PROXY" not in os.environ
        assert "HTTPS_PROXY" not in os.environ
        assert os.environ.get("NO_PROXY") == "*"

        with pytest.raises(privacy.PrivacyBlocked):
            socket.getaddrinfo("example.com", 443)
        with pytest.raises(privacy.PrivacyBlocked):
            socket.create_connection(("1.1.1.1", 443), timeout=1)

        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            with pytest.raises(privacy.PrivacyBlocked):
                s.sendto(b"ping", ("8.8.8.8", 53))
        finally:
            s.close()

        assert privacy.is_local_host("127.0.0.1") is True
        assert privacy.is_local_host("localhost") is True
        assert privacy.is_local_host("::1") is True
        assert privacy.is_local_host("1.1.1.1") is False
    finally:
        privacy.set_active(False, persist=False)

    assert os.environ.get("HTTP_PROXY") == "http://127.0.0.1:8888"
    assert os.environ.get("HTTPS_PROXY") == "http://127.0.0.1:8888"


def test_privacy_routing_filters_cloud_models_and_blocks_gemini(monkeypatch: pytest.MonkeyPatch):
    from core import gemini, llm_client, privacy
    from core.evolution import agents_247
    from core.tasks import brain
    from providers import local_router

    assert privacy.is_cloud_model("glm-5.3:cloud") is True
    assert privacy.is_cloud_model("llama3", {"remote_host": "https://ollama.com"}) is True
    assert privacy.is_cloud_model("qwen3:8b") is False
    assert privacy.local_model("glm-5.3:cloud") == privacy.DEFAULT_LOCAL_MODEL
    assert privacy.local_model("qwen2.5-coder:14b") == "qwen2.5-coder:14b"

    privacy.set_active(True, persist=False)

    assert agents_247.is_paused() is True

    url, model = llm_client.get_llm_settings()
    assert url == privacy.LOCAL_OLLAMA_ORIGIN
    assert not privacy.is_cloud_model(model)

    with pytest.raises(privacy.PrivacyBlocked):
        gemini.client()

    # SEARCH and image attachments fail closed without hitting cloud SDK
    assert gemini.call("latest news", tier=gemini.SEARCH) is None
    assert gemini.call([{"inline_data": {"mime_type": "image/png", "data": "AA=="}}, "what is this"]) is None

    # Text calls route strictly to local fallback
    called: list[str] = []
    monkeypatch.setattr(gemini, "_local_ollama_fallback", lambda contents, tier, timeout_ms: (called.append(tier), "local-ok")[1])
    assert gemini.call("hello", tier=gemini.FAST) == "local-ok"
    assert called == [gemini.FAST]

    # Background task brain routes to _ask_local without touching cloud ladder
    monkeypatch.setattr(brain, "_ask_local", lambda system, turns, decls: {"text": "local-brain", "calls": []})
    assert brain.ask("sys", [{"role": "user", "parts": ["hi"]}], None)["text"] == "local-brain"

    # local_router strips :cloud models when privacy mode is on
    seen_models: list[str] = []
    monkeypatch.setattr(
        local_router,
        "route_prompt",
        lambda *a, **kw: (
            local_router.RouteDecision(
                category="fast",
                preferred_models=("glm-5.3:cloud", "qwen3:8b"),
                reason="test",
                selected_model="glm-5.3:cloud",
            ),
            ["glm-5.3:cloud", "qwen3:8b"],
        ),
    )
    monkeypatch.setattr(local_router, "ensure_vram_ready", lambda m, o, timeout=5.0: {"ok": True})

    def _fake_urlopen(req, timeout=60.0):
        body = json.loads(req.data.decode("utf-8"))
        seen_models.append(body["model"])
        raise RuntimeError("stop")

    monkeypatch.setattr(local_router, "urlopen", _fake_urlopen)
    local_router.complete_local("hi")
    assert seen_models == ["qwen3:8b"]


def test_privacy_tool_gate_phrase_intent_and_confirmed_disable(monkeypatch: pytest.MonkeyPatch):
    from core import confirm, privacy
    from core.action_loader import discover_actions
    from core.memory import memory_extractor as mx
    from core.plugin_loader import discover_plugins

    assert privacy.phrase_intent("turn on strict local privacy mode") == "on"
    assert privacy.phrase_intent("please disable privacy mode") == "off"
    assert privacy.phrase_intent("what is privacy mode") is None
    assert privacy.phrase_intent("don't turn on privacy mode") is None

    privacy.set_active(True, persist=False)

    # Tool allowlist enforcement in ActionRegistry & PluginRegistry
    act_reg = discover_actions(REPO_ROOT / "actions", logger=lambda _m: None)
    assert "Strict Local Privacy Mode" in act_reg.run("web_search", {"query": "test"})

    plug_reg = discover_plugins(REPO_ROOT / "plugins", set(), logger=lambda _m: None)
    assert "Strict Local Privacy Mode" in plug_reg.run("email_automator", {"action": "status"})
    assert "Strict Local Privacy Mode" in plug_reg.run("mcp_bridge", {"mode": "stats"})
    assert "Strict Local Privacy Mode" not in plug_reg.run("emotion_soul", {"action": "status"})

    # memory_extractor never falls back to Gemini in privacy mode (even if local LLM is unavailable)
    from core import gemini
    from providers import local_router
    monkeypatch.setattr(local_router, "complete_local", lambda *a, **kw: None)
    monkeypatch.setattr(gemini, "as_json", lambda *a, **kw: (_ for _ in ()).throw(AssertionError("cloud called")))
    data, method = mx._llm_extract("User: secret note", allow_cloud=True)
    assert data is None and method == ""

    # Disabling privacy mode requires on-screen HUD confirmation
    orig_cbs = (confirm._show_cb, confirm._hide_cb, confirm._log_cb)
    confirm.resolve(False)
    try:
        confirm.bind(lambda *a: None, lambda: None, lambda *a: None)
        res = privacy.request_disable()
        assert "[CONFIRMATION_PENDING]" in res
        assert privacy.is_active() is True
        confirm.resolve(True)
        deadline = time.time() + 2.0
        while privacy.is_active() and time.time() < deadline:
            time.sleep(0.02)
        assert privacy.is_active() is False
    finally:
        confirm.resolve(False)
        confirm.bind(*orig_cbs)


# ── Cloudflare Quick Tunnel & /api/android/tunnel ────────────────────────────

class _FakeProc:
    def __init__(self, lines: list[bytes], returncode: int | None = None) -> None:
        self.stdout = io.BytesIO(b"".join(lines))
        self._rc = returncode
        self.terminated = False

    def poll(self):
        return None if not self.terminated and self._rc is None else (0 if self.terminated else self._rc)

    def terminate(self):
        self.terminated = True

    def wait(self, timeout: float = 2.0):
        return 0

    def kill(self):
        self.terminated = True


def test_tunnel_blocked_in_privacy_mode_and_parses_trycloudflare(monkeypatch: pytest.MonkeyPatch):
    from core import privacy
    from dashboard import server as srv_mod

    srv_mod.stop_quick_tunnel()

    # 1. Refused when Strict Local Privacy Mode is active
    privacy.set_active(True, persist=False)
    st_priv = srv_mod.start_quick_tunnel(wait_s=1.0)
    assert st_priv["running"] is False
    assert "Privacy Mode" in st_priv["error"]

    # 2. Succeeds with cloudflared mock output when privacy mode is off
    privacy.set_active(False, persist=False)
    monkeypatch.setattr(srv_mod, "_find_cloudflared", lambda: "/usr/bin/cloudflared")
    monkeypatch.setattr(srv_mod, "_find_ssh", lambda: None)

    fake = _FakeProc([
        b"2026-11-25 INF Requesting new quick Tunnel...\n",
        b"2026-11-25 INF |  https://jarvis-unit-test-1234.trycloudflare.com  |\n",
    ])
    monkeypatch.setattr(srv_mod.subprocess, "Popen", lambda *a, **kw: fake)

    try:
        st = srv_mod.start_quick_tunnel(local_port=8000, https=True, wait_s=3.0)
        assert st["running"] is True
        assert st["provider"] == "cloudflared"
        assert st["url"] == "https://jarvis-unit-test-1234.trycloudflare.com"
        assert st["pwa_url"] == "https://jarvis-unit-test-1234.trycloudflare.com/pwa"
    finally:
        st_stopped = srv_mod.stop_quick_tunnel()
        assert st_stopped["running"] is False


def test_tunnel_api_endpoints_and_fresh_5min_pin(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    from dashboard import server as srv_mod

    monkeypatch.setattr(srv_mod, "ANDROID_DEVICES_FILE", tmp_path / "devices.json")
    monkeypatch.setattr(srv_mod, "CERTS_DIR", tmp_path / "certs")

    srv = srv_mod.DashboardServer(certs_dir=tmp_path / "certs")
    assert srv_mod.PAIRING_PIN_TTL_SECONDS == 300

    # Mock active tunnel for API checks
    monkeypatch.setattr(
        srv_mod,
        "start_quick_tunnel",
        lambda **kw: {
            "installed": True,
            "binary": "cloudflared",
            "provider": "cloudflared",
            "running": True,
            "url": "https://mock-tunnel-demo.trycloudflare.com",
            "pwa_url": "https://mock-tunnel-demo.trycloudflare.com/pwa",
            "error": "",
        },
    )
    monkeypatch.setattr(
        srv_mod,
        "get_tunnel_status",
        lambda: {
            "installed": True,
            "binary": "cloudflared",
            "provider": "cloudflared",
            "running": True,
            "url": "https://mock-tunnel-demo.trycloudflare.com",
            "pwa_url": "https://mock-tunnel-demo.trycloudflare.com/pwa",
            "error": "",
        },
    )

    with TestClient(srv.app) as client:
        # Local trusted caller can start tunnel (even with empty body) and get pairing info
        r = client.post("/api/android/tunnel", json={"action": "start", "include_pairing": True})
        assert r.status_code == 200
        body = r.json()
        assert body["ok"] is True
        assert body["cloudflare_tunnel"]["pwa_url"] == "https://mock-tunnel-demo.trycloudflare.com/pwa"
        assert body["pairing"]["pwa_url"] == "https://mock-tunnel-demo.trycloudflare.com/pwa"
        assert len(body["pairing"]["pin"]) == 8

        # Proxied external request must be rejected with 401 on tunnel control
        r_ext = client.post(
            "/api/android/tunnel",
            headers={"X-Forwarded-For": "203.0.113.5"},
            json={"action": "start"},
        )
        assert r_ext.status_code == 401

        # Fresh 5-minute PIN on /api/android/qr?mode=tunnel&fresh=true
        qr1 = client.get("/api/android/qr?mode=tunnel&fresh=true").json()
        qr2 = client.get("/api/android/qr?mode=tunnel&fresh=true").json()
        assert qr1["pwa_url"] == "https://mock-tunnel-demo.trycloudflare.com/pwa"
        assert qr1["pin"] and qr2["pin"] and qr1["pin"] != qr2["pin"]
        ttl = srv._pending_keys[qr2["pin"]] - time.time()
        assert 280.0 <= ttl <= 305.0

        # Proxied QR caller must never receive a PIN
        qr_ext = client.get(
            "/api/android/qr?mode=tunnel&fresh=true",
            headers={"Host": "mock-tunnel-demo.trycloudflare.com"},
        ).json()
        assert qr_ext["pin"] == ""


def test_phone_link_ascii_qr_and_cli(monkeypatch: pytest.MonkeyPatch, capsys):
    from scripts import phone_link

    qr = phone_link.ascii_qr("https://mock-tunnel-demo.trycloudflare.com/pwa?pin=ABCD1234")
    assert len(qr.splitlines()) >= 10

    monkeypatch.setattr(
        phone_link,
        "local_api",
        lambda path, body=None, timeout=90.0: (
            {"cloudflare_tunnel": {"running": True, "provider": "cloudflared", "pwa_url": "https://mock-tunnel-demo.trycloudflare.com/pwa"}}
            if path == "/api/android/tunnel"
            else {
                "pwa_url": "https://mock-tunnel-demo.trycloudflare.com/pwa",
                "pin": "ABCD1234",
                "pairing_url": "https://mock-tunnel-demo.trycloudflare.com/pwa?pin=ABCD1234",
            }
        ),
    )
    monkeypatch.setattr(phone_link, "probe_public", lambda url: 200)
    assert phone_link.main([]) == 0
    out = capsys.readouterr().out
    assert "HTTP 200" in out and "ABCD1234" in out


# ── Setup Integrations Wizard & IP Audit Report ──────────────────────────────

def test_setup_integrations_env_and_mcp_generation(tmp_path: Path):
    from scripts import setup_integrations as si

    env_file = tmp_path / ".env"
    env_file.write_text("# Keep comment\nEXISTING_VAR=abc\n", encoding="utf-8")
    mcp_file = tmp_path / "mcp_servers.json"

    answers = iter([
        "y", "sp_id_123", "sp_secret_456", "",  # Spotify (uses default redirect)
        "y", "me@example.com", "app_pass_789", "", "", "", "",  # Email
        "n",  # Image gen skipped
        "y", "github, local_filesystem",  # MCP enable
    ])
    prompts: list[str] = []

    def fake_input(p: str) -> str:
        prompts.append(p)
        return next(answers)

    def fake_secret(p: str) -> str:
        prompts.append(p)
        return next(answers)

    st = si.run_interactive_wizard(
        env_path=env_file,
        example_path=REPO_ROOT / "config" / "mcp_servers.example.json",
        mcp_path=mcp_file,
        input_fn=fake_input,
        secret_fn=fake_secret,
    )
    raw_env = env_file.read_text(encoding="utf-8")
    assert "# Keep comment" in raw_env
    assert "EXISTING_VAR=abc" in raw_env
    assert "SPOTIPY_CLIENT_ID=sp_id_123" in raw_env
    assert "SPOTIPY_CLIENT_SECRET=sp_secret_456" in raw_env
    assert "EMAIL_ADDRESS=me@example.com" in raw_env
    assert st["integrations"]["spotify"]["SPOTIPY_CLIENT_SECRET"] is True
    assert st["integrations"]["email"]["EMAIL_PASSWORD"] is True
    assert st["mcp_servers_enabled"] == ["github", "local_filesystem"]

    mcp_cfg = json.loads(mcp_file.read_text(encoding="utf-8"))
    assert mcp_cfg["servers"]["github"]["enabled"] is True
    assert mcp_cfg["servers"]["notion"]["enabled"] is False
    assert (REPO_ROOT / "IP_AND_PRIVACY_AUDIT.md").is_file()


def test_start_quick_tunnel_idempotent_no_deadlock(monkeypatch: pytest.MonkeyPatch):
    from dashboard import server as ds

    class _AliveProc:
        def poll(self):
            return None

    orig_proc = ds._TUNNEL_PROC
    orig_url = ds._TUNNEL_URL
    orig_prov = ds._TUNNEL_PROVIDER
    try:
        monkeypatch.setattr(ds, "_TUNNEL_PROC", _AliveProc())
        monkeypatch.setattr(ds, "_TUNNEL_URL", "https://already-up.trycloudflare.com")
        monkeypatch.setattr(ds, "_TUNNEL_PROVIDER", "cloudflared")
        res = ds.start_quick_tunnel(wait_s=0.5)
        assert res["running"] is True
        assert res["url"] == "https://already-up.trycloudflare.com"
        assert res["pwa_url"] == "https://already-up.trycloudflare.com/pwa"
    finally:
        monkeypatch.setattr(ds, "_TUNNEL_PROC", orig_proc)
        monkeypatch.setattr(ds, "_TUNNEL_URL", orig_url)
        monkeypatch.setattr(ds, "_TUNNEL_PROVIDER", orig_prov)
