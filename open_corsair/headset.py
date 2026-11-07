"""High-level access to Bragi wireless headsets (receiver + headset)."""
from __future__ import annotations

import contextlib
from dataclasses import dataclass

from .bragi import (
    TARGET_HEADSET,
    TARGET_RECEIVER,
    AncMode,
    BatteryStatus,
    Bragi,
    BragiError,
    Mode,
    Prop,
)
from .hidraw import enumerate_hidraw

# receiver PID -> model name
SUPPORTED = {
    0x2A02: "Virtuoso MAX Wireless",
}
BRAGI_USAGE_PAGE = 0xFF42


@dataclass
class HeadsetState:
    connected: bool
    battery: float | None = None          # percent
    status: BatteryStatus = BatteryStatus.UNKNOWN
    voltage_mv: int | None = None
    external_power: bool = False

    @property
    def charging(self) -> bool:
        return self.status == BatteryStatus.CHARGING

    @property
    def on_charger(self) -> bool:
        """Charging, or full while still plugged in."""
        return self.charging or self.external_power or self.status == BatteryStatus.FULL


@dataclass
class HeadsetSettings:
    sidetone_on: bool
    sidetone_level: int          # percent
    anc: AncMode | None
    sleep_minutes: int | None    # None = auto-off disabled
    mic_led: bool | None
    brightness: int | None       # percent


def find_receiver() -> tuple[str, str] | None:
    """Return (hidraw path, model name) of the first supported receiver."""
    for info in enumerate_hidraw():
        if info.pid in SUPPORTED and info.usage_page == BRAGI_USAGE_PAGE:
            return info.path, SUPPORTED[info.pid]
    return None


class Headset:
    def __init__(self, path: str, model: str):
        self.path = path
        self.model = model
        self.bragi = Bragi(path)

    @classmethod
    def open(cls) -> "Headset":
        found = find_receiver()
        if not found:
            raise FileNotFoundError("no supported Corsair receiver found")
        return cls(*found)

    def close(self) -> None:
        self.bragi.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    def is_connected(self) -> bool:
        try:
            return bool(self.bragi.get_int(TARGET_RECEIVER, Prop.CONNECTION) & 0x02)
        except BragiError:
            # Fall back: if the headset answers, it is connected.
            try:
                self.bragi.get(TARGET_HEADSET, Prop.PID)
                return True
            except BragiError:
                return False

    def read_state(self) -> HeadsetState:
        if not self.is_connected():
            return HeadsetState(connected=False)
        b = self.bragi
        level = b.get_int(TARGET_HEADSET, Prop.BATTERY_LEVEL, 2)
        if not 0 <= level <= 1000:
            # the receiver occasionally answers with garbage; retry once
            level = b.get_int(TARGET_HEADSET, Prop.BATTERY_LEVEL, 2)
        level = min(level, 1000) / 10
        try:
            status = BatteryStatus(b.get_int(TARGET_HEADSET, Prop.BATTERY_STATUS, 1))
        except ValueError:
            status = BatteryStatus.UNKNOWN
        try:
            mv = b.get_int(TARGET_HEADSET, Prop.BATTERY_MV, 2)
        except BragiError:
            mv = None
        try:
            ext = b.get_int(TARGET_HEADSET, Prop.EXTERNAL_POWER, 1) != 0
        except BragiError:
            ext = False
        return HeadsetState(True, level, status, mv, ext)

    # -- settings --------------------------------------------------------
    def _get_opt(self, prop: int, size: int = 4) -> int | None:
        try:
            return self.bragi.get_int(TARGET_HEADSET, prop, size)
        except BragiError:
            return None

    def read_settings(self) -> HeadsetSettings:
        g = self._get_opt
        anc = g(Prop.ANC, 1)
        mic_led = g(Prop.MIC_LED, 1)
        bright = g(Prop.BRIGHTNESS, 2)
        sleep_on = g(Prop.SLEEP_ENABLED, 1)
        sleep_ms = g(Prop.SLEEP_TIMEOUT_MS)
        return HeadsetSettings(
            sidetone_on=g(Prop.SIDETONE_MUTE, 1) == 0,
            sidetone_level=round((g(Prop.SIDETONE_LEVEL, 2) or 0) / 10),
            anc=AncMode(anc) if anc in AncMode._value2member_map_ else None,
            sleep_minutes=round(sleep_ms / 60000) if sleep_on and sleep_ms else None,
            mic_led=None if mic_led is None else mic_led != 0,
            brightness=None if bright is None else round(bright / 10),
        )

    @contextlib.contextmanager
    def software_mode(self):
        """Writes are only accepted in software mode; restore hardware mode after.

        Settings written this way stay stored on the headset.
        """
        b = self.bragi
        with b.locked():
            b.set_int(TARGET_RECEIVER, Prop.MODE, Mode.SOFTWARE, 1)
            b.set_int(TARGET_HEADSET, Prop.MODE, Mode.SOFTWARE, 1)
            try:
                yield
            finally:
                b.set_int(TARGET_HEADSET, Prop.MODE, Mode.HARDWARE, 1)
                b.set_int(TARGET_RECEIVER, Prop.MODE, Mode.HARDWARE, 1)

    def write(self, *props: tuple[int, int, int]) -> None:
        """Write (prop, value, size) tuples to the headset in one session."""
        with self.software_mode():
            for prop, value, size in props:
                self.bragi.set_int(TARGET_HEADSET, prop, value, size)

    def set_sidetone(self, on: bool, level: int | None = None) -> None:
        props = [(Prop.SIDETONE_MUTE, 0 if on else 1, 1)]
        if level is not None:
            props.append((Prop.SIDETONE_LEVEL, max(0, min(100, level)) * 10, 2))
        self.write(*props)

    def set_anc(self, mode: AncMode) -> None:
        self.write((Prop.ANC, int(mode), 1))

    def set_sleep(self, minutes: int | None) -> None:
        if minutes:
            self.write((Prop.SLEEP_ENABLED, 1, 1), (Prop.SLEEP_TIMEOUT_MS, minutes * 60000, 4))
        else:
            self.write((Prop.SLEEP_ENABLED, 0, 1))

    def set_mic_led(self, on: bool) -> None:
        self.write((Prop.MIC_LED, 2 if on else 0, 1))

    def set_brightness(self, percent: int) -> None:
        self.write((Prop.BRIGHTNESS, max(0, min(100, percent)) * 10, 2))

    def firmware(self) -> str:
        v = self.bragi.get(TARGET_HEADSET, Prop.FIRMWARE)
        return f"{v[0]}.{v[1]}.{int.from_bytes(v[2:4], 'little')}"
