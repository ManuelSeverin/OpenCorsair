"""Minimal hidraw access without external dependencies."""
from __future__ import annotations

import os
import select
from dataclasses import dataclass
from pathlib import Path

CORSAIR_VID = 0x1B1C


@dataclass
class HidrawInfo:
    path: str
    vid: int
    pid: int
    name: str
    interface: int | None
    usage_page: int | None


def _usage_page(descriptor: bytes) -> int | None:
    """Return the first Usage Page item of a report descriptor."""
    i = 0
    while i < len(descriptor):
        prefix = descriptor[i]
        size = (0, 1, 2, 4)[prefix & 0x03]
        if prefix == 0x05 and size == 1:
            return descriptor[i + 1]
        if prefix == 0x06 and size == 2:
            return descriptor[i + 1] | (descriptor[i + 2] << 8)
        i += 1 + size
    return None


def enumerate_hidraw(vid: int | None = CORSAIR_VID) -> list[HidrawInfo]:
    result = []
    for node in sorted(Path("/sys/class/hidraw").glob("hidraw*")):
        uevent = (node / "device" / "uevent").read_text()
        fields = dict(line.split("=", 1) for line in uevent.splitlines() if "=" in line)
        _bus, v, p = fields.get("HID_ID", "0:0:0").split(":")
        v, p = int(v, 16), int(p, 16)
        if vid is not None and v != vid:
            continue
        iface = None
        intf_dir = (node / "device").resolve().parent
        num = intf_dir / "bInterfaceNumber"
        if num.exists():
            iface = int(num.read_text(), 16)
        try:
            page = _usage_page((node / "device" / "report_descriptor").read_bytes())
        except OSError:
            page = None
        result.append(HidrawInfo(f"/dev/{node.name}", v, p, fields.get("HID_NAME", ""), iface, page))
    return result


class Hidraw:
    def __init__(self, path: str):
        self.path = path
        self.fd = os.open(path, os.O_RDWR | os.O_NONBLOCK)

    def close(self) -> None:
        if self.fd >= 0:
            os.close(self.fd)
            self.fd = -1

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    def fileno(self) -> int:
        return self.fd

    def write(self, data: bytes) -> int:
        return os.write(self.fd, data)

    def read(self, timeout: float | None = 1.0, size: int = 1024) -> bytes | None:
        r, _, _ = select.select([self.fd], [], [], timeout)
        if not r:
            return None
        return os.read(self.fd, size)
