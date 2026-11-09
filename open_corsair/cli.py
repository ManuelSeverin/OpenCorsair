"""Command line: `open-corsair status`."""
from __future__ import annotations

import argparse
import json
import sys

from .bragi import AncMode, BragiError, WheelFunction
from .headset import Headset


def on_off(v: str) -> bool:
    if v.lower() in ("on", "1", "true", "yes"):
        return True
    if v.lower() in ("off", "0", "false", "no"):
        return False
    raise argparse.ArgumentTypeError("expected on/off")


WHEEL_NAMES = {WheelFunction.SYSTEM_VOLUME: "system", WheelFunction.BLUETOOTH_VOLUME: "bluetooth"}


def wheel_function(v: str) -> int:
    v = v.lower()
    if v == "off":
        return 0
    for fn, name in WHEEL_NAMES.items():
        if v == name:
            return fn
    raise ValueError(f"expected system, bluetooth or off, got {v!r}")


def cmd_settings(args) -> int:
    with Headset.open() as hs:
        st = hs.read_settings()
    anc = st.anc.name.lower() if st.anc is not None else "?"
    print(f"sidetone:   {'on' if st.sidetone_on else 'off'} (level {st.sidetone_level}%)")
    print(f"anc:        {anc}")
    print(f"auto-off:   {f'{st.sleep_minutes} min' if st.sleep_minutes else 'off'}")
    print(f"mic led:    {'on' if st.mic_led else 'off'}")
    print(f"brightness: {st.brightness}%")
    for side, v in (("left", st.wheel_left), ("right", st.wheel_right)):
        name = "?" if v is None else "off" if v == 0 else WHEEL_NAMES.get(v, str(v))
        print(f"{side + ' wheel:':<12} {name}")
    return 0


def cmd_set(args) -> int:
    with Headset.open() as hs:
        if not hs.is_connected():
            print("headset not connected", file=sys.stderr)
            return 1
        try:
            if args.what == "sidetone":
                v = args.value.lower()
                if v in ("on", "off"):
                    hs.set_sidetone(v == "on")
                else:
                    hs.set_sidetone(True, int(v))
            elif args.what == "anc":
                hs.set_anc(AncMode[args.value.upper()])
            elif args.what == "auto-off":
                hs.set_sleep(None if args.value.lower() == "off" else int(args.value))
            elif args.what == "mic-led":
                hs.set_mic_led(on_off(args.value))
            elif args.what == "brightness":
                hs.set_brightness(int(args.value))
            elif args.what in ("wheel-left", "wheel-right"):
                hs.set_wheel(args.what.split("-")[1], wheel_function(args.value))
        except (ValueError, KeyError, argparse.ArgumentTypeError) as e:
            print(f"invalid value: {e}", file=sys.stderr)
            return 2
        except BragiError as e:
            print(f"device error: {e}", file=sys.stderr)
            return 1
    return 0


def cmd_status(args) -> int:
    try:
        with Headset.open() as hs:
            st = hs.read_state()
            fw = hs.firmware() if st.connected else None
            model = hs.model
    except PermissionError as e:
        print(f"{e}\nInstall udev/70-open-corsair.rules (see README).", file=sys.stderr)
        return 2
    except FileNotFoundError as e:
        print(e, file=sys.stderr)
        return 1
    if args.json:
        print(json.dumps({
            "model": model,
            "connected": st.connected,
            "battery": st.battery,
            "status": st.status.name.lower(),
            "voltage_mv": st.voltage_mv,
            "firmware": fw,
        }))
    elif not st.connected:
        print(f"{model}: headset not connected")
    else:
        print(f"{model}: {int(st.battery)}% ({st.status.name.lower()})")
    return 0


def main() -> None:
    p = argparse.ArgumentParser(prog="open-corsair")
    sub = p.add_subparsers(dest="cmd")
    s = sub.add_parser("status", help="show headset battery")
    s.add_argument("--json", action="store_true")
    t = sub.add_parser("tray", help="run the system tray indicator")
    t.add_argument("--settings", action="store_true", help="open the settings window on start")
    sub.add_parser("settings", help="show headset settings")
    st = sub.add_parser("set", help="change a headset setting", description=(
        "sidetone on|off|0-100, anc off|on|transparency, auto-off off|<minutes>, "
        "mic-led on|off, brightness 0-100, wheel-left|wheel-right system|bluetooth|off"))
    st.add_argument("what", choices=["sidetone", "anc", "auto-off", "mic-led", "brightness",
                                     "wheel-left", "wheel-right"])
    st.add_argument("value")
    args = p.parse_args()
    if args.cmd == "settings":
        sys.exit(cmd_settings(args))
    if args.cmd == "set":
        sys.exit(cmd_set(args))
    if args.cmd == "tray":
        from .tray import main as tray_main
        tray_main(show_settings=args.settings)
    else:
        if args.cmd is None:
            args.json = False
        sys.exit(cmd_status(args))


if __name__ == "__main__":
    main()
