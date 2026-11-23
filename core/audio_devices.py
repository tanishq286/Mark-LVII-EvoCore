"""
core/audio_devices.py — pick which microphone and which speakers JARVIS uses.

WHY
    Both audio streams in main.py were opened without a `device=` argument, so
    they always took whatever the operating system called "default". On a laptop
    with a built-in mic, a webcam mic and a headset that is a coin toss — and on
    Windows the default *moves on its own* the moment you plug a headset in.
    "JARVIS can't hear me" almost always means "JARVIS is listening to the
    monitor's microphone".

WHY NAMES, NOT INDICES
    sounddevice identifies devices by integer index, and those indices shift
    whenever a device appears or disappears. Storing index 3 means that after
    unplugging a USB interface the saved setting silently points at something
    else. We store the device *name* and resolve it to an index at open time.

WHY THIS IS CACHED
    `sd.query_devices()` talks to the host audio API and can take a few hundred
    milliseconds on a Windows machine with many endpoints. Mark LV learned this
    lesson the expensive way — a 2.1-second `openwakeword` import on the Qt
    thread made the settings drawer look like it was broken. So the list is
    fetched once on a background thread at startup and served from cache.
"""

from __future__ import annotations

import threading
import time
from typing import Optional

# The label shown for "let the OS decide", and the value stored in config for
# it. Empty string, so an untouched install and a deliberately-default install
# are the same thing — nothing changes for anyone who never opens the picker.
DEFAULT_LABEL = "System default"
DEFAULT_VALUE = ""

_cache: dict[str, list[str]] | None = None
_cache_lock = threading.Lock()
_pa_lock = threading.RLock()

# Which host API each direction settled on, so resolve() opens the same endpoint
# the picker listed. Filled in by _query().
_chosen_api: dict = {"input": None, "output": None}


# ── Why the raw list is unusable, and what is filtered out ───────────────────
#
# `sd.query_devices()` returns one entry per (device × host API), not one per
# device. Measured on a normal Windows machine: 41 entries for what the Windows
# sound settings show as 4 microphones and 4 speakers. The same Realtek
# microphone appears four times — once each under MME, DirectSound, WASAPI and
# WDM-KS — and none of the four is labelled to say which is which.
#
# Handing that to a person is not a choice, it is a quiz. So the list is reduced
# the way the operating system's own settings panel does it:
#
#   1. ONE host API per direction — chosen by measurement, not by reasoning.
#      See the preference note below.
#   2. No pseudo-devices. "Microsoft Sound Mapper", "Primary Sound Driver",
#      ALSA's "default"/"sysdefault"/"dmix" are aliases for "whatever the OS
#      picks" — which is precisely the "System default" entry already at the top
#      of the list. Offering them again as if they were hardware is noise.
#   3. No zero-channel or unnamed entries (WASAPI reports one of each).
#   4. Deduplicated by name.
#
# Nothing is hidden that a person could actually want: the same hardware is
# still there, listed once, under the name their operating system uses for it.

# ── Preference order, which is only a starting point ─────────────────────────
#
# Two earlier versions of this file were wrong in the same way: they decided
# which host API to use by reasoning about it instead of measuring it.
#
#   v1 ranked APIs by how clean their device names were and picked WASAPI.
#      WASAPI in shared mode does not resample — the hardware runs at 48 kHz,
#      this app streams 16 kHz in and 24 kHz out, and every open failed with
#      "Invalid sample rate". The picker looked right and did nothing.
#
#   v2 added a rate check and picked DirectSound, which passes that check on
#      both sides. PortAudio's DirectSound *output* is a silent sink: the
#      stream opens, every write returns success in ~0 ms, and nothing is ever
#      heard. Same failure, one layer deeper.
#
# So this list is a preference, not a promise. Which API actually gets used is
# decided below by _usable() (open it for real) and _transport_works() (does
# audio actually move), per direction. On this platform that lands on
# DirectSound for the microphone and MME for the speakers — a split that no
# amount of reasoning would have produced.
_PREFERRED_APIS = {
    "Windows": ("directsound", "mme", "wasapi"),
    # macOS has only Core Audio, so there is nothing to disambiguate.
    "Darwin":  ("core audio",),
    # PulseAudio/PipeWire present one clean endpoint per device; raw ALSA
    # presents dozens of routing permutations of the same card.
    "Linux":   ("pulse", "pipewire", "jack", "alsa"),
}

# ── "It opens" is not "it works" ─────────────────────────────────────────────
#
# Opening a stream successfully proves nothing. Measured, writing 2.0 s of audio:
#
#     device=None (MME)                    2.02 s   consumed in real time
#     DirectSound, any output device       0.00 s   swallowed instantly
#
# No flag or capability field reports this. The only thing that separates a real
# sink from a fake one is whether it consumes audio at the rate audio is
# consumed at — so that is what gets measured, once per host API per direction,
# on the background thread at startup, using silence.
#
# Each direction is probed **the way main.py actually uses it**. That is not a
# detail: DirectSound input passes a callback stream and fails a blocking read,
# so an earlier version of this probe rejected a microphone that works perfectly
# in the app. Probe the mode you ship, not the mode that is easier to write.
_PROBE_SECONDS = {"output": 0.25, "input": 0.20}

# Cache: {(api_name_or_None, kind): bool}
_probe_results: dict = {}


def _transport_works(idx: int, kind: str, api_key) -> bool:
    """Does this host API actually move audio, or only pretend to?

    Probed once per API per direction and cached. Output writes silence, so the
    probe is inaudible; input reads and discards."""
    if api_key in _probe_results:
        return _probe_results[api_key]

    ok = False
    try:
        import sounddevice as sd
        rate = _RATES.get(kind, 16000)
        secs = _PROBE_SECONDS.get(kind, 0.5)

        # Each direction is probed the way main.py actually uses it. That is not
        # a detail: DirectSound input passes a callback stream and fails a
        # blocking read, so probing the wrong mode rejected a microphone that
        # works perfectly in the app.
        if kind == "output":
            # main.py writes with stream.write() — a real sink is rate-limited
            # by the hardware clock, a fake one swallows the buffer instantly.
            st = sd.RawOutputStream(samplerate=rate, channels=1, dtype="int16",
                                    blocksize=1024, device=idx)
            st.start()
            t0 = time.monotonic()
            st.write(bytes(int(rate * secs) * 2))   # silence — inaudible
            elapsed = time.monotonic() - t0
            st.stop(); st.close()
            ok = elapsed > secs * 0.5
            if not ok:
                print(f"[Audio] output: host API reports success but moves no "
                      f"audio ({elapsed*1000:.0f} ms for {secs*1000:.0f} ms) "
                      f"— skipping it")
        else:
            # main.py reads through a callback — count what arrives.
            frames = [0]

            def _cb(indata, n, *_a):
                frames[0] += n

            st = sd.InputStream(samplerate=rate, channels=1, dtype="int16",
                                blocksize=1024, device=idx, callback=_cb)
            st.start()
            time.sleep(secs)
            st.stop(); st.close()
            ok = frames[0] > rate * secs * 0.3
            if not ok:
                print(f"[Audio] input: host API delivered {frames[0]} frames in "
                      f"{secs*1000:.0f} ms — skipping it")
    except Exception as e:
        print(f"[Audio] {kind} transport probe failed: {e}")
        ok = False

    _probe_results[api_key] = ok
    return ok


def _display_name(name: str, devices) -> str:
    """MME truncates device names to 31 characters, so the API that actually
    carries the audio may not be the one that can spell. If another host API
    knows a longer name that starts with this one, show that instead — the user
    reads 'Realtek HD Audio 2nd output (Realtek(R) Audio)' while the stream runs
    on the endpoint called 'Realtek HD Audio 2nd output (Re'."""
    if len(name) < 30:
        return name
    best = name
    for dev in devices:
        other = (dev.get("name") or "").strip()
        if len(other) > len(best) and other.startswith(name):
            best = other
    return best

# The rates the app opens its streams at. Defaults match main.py; main.py calls
# configure() at startup with its own constants so the two can never drift apart
# and silently reintroduce the bug above.
_RATES = {"input": 16000, "output": 24000}


def configure(input_rate: int, output_rate: int) -> None:
    """Tell this module the sample rates the audio streams will use, so the
    picker can rule out devices that cannot be opened at them.

    Drops any cached list: which devices are usable depends on the rate, so a
    list built under the old rates would be stale."""
    global _cache
    _RATES["input"]  = int(input_rate)
    _RATES["output"] = int(output_rate)
    with _cache_lock:
        _cache = None


def _usable(idx: int, kind: str) -> bool:
    """Can this device actually be opened at the rate we need?

    Deliberately opens a real stream rather than asking
    `check_output_settings`, because that function lies: it passed for an MME
    endpoint that then failed to open with "The specified format is not
    supported or cannot be translated" [MME error 32]. Opening and immediately
    closing costs milliseconds and is the only answer that holds."""
    st = None
    try:
        import sounddevice as sd
        rate = _RATES.get(kind, 16000)
        if kind == "input":
            st = sd.InputStream(samplerate=rate, channels=1, dtype="int16",
                                blocksize=1024, device=idx,
                                callback=lambda *_a: None)
        else:
            st = sd.RawOutputStream(samplerate=rate, channels=1, dtype="int16",
                                    blocksize=1024, device=idx)
        st.start()
        return True
    except Exception:
        return False
    finally:
        if st is not None:
            try:
                st.stop(); st.close()
            except Exception:
                pass

# Aliases for "the default device" and internal routing endpoints. Matched
# case-insensitively as substrings against the device name.
_PSEUDO_DEVICES = (
    "sound mapper",        # Windows MME
    "primary sound",       # Windows DirectSound ("Primary Sound Capture Driver")
    "sysdefault",          # ALSA
    "default",             # ALSA / PulseAudio alias
    "dmix", "dsnoop",      # ALSA software mixing plugins
    "surround",            # ALSA channel-layout permutations of one card
    "samplerate", "speexrate", "upmix", "vdownmix", "null",
)


def _is_pseudo(name: str) -> bool:
    low = name.lower()
    return any(tok in low for tok in _PSEUDO_DEVICES)


def _query() -> dict[str, list[str]]:
    """Return {'input': [names...], 'output': [names...]}. Never raises.

    Only real, selectable devices — see the note above."""
    out: dict[str, list[str]] = {"input": [], "output": []}
    try:
        import platform
        import sounddevice as sd

        devices = list(sd.query_devices())
        try:
            apis = [a.get("name", "") for a in sd.query_hostapis()]
        except Exception:
            apis = []

        preferred = _PREFERRED_APIS.get(platform.system(), ())

        def _collect(api_filter, kind) -> list[tuple[int, str]]:
            """(index, name) for named, non-pseudo devices on one side that can
            be opened at the rate that side runs at."""
            chan = "max_input_channels" if kind == "input" else "max_output_channels"
            found, seen = [], set()
            for idx, dev in enumerate(devices):
                name = (dev.get("name") or "").strip()
                if not name or _is_pseudo(name) or name in seen:
                    continue
                if dev.get(chan, 0) <= 0:
                    continue
                if api_filter is not None:
                    api = apis[dev["hostapi"]].lower() if dev.get("hostapi", -1) < len(apis) else ""
                    if api_filter not in api:
                        continue
                if not _usable(idx, kind):
                    continue
                seen.add(name)
                found.append((idx, name))
            return found

        # Each direction picks its own host API. They are genuinely different
        # problems — on Windows the microphone works on DirectSound while the
        # speakers only work on MME — and a single global choice cannot be right
        # for both.
        for kind in ("input", "output"):
            for api_filter in list(preferred) + [None]:
                api_key = (api_filter, kind)
                if _probe_results.get(api_key) is False:
                    continue
                found = _collect(api_filter, kind)
                if not found:
                    continue
                # Probe up to the first 2 endpoints on this API so a single
                # sleeping/virtual endpoint at index 0 cannot disqualify real
                # hardware (e.g. Bluetooth earphones) behind it.
                worked = False
                for idx_pos, (probe_idx, _probe_name) in enumerate(found[:2]):
                    if idx_pos > 0:
                        _probe_results.pop(api_key, None)
                    if _transport_works(probe_idx, kind, api_key):
                        worked = True
                        break
                if not worked:
                    _probe_results[api_key] = False
                    continue
                _chosen_api[kind] = api_filter
                out[kind] = [_display_name(n, devices) for _i, n in found]
                break
            if out[kind]:
                print(f"[Audio] {kind}: using "
                      f"{_chosen_api[kind] or 'any host API'} "
                      f"({len(out[kind])} devices)")
        return out

    except Exception as e:
        print(f"[Audio] Device enumeration failed: {e}")
    return out


def reinit_portaudio() -> bool:
    """Re-initialize PortAudio so hot-plugged earphones/headsets become visible
    and `sd.default.device` matches the current OS default.

    MUST ONLY be called when no PortAudio streams are open (between session
    reconnects in main.py)."""
    global _cache
    with _pa_lock:
        try:
            import sounddevice as sd
            sd._terminate()
            sd._initialize()
            with _cache_lock:
                _cache = None
                _probe_results.clear()
                _chosen_api["input"] = None
                _chosen_api["output"] = None
            n = len(sd.query_devices())
            print(f"[Audio] PortAudio refreshed ({n} devices)")
            return True
        except Exception as e:
            print(f"[Audio] PortAudio refresh failed: {e}")
            return False


_EARPHONE_HINTS = (
    "headphone", "headset", "earphone", "earbud", "airpod",
    "buds", "pods", "hands-free", "handsfree", "bluetooth", "tws",
)
_GENERIC_TAGS = (
    "realtek", "high definition audio", "nvidia", "intel", "amd",
    "nahimic", "microsoft", "steam", "virtual", "vb-audio", "voicemeeter",
)


def _extract_headset_tag(output_name: str) -> str | None:
    """If `output_name` looks like earphones/headset (e.g. 'Headphones (tanishq)'
    or 'Headphones (Dubstep BX)'), return the device-specific tag ('tanishq' /
    'dubstep bx') so we can pair it with its matching microphone."""
    name = (output_name or "").strip()
    low = name.lower()
    if not name or not any(k in low for k in _EARPHONE_HINTS):
        return None
    tag = ""
    if "(" in name and ")" in name:
        start = name.find("(") + 1
        end = name.rfind(")")
        if end > start:
            tag = name[start:end].strip()
    if not tag:
        tag = name
    tlow = tag.lower()
    if len(tlow) < 2 or any(g in tlow for g in _GENERIC_TAGS):
        return None
    return tlow


def _win_audio_state() -> dict | None:
    """Query live active Windows audio endpoints via Core Audio (pycaw/COM),
    completely bypassing PortAudio's startup cache. Safe to call while
    PortAudio streams are running."""
    if _platform_is() != "Windows":
        return None
    import warnings
    com_inited = False
    try:
        import comtypes
        comtypes.CoInitialize()
        com_inited = True
    except Exception:
        pass
    try:
        from pycaw.pycaw import AudioUtilities

        def _imm_info(imm) -> tuple[str, str]:
            dev_id = ""
            try:
                dev_id = str(imm.GetId() or "")
            except Exception:
                pass
            fname = ""
            try:
                with warnings.catch_warnings():
                    warnings.simplefilter("ignore")
                    dev = AudioUtilities.CreateDevice(imm)
                fname = (getattr(dev, "FriendlyName", "") or "").strip()
            except Exception:
                pass
            return (dev_id, fname)

        enumerator = AudioUtilities.GetDeviceEnumerator()

        def _default(flow: int) -> tuple[str, str]:
            for role in (1, 0):  # eMultimedia=1, fallback eConsole=0
                try:
                    imm = enumerator.GetDefaultAudioEndpoint(flow, role)
                    if imm is not None:
                        return _imm_info(imm)
                except Exception:
                    continue
            return ("", "")

        def _active(flow: int) -> tuple[tuple[str, str], ...]:
            items = []
            try:
                col = enumerator.EnumAudioEndpoints(flow, 1)  # DEVICE_STATE_ACTIVE=1
                cnt = col.GetCount()
                for i in range(cnt):
                    try:
                        items.append(_imm_info(col.Item(i)))
                    except Exception:
                        continue
            except Exception:
                pass
            items.sort()
            return tuple(items)

        return {
            "default_out": _default(0),  # eRender=0
            "default_in":  _default(1),  # eCapture=1
            "active_out":  _active(0),
            "active_in":   _active(1),
        }
    except Exception:
        return None
    finally:
        if com_inited:
            try:
                import comtypes
                comtypes.CoUninitialize()
            except Exception:
                pass


def _linux_audio_state() -> dict | None:
    """Query live PulseAudio/PipeWire sinks & sources without touching PortAudio."""
    if _platform_is() != "Linux":
        return None
    try:
        import subprocess
        info = subprocess.run(
            ["pactl", "info"], capture_output=True, text=True, timeout=2
        )
        if info.returncode != 0:
            return None
        def_out = def_in = ""
        for line in info.stdout.splitlines():
            if line.startswith("Default Sink:"):
                def_out = line.split(":", 1)[1].strip()
            elif line.startswith("Default Source:"):
                def_in = line.split(":", 1)[1].strip()
        sinks_r = subprocess.run(
            ["pactl", "list", "short", "sinks"],
            capture_output=True, text=True, timeout=2
        )
        srcs_r = subprocess.run(
            ["pactl", "list", "short", "sources"],
            capture_output=True, text=True, timeout=2
        )
        active_out = tuple(sorted(
            (ln.split("\t")[1], ln.split("\t")[1])
            for ln in sinks_r.stdout.splitlines() if "\t" in ln
        ))
        active_in = tuple(sorted(
            (ln.split("\t")[1], ln.split("\t")[1])
            for ln in srcs_r.stdout.splitlines() if "\t" in ln
        ))
        return {
            "default_out": (def_out, def_out),
            "default_in":  (def_in, def_in),
            "active_out":  active_out,
            "active_in":   active_in,
        }
    except Exception:
        return None


def get_system_audio_fingerprint():
    """Return a hashable snapshot of active OS audio endpoints + defaults, or None.
    Safe to call from a background thread while audio streams are running."""
    os_name = _platform_is()
    st = _win_audio_state() if os_name == "Windows" else (
        _linux_audio_state() if os_name == "Linux" else None
    )
    if not st:
        return None
    return (st["default_out"], st["default_in"], st["active_out"], st["active_in"])


def describe_audio_change(old_fp, new_fp) -> str:
    """Short human-readable summary of what changed between two audio fingerprints."""
    if not old_fp or not new_fp:
        return "audio devices updated"
    try:
        old_out, old_in, old_ao, old_ai = old_fp
        new_out, new_in, new_ao, new_ai = new_fp
        old_names = {n for _, n in old_ao if n} | {n for _, n in old_ai if n}
        new_names = {n for _, n in new_ao if n} | {n for _, n in new_ai if n}
        added = sorted(new_names - old_names)
        removed = sorted(old_names - new_names)
        if added:
            return f"{', '.join(added[:2])} connected"
        if removed:
            return f"{', '.join(removed[:2])} disconnected"
        if new_out != old_out and new_out[1]:
            return f"output switched to {new_out[1]}"
        if new_in != old_in and new_in[1]:
            return f"mic switched to {new_in[1]}"
    except Exception:
        pass
    return "audio devices updated"


def has_unseen_system_devices() -> bool:
    """True when the OS reports active endpoints not present in our cached list
    (e.g. earphones connected before PortAudio has been refreshed)."""
    if _platform_is() != "Windows":
        return False
    st = _win_audio_state()
    if not st:
        return False
    with _cache_lock:
        cached = _cache
    if cached is None:
        return False
    known_out = {_fold(n) for n in cached.get("output", [])}
    known_in  = {_fold(n) for n in cached.get("input", [])}
    for _id, fn in st["active_out"]:
        f = _fold(fn)
        if f and not any(f == k or f.startswith(k[:20]) or k.startswith(f[:20]) for k in known_out):
            return True
    for _id, fn in st["active_in"]:
        f = _fold(fn)
        if f and not any(f == k or f.startswith(k[:20]) or k.startswith(f[:20]) for k in known_in):
            return True
    return False


def prefetch() -> None:
    """Warm the cache on a background thread. Called once at startup so the
    settings drawer never pays for enumeration on the Qt thread."""
    def _work():
        global _cache
        with _pa_lock:
            result = _query()
        with _cache_lock:
            _cache = result
        print(f"[Audio] {len(result['input'])} input / "
              f"{len(result['output'])} output devices found")
    threading.Thread(target=_work, daemon=True, name="audio-devices").start()


def list_devices(kind: str, refresh: bool = False) -> list[str]:
    """Device names for 'input' or 'output'. Falls back to a synchronous query
    if the prefetch has not landed yet — correctness over the cache."""
    global _cache
    with _cache_lock:
        cached = None if refresh else _cache
    if cached is None:
        with _pa_lock:
            cached = _query()
        with _cache_lock:
            _cache = cached
    return list(cached.get(kind, []))


def resolve_with_name(name: str, kind: str) -> tuple[int | None, str]:
    """Turn a saved device name into `(sounddevice_index_or_None, resolved_name)`.

    When `name` is empty ("System default") on Windows:
      * For `output`, resolves the live Windows default render endpoint on the
        verified working host API (MME) so earphones/speakers always get audio.
      * For `input`, if the active default output is Bluetooth/USB earphones
        (e.g. 'Headphones (tanishq)') and a matching active microphone for that
        same earphone (e.g. 'Headset (tanishq)') is available and usable,
        pairs the earphone microphone automatically so the user does not have
        to talk into a distant/quiet laptop mic while wearing earphones.
    An explicit saved device name always takes precedence."""
    wanted = (name or "").strip()

    try:
        import platform
        import sounddevice as sd

        with _pa_lock:
            devices = list(sd.query_devices())
            try:
                apis = [a.get("name", "") for a in sd.query_hostapis()]
            except Exception:
                apis = []

            want_in  = (kind == "input")
            chan_key = "max_input_channels" if want_in else "max_output_channels"

            def _candidates(api_filter):
                for idx, dev in enumerate(devices):
                    if dev.get(chan_key, 0) <= 0:
                        continue
                    if api_filter is not None:
                        api = apis[dev["hostapi"]].lower() if dev.get("hostapi", -1) < len(apis) else ""
                        if api_filter not in api:
                            continue
                    yield idx, (dev.get("name") or "").strip()

            list_devices(kind)
            chosen = _chosen_api.get(kind)
            orders = ([chosen] if chosen is not None else []) \
                + [a for a in _PREFERRED_APIS.get(platform.system(), ()) if a != chosen] \
                + [None]

            # ── System default ("" or "System default") ──────────────────────
            if not wanted or wanted == DEFAULT_LABEL:
                if platform.system() == "Windows":
                    st = _win_audio_state()
                    def_out_name = st["default_out"][1] if st else ""
                    if not def_out_name:
                        try:
                            di = sd.default.device[1]
                            if isinstance(di, int) and 0 <= di < len(devices):
                                def_out_name = (devices[di].get("name") or "").strip()
                        except Exception:
                            pass

                    # Auto-pair earphone microphone with active earphone output
                    if want_in and def_out_name:
                        tag = _extract_headset_tag(def_out_name)
                        if tag:
                            for api_filter in orders:
                                best = None
                                for idx, dev_name in _candidates(api_filter):
                                    dlow = dev_name.lower()
                                    if _is_pseudo(dev_name) or "microphone array" in dlow:
                                        continue
                                    if tag in dlow and _usable(idx, "input"):
                                        disp = _display_name(dev_name, devices)
                                        if any(h in dlow for h in ("headset", "hands-free", "handsfree", "mic", "bluetooth")):
                                            print(f"[Audio] input: auto-matched earphone mic '{disp}' for '{def_out_name}'")
                                            return idx, disp
                                        if best is None:
                                            best = (idx, disp)
                                if best is not None:
                                    print(f"[Audio] input: auto-matched earphone mic '{best[1]}' for '{def_out_name}'")
                                    return best

                    # Resolve Windows default output on verified working host API
                    if not want_in and def_out_name:
                        for api_filter in orders:
                            for idx, dev_name in _candidates(api_filter):
                                if _is_pseudo(dev_name):
                                    continue
                                if (dev_name == def_out_name
                                        or dev_name.startswith(def_out_name[:24])
                                        or def_out_name.startswith(dev_name[:24])):
                                    if _usable(idx, "output"):
                                        return idx, _display_name(dev_name, devices)
                return None, ""

            # ── Explicit saved device name ───────────────────────────────────
            for api_filter in orders:
                partial = None
                for idx, dev_name in _candidates(api_filter):
                    if dev_name == wanted:
                        if _usable(idx, kind):
                            return idx, _display_name(dev_name, devices)
                        continue
                    if partial is None and (dev_name.startswith(wanted[:24])
                                            or wanted.startswith(dev_name[:24])):
                        if _usable(idx, kind):
                            partial = (idx, _display_name(dev_name, devices))
                if partial is not None:
                    return partial

        print(f"[Audio] Saved {kind} device '{wanted}' cannot be opened at "
              f"{_RATES.get(kind)} Hz on any host API — using system default")
        return None, ""
    except Exception as e:
        print(f"[Audio] resolve({kind}) failed: {e} — using system default")
        return None, ""


def resolve(name: str, kind: str):
    """Turn a saved device name into something sounddevice accepts.

    Returns None for "system default" — which is also what we return when the
    saved device is gone, because a missing headset must degrade to the built-in
    speakers, not to a crash on startup."""
    idx, _resolved_name = resolve_with_name(name, kind)
    return idx


# ─────────────────────────────────────────────────────────────────────────────
#  The one question automation cannot answer: is it hearing YOU?
# ─────────────────────────────────────────────────────────────────────────────
#
# Everything above decides whether a device can be OPENED and whether it moves
# audio, and it does that with silence so it can run at startup without anyone
# noticing. That is not the same question as "is my microphone picking up my
# voice", and it never will be: a muted mic, a level slider at zero, a headset
# boom pointing at the ceiling and a privacy shutter all pass every automated
# probe in this file and deliver nothing but a flat line.
#
# Only a person can answer that, by speaking. These two functions are what a
# TEST button in the settings panel calls — measure what actually arrives, and
# play something the user can confirm they heard.
#
# This began life as `ses_testi.py`, a loose script in the project root that
# nobody would ever find. It is here because the moment somebody needs it is
# the moment they have opened AUDIO DEVICES wondering why JARVIS cannot hear
# them.

# Below this peak, nothing usable arrived; below the second, speech is present
# but so quiet that recognition will struggle. Both are int16 sample values,
# taken from the levels a normal speaking voice produces at arm's length.
_SILENT_PEAK = 100
_QUIET_PEAK = 1500


def measure_input(name: str, seconds: float = 3.0, on_level=None) -> dict:
    """Record from one microphone and report what actually arrived.

    -> {"ok", "peak", "level", "verdict"} — `level` is 0.0-1.0 for a meter,
    `verdict` a short English sentence for the panel. Never raises: a device
    that cannot be opened is a result, not an exception, because the user is
    standing in front of the panel waiting for an answer either way.

    `on_level(0.0-1.0)` is called as the audio arrives, roughly twenty times a
    second, so the caller can move a meter WHILE the user is speaking.

    THE METER IS NOT DECORATION.
        The first version recorded for three seconds and then printed a
        verdict. Reported as "I saw no sign of anything, no level, no
        movement" — which is exactly right: three seconds of a static label
        reading "Speak now" is indistinguishable from a test that has hung.
        A person needs to see the thing respond to their voice to believe it
        is listening, and that is the entire point of the button.
    """
    idx = resolve(name, "input")
    peak_seen = [0]
    try:
        import numpy as np
        import sounddevice as sd

        rate = _RATES.get("input", 16000)

        def _cb(indata, _frames, _t, _status):
            block = int(np.abs(np.frombuffer(indata, dtype="int16")).max())                 if len(indata) else 0
            if block > peak_seen[0]:
                peak_seen[0] = block
            if on_level is not None:
                try:
                    on_level(min(1.0, block / 12000.0))
                except Exception:
                    pass

        with _pa_lock:
            stream = sd.RawInputStream(samplerate=rate, channels=1, dtype="int16",
                                       blocksize=int(rate / 20), device=idx,
                                       callback=_cb)
            with stream:
                time.sleep(max(0.5, float(seconds)))
        peak = peak_seen[0]
    except Exception as e:
        return {"ok": False, "peak": 0, "level": 0.0,
                "verdict": f"This microphone could not be opened: {e}"}

    if peak < _SILENT_PEAK:
        verdict = ("Nothing arrived. The microphone is muted, its level is at "
                   "zero, or this is not the device you are speaking into.")
    elif peak < _QUIET_PEAK:
        verdict = ("Very quiet. Speech recognition will struggle — raise the "
                   "input level in the system sound settings.")
    else:
        verdict = "Working. Your voice is coming through clearly."

    return {"ok": peak >= _QUIET_PEAK, "peak": peak,
            "level": min(1.0, peak / 12000.0), "verdict": verdict}


def play_tone(name: str, seconds: float = 0.6) -> dict:
    """Play a short tone through one output so the user can confirm they hear it.

    -> {"ok", "verdict"}. There is no way to check this from software — the
    silent-sink probe above proves audio was CONSUMED, not that a speaker made
    a sound — so the answer comes from the person, and this only makes the
    sound for them to answer about.
    """
    idx = resolve(name, "output")
    try:
        import numpy as np
        import sounddevice as sd

        rate = _RATES.get("output", 24000)
        t = np.linspace(0, seconds, int(rate * seconds), endpoint=False)
        # 660 Hz, well inside every speaker's range and unmistakable against
        # room noise. Quiet enough not to startle somebody wearing headphones.
        tone = (np.sin(2 * np.pi * 660 * t) * 6000).astype(np.int16)
        with _pa_lock:
            sd.play(tone, samplerate=rate, device=idx)
            sd.wait()
    except Exception as e:
        return {"ok": False, "verdict": f"This output could not be played: {e}"}
    return {"ok": True, "verdict": "Tone played. Did you hear it?"}


# ─────────────────────────────────────────────────────────────────────────────
#  Output volume, so the panel can set it where the device is chosen
# ─────────────────────────────────────────────────────────────────────────────
#
# Choosing "Realtek(R) Audio" in this panel and then finding it far too quiet
# means going to the system sound settings, finding the same endpoint again and
# moving a slider there — reported as exactly that annoyance. The volume
# belongs beside the device it applies to.
#
# These delegate to actions/computer_settings, which already owns the
# per-platform volume plumbing and had a real bug in it (see
# _win_endpoint_volume there). One implementation, not two.

def _endpoint_for(name: str):
    """The Windows volume interface for ONE named output, or None.

    WHY THIS IS NOT JUST THE DEFAULT DEVICE
        The first version set the system's default endpoint, so moving the
        slider in this panel changed the volume of whichever output Windows
        happened to consider default — not the one chosen two lines above it.

        That is wrong in a way that matters to how people actually use this:
        somebody puts the assistant on their speakers and keeps everything else
        in their headphones, and a slider labelled with one device silently
        moving the other is worse than having no slider.

        It also explains a complaint that looked separate. The selected output
        here was sitting at 10% while the default endpoint was at 100% — so the
        assistant was almost inaudible, and the slider "fixed" it by turning up
        something the user could not hear either.

    Matching is by the name Windows itself reports, which is the same string
    sounddevice lists, folded so case and punctuation cannot separate them.
    """
    if _platform_is() != "Windows" or not name:
        return None
    try:
        from pycaw.pycaw import AudioUtilities
        wanted = _fold(name)
        if not wanted:
            return None

        # Collect first, decide after. An earlier version returned the first
        # candidate that matched on a PREFIX, which meant a loosely-similar
        # name could win over the exact one simply by coming first in the
        # enumeration — and the slider then moved a device the user had not
        # chosen. Exact wins, always; the prefix pass exists only because MME
        # truncates long names to 31 characters.
        exact, partial = None, None
        for dev in AudioUtilities.GetAllDevices():
            # ONLY ACTIVE ENDPOINTS, AND THIS IS NOT A TIDINESS FILTER.
            #
            # Windows keeps a record of hardware that is no longer here, and it
            # keeps the SAME FRIENDLY NAME. Measured on this machine, two
            # devices answered to "Hoparlör (Realtek(R) Audio)": one
            # NotPresent sitting at 100%, and the real one, Active, at 10%.
            # Matching by name alone returned whichever came first — so the
            # panel read 100% for a speaker that was actually at 10%, and
            # setting it moved a device that does not exist.
            try:
                if str(getattr(dev, "state", "")) != "AudioDeviceState.Active":
                    continue
            except Exception:
                continue
            try:
                # Reading this property activates a COM interface and raises
                # for devices that are unplugged or disabled — normal, and not
                # worth a line in the log for each one.
                endpoint = dev.EndpointVolume
            except Exception:
                continue
            if endpoint is None:
                continue
            try:
                friendly = _fold(getattr(dev, "FriendlyName", "") or "")
            except Exception:
                continue
            if not friendly:
                continue
            if friendly == wanted:
                exact = endpoint
                break
            if partial is None and (friendly.startswith(wanted[:24])
                                    or wanted.startswith(friendly[:24])):
                partial = endpoint
        return exact or partial
    except Exception as e:
        print(f"[Audio] per-device volume unavailable: {e}")
    return None


def _platform_is() -> str:
    import platform
    return platform.system()


def _fold(text: str) -> str:
    """Same reduction core/appidentity uses for application names — imported
    lazily so this module keeps working if that one is ever absent."""
    try:
        from core.appidentity import fold
        return fold(text)
    except Exception:
        return "".join(c for c in str(text or "").lower() if c.isalnum())


def get_output_volume(name: str = "") -> Optional[int]:
    """Volume 0-100 of ONE output, or of the system default when unnamed.

    None where the platform will not report it — macOS and Linux have no
    per-device volume through this plumbing, so a name there falls back to the
    master level rather than silently answering about the wrong device.
    """
    endpoint = _endpoint_for(name)
    if endpoint is not None:
        try:
            return max(0, min(100, round(
                endpoint.GetMasterVolumeLevelScalar() * 100)))
        except Exception as e:
            print(f"[Audio] could not read '{name}' volume: {e}")
    try:
        from actions.computer_settings import volume_get
        return volume_get()
    except Exception as e:
        print(f"[Audio] could not read the output volume: {e}")
        return None


def set_output_volume(percent: int, name: str = "") -> bool:
    """Set ONE output's volume 0-100. -> did it take.

    Falls back to the system master only when the named device cannot be
    reached, and says so, because silently moving a different device's volume
    is the bug this function exists to fix.
    """
    percent = max(0, min(100, int(percent)))
    endpoint = _endpoint_for(name)
    if endpoint is not None:
        try:
            endpoint.SetMasterVolumeLevelScalar(percent / 100.0, None)
            return True
        except Exception as e:
            print(f"[Audio] could not set '{name}' volume: {e}")
    if name:
        print(f"[Audio] '{name}' has no separate volume control here — "
              f"setting the system level instead.")
    try:
        from actions.computer_settings import volume_set, volume_get
        volume_set(percent)
        now = volume_get()
        return now is None or abs(now - percent) <= 3
    except Exception as e:
        print(f"[Audio] could not set the output volume: {e}")
        return False
