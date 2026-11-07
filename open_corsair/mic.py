"""Host-side microphone mute via PipeWire/PulseAudio (pactl).

In software mode the headset no longer mutes its microphone itself. It only
reports the mute button, and the host has to mute the audio source, like iCUE
and OpenLinkHub do.
"""
from __future__ import annotations

import math
import os
import shutil
import struct
import subprocess
import wave
from pathlib import Path


def _pactl(*args: str) -> str:
    return subprocess.run(
        ["pactl", *args], capture_output=True, text=True, timeout=5, check=True
    ).stdout


def _find(kind: str, usb_name: str) -> str | None:
    """Find the ALSA source/sink whose name contains usb_name."""
    try:
        out = _pactl("list", kind, "short")
    except (OSError, subprocess.SubprocessError):
        return None
    for line in out.splitlines():
        parts = line.split("\t")
        if len(parts) > 1 and usb_name in parts[1] and ".monitor" not in parts[1]:
            return parts[1]
    return None


def _tone_file(name: str, freqs: tuple[float, ...]) -> Path:
    """Write (once) a short beep sequence as a WAV file."""
    cache = Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache")) / "open-corsair"
    path = cache / f"{name}.wav"
    if path.exists():
        return path
    cache.mkdir(parents=True, exist_ok=True)
    rate, beep, gap = 48000, 0.09, 0.04
    frames = bytearray()
    for f in freqs:
        n = int(rate * beep)
        for i in range(n):
            env = min(1.0, i / 240, (n - i) / 240)  # short fade in/out
            frames += struct.pack("<h", int(9000 * env * math.sin(2 * math.pi * f * i / rate)))
        frames += bytes(2 * int(rate * gap))
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(bytes(frames))
    return path


class MicControl:
    def __init__(self, usb_name: str):
        # e.g. "Corsair_CORSAIR_VIRTUOSO_MAX_WIRELESS_Gaming_Receiver"
        self.usb_name = usb_name
        self.available = shutil.which("pactl") is not None

    def source(self) -> str | None:
        return _find("sources", self.usb_name) if self.available else None

    def sink(self) -> str | None:
        return _find("sinks", self.usb_name) if self.available else None

    def is_muted(self) -> bool | None:
        src = self.source()
        if not src:
            return None
        try:
            return _pactl("get-source-mute", src).strip().endswith("yes")
        except (OSError, subprocess.SubprocessError):
            return None

    def set_muted(self, muted: bool) -> bool:
        src = self.source()
        if not src:
            return False
        try:
            _pactl("set-source-mute", src, "1" if muted else "0")
        except (OSError, subprocess.SubprocessError):
            return False
        self.play_feedback(muted)
        return True

    def toggle(self) -> bool | None:
        cur = self.is_muted()
        if cur is None:
            return None
        return not cur if self.set_muted(not cur) else cur

    def play_feedback(self, muted: bool) -> None:
        """Short beeps in the headset: falling = muted, rising = unmuted."""
        if not shutil.which("paplay"):
            return
        tone = _tone_file("muted", (880, 587)) if muted else _tone_file("unmuted", (587, 880))
        cmd = ["paplay", str(tone)]
        if sink := self.sink():
            cmd[1:1] = ["-d", sink]
        subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
