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
- **Mic mute** via the headset button, handled on the host like iCUE does
- Settings from the tray menu or the command line:
  - noise cancelling: off, on, transparency
  - sidetone: on/off and level
  - auto power-off timer
  - mic mute LED
  - LED brightness (CLI only)

## Install

### AppImage (recommended)

1. Download `open-corsair-<version>-x86_64.AppImage` and
   `70-open-corsair.rules` from the
   [releases page](https://github.com/ManuelSeverin/open-corsair/releases).
2. Install the udev rule once. It gives the logged-in user access to Corsair
   HID devices:

   ```sh
   sudo install -m 644 70-open-corsair.rules /etc/udev/rules.d/
   sudo udevadm control --reload && sudo udevadm trigger --subsystem-match=hidraw
   ```

3. Make the AppImage executable and start it. Without arguments it starts
   the tray, and with arguments it works as the CLI (`… status`, `… set anc on`):

   ```sh
   chmod +x open-corsair-*-x86_64.AppImage
   ./open-corsair-*-x86_64.AppImage
   ```

4. For autostart, add it to your desktop's autostart, e.g. in KDE under
   System Settings → Autostart.

Build it yourself with `packaging/build-appimage.sh`. The AppImage bundles
Python and Qt. The host needs FUSE plus `pactl`/`paplay` (PipeWire or
PulseAudio) for mic mute.

### From source

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

### How mute works

While the tray runs it keeps the headset in *software mode*, just like iCUE.
In this mode the headset reports its mute button instead of muting by itself.
The tray then mutes the headset's microphone in PipeWire, plays a short sound
(falling = muted, rising = unmuted) and shows a red badge on the icon. The
sound can be switched between Soft, Beeps and Off in the tray menu ("Mute
sound"); the choice is stored in `~/.config/open-corsair/config.json`.
Settings can be written in this mode without any mode switch, so changing a
setting never unmutes the mic.

When the tray quits, it hands the headset back to hardware mode, where the
mute button works on its own again. A PipeWire mute set by the tray stays in
place, so a muted mic stays muted. Unmute it in the KDE volume applet if
needed.

Without the tray running, `open-corsair set …` switches to software mode only
for the write. The headset firmware resets its own mic mute when it does
that.

## Protocol notes

See [docs/bragi-protocol.md](docs/bragi-protocol.md). `tools/probe.py`
sends raw packets and `tools/scan.py` lists all readable properties. Both are
useful when adding a new device.

## Credits

Protocol details were checked against
[OpenLinkHub](https://github.com/jurkovic-nikola/OpenLinkHub) and
[HeadsetControl](https://github.com/Sapd/HeadsetControl), and against the
device manifests shipped with iCUE.
