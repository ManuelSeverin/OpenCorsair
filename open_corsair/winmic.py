"""Host-side microphone mute on Windows (Core Audio, IAudioEndpointVolume).

Same interface as mic.MicControl. The headset microphone is the active
capture endpoint whose name contains the receiver's USB product name, for
example "Microphone (CORSAIR VIRTUOSO MAX WIRELESS Gaming Receiver)".
Calls go through COM vtables with ctypes, so no extra packages are needed.
"""
from __future__ import annotations

import ctypes
import uuid
from ctypes import wintypes

from .mic import SOUND_STYLES, _tone_file

ole32 = ctypes.OleDLL("ole32")
ole32.CoCreateInstance.argtypes = [ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD,
                                   ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p)]
ole32.PropVariantClear.argtypes = [ctypes.c_void_p]
ole32.CoTaskMemFree.argtypes = [ctypes.c_void_p]

CLSCTX_ALL = 0x17
COINIT_MULTITHREADED = 0x0
E_CAPTURE = 1
DEVICE_STATE_ACTIVE = 0x1
VT_LPWSTR = 31


def _guid(s: str) -> ctypes.Array:
    return (ctypes.c_ubyte * 16).from_buffer_copy(uuid.UUID(s).bytes_le)


CLSID_MMDeviceEnumerator = _guid("BCDE0395-E52F-467C-8E3D-C4579291692E")
IID_IMMDeviceEnumerator = _guid("A95664D2-9614-4F35-A746-DE8DB63617E6")
IID_IAudioEndpointVolume = _guid("5CDF2C82-841E-4546-9722-0CF74078229A")


class PROPERTYKEY(ctypes.Structure):
    _fields_ = [("fmtid", ctypes.c_ubyte * 16), ("pid", wintypes.DWORD)]


class PROPVARIANT(ctypes.Structure):
    _fields_ = [("vt", wintypes.USHORT), ("r1", wintypes.USHORT), ("r2", wintypes.USHORT),
                ("r3", wintypes.USHORT), ("ptr", ctypes.c_void_p), ("pad", ctypes.c_void_p)]


PKEY_Device_FriendlyName = PROPERTYKEY(_guid("A45C254E-DF1C-4EFD-8020-67D146A850E0"), 14)


class _Com:
    """A COM interface pointer; methods are called by vtable index."""

    def __init__(self, ptr: ctypes.c_void_p):
        self.ptr = ptr

    def call(self, index: int, *args, argtypes=()):
        vtbl = ctypes.cast(self.ptr, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))).contents
        fn = ctypes.WINFUNCTYPE(ctypes.HRESULT, ctypes.c_void_p, *argtypes)(vtbl[index])
        return fn(self.ptr, *args)

    def release(self) -> None:
        if self.ptr:
            vtbl = ctypes.cast(self.ptr, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))).contents
            ctypes.WINFUNCTYPE(wintypes.ULONG, ctypes.c_void_p)(vtbl[2])(self.ptr)
            self.ptr = None

    def __del__(self):
        try:
            self.release()
        except Exception:
            pass


def _init_com() -> None:
    try:
        ole32.CoInitializeEx(None, COINIT_MULTITHREADED)
    except OSError:
        pass  # already initialised in another mode: fine for these calls


def _capture_endpoints() -> list[tuple[str, _Com]]:
    """(friendly name, IMMDevice) of all active microphones."""
    _init_com()
    ptr = ctypes.c_void_p()
    ole32.CoCreateInstance(CLSID_MMDeviceEnumerator, None, CLSCTX_ALL,
                           IID_IMMDeviceEnumerator, ctypes.byref(ptr))
    enum = _Com(ptr)
    coll_ptr = ctypes.c_void_p()
    # IMMDeviceEnumerator::EnumAudioEndpoints
    enum.call(3, E_CAPTURE, DEVICE_STATE_ACTIVE, ctypes.byref(coll_ptr),
              argtypes=(ctypes.c_int, wintypes.DWORD, ctypes.POINTER(ctypes.c_void_p)))
    coll = _Com(coll_ptr)
    count = wintypes.UINT()
    coll.call(3, ctypes.byref(count), argtypes=(ctypes.POINTER(wintypes.UINT),))
    result = []
    for i in range(count.value):
        dev_ptr = ctypes.c_void_p()
        coll.call(4, i, ctypes.byref(dev_ptr), argtypes=(wintypes.UINT, ctypes.POINTER(ctypes.c_void_p)))
        dev = _Com(dev_ptr)
        result.append((_friendly_name(dev), dev))
    return result


def _friendly_name(dev: _Com) -> str:
    store_ptr = ctypes.c_void_p()
    try:
        # IMMDevice::OpenPropertyStore(STGM_READ)
        dev.call(4, 0, ctypes.byref(store_ptr), argtypes=(wintypes.DWORD, ctypes.POINTER(ctypes.c_void_p)))
    except OSError:
        return ""
    store = _Com(store_ptr)
    pv = PROPVARIANT()
    try:
        # IPropertyStore::GetValue
        store.call(5, ctypes.byref(PKEY_Device_FriendlyName), ctypes.byref(pv),
                   argtypes=(ctypes.POINTER(PROPERTYKEY), ctypes.POINTER(PROPVARIANT)))
        return ctypes.wstring_at(pv.ptr) if pv.vt == VT_LPWSTR and pv.ptr else ""
    except OSError:
        return ""
    finally:
        ole32.PropVariantClear(ctypes.byref(pv))
        store.release()


def _matches(name: str, usb_name: str) -> bool:
    """Windows may cut long USB names short, so compare a prefix only."""
    name = name.lower().replace("_", " ")
    usb = usb_name.lower().replace("_", " ").removeprefix("corsair corsair").strip()
    return bool(usb) and usb[:24] in name


class MicControl:
    def __init__(self, usb_name: str, sound: str = "soft"):
        self.usb_name = usb_name
        self.sound = sound
        self.available = True
        self._volume: _Com | None = None   # cached IAudioEndpointVolume

    def _endpoint_volume(self) -> _Com | None:
        if self._volume is not None:
            return self._volume
        try:
            endpoints = _capture_endpoints()
        except OSError:
            return None
        # the receiver's own name first, any Corsair microphone as a fallback
        # ("Monitor of …" sources only exist under Wine/PipeWire, never the mic)
        endpoints = [(n, d) for n, d in endpoints if "monitor of" not in n.lower()]
        picks = [d for n, d in endpoints if _matches(n, self.usb_name)]
        picks += [d for n, d in endpoints if "corsair" in n.lower()]
        for dev in picks[:1]:
                ptr = ctypes.c_void_p()
                try:
                    # IMMDevice::Activate(IID_IAudioEndpointVolume)
                    dev.call(3, IID_IAudioEndpointVolume, CLSCTX_ALL, None, ctypes.byref(ptr),
                             argtypes=(ctypes.c_void_p, wintypes.DWORD, ctypes.c_void_p,
                                       ctypes.POINTER(ctypes.c_void_p)))
                except OSError:
                    return None
                self._volume = _Com(ptr)
                return self._volume
        return None

    def _with_volume(self, index: int, *args, argtypes=()):
        """Call an IAudioEndpointVolume method; look the device up again once
        if the cached one is gone (unplugged, driver restart)."""
        for _ in range(2):
            vol = self._endpoint_volume()
            if vol is None:
                return None
            try:
                vol.call(index, *args, argtypes=argtypes)
                return True
            except OSError:
                self._volume = None
        return None

    def is_muted(self) -> bool | None:
        muted = wintypes.BOOL()
        # IAudioEndpointVolume::GetMute
        if self._with_volume(15, ctypes.byref(muted), argtypes=(ctypes.POINTER(wintypes.BOOL),)) is None:
            return None
        return bool(muted.value)

    def set_muted(self, muted: bool) -> bool:
        # IAudioEndpointVolume::SetMute
        if self._with_volume(14, bool(muted), None, argtypes=(wintypes.BOOL, ctypes.c_void_p)) is None:
            return False
        self.play_feedback(muted)
        return True

    def toggle(self) -> bool | None:
        cur = self.is_muted()
        if cur is None:
            return None
        return not cur if self.set_muted(not cur) else cur

    def play_feedback(self, muted: bool) -> None:
        """Short sound on the default playback device: falling = muted, rising = unmuted."""
        if self.sound not in SOUND_STYLES or self.sound == "off":
            return
        import winsound
        winsound.PlaySound(str(_tone_file(self.sound, muted)),
                           winsound.SND_FILENAME | winsound.SND_ASYNC | winsound.SND_NODEFAULT)
