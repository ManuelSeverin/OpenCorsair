<img src="assets/icon.svg" width="64" height="64" alt="">

# open-corsair

Linux userspace driver for Corsair wireless headsets that use Corsair's
"Bragi" HID protocol. It talks to the USB receiver directly through
`/dev/hidraw`, so no kernel module and no iCUE are needed.

Supported right now:

| Device | Receiver USB ID | Status |
|---|---|---|
| Virtuoso MAX Wireless | `1b1c:2a02` | tested |

## Features

- **Tray indicator** showing the battery level, colour-coded, with a
  charging state and a low-battery notification
- Settings from the tray menu or the command line:
  - noise cancelling: off, on, transparency
  - sidetone: on/off and level
  - auto power-off timer
  - mic mute LED
  - LED brightness (CLI only)

## Install

```sh
./install.sh
```

This does three things:

1. Installs the udev rule `udev/70-open-corsair.rules`, which gives the
   logged-in user access to Corsair hidraw devices. This step needs `sudo`.
2. Installs the `open-corsair` command, with pipx or `pip --user`.
3. Adds an autostart entry so the tray starts at login.

The tray needs PyQt6. On Arch-based systems: `sudo pacman -S python-pyqt6`.

To run without installing:

```sh
python3 -m open_corsair.cli tray
```

## Command line

```console
$ open-corsair status
Virtuoso MAX Wireless: 12% (discharging, 3672 mV, fw 0.37.493)
$ open-corsair status --json
$ open-corsair settings
$ open-corsair set anc transparency        # off | on | transparency
$ open-corsair set sidetone 40             # on | off | 0-100
$ open-corsair set auto-off 10             # off | minutes
$ open-corsair set mic-led on
```

Reading is passive: the headset stays in hardware mode. To write a setting,
the tool switches the receiver and headset to software mode for a moment and
then back to hardware mode. The headset keeps the setting.

**Known limitation:** the headset firmware unmutes the microphone whenever a
setting is written. Re-mute it with the mute button afterwards.

## Protocol notes

See [docs/bragi-protocol.md](docs/bragi-protocol.md). `tools/probe.py`
sends raw packets and `tools/scan.py` lists all readable properties. Both are
useful when adding a new device.

## Credits

Protocol details were checked against
[OpenLinkHub](https://github.com/jurkovic-nikola/OpenLinkHub) and
[HeadsetControl](https://github.com/Sapd/HeadsetControl), and against the
device manifests shipped with iCUE.
