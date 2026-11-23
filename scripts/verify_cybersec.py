"""scripts/verify_cybersec.py — End-to-end verification for J.A.R.V.I.S. CyberSec module.

Verifies:
  1. plugins/cybersec_defender.py passes validate_plugin.py --json (0 errors, 0 warnings).
  2. audit_ports (live local listening sockets + remote refusal).
  3. audit_posture (live read-only Windows Firewall, Defender, and UAC check).
  4. audit_code (AST/regex CWEs, secret redaction, protected file skip, symlink escape blocking).
  5. audit_headers (rejects remote/non-loopback hosts, audits ephemeral localhost server).
  6. cve_lookup (live OSV.dev advisory lookup + offline fallback wrapped in <<<UNTRUSTED>>>).
  7. agents/cybersec_analyst.md (research mode, web tools + file_processor only, no recall_memory).
  8. providers/local_router.py routing for cybersec coding vs threat-modeling + M6 regression check.
"""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from unittest.mock import patch

BASE_DIR = Path(__file__).resolve().parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def _banner(name: str) -> None:
    print(f"[CHECK] {name}...")


def _check_validator() -> None:
    _banner("1. validate_plugin.py --json")
    proc = subprocess.run(
        [
            sys.executable,
            str(BASE_DIR / "scripts" / "validate_plugin.py"),
            "--json",
            str(BASE_DIR / "plugins" / "cybersec_defender.py"),
        ],
        capture_output=True,
        text=True,
        stdin=subprocess.DEVNULL,
        timeout=30,
    )
    assert proc.returncode == 0, f"validate_plugin failed rc={proc.returncode}: {proc.stdout}\n{proc.stderr}"
    data = json.loads(proc.stdout)
    results = data.get("results") or [data]
    rec = results[0]
    assert rec.get("passed") is True, f"Plugin did not pass validator: {rec}"
    assert rec.get("errors") == [], f"Unexpected validator errors: {rec.get('errors')}"
    assert rec.get("warnings") == [], f"Unexpected validator warnings: {rec.get('warnings')}"
    print(f"  OK: passed=True, errors=0, warnings=0 (tool={rec.get('tool_name')})")


def _check_audit_ports() -> None:
    from plugins import cybersec_defender as cd

    _banner("2. audit_ports + remote target refusal")
    out = cd.run({"mode": "audit_ports"}, player=None)
    assert "Local" in out and ("listeners" in out or "entries" in out), f"Unexpected audit_ports: {out}"
    blocked = cd.run({"mode": "audit_ports", "target": "8.8.8.8"}, player=None)
    assert "AUTHORIZATION BLOCKED" in blocked, f"Expected remote refusal, got: {blocked}"
    print(f"  OK: {out.splitlines()[0]}")


def _check_audit_posture() -> None:
    from plugins import cybersec_defender as cd

    _banner("3. audit_posture (read-only)")
    out = cd.run({"mode": "audit_posture"}, player=None)
    assert "Host" in out and "Posture" in out, f"Unexpected audit_posture: {out}"
    print(f"  OK: {out.splitlines()[0]}")


def _check_audit_code() -> None:
    from plugins import cybersec_defender as cd

    _banner("4. audit_code (SAST + redaction + protected skip + symlink blocking)")
    with tempfile.TemporaryDirectory(prefix="jarvis_sec_test_") as tmp_outer, tempfile.TemporaryDirectory(
        prefix="jarvis_sec_outside_"
    ) as tmp_outside:
        root = Path(tmp_outer)
        outside = Path(tmp_outside)

        raw_google = "AIza" + "FakeTestKey1234567890ABCDEFGHIJKLMN"
        raw_sk = "sk-proj-" + "fakeSecretToken9876543210ZYXWVUT"
        raw_ghp = "ghp_" + "AbCdEfGhIjKlMnOpQrStUvWxYz123456"
        canary_outside = "sk-proj-" + "OUTSIDECANARYSECRET99887766554433"

        vuln_py = root / "sample_vuln.py"
        vuln_py.write_text(
            "\n".join(
                [
                    "import subprocess, yaml, sqlite3",
                    f'API_KEY = "{raw_google}"',
                    f'TOKEN = "{raw_sk}"',
                    f'GH_TOKEN = "{raw_ghp}"',
                    'DB_PASS = "supersecretpassword123"',
                    "def bad(cmd, user_id, blob):",
                    "    subprocess.run(cmd, shell=True)",
                    "    eval(cmd)",
                    "    cur = sqlite3.connect(':memory:').cursor()",
                    '    cur.execute(f"SELECT * FROM users WHERE id = {user_id}")',
                    "    return yaml.load(blob)",
                ]
            ),
            encoding="utf-8",
        )

        # Protected files must not be read or leak
        (root / ".env").write_text('ENV_SECRET="sk-proj-envsecretvalue1234567890abcd"\n', encoding="utf-8")
        (root / "api_keys.dummy.json").write_text('{"gemini": "AIzaFakeProtectedDummy1234567890ABCDE"}\n', encoding="utf-8")

        outside_secret = outside / "outside.py"
        outside_secret.write_text(f'OUTSIDE = "{canary_outside}"\n', encoding="utf-8")

        symlink_path = root / "escape.py"
        symlink_tested_via_os = False
        try:
            os.symlink(outside_secret, symlink_path)
            symlink_tested_via_os = True
        except OSError:
            symlink_tested_via_os = False

        out = cd.run({"mode": "audit_code", "target": str(root)}, player=None)
        for raw_secret in (raw_google, raw_sk, raw_ghp, "supersecretpassword123", canary_outside):
            assert raw_secret not in out, f"Raw secret leaked in audit_code output! ({raw_secret})"
        assert "envsecretvalue" not in out and "FakeProtectedDummy" not in out, "Protected file contents leaked!"
        assert "protected_skipped=2" in out, f"Expected 2 protected files skipped, got:\n{out}"
        assert "CWE-78" in out and "CWE-95" in out and "CWE-89" in out and "CWE-502" in out and "CWE-798" in out, out
        assert "****" in out or "[REDACTED]" in out, f"Expected redacted secret marker in output:\n{out}"

        # Always verify path-escape blocking directly & under patched symlink check
        assert cd._is_path_escaped(outside_secret, root) is True
        if symlink_tested_via_os:
            assert "blocked_symlinks=1" in out, f"Expected OS symlink to be blocked, got:\n{out}"
            print("  OK: secret redaction, CWE AST/regex, protected skip, and OS symlink escape verified")
        else:
            # Windows without Developer Mode raises WinError 1314 on os.symlink;
            # simulate symlink traversal via Path.is_symlink returning True for a plain file.
            shim_link = root / "simulated_symlink.py"
            shim_link.write_text(f'LEAK = "{canary_outside}"\n', encoding="utf-8")
            orig_is_symlink = Path.is_symlink

            def _fake_is_symlink(self: Path) -> bool:
                if self.name == "simulated_symlink.py":
                    return True
                return orig_is_symlink(self)

            with patch.object(Path, "is_symlink", _fake_is_symlink):
                assert cd._is_path_escaped(shim_link, root) is True
                sim_out = cd.run({"mode": "audit_code", "target": str(root)}, player=None)
            assert canary_outside not in sim_out
            assert "blocked_symlinks=1" in sim_out, f"Expected simulated symlink blocked:\n{sim_out}"
            print("  OK: secret redaction, CWEs, protected skip, and symlink escape (Win fallback) verified")


class _LoopbackHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802
        self.send_response(200)
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Set-Cookie", "session=demo123; Path=/")
        self.end_headers()
        self.wfile.write(b"ok")

    def log_message(self, format: str, *args: object) -> None:  # noqa: A002
        return


def _check_audit_headers() -> None:
    from plugins import cybersec_defender as cd

    _banner("5. audit_headers (loopback-only enforcement + live HTTPServer)")
    for bad_target in ("https://example.com", "http://8.8.8.8", "192.168.1.50"):
        res = cd.run({"mode": "audit_headers", "target": bad_target}, player=None)
        assert "AUTHORIZATION BLOCKED" in res, f"Expected loopback block for {bad_target}, got: {res}"

    srv = HTTPServer(("127.0.0.1", 0), _LoopbackHandler)
    port = srv.server_address[1]
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    try:
        out = cd.run({"mode": "audit_headers", "target": f"http://127.0.0.1:{port}/"}, player=None)
        assert "HTTP 200" in out, out
        assert "X-Content-Type-Options" in out, out
        assert "Missing Content-Security-Policy" in out and "Missing X-Frame-Options" in out, out
        assert "wildcard '*'" in out, out
        assert "missing flag(s): httponly, samesite" in out, out
        print(f"  OK: remote hosts blocked + ephemeral 127.0.0.1:{port} headers audited")
    finally:
        srv.shutdown()
        srv.server_close()


def _check_cve_lookup() -> None:
    from plugins import cybersec_defender as cd

    _banner("6. cve_lookup (live OSV.dev + offline fallback wrapped in UNTRUSTED)")
    live = cd.run({"mode": "cve_lookup", "target": "GHSA-j8r2-6x86-q33q"}, player=None)
    assert live.startswith("<<<UNTRUSTED source=cybersec_cve>>>"), live
    assert live.rstrip().endswith("<<<END UNTRUSTED>>>"), live
    assert "Proxy-Authorization" in live or "offline/lookup fallback" in live, live

    # Test deterministic offline fallback against an unused closed loopback port
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        unused_port = s.getsockname()[1]

    orig_base = cd.OSV_API_BASE
    try:
        cd.OSV_API_BASE = f"http://127.0.0.1:{unused_port}/v1"
        offline = cd.run({"mode": "cve_lookup", "target": "CVE-2024-0001"}, player=None)
    finally:
        cd.OSV_API_BASE = orig_base

    assert offline.startswith("<<<UNTRUSTED source=cybersec_cve>>>"), offline
    assert offline.rstrip().endswith("<<<END UNTRUSTED>>>"), offline
    assert "offline/lookup fallback" in offline, offline
    print("  OK: live OSV lookup & offline fallback wrapped cleanly")


def _check_agent_isolation() -> None:
    from core.tasks.agents import parse
    from core.tasks.tools import TaskTools

    _banner("7. agents/cybersec_analyst.md + TaskTools isolation")
    agent_file = BASE_DIR / "agents" / "cybersec_analyst.md"
    assert agent_file.is_file(), f"Missing {agent_file}"
    profile = parse(agent_file.read_text(encoding="utf-8"), str(agent_file))
    assert profile.name == "cybersec_analyst", profile
    assert profile.mode == "research", profile
    tools = TaskTools(BASE_DIR, profile.tools, log=lambda *_a: None)
    assert tools.has_web is True, "Expected web tools enabled"
    assert "recall_memory" not in tools.names, f"recall_memory leaked into tools: {tools.names}"
    assert set(tools.names) == {"search_web", "fetch_page", "file_processor"}, f"Unexpected tools: {tools.names}"
    print(f"  OK: mode={profile.mode}, tools={tools.names}, has_web={tools.has_web}")


def _check_local_router() -> None:
    from providers import local_router as lr

    _banner("8. providers/local_router.py cybersec specialist routing")
    installed = ["qwen3:8b", "qwen2.5-coder:14b", "qwen2.5:14b", "gemma4:12b"]

    code_dec, _ = lr.route_prompt(
        "Run audit_code static analysis and patch CWE-89 SQL injection in app.py",
        tier="fast",
        available_models=installed,
    )
    assert code_dec.category == lr.CATEGORY_CODING, code_dec
    assert code_dec.selected_model == "qwen2.5-coder:14b", code_dec

    tm_dec, _ = lr.route_prompt(
        "Perform a STRIDE threat model and OWASP ASVS review of our local auth boundary.",
        tier="fast",
        available_models=installed,
    )
    assert tm_dec.category == lr.CATEGORY_REASONING, tm_dec
    assert tm_dec.selected_model in {"qwen2.5:14b", "gemma4:12b"}, tm_dec

    m6_dec, _ = lr.route_prompt(
        "Conduct a root-cause analysis and threat model for an MCP stdio bridge.",
        tier="fast",
        available_models=installed,
    )
    assert m6_dec.category == lr.CATEGORY_REASONING, m6_dec
    print(
        f"  OK: coding->{code_dec.selected_model}, threat_model->{tm_dec.selected_model}, m6_regression->{m6_dec.category}"
    )


def main() -> int:
    _check_validator()
    _check_audit_ports()
    _check_audit_posture()
    _check_audit_code()
    _check_audit_headers()
    _check_cve_lookup()
    _check_agent_isolation()
    _check_local_router()
    print("\nALL CYBERSEC CHECKS PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
