"""Safe environment and credential lookup helper for Mark-LVII plugins."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Iterable

ROOT = Path(__file__).resolve().parent.parent
_DOTENV_PATH = ROOT / ".env"
_API_KEYS_PATH = ROOT / "config" / "api_keys.json"


def _read_dotenv(path: Path = _DOTENV_PATH) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.is_file():
        return values
    try:
        text = path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return values

    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export "):].strip()
        if "=" not in line:
            continue
        key, val = line.split("=", 1)
        key = key.strip()
        val = val.strip()
        if len(val) >= 2 and val[0] == val[-1] and val[0] in ("'", '"'):
            val = val[1:-1]
        if key:
            values[key] = val
    return values


def _read_api_keys(path: Path = _API_KEYS_PATH) -> dict[str, str]:
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8", errors="ignore"))
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(data, dict):
        return {}
    out: dict[str, str] = {}
    for k, v in data.items():
        if isinstance(k, str) and isinstance(v, (str, int, float, bool)):
            out[k] = str(v)
    return out


def get_env(name: str, default: str = "") -> str:
    """Return a trimmed secret/config value from OS env, `.env`, or `config/api_keys.json`."""
    key = (name or "").strip()
    if not key:
        return default

    val = os.environ.get(key)
    if val is not None and val.strip():
        return val.strip()

    dotenv_val = _read_dotenv().get(key, "").strip()
    if dotenv_val:
        return dotenv_val

    json_val = _read_api_keys().get(key, "").strip()
    if json_val:
        return json_val

    return default


def has_env(name: str) -> bool:
    """Return True if `name` resolves to a non-empty value."""
    return bool(get_env(name))


def missing_env(names: Iterable[str]) -> list[str]:
    """Return list of names that are empty or missing."""
    return [n for n in names if not has_env(n)]
