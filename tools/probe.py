#!/usr/bin/env python3
"""Send raw packets to a Corsair hidraw node and dump replies.

Usage:
  probe.py list
  probe.py send /dev/hidrawN 02 08 02 0f 00      # report id first; padded to 64 bytes
  probe.py listen /dev/hidrawN [seconds]
"""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from open_corsair.hidraw import Hidraw, enumerate_hidraw  # noqa: E402


def hexs(b: bytes) -> str:
    # trim trailing zeros for readability
    end = len(b)
    while end > 8 and b[end - 1] == 0:
        end -= 1
    return " ".join(f"{x:02x}" for x in b[:end]) + (" …" if end < len(b) else "")


def main() -> None:
    cmd = sys.argv[1] if len(sys.argv) > 1 else "list"
    if cmd == "list":
        for d in enumerate_hidraw():
            page = f"{d.usage_page:#06x}" if d.usage_page is not None else "?"
            print(f"{d.path:15} {d.vid:04x}:{d.pid:04x} if={d.interface} page={page} {d.name}")
    elif cmd == "send":
        path = sys.argv[2]
        data = bytes(int(x, 16) for x in sys.argv[3:])
        data = data + bytes(64 - len(data))
        with Hidraw(path) as h:
            while h.read(0):  # drain
                pass
            h.write(data)
            print(">>", hexs(data))
            t0 = time.time()
            while time.time() - t0 < 0.5:
                r = h.read(0.5)
                if r is None:
                    break
                print("<<", hexs(r))
    elif cmd == "listen":
        path = sys.argv[2]
        secs = float(sys.argv[3]) if len(sys.argv) > 3 else 30
        with Hidraw(path) as h:
            t0 = time.time()
            while time.time() - t0 < secs:
                r = h.read(0.5)
                if r:
                    print(f"{time.time() - t0:7.2f} <<", hexs(r))


if __name__ == "__main__":
    main()
