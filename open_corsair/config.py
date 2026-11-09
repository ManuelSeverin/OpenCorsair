"""Small persistent user settings (~/.config/open-corsair/config.json,
%APPDATA%\\open-corsair\\config.json on Windows)."""
from __future__ import annotations

import json
import os
from pathlib import Path

DEFAULTS = {
    "mute_sound": "soft",  # off | soft | beeps
    # LED effect, see lighting.EFFECTS; "device" leaves the LEDs alone
    "led_effect": "device",
    "led_left": "#ff3c00",
    "led_right": "#ff3c00",
    "led_speed": "medium",
}


def _path() -> Path:
    if os.name == "nt":
        base = Path(os.environ.get("APPDATA") or Path.home() / "AppData" / "Roaming")
    else:
        base = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    return base / "open-corsair" / "config.json"


def load() -> dict:
    try:
        data = json.loads(_path().read_text())
    except (OSError, ValueError):
        data = {}
    return {**DEFAULTS, **{k: v for k, v in data.items() if k in DEFAULTS}}


def save(cfg: dict) -> None:
    p = _path()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(cfg, indent=2) + "\n")
    tmp.replace(p)
