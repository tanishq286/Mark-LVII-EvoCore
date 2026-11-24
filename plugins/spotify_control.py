"""plugins/spotify_control.py — Spotify Web API playback & search via spotipy."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from core.env_loader import get_env, missing_env

_BASE_DIR = Path(__file__).resolve().parent.parent
_DEFAULT_REDIRECT = "http://127.0.0.1:8888/callback"
_DEFAULT_CACHE = _BASE_DIR / "memory" / ".spotify_cache"
_SCOPES = "user-read-playback-state user-modify-playback-state user-read-currently-playing"

PLUGIN = {
    "name": "spotify_control",
    "brief": "Control Spotify playback, volume, queue, and track search via Spotify Web API",
    "description": (
        "Connects to Spotify Web API via spotipy using credentials loaded cleanly through core.env_loader "
        "(SPOTIPY_CLIENT_ID, SPOTIPY_CLIENT_SECRET, optional SPOTIPY_REDIRECT_URI). "
        "Actions: status, now_playing, search, play, pause, next, previous, volume, shuffle, devices, queue."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "action": {
                "type": "STRING",
                "description": "Action: status | now_playing | search | play | pause | next | previous | volume | shuffle | devices | queue",
            },
            "query": {
                "type": "STRING",
                "description": "Track/artist/album query or Spotify URI for play, search, or queue.",
            },
            "volume": {
                "type": "INTEGER",
                "description": "Playback volume percentage 0-100 for action='volume'.",
            },
            "state": {
                "type": "BOOLEAN",
                "description": "True/False toggle for action='shuffle'.",
            },
            "device_id": {
                "type": "STRING",
                "description": "Optional target Spotify device ID.",
            },
            "limit": {
                "type": "INTEGER",
                "description": "Number of search results (default: 5).",
            },
        },
        "required": [],
    },
}


def _check_credentials() -> tuple[str, str, str]:
    cid = get_env("SPOTIPY_CLIENT_ID")
    csec = get_env("SPOTIPY_CLIENT_SECRET")
    redir = get_env("SPOTIPY_REDIRECT_URI", _DEFAULT_REDIRECT)
    return cid, csec, redir


def _build_client(user_auth: bool = True) -> tuple[Any | None, str]:
    missing = missing_env(("SPOTIPY_CLIENT_ID", "SPOTIPY_CLIENT_SECRET"))
    if missing:
        return None, (
            "Spotify credentials not configured ("
            + ", ".join(missing)
            + "). Add them via environment variables or settings before using Spotify controls."
        )
    try:
        import spotipy
        from spotipy.oauth2 import SpotifyClientCredentials, SpotifyOAuth
    except ImportError:
        return None, "spotipy is not installed. Run: pip install spotipy"

    cid, csec, redir = _check_credentials()
    if user_auth:
        auth = SpotifyOAuth(
            client_id=cid,
            client_secret=csec,
            redirect_uri=redir,
            scope=_SCOPES,
            cache_path=str(_DEFAULT_CACHE),
            open_browser=False,
        )
        return spotipy.Spotify(auth_manager=auth), ""
    creds = SpotifyClientCredentials(client_id=cid, client_secret=csec)
    return spotipy.Spotify(client_credentials_manager=creds), ""


def _fmt_track(item: dict) -> str:
    name = item.get("name") or "Unknown track"
    artists = ", ".join(a.get("name", "") for a in (item.get("artists") or []) if a.get("name"))
    uri = item.get("uri") or ""
    return f"{name} — {artists or 'Unknown Artist'} ({uri})"


def run(parameters: dict, player=None, session_memory=None) -> str:
    """Plugin entrypoint for Spotify Web API control."""
    params = parameters if isinstance(parameters, dict) else {}
    action = str(params.get("action") or "status").strip().lower()

    if action == "status":
        missing = missing_env(("SPOTIPY_CLIENT_ID", "SPOTIPY_CLIENT_SECRET"))
        if missing:
            return (
                "Spotify status: unconfigured (missing "
                + ", ".join(missing)
                + ")."
            )
        cached = _DEFAULT_CACHE.is_file()
        return f"Spotify status: configured | oauth_cache={'present' if cached else 'pending_login'}"

    needs_user = action != "search"
    sp, err = _build_client(user_auth=needs_user)
    if sp is None:
        return err

    device_id = str(params.get("device_id") or "").strip() or None
    query = str(params.get("query") or "").strip()

    try:
        if action == "search":
            if not query:
                return "Provide query='...' to search Spotify tracks."
            limit = max(1, min(10, int(params.get("limit") or 5)))
            res = sp.search(q=query, type="track", limit=limit)
            items = ((res or {}).get("tracks") or {}).get("items") or []
            if not items:
                return f"No Spotify tracks matched '{query}'."
            return "Spotify search results:\n" + "\n".join(f"- {_fmt_track(it)}" for it in items)

        if action in ("now_playing", "current"):
            cur = sp.current_playback()
            if not cur or not cur.get("item"):
                return "Nothing is currently playing on Spotify."
            state = "Playing" if cur.get("is_playing") else "Paused"
            return f"{state}: {_fmt_track(cur['item'])}"

        if action == "devices":
            devs = (sp.devices() or {}).get("devices") or []
            if not devs:
                return "No active Spotify Connect devices found."
            lines = [
                f"- {d.get('name')} ({d.get('type')}, id={d.get('id')}, active={d.get('is_active')})"
                for d in devs
            ]
            return "Spotify devices:\n" + "\n".join(lines)

        if action == "play":
            if query.startswith("spotify:"):
                kwargs = {"context_uri": query} if ":playlist:" in query or ":album:" in query else {"uris": [query]}
                sp.start_playback(device_id=device_id, **kwargs)
                return f"Playing {query} on Spotify."
            if query:
                res = sp.search(q=query, type="track", limit=1)
                items = ((res or {}).get("tracks") or {}).get("items") or []
                if not items:
                    return f"Could not find Spotify track '{query}'."
                sp.start_playback(device_id=device_id, uris=[items[0]["uri"]])
                return f"Playing {_fmt_track(items[0])}."
            sp.start_playback(device_id=device_id)
            return "Resumed Spotify playback."

        if action == "pause":
            sp.pause_playback(device_id=device_id)
            return "Paused Spotify playback."

        if action == "next":
            sp.next_track(device_id=device_id)
            return "Skipped to next Spotify track."

        if action in ("previous", "prev"):
            sp.previous_track(device_id=device_id)
            return "Returned to previous Spotify track."

        if action == "volume":
            vol = max(0, min(100, int(params.get("volume") if params.get("volume") is not None else 50)))
            sp.volume(vol, device_id=device_id)
            return f"Set Spotify volume to {vol}%."

        if action == "shuffle":
            state = bool(params.get("state", True))
            sp.shuffle(state, device_id=device_id)
            return f"Spotify shuffle {'enabled' if state else 'disabled'}."

        if action == "queue":
            if not query:
                return "Provide query='...' or spotify:track:... URI to add to queue."
            uri = query
            if not query.startswith("spotify:"):
                res = sp.search(q=query, type="track", limit=1)
                items = ((res or {}).get("tracks") or {}).get("items") or []
                if not items:
                    return f"Could not find track '{query}' to queue."
                uri = items[0]["uri"]
            sp.add_to_queue(uri, device_id=device_id)
            return f"Added {uri} to Spotify queue."

        return (
            f"Unknown spotify_control action '{action}'. Use: "
            "status, now_playing, search, play, pause, next, previous, volume, shuffle, devices, queue."
        )
    except Exception as exc:
        return f"Spotify API error: {exc}"
