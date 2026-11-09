"""LED effects, rendered on the host and sent to the headset frame by frame.

This is how iCUE does it in software mode too. Effects work per earcup:
LEDs 0-2 are the left earcup, 3-5 the right one.
"""
from __future__ import annotations

import colorsys
import math

from .headset import LED_COUNT

Color = tuple[int, int, int]

# effect id -> label
EFFECTS = {
    "device": "Headset default",
    "static": "Static",
    "pulse": "Pulse",
    "shift": "Colour shift",
    "rainbow": "Rainbow",
    "off": "Off",
}
ANIMATED = {"pulse", "shift", "rainbow"}
USES_COLORS = {"static", "pulse", "shift"}
# speed id -> (label, seconds per cycle)
SPEEDS = {
    "slow": ("Slow", 8.0),
    "medium": ("Medium", 4.0),
    "fast": ("Fast", 1.6),
}


def parse_color(text: str, fallback: Color = (255, 255, 255)) -> Color:
    t = text.strip().lstrip("#")
    if len(t) != 6:
        return fallback
    try:
        return int(t[0:2], 16), int(t[2:4], 16), int(t[4:6], 16)
    except ValueError:
        return fallback


def format_color(c: Color) -> str:
    return "#{:02x}{:02x}{:02x}".format(*c)


def _scale(c: Color, f: float) -> Color:
    return tuple(round(v * f) for v in c)


def _mix(a: Color, b: Color, f: float) -> Color:
    return tuple(round(x + (y - x) * f) for x, y in zip(a, b))


def render(effect: str, left: Color, right: Color, t: float, period: float) -> list[Color] | None:
    """Colours for all LEDs at time t (seconds). None: leave the LEDs alone."""
    half = LED_COUNT // 2
    phase = (t / period) % 1.0
    wave = (1 - math.cos(2 * math.pi * phase)) / 2  # 0 -> 1 -> 0
    if effect == "static":
        return [left] * half + [right] * half
    if effect == "off":
        return [(0, 0, 0)] * LED_COUNT
    if effect == "pulse":
        f = 0.08 + 0.92 * wave
        return [_scale(left, f)] * half + [_scale(right, f)] * half
    if effect == "shift":
        return [_mix(left, right, wave)] * LED_COUNT
    if effect == "rainbow":
        out = []
        for i in range(LED_COUNT):
            r, g, b = colorsys.hsv_to_rgb((phase + (i % half) / LED_COUNT) % 1.0, 1.0, 1.0)
            out.append((round(r * 255), round(g * 255), round(b * 255)))
        return out
    return None
