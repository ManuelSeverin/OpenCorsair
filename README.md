<img src="assets/icon.svg" width="64" height="64" alt="">

# OpenCorsair

Linux userspace driver for Corsair wireless headsets that use Corsair's
"Bragi" HID protocol. It talks to the USB receiver directly through
`/dev/hidraw`, so no kernel module and no iCUE are needed.

> [!NOTE]
> **Made with AI.** The program, the Stream Deck plugin and this README were
> written by an AI (Claude, by Anthropic) under the direction of the
> maintainer and tested on a real Virtuoso MAX Wireless. The protocol
> knowledge comes from observing the receiver and from iCUE. Use it at your
> own risk. This project is not affiliated with or endorsed by Corsair or
> Elgato.

Supported right now:

| Device | Receiver USB ID | Status |
|---|---|---|
| Virtuoso MAX Wireless | `1b1c:2a02` | tested |

## Features

- **Tray indicator** showing the battery level, colour-coded, with a
  charging state and a low-battery notification
- **Mic mute** via the headset button, handled on the host like iCUE does
- **Settings window** with a picture of the headset, live battery and mute
  status. Open it with a left click on the tray icon or "Settings…" in the
  tray menu (`open-corsair tray --settings` opens it on start).
- Settings in the window, the tray menu or on the command line:
  - noise cancelling: off, on, transparency
  - sidetone: on/off and level. As in iCUE, noise cancelling and sidetone
    turn each other off.
  - auto power-off timer
  - mic mute LED
  - LED brightness (window and CLI)
  - earcup wheels: system volume, Bluetooth volume or off (window and CLI)
- **Lighting** (window and tray menu, while the tray runs): static colours per
  earcup, pulse, colour shift, rainbow or off. Effects are drawn on the PC and
  sent to the headset, like iCUE does. Animated effects send 20 updates a
  second over the radio, which costs some battery. "Headset default" leaves
  the LEDs alone.

## Install

### AppImage (recommended)

1. Download `OpenCorsair-<version>-x86_64.AppImage` and
   `70-open-corsair.rules` from the
   [releases page](https://github.com/ManuelSeverin/OpenCorsair/releases).
2. Install the udev rule once. It gives the logged-in user access to Corsair
   HID devices:

   ```sh
   sudo install -m 644 70-open-corsair.rules /etc/udev/rules.d/
   sudo udevadm control --reload && sudo udevadm trigger --subsystem-match=hidraw
   ```

3. Make the AppImage executable and start it. Without arguments it starts
   the tray, and with arguments it works as the CLI (`… status`, `… set anc on`):

   ```sh
   chmod +x OpenCorsair-*-x86_64.AppImage
   ./OpenCorsair-*-x86_64.AppImage
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
Virtuoso MAX Wireless: 12% (discharging)
$ open-corsair status --json
$ open-corsair settings
$ open-corsair set anc transparency        # off | on | transparency
$ open-corsair set sidetone 40             # on | off | 0-100
$ open-corsair set auto-off 10             # off | minutes
$ open-corsair set mic-led on
$ open-corsair set wheel-right system      # system | bluetooth | off
```

## Stream Deck plugin (OpenDeck and Elgato)

The `streamdeck/` folder holds a plugin for [OpenDeck](https://github.com/nekename/OpenDeck)
(Linux) and the Elgato Stream Deck app (Windows, see below)
with keys for mic mute, noise cancelling, sidetone, LED brightness, auto
power-off, mic LED, earcup wheels and battery level. Each key shows the current value,
and its settings choose what a press does (cycle, toggle, set a value, step
up/down). Sidetone and brightness also work on Stream Deck+ dials.

```sh
packaging/build-streamdeck-plugin.sh --install   # then restart OpenDeck
```

This builds `dist/OpenCorsair-<version>.streamDeckPlugin` and copies the
plugin to `~/.config/opendeck/plugins/`. The plugin only needs `python3`
(3.10+) and the udev rule; it brings its own copy of the device code, so it
works next to the AppImage. It can run while the tray runs. Without the tray,
each change switches the headset to software mode for the write, as
`open-corsair set` does. The tray menu and settings window read the settings
again when they open, so they show changes made on the deck. Lighting effects
stay in the tray, since the tray draws them.

The mute key mutes the headset microphone in PipeWire, the same way the tray
does, and plays the sound chosen under "Mute sound" in the tray. It can
toggle, mute, unmute or work as push to talk. The key and the tray icon follow
each other and the headset button right away. Each mute key can also pick its
own sound.

### Elgato Stream Deck on Windows

The same plugin also runs in the Elgato Stream Deck app on Windows 10/11:

```sh
packaging/build-streamdeck-plugin.sh --windows
```

This builds `dist/OpenCorsair-<version>-windows.streamDeckPlugin` (needs
`curl`, `unzip` and `rsvg-convert`). Copy it to the Windows PC and
double-click it to install. Nothing else has to be installed: the package
brings the official embeddable Python from python.org, and the Stream Deck
app starts it through its built-in Node.js (`launcher.js`). Stream Deck 6.4
or newer is needed.

On Windows the plugin talks to the receiver through `hid.dll` and mutes the
headset microphone through the Windows audio mixer, the same switch as
"Mute" in the Windows sound settings. The mute sound plays on the default
playback device. The mute key checks the mixer once a second, so it follows
mutes made elsewhere. There is no tray on Windows, so each settings change
switches the headset to software mode only for the write.

Quit iCUE while using the plugin. iCUE talks to the same receiver and holds
the headset in its own software mode, so the two get in each other's way.
The plugin writes its log to `logs\opencorsair.log` in its plugin folder
(`%APPDATA%\Elgato\StreamDeck\Plugins\io.github.manuelseverin.opencorsair.sdPlugin`).

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

The headset picture in `open_corsair/assets/` comes from the device files of
Corsair iCUE (photo plus cut-out mask). It belongs to Corsair.
