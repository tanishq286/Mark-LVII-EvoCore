"""
Windows WASAPI shared-mode RAW audio capture (AUDCLNT_STREAMOPTIONS_RAW).

Why this exists
---------------
Laptop OEM audio APOs such as Lenovo's Elevoc AI Noise Cancellation (ELEVOCAPO)
act as a neural voice gate on the default Windows capture pipeline (MME,
DirectSound, and standard shared WASAPI). They pass speech cleanly while
suppressing non-speech transients like hand claps to digital zero.

Opening an IAudioClient2 capture stream with AUDCLNT_STREAMOPTIONS_RAW = 1 in
shared mode bypasses vendor APO processing without exclusive-mode lockouts and
without requiring Administrator privileges, while still letting JARVIS's main
mic stream run normally.
"""
from __future__ import annotations

import ctypes
from ctypes import POINTER, Structure, byref, c_int32, c_int64, c_ubyte, c_uint16, c_uint32, c_uint64, c_void_p, cast
import sys
import threading
import warnings
from typing import Callable

import numpy as np

AUDCLNT_STREAMFLAGS_NOPERSIST = 0x00000000
AUDCLNT_STREAMOPTIONS_RAW = 0x00000001
AUDCLNT_BUFFERFLAGS_SILENT = 0x00000002


class AudioClientProperties(Structure):
    _fields_ = [
        ("cbSize", c_uint32),
        ("bIsOffload", c_int32),
        ("eCategory", c_int32),
        ("Options", c_uint32),
    ]


class WAVEFORMATEX(Structure):
    _fields_ = [
        ("wFormatTag", c_uint16),
        ("nChannels", c_uint16),
        ("nSamplesPerSec", c_uint32),
        ("nAvgBytesPerSec", c_uint32),
        ("nBlockAlign", c_uint16),
        ("wBitsPerSample", c_uint16),
        ("cbSize", c_uint16),
    ]


def _build_com_types():
    from comtypes import COMMETHOD, GUID, HRESULT, IUnknown

    class IAudioClient(IUnknown):
        _iid_ = GUID("{1CB9AD4C-DBFA-4c32-B178-C2F568A703B2}")
        _methods_ = [
            COMMETHOD(
                [],
                HRESULT,
                "Initialize",
                (["in"], c_int32, "ShareMode"),
                (["in"], c_uint32, "StreamFlags"),
                (["in"], c_int64, "hnsBufferDuration"),
                (["in"], c_int64, "hnsPeriodicity"),
                (["in"], POINTER(WAVEFORMATEX), "pFormat"),
                (["in"], c_void_p, "AudioSessionGuid"),
            ),
            COMMETHOD([], HRESULT, "GetBufferSize", (["out"], POINTER(c_uint32), "pNumBufferFrames")),
            COMMETHOD([], HRESULT, "GetStreamLatency", (["out"], POINTER(c_int64), "phnsLatency")),
            COMMETHOD([], HRESULT, "GetCurrentPadding", (["out"], POINTER(c_uint32), "pNumPaddingFrames")),
            COMMETHOD(
                [],
                HRESULT,
                "IsFormatSupported",
                (["in"], c_int32, "ShareMode"),
                (["in"], POINTER(WAVEFORMATEX), "pFormat"),
                (["out"], POINTER(POINTER(WAVEFORMATEX)), "ppClosestMatch"),
            ),
            COMMETHOD([], HRESULT, "GetMixFormat", (["out"], POINTER(POINTER(WAVEFORMATEX)), "ppDeviceFormat")),
            COMMETHOD(
                [],
                HRESULT,
                "GetDevicePeriod",
                (["out"], POINTER(c_int64), "phnsDefaultDevicePeriod"),
                (["out"], POINTER(c_int64), "phnsMinimumDevicePeriod"),
            ),
            COMMETHOD([], HRESULT, "Start"),
            COMMETHOD([], HRESULT, "Stop"),
            COMMETHOD([], HRESULT, "Reset"),
            COMMETHOD([], HRESULT, "SetEventHandle", (["in"], c_void_p, "eventHandle")),
            COMMETHOD(
                [],
                HRESULT,
                "GetService",
                (["in"], POINTER(GUID), "riid"),
                (["out"], POINTER(c_void_p), "ppv"),
            ),
        ]

    class IAudioClient2(IAudioClient):
        _iid_ = GUID("{726778CD-F60A-4eda-82DE-E47610CD78AA}")
        _methods_ = [
            COMMETHOD(
                [],
                HRESULT,
                "IsOffloadCapable",
                (["in"], c_int32, "Category"),
                (["out"], POINTER(c_int32), "pbOffloadCapable"),
            ),
            COMMETHOD(
                [],
                HRESULT,
                "SetClientProperties",
                (["in"], POINTER(AudioClientProperties), "pProperties"),
            ),
            COMMETHOD(
                [],
                HRESULT,
                "GetBufferSizeLimits",
                (["in"], POINTER(WAVEFORMATEX), "pFormat"),
                (["in"], c_int32, "bEventDriven"),
                (["out"], POINTER(c_int64), "phnsMinBufferDuration"),
                (["out"], POINTER(c_int64), "phnsMaxBufferDuration"),
            ),
        ]

    class IAudioCaptureClient(IUnknown):
        _iid_ = GUID("{C8ADBD64-E71E-48a0-A4DE-185C395CD317}")
        _methods_ = [
            COMMETHOD(
                [],
                HRESULT,
                "GetBuffer",
                (["out"], POINTER(POINTER(c_ubyte)), "ppData"),
                (["out"], POINTER(c_uint32), "pNumFramesToRead"),
                (["out"], POINTER(c_uint32), "pdwFlags"),
                (["out"], POINTER(c_uint64), "pu64DevicePosition"),
                (["out"], POINTER(c_uint64), "pu64QPCPosition"),
            ),
            COMMETHOD([], HRESULT, "ReleaseBuffer", (["in"], c_uint32, "NumFramesRead")),
            COMMETHOD([], HRESULT, "GetNextPacketSize", (["out"], POINTER(c_uint32), "pNumFramesInNextPacket")),
        ]

    return IAudioClient2, IAudioCaptureClient


def _friendly_name(AudioUtilities, dev) -> str:
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            ad = AudioUtilities.CreateDevice(dev)
            return str(getattr(ad, "FriendlyName", "") or "").strip()
    except Exception:
        return ""


def _pick_endpoint(AudioUtilities, EDataFlow, ERole, DEVICE_STATE, device_name: str):
    """Resolve `device_name` among active capture endpoints, or return default."""
    enum = AudioUtilities.GetDeviceEnumerator()
    want = (device_name or "").strip().lower()
    if want:
        try:
            coll = enum.EnumAudioEndpoints(EDataFlow.eCapture.value, DEVICE_STATE.ACTIVE.value)
            count = coll.GetCount()
            partial = None
            for i in range(count):
                cand = coll.Item(i)
                fname = _friendly_name(AudioUtilities, cand)
                low = fname.lower()
                if not low:
                    continue
                if low == want:
                    return cand, fname
                if partial is None and (want in low or low in want):
                    partial = (cand, fname)
            if partial is not None:
                return partial
        except Exception:
            pass
    default_dev = enum.GetDefaultAudioEndpoint(EDataFlow.eCapture.value, ERole.eConsole.value)
    return default_dev, _friendly_name(AudioUtilities, default_dev) or "Default capture"


class WasapiRawCapture:
    """Captures shared-mode WASAPI RAW audio on a background COM thread."""

    def __init__(
        self,
        on_chunk: Callable[[np.ndarray], None],
        device_name: str = "",
        target_rate: int = 16000,
        logger: Callable[[str], None] = print,
    ):
        self._on_chunk = on_chunk
        self._device_name = (device_name or "").strip()
        self._target_rate = max(8000, int(target_rate))
        self._logger = logger

        self._thread: threading.Thread | None = None
        self._stop_event = threading.Event()
        self._ready_event = threading.Event()
        self._active = False
        self._device_label = ""

    @property
    def is_active(self) -> bool:
        return self._active and self._thread is not None and self._thread.is_alive()

    @property
    def device_label(self) -> str:
        return self._device_label

    def start(self) -> bool:
        """Open WASAPI RAW capture synchronously (bounded 2s). Returns True if active."""
        if sys.platform != "win32":
            return False
        if self.is_active:
            return True
        self.stop()
        self._stop_event.clear()
        self._ready_event.clear()
        self._active = False
        self._thread = threading.Thread(
            target=self._run, daemon=True, name="WasapiRawCapture"
        )
        self._thread.start()
        self._ready_event.wait(timeout=2.0)
        return self.is_active

    def stop(self) -> None:
        self._stop_event.set()
        self._active = False
        t, self._thread = self._thread, None
        if t is not None and t.is_alive() and threading.current_thread() is not t:
            t.join(timeout=1.5)

    def _run(self) -> None:
        comtypes = None
        ac = None
        cap = None
        dev = None
        try:
            import comtypes as _comtypes
            from comtypes import CLSCTX_ALL
            from pycaw.constants import DEVICE_STATE, EDataFlow, ERole
            from pycaw.pycaw import AudioUtilities

            comtypes = _comtypes
            comtypes.CoInitialize()
            IAudioClient2, IAudioCaptureClient = _build_com_types()

            dev, label = _pick_endpoint(
                AudioUtilities, EDataFlow, ERole, DEVICE_STATE, self._device_name
            )
            self._device_label = label

            ac = cast(dev.Activate(IAudioClient2._iid_, CLSCTX_ALL, None), POINTER(IAudioClient2))
            props = AudioClientProperties(
                ctypes.sizeof(AudioClientProperties),
                0,
                0,
                AUDCLNT_STREAMOPTIONS_RAW,
            )
            ac.SetClientProperties(byref(props))

            fmt_ptr = ac.GetMixFormat()
            try:
                fmt = fmt_ptr.contents
                native_sr = int(fmt.nSamplesPerSec)
                n_ch = max(1, int(fmt.nChannels))
                bits = int(fmt.wBitsPerSample)
                block_align = max(1, int(fmt.nBlockAlign))
                if bits not in (16, 32) or native_sr <= 0:
                    raise RuntimeError(f"unsupported mix format: {native_sr}Hz {bits}-bit {n_ch}ch")

                ac.Initialize(0, AUDCLNT_STREAMFLAGS_NOPERSIST, 1_000_000, 0, fmt_ptr, None)
            finally:
                try:
                    ctypes.windll.ole32.CoTaskMemFree(fmt_ptr)
                except Exception:
                    pass
            cap = cast(ac.GetService(byref(IAudioCaptureClient._iid_)), POINTER(IAudioCaptureClient))
            ac.Start()
            self._active = True
            self._ready_event.set()
            self._logger(
                f"Double-clap: WASAPI RAW active on '{label}' ({native_sr}Hz->{self._target_rate}Hz)."
            )

            rem = np.empty(0, dtype=np.float32)
            ratio_int = native_sr // self._target_rate if native_sr % self._target_rate == 0 else 0

            while not self._stop_event.is_set():
                try:
                    pkt_frames = cap.GetNextPacketSize()
                except Exception as exc:
                    self._logger(f"Double-clap: WASAPI RAW stream ended ({exc}).")
                    break

                while pkt_frames > 0 and not self._stop_event.is_set():
                    data_p, frames, flags, _, _ = cap.GetBuffer()
                    mono = None
                    try:
                        if frames > 0:
                            if flags & AUDCLNT_BUFFERFLAGS_SILENT:
                                mono = np.zeros(frames, dtype=np.float32)
                            else:
                                raw_bytes = ctypes.string_at(data_p, frames * block_align)
                                if bits == 32:
                                    arr = np.frombuffer(raw_bytes, dtype=np.float32).reshape(-1, n_ch)
                                    mono = (arr[:, 0] * 32767.0).astype(np.float32)
                                else:
                                    arr = np.frombuffer(raw_bytes, dtype=np.int16).reshape(-1, n_ch)
                                    mono = arr[:, 0].astype(np.float32)
                    finally:
                        cap.ReleaseBuffer(frames)

                    if mono is not None and mono.size > 0:
                        if ratio_int == 1:
                            pcm = np.clip(mono, -32767.0, 32767.0).astype(np.int16)
                            self._on_chunk(pcm)
                        elif ratio_int > 1:
                            buf = np.concatenate((rem, mono)) if rem.size else mono
                            usable = (buf.size // ratio_int) * ratio_int
                            if usable > 0:
                                down = buf[:usable].reshape(-1, ratio_int).mean(axis=1)
                                pcm = np.clip(down, -32767.0, 32767.0).astype(np.int16)
                                self._on_chunk(pcm)
                            rem = buf[usable:].copy()
                        else:
                            out_len = max(1, int(round(mono.size * self._target_rate / native_sr)))
                            x_old = np.linspace(0.0, 1.0, mono.size, endpoint=False)
                            x_new = np.linspace(0.0, 1.0, out_len, endpoint=False)
                            down = np.interp(x_new, x_old, mono)
                            pcm = np.clip(down, -32767.0, 32767.0).astype(np.int16)
                            self._on_chunk(pcm)

                    pkt_frames = cap.GetNextPacketSize()

                self._stop_event.wait(0.010)
        except Exception as exc:
            if not self._active:
                self._logger(f"Double-clap: WASAPI RAW open skipped ({exc}).")
            else:
                self._logger(f"Double-clap: WASAPI RAW error ({exc}).")
        finally:
            self._active = False
            self._ready_event.set()
            if ac is not None:
                try:
                    ac.Stop()
                except Exception:
                    pass
            cap = None
            ac = None
            dev = None
            if comtypes is not None:
                try:
                    comtypes.CoUninitialize()
                except Exception:
                    pass
