#!/usr/bin/env python3
"""Read-only scan of Bragi properties: scan.py /dev/hidrawN 08|09 [max]"""
import sys, time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from open_corsair.hidraw import Hidraw

path, dev = sys.argv[1], int(sys.argv[2], 16)
mx = int(sys.argv[3], 16) if len(sys.argv) > 3 else 0x100
with Hidraw(path) as h:
    while h.read(0.1): pass
    for prop in range(1, mx):
        pkt = bytes([0x02, dev, 0x02, prop & 0xff, prop >> 8]) + bytes(59)
        h.write(pkt)
        t0 = time.time(); resp = None
        while time.time() - t0 < 1.0:
            r = h.read(1.0)
            if r is None: break
            if r[0] == 0x01 and r[2] == 0x02:
                resp = r; break
            else:
                print(f"   async: {r[:16].hex(' ')}")
        if resp is None:
            print(f"{prop:#06x}: timeout"); continue
        if resp[3] != 0:
            continue  # status != 0 -> unsupported
        v = resp[4:12]
        print(f"{prop:#06x}: {v.hex(' ')}  u16={int.from_bytes(v[:2],'little')} u32={int.from_bytes(v[:4],'little')}")
