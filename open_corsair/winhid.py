"""HID access on Windows through hid.dll, with the same interface as hidraw.py.

Windows splits a HID interface into one device per top-level collection. The
Bragi receiver has two: usage 1 (responses, report 1, and requests, report 2)
and usage 2 (notifications, report 3). Requests and their replies both live in
the first one, which is the one with an output report.
"""
from __future__ import annotations

import ctypes
import re
from ctypes import wintypes

from .hidraw import CORSAIR_VID, HidrawInfo

hid = ctypes.WinDLL("hid")
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
cfgmgr32 = ctypes.WinDLL("cfgmgr32")

GENERIC_READ = 0x80000000
GENERIC_WRITE = 0x40000000
FILE_SHARE_READ = 0x1
FILE_SHARE_WRITE = 0x2
OPEN_EXISTING = 3
FILE_FLAG_OVERLAPPED = 0x40000000
INVALID_HANDLE_VALUE = wintypes.HANDLE(-1).value
ERROR_IO_PENDING = 997
WAIT_OBJECT_0 = 0
WAIT_TIMEOUT = 0x102
INFINITE = 0xFFFFFFFF
HIDP_STATUS_SUCCESS = 0x00110000
CM_GET_DEVICE_INTERFACE_LIST_PRESENT = 0


class GUID(ctypes.Structure):
    _fields_ = [("Data1", wintypes.DWORD), ("Data2", wintypes.WORD),
                ("Data3", wintypes.WORD), ("Data4", ctypes.c_ubyte * 8)]


class HIDD_ATTRIBUTES(ctypes.Structure):
    _fields_ = [("Size", wintypes.ULONG), ("VendorID", wintypes.USHORT),
                ("ProductID", wintypes.USHORT), ("VersionNumber", wintypes.USHORT)]


class HIDP_CAPS(ctypes.Structure):
    _fields_ = [("Usage", wintypes.USHORT), ("UsagePage", wintypes.USHORT),
                ("InputReportByteLength", wintypes.USHORT),
                ("OutputReportByteLength", wintypes.USHORT),
                ("FeatureReportByteLength", wintypes.USHORT),
                ("Reserved", wintypes.USHORT * 17),
                ("Counts", wintypes.USHORT * 10)]


class OVERLAPPED(ctypes.Structure):
    _fields_ = [("Internal", ctypes.c_void_p), ("InternalHigh", ctypes.c_void_p),
                ("Offset", wintypes.DWORD), ("OffsetHigh", wintypes.DWORD),
                ("hEvent", wintypes.HANDLE)]


kernel32.CreateFileW.restype = wintypes.HANDLE
kernel32.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p,
                                 wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
kernel32.CreateEventW.restype = wintypes.HANDLE
kernel32.CreateEventW.argtypes = [ctypes.c_void_p, wintypes.BOOL, wintypes.BOOL, wintypes.LPCWSTR]
kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
kernel32.ResetEvent.argtypes = [wintypes.HANDLE]
kernel32.ReadFile.argtypes = [wintypes.HANDLE, ctypes.c_void_p, wintypes.DWORD,
                              ctypes.POINTER(wintypes.DWORD), ctypes.POINTER(OVERLAPPED)]
kernel32.WriteFile.argtypes = kernel32.ReadFile.argtypes
kernel32.GetOverlappedResult.argtypes = [wintypes.HANDLE, ctypes.POINTER(OVERLAPPED),
                                         ctypes.POINTER(wintypes.DWORD), wintypes.BOOL]
kernel32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
kernel32.WaitForSingleObject.restype = wintypes.DWORD
kernel32.CancelIoEx.argtypes = [wintypes.HANDLE, ctypes.POINTER(OVERLAPPED)]
hid.HidD_GetHidGuid.argtypes = [ctypes.POINTER(GUID)]
hid.HidD_GetAttributes.argtypes = [wintypes.HANDLE, ctypes.POINTER(HIDD_ATTRIBUTES)]
hid.HidD_GetPreparsedData.argtypes = [wintypes.HANDLE, ctypes.POINTER(ctypes.c_void_p)]
hid.HidD_FreePreparsedData.argtypes = [ctypes.c_void_p]
hid.HidP_GetCaps.argtypes = [ctypes.c_void_p, ctypes.POINTER(HIDP_CAPS)]
hid.HidP_GetCaps.restype = ctypes.c_long
hid.HidD_GetProductString.argtypes = [wintypes.HANDLE, ctypes.c_void_p, wintypes.ULONG]
hid.HidD_GetManufacturerString.argtypes = hid.HidD_GetProductString.argtypes
cfgmgr32.CM_Get_Device_Interface_List_SizeW.argtypes = [
    ctypes.POINTER(wintypes.ULONG), ctypes.POINTER(GUID), wintypes.LPCWSTR, wintypes.ULONG]
cfgmgr32.CM_Get_Device_Interface_ListW.argtypes = [
    ctypes.POINTER(GUID), wintypes.LPCWSTR, ctypes.c_void_p, wintypes.ULONG, wintypes.ULONG]


def _interface_paths() -> list[str]:
    guid = GUID()
    hid.HidD_GetHidGuid(ctypes.byref(guid))
    for _ in range(3):  # the list can grow between the two calls
        size = wintypes.ULONG()
        if cfgmgr32.CM_Get_Device_Interface_List_SizeW(
                ctypes.byref(size), ctypes.byref(guid), None, CM_GET_DEVICE_INTERFACE_LIST_PRESENT):
            return []
        buf = ctypes.create_unicode_buffer(size.value)
        if not cfgmgr32.CM_Get_Device_Interface_ListW(
                ctypes.byref(guid), None, buf, size.value, CM_GET_DEVICE_INTERFACE_LIST_PRESENT):
            return [p for p in buf[:size.value].split("\0") if p]
    return []


def _open(path: str, access: int, flags: int = 0):
    h = kernel32.CreateFileW(path, access, FILE_SHARE_READ | FILE_SHARE_WRITE,
                             None, OPEN_EXISTING, flags, None)
    return None if h in (None, INVALID_HANDLE_VALUE) else h


def _string(fn, h) -> str:
    buf = ctypes.create_unicode_buffer(128)
    return buf.value.strip() if fn(h, buf, ctypes.sizeof(buf)) else ""


def _caps(h) -> HIDP_CAPS | None:
    pre = ctypes.c_void_p()
    if not hid.HidD_GetPreparsedData(h, ctypes.byref(pre)):
        return None
    try:
        caps = HIDP_CAPS()
        return caps if hid.HidP_GetCaps(pre, ctypes.byref(caps)) == HIDP_STATUS_SUCCESS else None
    finally:
        hid.HidD_FreePreparsedData(pre)


def enumerate_hidraw(vid: int | None = CORSAIR_VID) -> list[HidrawInfo]:
    """HID collections, filtered by vendor. Collections without an output
    report (like the Bragi notification collection) are left out, since
    requests cannot be sent to them."""
    result = []
    for path in _interface_paths():
        m = re.search(r"vid_([0-9a-f]{4})", path, re.I)
        if vid is not None and (not m or int(m.group(1), 16) != vid):
            continue
        h = _open(path, 0)  # no access needed for attributes and caps
        if h is None:
            continue
        try:
            attrs = HIDD_ATTRIBUTES(Size=ctypes.sizeof(HIDD_ATTRIBUTES))
            if not hid.HidD_GetAttributes(h, ctypes.byref(attrs)):
                continue
            caps = _caps(h)
            if caps is None or not caps.OutputReportByteLength:
                continue
            name = _string(hid.HidD_GetProductString, h)
        finally:
            kernel32.CloseHandle(h)
        mi = re.search(r"&mi_([0-9a-f]{2})", path, re.I)
        result.append(HidrawInfo(path, attrs.VendorID, attrs.ProductID, name,
                                 int(mi.group(1), 16) if mi else None, caps.UsagePage))
    return result


class Hidraw:
    """One HID collection opened for overlapped reads and writes."""

    def __init__(self, path: str):
        self.path = path
        h = _open(path, GENERIC_READ | GENERIC_WRITE, FILE_FLAG_OVERLAPPED)
        if h is None:
            err = ctypes.get_last_error()
            raise (PermissionError if err == 5 else OSError)(err, ctypes.FormatError(err), path)
        self.handle = h
        caps = _caps(h)
        self.out_len = caps.OutputReportByteLength if caps else 64
        self.in_len = caps.InputReportByteLength if caps else 64
        self._rbuf = ctypes.create_string_buffer(self.in_len)
        self._rov = OVERLAPPED(hEvent=kernel32.CreateEventW(None, True, False, None))
        self._wov = OVERLAPPED(hEvent=kernel32.CreateEventW(None, True, False, None))
        self._reading = False   # a read is in flight from an earlier timeout

    def close(self) -> None:
        if self.handle is None:
            return
        if self._reading:
            kernel32.CancelIoEx(self.handle, ctypes.byref(self._rov))
            n = wintypes.DWORD()
            kernel32.GetOverlappedResult(self.handle, ctypes.byref(self._rov), ctypes.byref(n), True)
        kernel32.CloseHandle(self.handle)
        kernel32.CloseHandle(self._rov.hEvent)
        kernel32.CloseHandle(self._wov.hEvent)
        self.handle = None

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    def fileno(self) -> int:
        raise OSError("no file descriptor on Windows")

    def write(self, data: bytes) -> int:
        # Windows wants exactly the report length, report id included
        data = data[:self.out_len].ljust(self.out_len, b"\0")
        kernel32.ResetEvent(self._wov.hEvent)
        n = wintypes.DWORD()
        if not kernel32.WriteFile(self.handle, data, len(data), None, ctypes.byref(self._wov)):
            if ctypes.get_last_error() != ERROR_IO_PENDING:
                raise ctypes.WinError(ctypes.get_last_error())
            if kernel32.WaitForSingleObject(self._wov.hEvent, 1000) != WAIT_OBJECT_0:
                kernel32.CancelIoEx(self.handle, ctypes.byref(self._wov))
                kernel32.GetOverlappedResult(self.handle, ctypes.byref(self._wov), ctypes.byref(n), True)
                raise TimeoutError("HID write timed out")
        if not kernel32.GetOverlappedResult(self.handle, ctypes.byref(self._wov), ctypes.byref(n), True):
            raise ctypes.WinError(ctypes.get_last_error())
        return n.value

    def read(self, timeout: float | None = 1.0, size: int = 1024) -> bytes | None:
        """One input report, or None after `timeout` seconds. A read that times
        out stays queued, so no report gets lost between calls."""
        if not self._reading:
            kernel32.ResetEvent(self._rov.hEvent)
            if not kernel32.ReadFile(self.handle, self._rbuf, self.in_len, None, ctypes.byref(self._rov)):
                if ctypes.get_last_error() != ERROR_IO_PENDING:
                    raise ctypes.WinError(ctypes.get_last_error())
            self._reading = True
        ms = INFINITE if timeout is None else max(0, int(timeout * 1000))
        if kernel32.WaitForSingleObject(self._rov.hEvent, ms) != WAIT_OBJECT_0:
            return None
        self._reading = False
        n = wintypes.DWORD()
        if not kernel32.GetOverlappedResult(self.handle, ctypes.byref(self._rov), ctypes.byref(n), False):
            raise ctypes.WinError(ctypes.get_last_error())
        return self._rbuf.raw[:min(n.value, size)]
