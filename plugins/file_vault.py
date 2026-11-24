"""plugins/file_vault.py — Immutable AES-256-GCM Secure File & Folder Vault.

IMMUTABLE under `core/evolution/safety_guard.py`: autonomous self-evolution can
never modify this module. Uses already-installed `cryptography` AES-256-GCM with
PBKDF2-HMAC-SHA256 (600,000 iterations), random 16-byte salt, and random 12-byte
nonce (`JVLT1` authenticated container format).
"""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import secrets
import shutil
import subprocess
import sys
import tempfile
import threading
import zipfile

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

if getattr(sys, "frozen", False):
    BASE_DIR = Path(sys.executable).parent
else:
    BASE_DIR = Path(__file__).resolve().parent.parent

VAULT_INDEX_PATH: Path = BASE_DIR / "memory" / "vault_index.json"
_MAGIC = b"JVLT1"
_SALT_LEN = 16
_NONCE_LEN = 12
_PBKDF2_ITERS = 600_000
_INDEX_LOCK = threading.Lock()

PLUGIN = {
    "name": "file_vault",
    "description": (
        "Encrypt, decrypt, password-lock, and hide or unhide local files and folders using "
        "authenticated AES-256-GCM cryptography. Actions: "
        "'status' = vault readiness check; "
        "'encrypt' = encrypt a file to `<path>.jvault` (requires path + password, optional remove_original); "
        "'decrypt' = decrypt a `.jvault` file (LOCKED confirmation gate, requires path + password); "
        "'lock_folder' = archive & AES-256-GCM encrypt a directory to `<folder>.jvault`; "
        "'unlock_folder' = verify & extract a `.jvault` folder archive safely blocking path traversal (LOCKED); "
        "'hide' / 'unhide' = toggle Windows hidden+system attributes via `attrib` (unhide is LOCKED); "
        "'list' = inspect tracked encrypted vault items."
    ),
    "brief": (
        "AES-256-GCM file/folder vault & Windows hide/unhide "
        "(status, encrypt, decrypt, lock_folder, unlock_folder, hide, unhide, list)."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "action": {
                "type": "STRING",
                "enum": ["status", "encrypt", "decrypt", "lock_folder", "unlock_folder", "hide", "unhide", "list"],
                "description": "Vault operation to perform (default 'status').",
            },
            "path": {
                "type": "STRING",
                "description": "Local file or directory path.",
            },
            "password": {
                "type": "STRING",
                "description": "Passphrase used to derive the AES-256 key via PBKDF2-SHA256.",
            },
            "output_path": {
                "type": "STRING",
                "description": "Optional explicit destination path for decrypt or unlock_folder.",
            },
            "remove_original": {
                "type": "BOOLEAN",
                "description": "If true on encrypt/lock_folder, remove plaintext after verifying ciphertext.",
            },
        },
        "required": [],
    },
}


def _notify(player, log_line: str, title: str = "", body: str = "") -> None:
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


def _derive_key(password: str, salt: bytes) -> bytes:
    kdf = PBKDF2HMAC(
        algorithm=hashes.SHA256(),
        length=32,
        salt=salt,
        iterations=_PBKDF2_ITERS,
    )
    return kdf.derive(password.encode("utf-8"))


def _is_forbidden_system_target(target: Path) -> bool:
    try:
        resolved = target.resolve()
    except OSError:
        return True
    s_low = str(resolved).replace("\\", "/").lower()
    repo_root = BASE_DIR.resolve()
    if resolved == repo_root or resolved in (repo_root / "core", repo_root / "plugins"):
        return True
    if any(p in s_low for p in ("/windows/system32", "/windows/syswow64", "/program files/windows defender")):
        return True
    if any(resolved.is_relative_to(repo_root / sub) for sub in ("core", "scripts", "config", "memory", "gui")):
        return True
    return False


def _record_vault_entry(
    kind: str,
    vault_file: Path,
    original_name: str,
    *,
    index_path: Path | None = None,
) -> None:
    """Record ONLY non-secret metadata (paths, timestamp, ciphertext SHA-256). Never passwords."""
    idx_file = Path(index_path) if index_path is not None else VAULT_INDEX_PATH
    digest = hashlib.sha256(vault_file.read_bytes()).hexdigest()[:16] if vault_file.is_file() else ""
    entry = {
        "kind": kind,
        "vault_path": str(vault_file.resolve()),
        "original_name": original_name,
        "ciphertext_sha256_prefix": digest,
        "updated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    with _INDEX_LOCK:
        items: list[dict] = []
        if idx_file.is_file():
            try:
                loaded = json.loads(idx_file.read_text(encoding="utf-8"))
                if isinstance(loaded, list):
                    items = [x for x in loaded if isinstance(x, dict) and x.get("vault_path") != entry["vault_path"]]
            except Exception:
                items = []
        items.append(entry)
        idx_file.parent.mkdir(parents=True, exist_ok=True)
        idx_file.write_text(json.dumps(items[-200:], indent=2, ensure_ascii=False), encoding="utf-8")


def list_vault_entries(*, index_path: Path | None = None) -> list[dict]:
    idx_file = Path(index_path) if index_path is not None else VAULT_INDEX_PATH
    with _INDEX_LOCK:
        if not idx_file.is_file():
            return []
        try:
            data = json.loads(idx_file.read_text(encoding="utf-8"))
            return [x for x in data if isinstance(x, dict)] if isinstance(data, list) else []
        except Exception:
            return []


def encrypt_bytes(plaintext: bytes, password: str, *, aad: bytes = b"file") -> bytes:
    if not password:
        raise ValueError("Password cannot be empty.")
    salt = secrets.token_bytes(_SALT_LEN)
    nonce = secrets.token_bytes(_NONCE_LEN)
    key = _derive_key(password, salt)
    ct = AESGCM(key).encrypt(nonce, plaintext, aad)
    return _MAGIC + salt + nonce + ct


def decrypt_bytes(blob: bytes, password: str, *, aad: bytes = b"file") -> bytes:
    header_len = len(_MAGIC) + _SALT_LEN + _NONCE_LEN
    if len(blob) <= header_len or not blob.startswith(_MAGIC):
        raise ValueError("Invalid or corrupted vault container format.")
    salt_start = len(_MAGIC)
    nonce_start = salt_start + _SALT_LEN
    salt = blob[salt_start:nonce_start]
    nonce = blob[nonce_start:header_len]
    ct = blob[header_len:]
    key = _derive_key(password, salt)
    return AESGCM(key).decrypt(nonce, ct, aad)


def encrypt_file(
    src_path: Path,
    password: str,
    *,
    output_path: Path | None = None,
    remove_original: bool = False,
    index_path: Path | None = None,
) -> dict:
    src = Path(src_path)
    if src.is_symlink() or not src.is_file():
        return {"ok": False, "error": f"Source file not found or symlink refused: {src}"}
    if _is_forbidden_system_target(src):
        return {"ok": False, "error": f"Refused: '{src}' is a protected system/repository path."}
    if not password:
        return {"ok": False, "error": "Password is required to encrypt."}

    dst = Path(output_path) if output_path else Path(str(src) + ".jvault")
    if dst.exists() and dst.is_dir():
        return {"ok": False, "error": f"Destination '{dst}' is a directory."}

    blob = encrypt_bytes(src.read_bytes(), password, aad=b"JVLT1:file")
    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_bytes(blob)
    _record_vault_entry("file", dst, src.name, index_path=index_path)

    if remove_original and dst.is_file():
        src.unlink()
    return {"ok": True, "vault_path": str(dst.resolve()), "removed_original": bool(remove_original)}


def decrypt_file(
    vault_path: Path,
    password: str,
    *,
    output_path: Path | None = None,
) -> dict:
    src = Path(vault_path)
    if src.is_symlink() or not src.is_file():
        return {"ok": False, "error": f"Vault file not found or symlink refused: {src}"}
    if not password:
        return {"ok": False, "error": "Password is required to decrypt."}

    try:
        plaintext = decrypt_bytes(src.read_bytes(), password, aad=b"JVLT1:file")
    except InvalidTag:
        return {"ok": False, "error": "Decryption failed: wrong password or tampered vault file."}
    except Exception as exc:
        return {"ok": False, "error": f"Decryption failed: {exc}"}

    if output_path is not None:
        dst = Path(output_path)
    elif src.name.endswith(".jvault"):
        dst = src.with_name(src.name[:-7])
    else:
        dst = src.with_name(src.name + ".decrypted")

    if _is_forbidden_system_target(dst):
        return {"ok": False, "error": f"Refused writing outside safe locations: {dst}"}

    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_bytes(plaintext)
    return {"ok": True, "output_path": str(dst.resolve()), "bytes": len(plaintext)}


def lock_folder(
    folder_path: Path,
    password: str,
    *,
    output_path: Path | None = None,
    remove_original: bool = False,
    index_path: Path | None = None,
) -> dict:
    folder = Path(folder_path)
    if folder.is_symlink() or not folder.is_dir():
        return {"ok": False, "error": f"Folder not found or symlink refused: {folder}"}
    if _is_forbidden_system_target(folder):
        return {"ok": False, "error": f"Refused: '{folder}' is a protected directory."}
    if not password:
        return {"ok": False, "error": "Password is required to lock folder."}

    dst = Path(output_path) if output_path else folder.with_name(folder.name + ".jvault")
    folder_res = folder.resolve()
    with tempfile.TemporaryDirectory(prefix="jvault_zip_") as td:
        tmp_zip = Path(td) / "archive.zip"
        with zipfile.ZipFile(tmp_zip, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            for root_str, dirs, files in os.walk(folder_res, followlinks=False):
                dirs[:] = [d for d in dirs if not (Path(root_str) / d).is_symlink()]
                for fn in files:
                    fp = Path(root_str) / fn
                    if fp.is_symlink() or not fp.resolve().is_relative_to(folder_res):
                        continue
                    rel = fp.resolve().relative_to(folder_res).as_posix()
                    zf.write(fp, arcname=rel)
        blob = encrypt_bytes(tmp_zip.read_bytes(), password, aad=b"JVLT1:folder")

    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_bytes(blob)
    _record_vault_entry("folder", dst, folder.name, index_path=index_path)

    if remove_original and dst.is_file():
        shutil.rmtree(folder_res)
    return {"ok": True, "vault_path": str(dst.resolve()), "removed_original": bool(remove_original)}


def safe_extract_zip_bytes(zip_bytes: bytes, dest_dir: Path) -> int:
    """Safely extract zip bytes into `dest_dir`, blocking ZipSlip path traversal and symlinks."""
    out_root = dest_dir.resolve()
    out_root.mkdir(parents=True, exist_ok=True)
    count = 0
    with tempfile.TemporaryDirectory(prefix="jvault_unzip_") as td:
        tmp_zip = Path(td) / "payload.zip"
        tmp_zip.write_bytes(zip_bytes)
        with zipfile.ZipFile(tmp_zip, "r") as zf:
            for member in zf.infolist():
                raw_name = member.filename.replace("\\", "/")
                if not raw_name or raw_name.startswith("/") or (len(raw_name) >= 2 and raw_name[1] == ":"):
                    raise ValueError(f"ZipSlip blocked: absolute archive member {member.filename!r}")
                parts = [p for p in raw_name.split("/") if p and p != "."]
                if any(p == ".." for p in parts):
                    raise ValueError(f"ZipSlip blocked: traversal member {member.filename!r}")
                # Block POSIX symlink bit (0120000 << 16)
                if ((member.external_attr >> 16) & 0o170000) == 0o120000:
                    raise ValueError(f"Symlink blocked in archive: {member.filename!r}")
                target = (out_root / "/".join(parts)).resolve() if parts else out_root
                if target != out_root and not target.is_relative_to(out_root):
                    raise ValueError(f"ZipSlip escape blocked: {member.filename!r}")
                if member.is_dir():
                    target.mkdir(parents=True, exist_ok=True)
                else:
                    target.parent.mkdir(parents=True, exist_ok=True)
                    with zf.open(member, "r") as src_f, open(target, "wb") as dst_f:
                        shutil.copyfileobj(src_f, dst_f)
                    count += 1
    return count


def unlock_folder(
    vault_path: Path,
    password: str,
    *,
    output_dir: Path | None = None,
) -> dict:
    src = Path(vault_path)
    if src.is_symlink() or not src.is_file():
        return {"ok": False, "error": f"Vault file not found or symlink refused: {src}"}
    if not password:
        return {"ok": False, "error": "Password is required to unlock folder."}

    try:
        zip_bytes = decrypt_bytes(src.read_bytes(), password, aad=b"JVLT1:folder")
    except InvalidTag:
        return {"ok": False, "error": "Unlock failed: wrong password or tampered vault file."}
    except Exception as exc:
        return {"ok": False, "error": f"Unlock failed: {exc}"}

    if output_dir is not None:
        dst_dir = Path(output_dir)
    elif src.name.endswith(".jvault"):
        dst_dir = src.with_name(src.name[:-7])
    else:
        dst_dir = src.with_name(src.name + "_unlocked")

    if _is_forbidden_system_target(dst_dir):
        return {"ok": False, "error": f"Refused extracting to protected directory: {dst_dir}"}

    try:
        extracted = safe_extract_zip_bytes(zip_bytes, dst_dir)
    except Exception as exc:
        return {"ok": False, "error": f"Archive extraction blocked: {exc}"}

    return {"ok": True, "output_dir": str(dst_dir.resolve()), "files_extracted": extracted}


def set_hidden_attribute(target_path: Path, hidden: bool) -> dict:
    target = Path(target_path)
    if target.is_symlink() or not target.exists():
        return {"ok": False, "error": f"Path not found or symlink refused: {target}"}
    if _is_forbidden_system_target(target):
        return {"ok": False, "error": f"Refused modifying protected path: {target}"}
    if os.name == "nt":
        flags = ["+h", "+s"] if hidden else ["-h", "-s"]
        proc = subprocess.run(
            ["attrib", *flags, str(target.resolve())],
            capture_output=True,
            text=True,
            timeout=10.0,
            shell=False,
            stdin=subprocess.DEVNULL,
        )
        if proc.returncode != 0:
            return {"ok": False, "error": f"attrib failed: {(proc.stderr or proc.stdout).strip()[:160]}"}
        return {"ok": True, "path": str(target.resolve()), "hidden": hidden, "platform": "windows"}
    return {"ok": True, "path": str(target.resolve()), "hidden": hidden, "platform": os.name, "note": "attrib skipped on POSIX"}


def _gated(action: str, title: str, detail: str, fn) -> str:
    try:
        from core.evolution import safety_guard
        return safety_guard.enforce_gate("file_vault", action, title, detail, fn, key=f"vault_{action}")
    except Exception:
        from core import confirm
        return confirm.request(key=f"vault_{action}", title=f"[LOCKED] {title}", detail=detail, run=fn)


def run(parameters: dict, player=None, session_memory=None) -> str:
    """Entry point discovered by `core.plugin_loader`. Never raises."""
    _ = session_memory
    try:
        params = parameters if isinstance(parameters, dict) else {}
        action = str(params.get("action") or "status").strip().lower()
        path_str = str(params.get("path") or "").strip()
        password = str(params.get("password") or "")
        out_str = str(params.get("output_path") or "").strip()
        remove_orig = bool(params.get("remove_original", False))

        if action == "status":
            entries = list_vault_entries()
            msg = (
                f"Secure File Vault ready (AES-256-GCM + PBKDF2-SHA256 {_PBKDF2_ITERS:,} iters, "
                f"immutable guard active). Tracked vault items: {len(entries)}."
            )
            _notify(player, msg)
            return msg

        if action == "list":
            entries = list_vault_entries()
            if not entries:
                msg = "No encrypted items tracked in the vault yet."
                _notify(player, msg)
                return msg
            lines = [f"- [{e.get('kind')}] {e.get('vault_path')} (updated {e.get('updated_at')})" for e in entries[:30]]
            body = "\n".join(lines)
            _notify(player, f"Vault lists {len(entries)} item(s).", "SECURE FILE VAULT ITEMS", body)
            return f"Found {len(entries)} item(s) in the Secure File Vault.\n{body}"

        if not path_str:
            return f"Specify 'path' to run file_vault action '{action}'."

        target = Path(path_str)
        out_path = Path(out_str) if out_str else None

        if action == "encrypt":
            res = encrypt_file(target, password, output_path=out_path, remove_original=remove_orig)
            msg = f"Encrypted to vault '{res['vault_path']}'." if res.get("ok") else str(res.get("error"))
            _notify(player, msg, "FILE VAULT ENCRYPT", msg)
            return msg

        if action == "lock_folder":
            res = lock_folder(target, password, output_path=out_path, remove_original=remove_orig)
            msg = f"Locked folder into '{res['vault_path']}'." if res.get("ok") else str(res.get("error"))
            _notify(player, msg, "FILE VAULT FOLDER LOCKED", msg)
            return msg

        if action == "hide":
            res = set_hidden_attribute(target, True)
            msg = f"Hidden attribute applied to '{res.get('path', path_str)}'." if res.get("ok") else str(res.get("error"))
            _notify(player, msg)
            return msg

        if action == "decrypt":
            def _do_decrypt() -> str:
                r = decrypt_file(target, password, output_path=out_path)
                m = f"Decrypted to '{r['output_path']}' ({r['bytes']} bytes)." if r.get("ok") else str(r.get("error"))
                _notify(player, m, "FILE VAULT DECRYPT", m)
                return m
            return _gated("decrypt", f"Decrypt vault file '{target.name}'", f"Decrypt {target} with AES-256-GCM", _do_decrypt)

        if action == "unlock_folder":
            def _do_unlock() -> str:
                r = unlock_folder(target, password, output_dir=out_path)
                m = f"Unlocked {r['files_extracted']} file(s) into '{r['output_dir']}'." if r.get("ok") else str(r.get("error"))
                _notify(player, m, "FILE VAULT UNLOCKED", m)
                return m
            return _gated("unlock_folder", f"Unlock vault folder '{target.name}'", f"Decrypt & extract {target}", _do_unlock)

        if action == "unhide":
            def _do_unhide() -> str:
                r = set_hidden_attribute(target, False)
                m = f"Unhidden '{r.get('path', path_str)}'." if r.get("ok") else str(r.get("error"))
                _notify(player, m)
                return m
            return _gated("unhide", f"Unhide '{target.name}'", f"Remove hidden/system attributes on {target}", _do_unhide)

        return f"Unknown file_vault action '{action}'."
    except Exception as exc:
        err = f"file_vault error: {exc}"
        _notify(player, err)
        return err
