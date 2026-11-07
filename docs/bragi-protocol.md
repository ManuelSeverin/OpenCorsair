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
- **cmd:** `01` set, `02` get, `0d` open handle, `06` write data, `05` close, `12` heartbeat.
- **status:** `00` ok, `01` invalid value, `03` failed (for example writing in hardware mode), `05` unsupported, `09` refused.

Replies carry no request id and go to every process that has the hidraw node
open. open-corsair therefore serialises requests with an flock in
`$XDG_RUNTIME_DIR`.

## Modes

Property `0x03`: `1` hardware, `2` software, `4` sleep.

- **Reads:** work in hardware mode.
- **Writes:** set software mode on `09` only, write the property, then restore
  hardware mode on `09`. The settings persist. Do not switch the receiver
  (`08`) as well.
- **Mic mute quirk:** each write session resets the headset's mic mute. The
  mute button is handled inside the headset and is not reported over HID in
  hardware mode, not even with `92` (key press notifications) enabled. The mute
  state cannot be read either (`8e`/`a6` are unsupported). open-corsair
  therefore skips writes when a value is unchanged.

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
| `d4` | mic mute LED | 0 off, 2 on |
| `35` | wireless mode switch position | |
| `3e` | free onboard storage | |
| `40` | idle time | |
| `48` | external power present | |
| `d0` | startup animation | |
| `f6`–`f9` | earcup ring function enabled / rotation (L/R) | |

Not supported on this firmware (status `05`): `8e` mic muted, `8f` mic level,
`a6` mic mute switch, `37` power-save timeout. Mic volume goes through the
regular USB audio mixer instead.

`tools/scan.py` lists what a device supports. `docs/bragi-properties.txt` maps
all 278 property ids from iCUE's `BragiProtocols.dll` to names.

Receiver (`08`): `03` mode, `11`/`12` VID/PID (`2a02`), `13` firmware, `36` connected sub-device bitmap (bit 1 = headset).
