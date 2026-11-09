"""Corsair "Bragi" HID protocol (used by newer wireless headsets, mice, keyboards).

Packet layout, observed on a Virtuoso MAX Wireless receiver (1b1c:2a02):

  host -> device  (report 2, 64 bytes incl. report id)
    02 <target> <cmd> <args...>
      target: 0x08 = receiver / wired device, 0x09 = first wireless sub-device
      cmd 0x01 = set property  <prop lo> <prop hi> <value...>
      cmd 0x02 = get property  <prop lo> <prop hi>

  device -> host  (report 1)
    01 <target & 0x07> <cmd> <status> <value...>
      status 0x00 = ok, anything else = error / unsupported

  device -> host  (report 3) asynchronous notifications
"""
from __future__ import annotations

import contextlib
import enum
import os
import sys
import time
from typing import Callable

if sys.platform == "win32":
    from .winhid import Hidraw
else:
    import fcntl

    from .hidraw import Hidraw

REPORT_OUT = 0x02
REPORT_RESPONSE = 0x01
REPORT_EVENT = 0x03
PACKET_SIZE = 64

TARGET_RECEIVER = 0x08
TARGET_HEADSET = 0x09


class Cmd(enum.IntEnum):
    SET = 0x01
    GET = 0x02
    CLOSE = 0x05        # <0x01> <handle>
    WRITE_BEGIN = 0x06  # <handle> <u32 total length> <data...>
    OPEN = 0x0D         # <handle> <u16 file id>
    HEARTBEAT = 0x12


class File(enum.IntEnum):
    """Bragi file ids, opened on a handle with Cmd.OPEN."""
    LED_BUFFER = 0x22   # live LED colours: u16 0x0012, then R G B per LED


class Event(enum.IntEnum):
    """Byte 2 of a report-3 notification."""
    PROPERTY = 0x01   # 03 <src> 01 <prop lo> <prop hi> <value LE...>
    KEY = 0x02        # 03 <src> 02 <key bitmap>: bit 0 = mic/mute button


KEY_MIC = 0x01
KEY_CUSTOM = 0x02   # second button (iCUE: Headset_Custom), unverified


class Mode(enum.IntEnum):
    HARDWARE = 1
    SOFTWARE = 2
    SLEEP = 4


class Prop(enum.IntEnum):
    BRIGHTNESS = 0x02        # LED brightness 0..1000
    MODE = 0x03              # see Mode
    SLEEP_ENABLED = 0x0D
    SLEEP_TIMEOUT_MS = 0x0E
    BATTERY_LEVEL = 0x0F     # tenths of a percent (0..1000)
    BATTERY_STATUS = 0x10    # see BatteryStatus
    VID = 0x11
    PID = 0x12
    FIRMWARE = 0x13
    CONNECTION = 0x36        # receiver: bitmap of connected sub-devices (bit 1 = headset)
    SIDETONE_MUTE = 0x46     # inverted: 0 = sidetone on, 1 = off
    SIDETONE_LEVEL = 0x47    # 0..1000
    EXTERNAL_POWER = 0x48    # u8, non-zero while a charger is connected
    BATTERY_MV = 0x53
    MIC_MUTE = 0xA6
    ANC = 0xD1               # see AncMode
    WHEEL_LEFT = 0xD2
    WHEEL_RIGHT = 0xD3
    MIC_LED = 0xD4           # LED lit while mic is unmuted: 0 = off, 2 = on
    WHEEL_LEFT_ENABLED = 0xF6
    WHEEL_RIGHT_ENABLED = 0xF7


class AncMode(enum.IntEnum):
    OFF = 0
    ON = 1
    TRANSPARENCY = 2


class WheelFunction(enum.IntEnum):
    """What an earcup wheel controls (props 0xd2/0xd3)."""
    SYSTEM_VOLUME = 1
    BLUETOOTH_VOLUME = 2


class BatteryStatus(enum.IntEnum):
    UNKNOWN = 0
    CHARGING = 1
    DISCHARGING = 2
    FULL = 3


class BragiError(Exception):
    pass


class BragiTimeout(BragiError):
    pass


def _lock_path(path: str) -> str:
    run = os.environ.get("XDG_RUNTIME_DIR") or "/tmp"
    return os.path.join(run, f"open-corsair-{os.path.basename(path)}.lock")


class _FileLock:
    """flock on a file in the runtime dir (Linux)."""

    def __init__(self, path: str):
        self.fd = os.open(_lock_path(path), os.O_RDWR | os.O_CREAT, 0o600)

    def acquire(self) -> None:
        fcntl.flock(self.fd, fcntl.LOCK_EX)

    def release(self) -> None:
        fcntl.flock(self.fd, fcntl.LOCK_UN)

    def close(self) -> None:
        if self.fd >= 0:
            os.close(self.fd)
            self.fd = -1


class _MutexLock:
    """Named mutex (Windows). Acquire and release must happen on the same thread."""

    def __init__(self, path: str):
        import ctypes
        import hashlib
        from ctypes import wintypes
        self.k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        self.k32.CreateMutexW.restype = wintypes.HANDLE
        self.k32.CreateMutexW.argtypes = [ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR]
        self.k32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        self.k32.WaitForSingleObject.restype = wintypes.DWORD
        self.k32.ReleaseMutex.argtypes = [wintypes.HANDLE]
        self.k32.CloseHandle.argtypes = [wintypes.HANDLE]
        # device paths contain backslashes, which mutex names must not
        tag = hashlib.sha1(path.lower().encode()).hexdigest()[:16]
        self.handle = self.k32.CreateMutexW(None, False, f"Local\\open-corsair-{tag}")
        if not self.handle:
            raise ctypes.WinError(ctypes.get_last_error())

    def acquire(self) -> None:
        # WAIT_OBJECT_0 or WAIT_ABANDONED (owner died): both mean we own it
        if self.k32.WaitForSingleObject(self.handle, 0xFFFFFFFF) not in (0, 0x80):
            raise OSError("could not take the device lock")

    def release(self) -> None:
        self.k32.ReleaseMutex(self.handle)

    def close(self) -> None:
        if self.handle:
            self.k32.CloseHandle(self.handle)
            self.handle = None


class Bragi:
    """Request/response access to one Bragi hidraw node.

    Replies are delivered to every process that has the hidraw node open and
    carry no request id, so requests from different processes are serialised
    with an flock (a named mutex on Windows).
    """

    def __init__(self, path: str, timeout: float = 1.0):
        self.dev = Hidraw(path)
        self.timeout = timeout
        self.on_event: Callable[[bytes], None] | None = None
        self._lock = (_MutexLock if sys.platform == "win32" else _FileLock)(path)
        self._lock_depth = 0

    def close(self) -> None:
        self.dev.close()
        self._lock.close()

    @contextlib.contextmanager
    def locked(self):
        """Hold the cross-process lock (re-entrant within this object)."""
        if self._lock_depth == 0:
            self._lock.acquire()
        self._lock_depth += 1
        try:
            yield
        finally:
            self._lock_depth -= 1
            if self._lock_depth == 0:
                self._lock.release()

    def fileno(self) -> int:
        return self.dev.fileno()

    def drain(self) -> None:
        """Handle everything already queued (events, stale replies)."""
        while (pkt := self.dev.read(0)) is not None:
            self._dispatch(pkt)

    def _dispatch(self, pkt: bytes) -> None:
        if pkt and pkt[0] == REPORT_EVENT and self.on_event:
            self.on_event(pkt)

    def request(self, target: int, cmd: int, args: bytes = b"") -> bytes:
        with self.locked():
            return self._request(target, cmd, args)

    def _request(self, target: int, cmd: int, args: bytes) -> bytes:
        self.drain()
        pkt = bytes([REPORT_OUT, target, cmd]) + args
        self.dev.write(pkt.ljust(PACKET_SIZE, b"\0"))
        deadline = time.monotonic() + self.timeout
        while (left := deadline - time.monotonic()) > 0:
            r = self.dev.read(left)
            if r is None:
                break
            if r[0] == REPORT_RESPONSE and r[2] == cmd and r[1] == target & 0x07:
                if r[3] != 0:
                    raise BragiError(f"target {target:#x} cmd {cmd:#x}: status {r[3]:#x}")
                return r[4:]
            self._dispatch(r)
        raise BragiTimeout(f"no reply from target {target:#x} cmd {cmd:#x}")

    def get(self, target: int, prop: int) -> bytes:
        return self.request(target, Cmd.GET, prop.to_bytes(2, "little"))

    def get_int(self, target: int, prop: int, size: int = 4) -> int:
        return int.from_bytes(self.get(target, prop)[:size], "little")

    def set_int(self, target: int, prop: int, value: int, size: int = 4) -> None:
        self.request(target, Cmd.SET, prop.to_bytes(2, "little") + value.to_bytes(size, "little"))
