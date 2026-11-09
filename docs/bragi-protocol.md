# Corsair Bragi protocol (as used by the Virtuoso MAX Wireless)

Observed on receiver `1b1c:2a02` (headset PID `2a00`), firmware 0.37.493.

## Transport

The vendor interface is USB interface 4, HID usage page `0xFF42`:

| Report | Direction | Size | Use |
|---|---|---|---|
| 2 | out | 63 + id | requests |
| 1 | in | 63 + id | replies |
| 3 | in | 63 + id | async notifications (none seen in hardware mode) |

Interface 3 (usage page `0xFF13`) carries consumer and telephony controls.

## Packets

```
request: 02 <target> <cmd> <prop lo> <prop hi> [value LE…]    padded to 64 bytes
reply:   01 <source> <cmd> <status> [value LE…]
```

- **target:** `08` = receiver, `09` = headset (through the receiver).
- **source:** in replies this is `target & 7`, so `00` = receiver and `01` = headset.
- **cmd** (names from iCUE's `BragiProtocols.dll`): `01` set, `02` get, `03` get multiple,
  `05` close handle, `06` write buffer (first chunk), `07` write buffer (more chunks),
  `08` read buffer, `09` describe buffer, `0b` create file, `0c` remove file,
  `0d` open file on a handle, `12` ping, `15` apply subsystem settings.
- **status:** `00` ok, `01` invalid value, `03` failed (for example writing in hardware mode), `05` unsupported, `09` refused.

Replies carry no request id and go to every process that has the hidraw node
open. OpenCorsair therefore serialises requests with an flock in
`$XDG_RUNTIME_DIR`.

## Modes

Property `0x03`: `1` hardware, `2` software, `4` sleep.

- **Reads:** work in hardware mode.
- **Writes:** set software mode on `09` only, write the property, then restore
  hardware mode on `09`. The settings persist. Do not switch the receiver
  (`08`) as well.
- **Mic mute:** in hardware mode the headset handles the mute button itself.
  Nothing is reported over HID, and the state cannot be read (`8e`/`a6` are
  unsupported). Every hardware → software → hardware round trip resets that
  mute.
- **Software mode:** the headset does *not* mute on its own. It reports the
  button on report 3 as `03 01 02 01` (press, bit 0 = mic button) and
  `03 01 02 00` (release). The host must mute the audio source, as iCUE and
  OpenLinkHub do. Property-change notifications (`03 01 01 <prop> 00
  <value>`, e.g. battery `0f`) also arrive in this mode.
- **Keep-alive:** cmd `12` (`02 09 12`) answers with status 0, but iCUE only
  pings devices whose manifest has a `RequiredPingInterval`, which the
  Virtuoso MAX does not have. OpenCorsair does not ping either. It re-checks
  the mode once a minute, because the headset comes back in hardware mode
  after a power cycle.
- iCUE does the same: it enters software mode once, mutes the OS capture
  endpoint on the mic key, and restores hardware mode on exit.

## Properties

Headset (`09`):

| Prop | Meaning | Format / example |
|---|---|---|
| `02` | LED brightness | u16, 0–1000 |
| `03` | mode | see above |
| `0d` | auto-off enabled | u8 0/1 |
| `0e` | auto-off timeout | u32 ms (300000 = 5 min) |
| `0f` | battery level | u16, tenths of a percent |
| `10` | battery state | 0 no battery, 1 charging, 2 discharging, 3 full (iCUE: on charger) |
| `11` / `12` | VID / PID | `1b1c` / `2a00` |
| `13` | firmware | `00 25 ed 01` |
| `46` | sidetone **mute** | 0 = sidetone on, 1 = off |
| `47` | sidetone level | u16, 0–1000 |
| `53` | battery voltage | u16 mV |
| `d1` | ANC | 0 off, 1 on, 2 transparency |
| `d2` / `d3` | left / right wheel function | 1 system volume, 2 Bluetooth volume |
| `d4` | mic LED while unmuted (`MicrophoneUnmutedIndicationBehavior`) | 0 off, 2 on |
| `35` | wireless mode switch position | |
| `3e` | free onboard storage | |
| `40` | idle time | |
| `48` | external power present | |
| `d0` | startup animation | |
| `f6` / `f7` | left / right wheel enabled | u8 0/1 (tested: 0 disables, `d2`/`d3` keep their value) |
| `f8` / `f9` | left / right wheel rotation (unverified) | |

Not supported on this firmware (status `05`): `8e` mic muted, `8f` mic level,
`a6` mic mute switch, `37` power-save timeout. Mic volume goes through the
regular USB audio mixer instead.

More properties, from iCUE: `d0` startup animation (0 = **on**, 1 = off), `cd` LED test
mode (0 normal, 1 white, 2 red, 3 green, 4 blue, 5 off), `15` second chip firmware,
`3a` connection mode, `4b` link encryption state.

### Noise cancelling and sidetone

The headset accepts ANC on and sidetone on at the same time. iCUE does not allow
it: turning ANC on mutes sidetone (`46` = 1), turning sidetone on sets ANC off.
OpenCorsair does the same for ANC on; transparency mode is left alone.

## LEDs

Six LEDs, three per earcup. In software mode the host can send colours; iCUE
renders all effects on the PC this way. A trip through hardware mode hands the
LEDs back to the headset's own effect.

```
02 09 0d 00 22 00                      open file 0x22 (live LED buffer) on handle 0
02 09 06 00 <u32 len> 12 00 R G B ×6   write: header 0x0012, then RGB per LED
02 09 05 01 00                         close handle 0
```

`len` counts the header, so it is 20. Opening an open handle is accepted;
writing after a close fails with status `03`. The link takes 200+ frames/s. LEDs 0–2 are one earcup, 3–5 the other
(OpenLinkHub calls 0–2 the left one). Property `02` brightness scales the
colours. The handle is gone after the headset is power-cycled.

Onboard effects (shown in hardware mode) are files in the headset's storage
(free file ids `6d60`–`7fff`, up to 15 files of 300 bytes). Each effect starts
with a u16 header: Watercolor `0022`, Watercolor spectrum `0031`, Arc `001b`,
Color pulse `ad4f`, Color shift `a5fa`, Color wave `7bff`, Rain `a07e`,
Rainbow `a402`, Rainbow wave `b94c`, Spiral rainbow `ab87`, Static `207e`,
Gradient `b1f9`. Then: u8 options (01 colours given, 02 random), u8 speed
(3 slow, 4 medium, 5 fast), u8 direction, u8 duration, u8 colour count +
`FF B G R` per colour, u8 LED count + LED ids. A list file (header `6c69`)
references the effect files. OpenCorsair does not write these.

## iCUE features that are host-only

Equalizer (10 bands, ±12 dB, in Corsair's Windows audio driver), mic volume
(the normal capture volume), Dolby Atmos, SoundID and NVIDIA Broadcast do not
talk to the headset. iCUE's EQ presets for this headset, 32 Hz … 16 kHz:
Pure Direct all 0; Movie Theater 3 3 3 1 −1 1 2 2 1 0; FPS Competition
−2 −2 −2 0 0 1 2 1 1 0; Bass Boost 2 2 3 1 0 0 0 0 −1 0.

`tools/scan.py` lists what a device supports. `docs/bragi-properties.txt` maps
all 278 property ids from iCUE's `BragiProtocols.dll` to names.

Receiver (`08`): `03` mode, `11`/`12` VID/PID (`2a02`), `13` firmware, `36` connected sub-device bitmap (bit 1 = headset).
