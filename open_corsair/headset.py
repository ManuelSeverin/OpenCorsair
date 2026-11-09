"""High-level access to Bragi wireless headsets (receiver + headset)."""
from __future__ import annotations

import contextlib
import sys
from dataclasses import dataclass

from .bragi import (
    TARGET_HEADSET,
    TARGET_RECEIVER,
    AncMode,
    BatteryStatus,
    Bragi,
    BragiError,
    Cmd,
    File,
    Mode,
    Prop,
    WheelFunction,
)
if sys.platform == "win32":
    from .winhid import enumerate_hidraw
else:
    from .hidraw import enumerate_hidraw

# receiver PID -> model name
SUPPORTED = {
    0x2A02: "Virtuoso MAX Wireless",
}
BRAGI_USAGE_PAGE = 0xFF42
LED_COUNT = 6        # 0-2 left earcup, 3-5 right earcup
LED_HANDLE = 0


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
    # earcup wheels: a WheelFunction value, 0 = wheel disabled, None = unknown
    wheel_left: int | None = None
    wheel_right: int | None = None


def find_receiver() -> tuple[str, str, str] | None:
    """Return (hidraw path, model name, USB product name) of the first receiver."""
    for info in enumerate_hidraw():
        if info.pid in SUPPORTED and info.usage_page == BRAGI_USAGE_PAGE:
            return info.path, SUPPORTED[info.pid], info.name
    return None


class Headset:
    def __init__(self, path: str, model: str, usb_name: str = ""):
        self.path = path
        self.model = model
        # as used in ALSA/PipeWire device names, e.g. "Corsair_CORSAIR_VIRTUOSO_..."
        self.usb_name = usb_name.replace(" ", "_")
        self.bragi = Bragi(path)
        self._leds_open = False

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

        def wheel(fn_prop: int, en_prop: int) -> int | None:
            fn, en = g(fn_prop, 1), g(en_prop, 1)
            if en == 0:
                return 0
            return fn if fn in WheelFunction._value2member_map_ else None

        return HeadsetSettings(
            sidetone_on=g(Prop.SIDETONE_MUTE, 1) == 0,
            sidetone_level=round((g(Prop.SIDETONE_LEVEL, 2) or 0) / 10),
            anc=AncMode(anc) if anc in AncMode._value2member_map_ else None,
            sleep_minutes=round(sleep_ms / 60000) if sleep_on and sleep_ms else None,
            mic_led=None if mic_led is None else mic_led != 0,
            brightness=None if bright is None else round(bright / 10),
            wheel_left=wheel(Prop.WHEEL_LEFT, Prop.WHEEL_LEFT_ENABLED),
            wheel_right=wheel(Prop.WHEEL_RIGHT, Prop.WHEEL_RIGHT_ENABLED),
        )

    # -- operating mode ----------------------------------------------------
    def mode(self) -> Mode | None:
        try:
            return Mode(self.bragi.get_int(TARGET_HEADSET, Prop.MODE, 1))
        except (BragiError, ValueError):
            return None

    def enter_software_mode(self) -> None:
        """Take control of the headset (like iCUE does while it runs).

        In software mode the headset reports its buttons instead of acting on
        them: the mute button no longer mutes the mic, the host has to do that.
        Only the headset is switched; the receiver stays in hardware mode.
        """
        self.bragi.set_int(TARGET_HEADSET, Prop.MODE, Mode.SOFTWARE, 1)
        self._leds_open = False

    def leave_software_mode(self) -> None:
        self.bragi.set_int(TARGET_HEADSET, Prop.MODE, Mode.HARDWARE, 1)
        self._leds_open = False

    def heartbeat(self) -> None:
        """Keep software mode alive; the headset falls back without host traffic."""
        self.bragi.request(TARGET_HEADSET, Cmd.HEARTBEAT)

    @contextlib.contextmanager
    def software_mode(self):
        """Writes are only accepted in software mode.

        If the headset is already in software mode (the tray holds it there),
        nothing is switched. Otherwise switch temporarily. Note: leaving
        software mode resets the headset's own mic mute.
        """
        with self.bragi.locked():
            if self.mode() == Mode.SOFTWARE:
                yield
                return
            self.enter_software_mode()
            try:
                yield
            finally:
                self.leave_software_mode()

    def write(self, *props: tuple[int, int, int]) -> bool:
        """Write (prop, value, size) tuples to the headset in one session.

        Values that are already set are skipped. Returns True if anything was
        written.
        """
        with self.bragi.locked():
            todo = [(p, v, n) for p, v, n in props if self._get_opt(p, n) != v]
            if not todo:
                return False
            with self.software_mode():
                for prop, value, size in todo:
                    self.bragi.set_int(TARGET_HEADSET, prop, value, size)
            return True

    # Noise cancelling and sidetone exclude each other, as in iCUE: turning
    # one on turns the other off. The headset itself does not enforce this.
    # Transparency mode is left alone.
    def set_sidetone(self, on: bool, level: int | None = None) -> bool:
        props = [(Prop.SIDETONE_MUTE, 0 if on else 1, 1)]
        if level is not None:
            props.append((Prop.SIDETONE_LEVEL, max(0, min(100, level)) * 10, 2))
        if on:
            with self.bragi.locked():
                if self._get_opt(Prop.ANC, 1) == AncMode.ON:
                    props.insert(0, (Prop.ANC, int(AncMode.OFF), 1))
                return self.write(*props)
        return self.write(*props)

    def set_anc(self, mode: AncMode) -> bool:
        props = [(Prop.ANC, int(mode), 1)]
        if mode == AncMode.ON:
            props.append((Prop.SIDETONE_MUTE, 1, 1))
        return self.write(*props)

    def set_sleep(self, minutes: int | None) -> bool:
        if minutes:
            return self.write((Prop.SLEEP_ENABLED, 1, 1), (Prop.SLEEP_TIMEOUT_MS, minutes * 60000, 4))
        else:
            return self.write((Prop.SLEEP_ENABLED, 0, 1))

    def set_mic_led(self, on: bool) -> bool:
        return self.write((Prop.MIC_LED, 2 if on else 0, 1))

    def set_brightness(self, percent: int) -> bool:
        return self.write((Prop.BRIGHTNESS, max(0, min(100, percent)) * 10, 2))

    def set_wheel(self, side: str, function: int) -> bool:
        """side "left"/"right"; function a WheelFunction, or 0 to disable the wheel."""
        fn_prop, en_prop = {
            "left": (Prop.WHEEL_LEFT, Prop.WHEEL_LEFT_ENABLED),
            "right": (Prop.WHEEL_RIGHT, Prop.WHEEL_RIGHT_ENABLED),
        }[side]
        if not function:
            return self.write((en_prop, 0, 1))
        return self.write((fn_prop, int(WheelFunction(function)), 1), (en_prop, 1, 1))

    # -- LEDs ------------------------------------------------------------
    def set_leds(self, colors: list[tuple[int, int, int]]) -> None:
        """Show one (r, g, b) per LED. Software mode only; the brightness
        setting (prop 0x02) still applies on top."""
        data = bytes([0x12, 0x00]) + b"".join(bytes(c) for c in colors[:LED_COUNT])
        args = bytes([LED_HANDLE]) + len(data).to_bytes(4, "little") + data
        with self.bragi.locked():
            if not self._leds_open:
                self._open_leds()
            try:
                self.bragi.request(TARGET_HEADSET, Cmd.WRITE_BEGIN, args)
            except BragiError:
                # the handle is gone, e.g. after the headset was power-cycled
                self._open_leds()
                self.bragi.request(TARGET_HEADSET, Cmd.WRITE_BEGIN, args)

    def _open_leds(self) -> None:
        self._leds_open = False
        open_args = bytes([LED_HANDLE]) + File.LED_BUFFER.to_bytes(2, "little")
        try:
            self.bragi.request(TARGET_HEADSET, Cmd.OPEN, open_args)
        except BragiError:
            # still open from an earlier session: close and open again
            with contextlib.suppress(BragiError):
                self.bragi.request(TARGET_HEADSET, Cmd.CLOSE, bytes([0x01, LED_HANDLE]))
            self.bragi.request(TARGET_HEADSET, Cmd.OPEN, open_args)
        self._leds_open = True

    def forget_leds(self) -> None:
        """Open the LED handle again before the next frame."""
        self._leds_open = False

    def firmware(self) -> str:
        v = self.bragi.get(TARGET_HEADSET, Prop.FIRMWARE)
        return f"{v[0]}.{v[1]}.{int.from_bytes(v[2:4], 'little')}"
