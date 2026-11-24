"""Tests for File Vault (AES-256-GCM), Defender Scanner, Unified Confirm Gates, and 3 Personas."""
from __future__ import annotations

import subprocess
import zipfile
from pathlib import Path
from unittest.mock import patch

from actions import computer_settings, send_message
from core import confirm, persona
from memory import config_manager
from plugins import cybersec_defender, file_vault, persona_control


def test_file_vault_aes256_roundtrip_and_zipslip(tmp_path: Path):
    secret_file = tmp_path / "contract.txt"
    secret_file.write_text("TOP SECRET TERMS", encoding="utf-8")
    idx = tmp_path / "vault_index.json"

    enc = file_vault.encrypt_file(secret_file, "S3cure!Pass", index_path=idx)
    assert enc["ok"] is True
    vault_path = Path(enc["vault_path"])
    assert vault_path.is_file()
    assert b"TOP SECRET" not in vault_path.read_bytes()

    # Wrong password fails authentication cleanly
    bad_dec = file_vault.decrypt_file(vault_path, "WrongPass", output_path=tmp_path / "bad.txt")
    assert bad_dec["ok"] is False
    assert not (tmp_path / "bad.txt").exists()

    # Tampered ciphertext rejected
    tampered = bytearray(vault_path.read_bytes())
    tampered[-2] ^= 0xFF
    tampered_path = tmp_path / "tampered.jvault"
    tampered_path.write_bytes(bytes(tampered))
    assert file_vault.decrypt_file(tampered_path, "S3cure!Pass", output_path=tmp_path / "bad2.txt")["ok"] is False

    # Valid decrypt succeeds
    dec = file_vault.decrypt_file(vault_path, "S3cure!Pass", output_path=tmp_path / "restored.txt")
    assert dec["ok"] is True
    assert (tmp_path / "restored.txt").read_text(encoding="utf-8") == "TOP SECRET TERMS"

    # Folder lock / unlock
    folder = tmp_path / "docs"
    folder.mkdir()
    (folder / "a.txt").write_text("alpha", encoding="utf-8")
    locked = file_vault.lock_folder(folder, "FolderKey#9", index_path=idx)
    assert locked["ok"] is True
    unlocked = file_vault.unlock_folder(Path(locked["vault_path"]), "FolderKey#9", output_dir=tmp_path / "restored_docs")
    assert unlocked["ok"] is True and unlocked["files_extracted"] == 1
    assert (tmp_path / "restored_docs" / "a.txt").read_text(encoding="utf-8") == "alpha"

    # ZipSlip traversal attack blocked
    malicious_zip = tmp_path / "evil.zip"
    with zipfile.ZipFile(malicious_zip, "w") as zf:
        zf.writestr("../escape.txt", "pwned")
    try:
        file_vault.safe_extract_zip_bytes(malicious_zip.read_bytes(), tmp_path / "extract_dst")
        raise AssertionError("Expected ZipSlip ValueError")
    except ValueError as exc:
        assert "zipslip" in str(exc).lower()

    # LOCKED confirmation gate on file_vault.run(action='decrypt')
    orig_cbs = (confirm._show_cb, confirm._hide_cb, confirm._log_cb)
    confirm.bind(None, None, None)
    try:
        gated = file_vault.run({"action": "decrypt", "path": str(vault_path), "password": "S3cure!Pass"}, player=None)
        assert "cannot confirm" in gated.lower()
    finally:
        confirm.bind(*orig_cbs)


def test_send_message_and_power_actions_enforce_confirm_gate():
    orig_cbs = (confirm._show_cb, confirm._hide_cb, confirm._log_cb)
    shown: list[tuple[str, str]] = []
    confirm.bind(None, None, None)
    try:
        wa_refused = send_message.send_message(
            {"receiver": "Alice", "message_text": "Hello", "platform": "WhatsApp"}
        )
        assert "cannot confirm" in wa_refused.lower()

        for power_act in ("hibernate", "sign_out", "schedule_shutdown", "schedule_restart"):
            res = computer_settings.computer_settings({"action": power_act, "value": "15"})
            assert "cannot confirm" in res.lower()

        # When UI is bound, confirmation is parked with [CONFIRM] and timer details
        confirm.bind(lambda t, d: shown.append((t, d)), lambda: None, lambda _m: None)
        sched = computer_settings.computer_settings({"action": "schedule_shutdown", "value": "20"})
        assert "[CONFIRMATION_PENDING]" in sched
        assert any("20 minute" in d for _, d in shown)
        confirm.resolve(False)
    finally:
        confirm.bind(*orig_cbs)


def test_defender_scanner_and_confirm_gate(tmp_path: Path):
    target = tmp_path / "sample.txt"
    target.write_text("clean", encoding="utf-8")

    fake_mp = tmp_path / "MpCmdRun.exe"
    fake_mp.write_bytes(b"")

    with patch.object(cybersec_defender, "_find_mpcmdrun", return_value=fake_mp), patch.object(
        subprocess,
        "run",
        return_value=subprocess.CompletedProcess(["MpCmdRun.exe"], 0, "Scan finished. No threats.", ""),
    ):
        clean_res = cybersec_defender.run({"mode": "defender_scan_file", "target": str(target)}, player=None)
        assert "CLEAN" in clean_res

    with patch.object(cybersec_defender, "_find_mpcmdrun", return_value=fake_mp), patch.object(
        subprocess,
        "run",
        return_value=subprocess.CompletedProcess(["MpCmdRun.exe"], 2, "Threat found!", ""),
    ):
        threat_res = cybersec_defender.run({"mode": "defender_scan_file", "target": str(target)}, player=None)
        assert "ALERT" in threat_res

    orig_cbs = (confirm._show_cb, confirm._hide_cb, confirm._log_cb)
    confirm.bind(None, None, None)
    try:
        with patch.object(cybersec_defender, "_find_mpcmdrun", return_value=fake_mp):
            quick_gated = cybersec_defender.run({"mode": "defender_quick_scan"}, player=None)
            assert "cannot confirm" in quick_gated.lower()
    finally:
        confirm.bind(*orig_cbs)


def test_persona_switcher_with_isolated_config(tmp_path: Path, monkeypatch):
    cfg_dir = tmp_path / "config"
    cfg_dir.mkdir()
    cfg_file = cfg_dir / "api_keys.json"
    cfg_file.write_text('{"gemini_api_key": "dummy"}', encoding="utf-8")

    monkeypatch.setattr(config_manager, "CONFIG_DIR", cfg_dir)
    monkeypatch.setattr(config_manager, "CONFIG_FILE", cfg_file)

    assert config_manager.get_persona_mode() == "precision"
    out_emp = persona_control.run({"action": "set", "persona": "empathetic"}, player=None)
    assert "empathetic" in out_emp.lower()
    assert config_manager.get_persona_mode() == "empathetic"
    assert "EMPATHETIC" in persona.format_persona_prompt(config_manager.get_persona_mode())

    out_exp = persona_control.run({"action": "set", "persona": "expressive"}, player=None)
    assert "expressive" in out_exp.lower()
    assert config_manager.get_persona_mode() == "expressive"
    assert "EXPRESSIVE" in persona.format_persona_prompt(config_manager.get_persona_mode())
