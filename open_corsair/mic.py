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


SOUND_STYLES = ("off", "soft", "beeps")
RATE = 48000


def _soft(rising: bool) -> list[float]:
    """One short, quiet gliding blip with a soft decay."""
    n = int(RATE * 0.12)
    f0, f1 = (392.0, 587.0) if rising else (587.0, 392.0)
    out, phase = [], 0.0
    for i in range(n):
        t = i / n
        phase += 2 * math.pi * (f0 + (f1 - f0) * t) / RATE
        env = min(1.0, i / 480) * math.exp(-4.0 * t)  # 10 ms attack, smooth decay
        out.append(0.12 * env * math.sin(phase))
    return out


def _beeps(rising: bool) -> list[float]:
    out = []
    for f in ((587, 880) if rising else (880, 587)):
        n = int(RATE * 0.09)
        out += [0.27 * min(1.0, i / 240, (n - i) / 240) * math.sin(2 * math.pi * f * i / RATE)
                for i in range(n)]
        out += [0.0] * int(RATE * 0.04)
    return out


def _tone_file(style: str, muted: bool) -> Path:
    """Write (once) the feedback sound as a WAV file in the cache."""
    cache = Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache")) / "open-corsair"
    path = cache / f"{style}-{'muted' if muted else 'unmuted'}-v2.wav"
    if path.exists():
        return path
    cache.mkdir(parents=True, exist_ok=True)
    samples = (_soft if style == "soft" else _beeps)(rising=not muted)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(RATE)
        w.writeframes(b"".join(struct.pack("<h", int(32767 * x)) for x in samples))
    return path


class MicControl:
    def __init__(self, usb_name: str, sound: str = "soft"):
        # e.g. "Corsair_CORSAIR_VIRTUOSO_MAX_WIRELESS_Gaming_Receiver"
        self.usb_name = usb_name
        self.sound = sound
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
        """Short sound in the headset: falling = muted, rising = unmuted."""
        if self.sound not in SOUND_STYLES or self.sound == "off" or not shutil.which("paplay"):
            return
        cmd = ["paplay", str(_tone_file(self.sound, muted))]
        if sink := self.sink():
            cmd[1:1] = ["-d", sink]
        subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
