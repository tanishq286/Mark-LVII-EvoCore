"""plugins/smart_form_filler.py — Encrypted personal form profile vault + Playwright/BS4 DOM autofill."""

from __future__ import annotations

import json
from pathlib import Path

from plugins.file_vault import decrypt_bytes, encrypt_bytes

_BASE_DIR = Path(__file__).resolve().parent.parent
DEFAULT_VAULT_PROFILE = _BASE_DIR / "memory" / "form_profile.jvault"
_PROFILE_AAD = b"form_profile_v1"

PLUGIN = {
    "name": "smart_form_filler",
    "brief": "AES-256-GCM encrypted user profile vault and intelligent web form autofill",
    "description": (
        "Stores user autofill details encrypted with AES-256-GCM via file_vault in memory/form_profile.jvault, "
        "inspects web forms via BeautifulSoup/Playwright DOM selectors, maps inputs semantically, and executes "
        "LOCKED-gated autofill cleanly. Actions: status, save_profile, inspect_form, preview_mapping, fill_form."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "action": {
                "type": "STRING",
                "description": "Action: status | save_profile | inspect_form | preview_mapping | fill_form",
            },
            "password": {
                "type": "STRING",
                "description": "Vault encryption password protecting memory/form_profile.jvault.",
            },
            "profile": {
                "type": "OBJECT",
                "description": "Key-value dict for save_profile (full_name, email, phone, address, city, postal_code, country).",
            },
            "url": {
                "type": "STRING",
                "description": "Webpage URL containing the target form.",
            },
            "html": {
                "type": "STRING",
                "description": "Optional raw HTML form markup for offline inspection/mapping.",
            },
            "submit": {
                "type": "BOOLEAN",
                "description": "Optional submit click after fill (default: False).",
            },
        },
        "required": [],
    },
}

_FIELD_SYNONYMS: dict[str, tuple[str, ...]] = {
    "full_name": ("fullname", "full_name", "name", "your_name", "applicant", "contact_name"),
    "first_name": ("firstname", "first_name", "fname", "given_name"),
    "last_name": ("lastname", "last_name", "lname", "surname", "family_name"),
    "email": ("email", "e-mail", "mail", "user_email"),
    "phone": ("phone", "tel", "mobile", "telephone", "contact_number"),
    "address": ("address", "street", "addr", "address1", "line1"),
    "city": ("city", "town", "locality"),
    "state": ("state", "province", "region"),
    "postal_code": ("zip", "postal", "postcode", "pincode", "postal_code"),
    "country": ("country", "nation"),
}


def save_encrypted_profile(profile: dict, password: str, *, store_path: Path | str | None = None) -> str:
    """Encrypt `profile` JSON via AES-256-GCM and persist to `.jvault`."""
    if not isinstance(profile, dict) or not profile:
        raise ValueError("Profile must be a non-empty dictionary.")
    if not password:
        raise ValueError("Password required to encrypt form profile.")
    dest = Path(store_path) if store_path else DEFAULT_VAULT_PROFILE
    dest.parent.mkdir(parents=True, exist_ok=True)
    raw = json.dumps(profile, ensure_ascii=False).encode("utf-8")
    blob = encrypt_bytes(raw, password, aad=_PROFILE_AAD)
    dest.write_bytes(blob)
    return f"Encrypted form profile saved ({len(profile)} fields) -> {dest.name}."


def load_encrypted_profile(password: str, *, store_path: Path | str | None = None) -> dict:
    """Decrypt and return the stored profile dictionary."""
    dest = Path(store_path) if store_path else DEFAULT_VAULT_PROFILE
    if not dest.is_file():
        raise FileNotFoundError(f"Encrypted form profile not found: {dest}")
    raw = decrypt_bytes(dest.read_bytes(), password, aad=_PROFILE_AAD)
    data = json.loads(raw.decode("utf-8"))
    if not isinstance(data, dict):
        raise ValueError("Corrupt encrypted form profile payload.")
    return data


def inspect_form_fields(html_text: str) -> list[dict]:
    """Extract fillable form elements and their semantic hints from HTML."""
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(html_text or "", "lxml")
    labels_by_for: dict[str, str] = {}
    for lbl in soup.find_all("label"):
        for_id = str(lbl.get("for") or "").strip()
        if for_id:
            labels_by_for[for_id] = lbl.get_text(" ", strip=True)

    fields: list[dict] = []
    for el in soup.find_all(["input", "textarea", "select"]):
        itype = str(el.get("type") or el.name).lower()
        if itype in ("hidden", "submit", "button", "reset", "image", "file"):
            continue
        eid = str(el.get("id") or "").strip()
        ename = str(el.get("name") or "").strip()
        placeholder = str(el.get("placeholder") or "").strip()
        label = labels_by_for.get(eid, "") or str(el.get("aria-label") or "").strip()
        selector = f"#{eid}" if eid else (f"[name='{ename}']" if ename else el.name)
        fields.append({
            "tag": el.name,
            "type": itype,
            "id": eid,
            "name": ename,
            "label": label,
            "placeholder": placeholder,
            "selector": selector,
        })
    return fields


def map_profile_to_fields(profile: dict, form_fields: list[dict]) -> list[dict]:
    """Match encrypted profile keys to discovered HTML form fields."""
    mapped: list[dict] = []
    norm_prof = {str(k).strip().lower(): str(v) for k, v in profile.items() if v is not None}
    for fld in form_fields:
        hay = " ".join((
            fld.get("name", ""),
            fld.get("id", ""),
            fld.get("label", ""),
            fld.get("placeholder", ""),
            fld.get("type", ""),
        )).lower()
        matched_key = ""
        # Check exact profile keys first
        for pk in norm_prof:
            if pk in hay:
                matched_key = pk
                break
        if not matched_key:
            for canon, syns in _FIELD_SYNONYMS.items():
                if canon in norm_prof and any(s in hay for s in syns):
                    matched_key = canon
                    break
        if matched_key:
            mapped.append({
                "selector": fld["selector"],
                "field_name": fld.get("name") or fld.get("id") or fld["selector"],
                "profile_key": matched_key,
                "value": norm_prof[matched_key],
            })
    return mapped


def _execute_fill(params: dict) -> str:
    password = str(params.get("password") or "")
    if not password:
        return "Provide password='...' to decrypt stored form profile."
    store_path = params.get("store_path")
    profile = load_encrypted_profile(password, store_path=store_path)

    raw_html = str(params.get("html") or "")
    url = str(params.get("url") or "").strip()
    if raw_html:
        fields = inspect_form_fields(raw_html)
        mappings = map_profile_to_fields(profile, fields)
        return (
            f"Form Autofill (HTML simulation): matched {len(mappings)}/{len(fields)} fields: "
            + ", ".join(f"{m['field_name']}<-{m['profile_key']}" for m in mappings)
        )

    if not url:
        return "Provide url='...' or html='...' to autofill."

    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=bool(params.get("headless", False)))
        try:
            page = browser.new_page()
            page.goto(url, wait_until="domcontentloaded", timeout=15000)
            fields = inspect_form_fields(page.content())
            mappings = map_profile_to_fields(profile, fields)
            filled = []
            for m in mappings:
                try:
                    page.fill(m["selector"], m["value"], timeout=2000)
                    filled.append(m["field_name"])
                except Exception:
                    continue
            if params.get("submit") and filled:
                page.keyboard.press("Enter")
            return f"Playwright filled {len(filled)} field(s) on {url}: {', '.join(filled)}"
        finally:
            browser.close()


def run(parameters: dict, player=None, session_memory=None) -> str:
    """Plugin entrypoint for encrypted smart form filling."""
    params = parameters if isinstance(parameters, dict) else {}
    action = str(params.get("action") or "status").strip().lower()
    store_path = params.get("store_path")
    vault_file = Path(store_path) if store_path else DEFAULT_VAULT_PROFILE

    if action == "status":
        return f"Smart Form Filler ready (encrypted_vault={'present' if vault_file.is_file() else 'not_set'})."

    if action == "save_profile":
        return save_encrypted_profile(
            params.get("profile") or {},
            str(params.get("password") or ""),
            store_path=store_path,
        )

    if action == "inspect_form":
        raw_html = str(params.get("html") or "")
        if not raw_html and params.get("url"):
            import httpx
            with httpx.Client(timeout=10.0, follow_redirects=True) as client:
                raw_html = client.get(str(params["url"])).text
        if not raw_html:
            return "Provide html='...' or url='...' to inspect form inputs."
        fields = inspect_form_fields(raw_html)
        return json.dumps(fields, indent=2)

    if action == "preview_mapping":
        profile = load_encrypted_profile(str(params.get("password") or ""), store_path=store_path)
        fields = inspect_form_fields(str(params.get("html") or ""))
        mappings = map_profile_to_fields(profile, fields)
        redacted = [
            {"selector": m["selector"], "field_name": m["field_name"], "profile_key": m["profile_key"]}
            for m in mappings
        ]
        return json.dumps(redacted, indent=2)

    if action == "fill_form":
        from core.evolution import safety_guard
        target_label = str(params.get("url") or "local HTML snapshot")
        return safety_guard.enforce_gate(
            "smart_form_filler",
            "fill_form",
            "Autofill Encrypted Form Profile",
            f"Decrypt personal form vault and populate inputs on: {target_label}",
            lambda: _execute_fill(params),
            key="smart_form_fill",
        )

    return f"Unknown smart_form_filler action '{action}'."
