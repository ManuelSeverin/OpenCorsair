#!/usr/bin/env python3
"""OpenDeck / Stream Deck plugin for OpenCorsair headsets.

OpenDeck (Linux) and the Elgato Stream Deck app (Windows) start this file with
    -port <n> -pluginUUID <uuid> -registerEvent <event> -info <json>
and talk to it over a WebSocket on localhost. Only the standard library is
used, so the plugin runs on the system Python without extra packages. The
open_corsair package is copied next to this file by
packaging/build-opendeck-plugin.sh; when run from the repo it is found one
directory up. On Windows, launcher.js starts this file with the Python that
the Windows build bundles.

Device access goes through open_corsair.headset, which serialises requests
with a cross-process lock, so the plugin works next to a running tray.
"""
from __future__ import annotations

import asyncio
import base64
import concurrent.futures
import json
import os
import struct
import sys
import time
import traceback
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
try:
    import open_corsair  # noqa: F401
except ImportError:
    sys.path.insert(0, str(HERE.parent))

from open_corsair.bragi import AncMode, BragiError, WheelFunction  # noqa: E402
from open_corsair import config  # noqa: E402
from open_corsair.headset import Headset, HeadsetSettings, HeadsetState, find_receiver  # noqa: E402
if sys.platform == "win32":
    from open_corsair.winmic import MicControl  # noqa: E402
else:
    from open_corsair.mic import MicControl  # noqa: E402

PREFIX = "io.github.manuelseverin.opencorsair."
POLL_S = 60          # battery and settings refresh while keys are visible
MUTE_POLL_S = 1.0    # Windows: how often the mute key checks the mic mute
WINDOWS = sys.platform == "win32"
AUTO_OFF_STEPS = (0, 5, 10, 15, 30, 60, 90)
ANC_CYCLES = {
    "cycle": (AncMode.OFF, AncMode.ON, AncMode.TRANSPARENCY),
    "toggle": (AncMode.OFF, AncMode.ON),
    "toggle-transparency": (AncMode.OFF, AncMode.TRANSPARENCY),
    "on-transparency": (AncMode.ON, AncMode.TRANSPARENCY),
}
ANC_FIXED = {"off": AncMode.OFF, "on": AncMode.ON, "transparency": AncMode.TRANSPARENCY}
ANC_TITLES = {AncMode.OFF: "Off", AncMode.ON: "ANC", AncMode.TRANSPARENCY: "Transp."}
WHEEL_CYCLE = (WheelFunction.SYSTEM_VOLUME, WheelFunction.BLUETOOTH_VOLUME, 0)
WHEEL_FIXED = {"system": WheelFunction.SYSTEM_VOLUME, "bluetooth": WheelFunction.BLUETOOTH_VOLUME, "off": 0}
WHEEL_TITLES = {WheelFunction.SYSTEM_VOLUME: "System", WheelFunction.BLUETOOTH_VOLUME: "BT", 0: "Off"}
WHEEL_STATES = {WheelFunction.SYSTEM_VOLUME: 0, WheelFunction.BLUETOOTH_VOLUME: 1, 0: 2}


def battery_color(pct: float, charging: bool) -> str:
    """Same colours as the tray icon."""
    if charging:
        return "#27ae60" if pct >= 99 else "#3daee9"
    if pct <= 10:
        return "#da4453"
    if pct <= 33:
        return "#f67400"
    return "#27ae60"


def battery_image(st: HeadsetState | None) -> str:
    """Key image in the style of the tray icon: battery outline, coloured
    fill for the level, the percentage (or a bolt while charging) inside."""
    connected = st is not None and st.connected and st.battery is not None
    outline = "#f2f3f7" if connected else "#6b7686"
    body = (f'<rect x="12" y="34" width="108" height="76" rx="15" fill="none" '
            f'stroke="{outline}" stroke-width="10"/>'
            f'<rect x="122" y="56" width="12" height="32" rx="4" fill="{outline}"/>')
    halo = 'stroke="#000" stroke-width="7" stroke-linejoin="round" paint-order="stroke"'
    if not connected:
        inner = f'<rect x="46" y="67" width="40" height="10" rx="4" fill="{outline}"/>'
    else:
        pct = max(0.0, min(100.0, st.battery))
        width = 84 * pct / 100
        inner = (f'<rect x="24" y="46" width="{width:.1f}" height="52" rx="5" '
                 f'fill="{battery_color(pct, st.on_charger)}"/>')
        if st.on_charger:
            inner += (f'<path d="M74 40 L48 76 H66 L58 104 L86 66 H68 Z" fill="#f2f3f7" {halo}/>')
        else:
            text = str(int(pct))  # truncated, like the tray and iCUE
            size = 50 if len(text) < 3 else 40
            inner += (f'<text x="66" y="72" font-family="Liberation Sans, DejaVu Sans, Segoe UI, Arial, sans-serif" '
                      f'font-weight="bold" font-size="{size}" text-anchor="middle" '
                      f'dominant-baseline="central" fill="#f2f3f7" {halo}>{text}</text>')
    svg = f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 144 144">{body}{inner}</svg>'
    return "data:image/svg+xml;base64," + base64.b64encode(svg.encode()).decode()


LOG_FILE = HERE / "logs" / "opencorsair.log"


def log(*args) -> None:
    # OpenDeck writes plugin stdout/stderr to ~/.local/share/opendeck/logs/plugins/.
    # On Windows the plugin runs without a console, so it logs next to itself.
    line = " ".join([time.strftime("%Y-%m-%d %H:%M:%S"), *map(str, args)])
    if not WINDOWS:
        print(line, file=sys.stderr, flush=True)
        return
    try:
        LOG_FILE.parent.mkdir(exist_ok=True)
        if LOG_FILE.exists() and LOG_FILE.stat().st_size > 1_000_000:
            LOG_FILE.replace(LOG_FILE.with_suffix(".old.log"))
        with LOG_FILE.open("a", encoding="utf-8") as f:
            f.write(line + "\n")
    except OSError:
        pass


def clamp(v: int, lo: int = 0, hi: int = 100) -> int:
    return max(lo, min(hi, v))


def num(settings: dict, key: str, default: int) -> int:
    try:
        return int(settings.get(key, default))
    except (TypeError, ValueError):
        return default


def next_in(cycle, current):
    """The entry after `current` in `cycle` (the first one if unknown)."""
    try:
        return cycle[(cycle.index(current) + 1) % len(cycle)]
    except ValueError:
        return cycle[0]


# -- minimal WebSocket client (RFC 6455, text frames only) -------------------
class WebSocket:
    def __init__(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
        self.reader = reader
        self.writer = writer

    @classmethod
    async def connect(cls, port: int) -> "WebSocket":
        # 127.0.0.1, not localhost: on Windows localhost may resolve to ::1
        # first, and the Stream Deck app listens on IPv4 only
        reader, writer = await asyncio.open_connection("127.0.0.1", port)
        key = base64.b64encode(os.urandom(16)).decode()
        writer.write((
            f"GET / HTTP/1.1\r\nHost: 127.0.0.1:{port}\r\nUpgrade: websocket\r\n"
            f"Connection: Upgrade\r\nSec-WebSocket-Key: {key}\r\n"
            "Sec-WebSocket-Version: 13\r\n\r\n").encode())
        await writer.drain()
        head = await reader.readuntil(b"\r\n\r\n")
        if b" 101 " not in head.split(b"\r\n", 1)[0]:
            raise ConnectionError(f"WebSocket handshake failed: {head[:80]!r}")
        return cls(reader, writer)

    async def _send_frame(self, opcode: int, data: bytes) -> None:
        n = len(data)
        if n < 126:
            head = struct.pack("!BB", 0x80 | opcode, 0x80 | n)
        elif n < 1 << 16:
            head = struct.pack("!BBH", 0x80 | opcode, 0x80 | 126, n)
        else:
            head = struct.pack("!BBQ", 0x80 | opcode, 0x80 | 127, n)
        mask = os.urandom(4)
        # XOR the payload with the repeated mask in one big-integer operation
        rep = (mask * (n // 4 + 1))[:n]
        masked = (int.from_bytes(data, "big") ^ int.from_bytes(rep, "big")).to_bytes(n, "big")
        self.writer.write(head + mask + masked)
        await self.writer.drain()

    async def send(self, msg: dict) -> None:
        await self._send_frame(0x1, json.dumps(msg).encode())

    async def recv(self) -> str | None:
        """Next text message, or None when the connection closed."""
        parts: list[bytes] = []
        while True:
            b0, b1 = await self.reader.readexactly(2)
            opcode, n = b0 & 0x0F, b1 & 0x7F
            if n == 126:
                n = struct.unpack("!H", await self.reader.readexactly(2))[0]
            elif n == 127:
                n = struct.unpack("!Q", await self.reader.readexactly(8))[0]
            mask = await self.reader.readexactly(4) if b1 & 0x80 else None
            data = await self.reader.readexactly(n)
            if mask:
                data = bytes(c ^ mask[i % 4] for i, c in enumerate(data))
            if opcode == 0x8:      # close
                return None
            if opcode == 0x9:      # ping
                await self._send_frame(0xA, data)
                continue
            if opcode == 0xA:      # pong
                continue
            parts.append(data)
            if b0 & 0x80:          # FIN
                return b"".join(parts).decode()


# -- the plugin -------------------------------------------------------------
class Plugin:
    def __init__(self, ws: WebSocket):
        self.ws = ws
        self.contexts: dict[str, dict] = {}    # context -> {"action", "settings", "controller"}
        self.state: HeadsetState | None = None
        self.settings: HeadsetSettings | None = None
        self.error: str | None = None
        # one thread for all device access: requests must not interleave
        self.pool = concurrent.futures.ThreadPoolExecutor(max_workers=1)
        self.pending: dict[str, tuple] = {}    # setting -> (method name, args); latest wins
        self.writer: asyncio.Task | None = None
        self.refresh_task: asyncio.Task | None = None
        # mic mute goes through PipeWire, not the radio: own thread, so it
        # never waits behind a slow headset request
        self.mic_pool = concurrent.futures.ThreadPoolExecutor(max_workers=1)
        self.mic: MicControl | None = None
        self.muted: bool | None = None
        self.mic_task: asyncio.Task | None = None

    # -- device access (runs in the worker thread) ------------------------
    @staticmethod
    def _read() -> tuple[HeadsetState, HeadsetSettings | None]:
        with Headset.open() as hs:
            st = hs.read_state()
            return st, hs.read_settings() if st.connected else None

    @staticmethod
    def _write(jobs: list[tuple]) -> None:
        with Headset.open() as hs:
            if not hs.is_connected():
                raise BragiError("headset not connected")
            for method, args in jobs:
                getattr(hs, method)(*args)

    async def device(self, fn, *args):
        return await asyncio.get_running_loop().run_in_executor(self.pool, fn, *args)

    async def refresh(self) -> None:
        try:
            self.state, self.settings = await self.device(self._read)
            self.error = None
        except PermissionError:
            self.state, self.settings, self.error = None, None, (
                "No access to the receiver (is another app holding it?)" if WINDOWS
                else "No permission for the receiver (udev rule missing?)")
        except (OSError, BragiError) as e:
            self.state, self.settings, self.error = None, None, str(e) or type(e).__name__
        if self.error:
            log("read failed:", self.error)
        await self.render_all()

    def schedule_refresh(self) -> None:
        if not self.refresh_task or self.refresh_task.done():
            self.refresh_task = asyncio.create_task(self.refresh())

    def queue(self, key: str, method: str, *args) -> None:
        """Write a setting soon. Repeated changes of the same setting (fast key
        presses, dial turns) collapse into one write of the newest value."""
        self.pending[key] = (method, args)
        if not self.writer or self.writer.done():
            self.writer = asyncio.create_task(self.flush())

    async def flush(self) -> None:
        ok = True
        while self.pending:
            jobs = list(self.pending.values())
            self.pending.clear()
            try:
                await self.device(self._write, jobs)
            except (OSError, BragiError) as e:
                log("write failed:", e)
                ok = False
        if not ok:
            for ctx in self.contexts:
                await self.send("showAlert", ctx)
        await self.refresh()

    async def poll(self) -> None:
        while True:
            await asyncio.sleep(POLL_S)
            if any(i["action"] != "mute" for i in self.contexts.values()):
                self.schedule_refresh()

    # -- microphone mute (PipeWire, same source and sound as the tray) -----
    def _mic(self) -> MicControl | None:
        if self.mic is None:
            found = find_receiver()
            if found:
                self.mic = MicControl(found[2].replace(" ", "_"))
        return self.mic

    def _read_mute(self) -> bool | None:
        mic = self._mic()
        return mic.is_muted() if mic else None

    def _write_mute(self, muted: bool, sound: str) -> bool:
        mic = self._mic()
        if not mic:
            return False
        # the key's own choice, else the tray's "Mute sound" setting
        mic.sound = sound if sound in ("off", "soft", "beeps") else config.load()["mute_sound"]
        return mic.set_muted(muted)

    async def mic_call(self, fn, *args):
        return await asyncio.get_running_loop().run_in_executor(self.mic_pool, fn, *args)

    async def refresh_mute(self) -> None:
        muted = await self.mic_call(self._read_mute)
        if muted != self.muted:
            self.muted = muted
            for ctx, info in list(self.contexts.items()):
                if info["action"] == "mute":
                    await self.render(ctx)

    def schedule_mute_refresh(self) -> None:
        if not self.mic_task or self.mic_task.done():
            self.mic_task = asyncio.create_task(self.refresh_mute())

    async def set_mute(self, ctx: str, muted: bool) -> None:
        if muted == self.muted:
            return
        sound = self.contexts.get(ctx, {}).get("settings", {}).get("sound", "")
        if await self.mic_call(self._write_mute, muted, sound):
            self.muted = muted
            await self.render_all()
        else:
            await self.send("showAlert", ctx)

    async def watch_mute(self) -> None:
        """Follow mute changes made elsewhere (tray, headset button, KDE)."""
        if WINDOWS:
            # no change events without a COM callback: ask the mixer instead
            while True:
                await asyncio.sleep(MUTE_POLL_S)
                if any(i["action"] == "mute" for i in self.contexts.values()):
                    self.schedule_mute_refresh()
        while True:
            try:
                proc = await asyncio.create_subprocess_exec(
                    "pactl", "subscribe", stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.DEVNULL)
            except OSError:
                log("pactl not found, mute key will not follow outside changes")
                return
            try:
                while line := await proc.stdout.readline():
                    if b" on source " in line or b" on server " in line:
                        self.schedule_mute_refresh()
            finally:
                if proc.returncode is None:
                    proc.kill()
            await asyncio.sleep(5)  # PipeWire restarted: subscribe again

    # -- messages to OpenDeck ---------------------------------------------
    async def send(self, event: str, context: str, payload: dict | None = None) -> None:
        msg = {"event": event, "context": context}
        if payload is not None:
            msg["payload"] = payload
        await self.ws.send(msg)

    async def show(self, ctx: str, title: str, state: int | None = None,
                   value: int | None = None) -> None:
        await self.send("setTitle", ctx, {"title": title, "target": 0})
        if state is not None:
            await self.send("setState", ctx, {"state": state})
        if self.contexts[ctx].get("controller") == "Encoder":
            fb = {"title": self.contexts[ctx]["name"], "value": title}
            if value is not None:
                fb["indicator"] = {"value": value}
            await self.send("setFeedback", ctx, fb)

    async def render_all(self) -> None:
        for ctx in list(self.contexts):
            await self.render(ctx)

    async def render(self, ctx: str) -> None:
        info = self.contexts.get(ctx)
        if not info:
            return
        kind = info["action"]
        st, s = self.state, self.settings
        if kind == "battery":
            image = battery_image(st)
            if info.get("image") != image:
                info["image"] = image
                await self.send("setImage", ctx, {"image": image, "target": 0})
            if info.get("title") != "":
                info["title"] = ""
                await self.send("setTitle", ctx, {"title": "", "target": 0})
            return
        if kind == "mute":
            if self.muted is None:
                await self.show(ctx, "—", 0)
            else:
                await self.show(ctx, "", 1 if self.muted else 0)
            return
        if s is None:
            await self.show(ctx, "—")
            return
        if kind == "anc":
            await self.show(ctx, ANC_TITLES.get(s.anc, "?"),
                            {AncMode.OFF: 0, AncMode.ON: 1, AncMode.TRANSPARENCY: 2}.get(s.anc, 0))
        elif kind == "sidetone":
            title = f"{s.sidetone_level}%" if s.sidetone_on else "Off"
            await self.show(ctx, title, 1 if s.sidetone_on else 0, s.sidetone_level)
        elif kind == "brightness":
            await self.show(ctx, "?" if s.brightness is None else f"{s.brightness}%", None, s.brightness)
        elif kind == "autooff":
            await self.show(ctx, f"{s.sleep_minutes} min" if s.sleep_minutes else "Off")
        elif kind == "micled":
            if s.mic_led is None:
                await self.show(ctx, "?")
            else:
                await self.show(ctx, "On" if s.mic_led else "Off", 1 if s.mic_led else 0)
        elif kind == "wheel":
            side = "left" if info["settings"].get("side", "right") == "left" else "right"
            fn = s.wheel_left if side == "left" else s.wheel_right
            label = "Left" if side == "left" else "Right"
            await self.show(ctx, f"{label}\n{WHEEL_TITLES.get(fn, '?')}", WHEEL_STATES.get(fn, 2))

    # -- actions ----------------------------------------------------------
    async def press(self, ctx: str) -> None:
        info = self.contexts[ctx]
        kind, cfg, s = info["action"], info["settings"], self.settings
        if kind == "battery":
            self.schedule_refresh()
            return
        if kind == "mute":
            mode = cfg.get("press", "toggle")
            if self.muted is None:
                await self.refresh_mute()
            if mode == "ptt":
                await self.set_mute(ctx, False)
            elif self.muted is None:
                await self.send("showAlert", ctx)
            else:
                await self.set_mute(ctx, {"mute": True, "unmute": False}.get(mode, not self.muted))
            return
        if s is None:
            # unknown state: read first, then try once more
            await self.refresh()
            s = self.settings
            if s is None:
                await self.send("showAlert", ctx)
                return
        mode = cfg.get("press")
        if kind == "anc":
            target = ANC_FIXED.get(mode) or next_in(ANC_CYCLES.get(mode, ANC_CYCLES["cycle"]), s.anc)
            s.anc = target
            if target == AncMode.ON:
                s.sidetone_on = False
            self.queue("anc", "set_anc", target)
        elif kind == "sidetone":
            step = num(cfg, "step", 10)
            if mode in ("up", "down"):
                level = clamp(s.sidetone_level + (step if mode == "up" else -step))
                self.set_sidetone(True, level)
            elif mode == "set":
                self.set_sidetone(True, clamp(num(cfg, "level", 50)))
            else:
                on = {"on": True, "off": False}.get(mode, not s.sidetone_on)
                self.set_sidetone(on, None)
        elif kind == "brightness":
            cur = s.brightness or 0
            step = num(cfg, "step", 25)
            if mode == "set":
                new = clamp(num(cfg, "value", 100))
            elif mode == "down":
                new = clamp(cur - step)
            elif mode == "up":
                new = clamp(cur + step)
            else:  # cycle: up, then back to 0 after 100
                new = 0 if cur >= 100 else clamp(cur + step)
            self.set_brightness(new)
        elif kind == "autooff":
            if mode == "set":
                minutes = clamp(num(cfg, "minutes", 10), 0, 240)
            else:
                minutes = next_in(AUTO_OFF_STEPS, s.sleep_minutes or 0)
            s.sleep_minutes = minutes or None
            self.queue("autooff", "set_sleep", minutes or None)
        elif kind == "micled":
            on = {"on": True, "off": False}.get(mode, not s.mic_led)
            s.mic_led = on
            self.queue("micled", "set_mic_led", on)
        elif kind == "wheel":
            side = "left" if cfg.get("side", "right") == "left" else "right"
            cur = s.wheel_left if side == "left" else s.wheel_right
            fn = WHEEL_FIXED[mode] if mode in WHEEL_FIXED else next_in(WHEEL_CYCLE, cur)
            setattr(s, f"wheel_{side}", fn)
            self.queue(f"wheel-{side}", "set_wheel", side, fn)
        # show the new value right away; the read after the write confirms it
        await self.render_all()

    def set_sidetone(self, on: bool, level: int | None) -> None:
        s = self.settings
        s.sidetone_on = on
        if level is not None:
            s.sidetone_level = level
        if on and s.anc == AncMode.ON:
            s.anc = AncMode.OFF   # headset.set_sidetone does the same
        self.queue("sidetone", "set_sidetone", on, level)

    def set_brightness(self, percent: int) -> None:
        self.settings.brightness = percent
        self.queue("brightness", "set_brightness", percent)

    async def rotate(self, ctx: str, ticks: int) -> None:
        info, s = self.contexts[ctx], self.settings
        if s is None:
            return
        step = num(info["settings"], "dialStep", 5) * ticks
        if info["action"] == "sidetone":
            self.set_sidetone(True, clamp(s.sidetone_level + step))
        elif info["action"] == "brightness":
            self.set_brightness(clamp((s.brightness or 0) + step))
        else:
            return
        await self.render_all()

    async def dial_press(self, ctx: str) -> None:
        info, s = self.contexts[ctx], self.settings
        if s is None:
            return
        if info["action"] == "sidetone":
            self.set_sidetone(not s.sidetone_on, None)
        elif info["action"] == "brightness":
            self.set_brightness(0 if s.brightness else 100)
        else:
            return
        await self.render_all()

    # -- event loop ---------------------------------------------------------
    async def handle(self, msg: dict) -> None:
        event, ctx = msg.get("event"), msg.get("context")
        payload = msg.get("payload") or {}
        action = msg.get("action", "")
        if event == "willAppear":
            kind = action.removeprefix(PREFIX)
            self.contexts[ctx] = {
                "action": kind,
                "name": ACTION_NAMES.get(kind, kind),
                "settings": payload.get("settings") or {},
                "controller": payload.get("controller", "Keypad"),
            }
            if kind == "mute":
                self.schedule_mute_refresh()
            elif self.state is None and self.settings is None:
                self.schedule_refresh()
            else:
                await self.render(ctx)
        elif event == "willDisappear":
            self.contexts.pop(ctx, None)
        elif ctx not in self.contexts:
            if event == "systemDidWakeUp":
                self.schedule_refresh()
            return
        elif event == "didReceiveSettings":
            self.contexts[ctx]["settings"] = payload.get("settings") or {}
            await self.render(ctx)
        elif event == "keyDown":
            if "settings" in payload:
                self.contexts[ctx]["settings"] = payload["settings"]
            await self.press(ctx)
        elif event == "keyUp":
            info = self.contexts[ctx]
            if info["action"] == "mute" and info["settings"].get("press") == "ptt":
                await self.set_mute(ctx, True)
        elif event == "dialRotate":
            await self.rotate(ctx, int(payload.get("ticks", 0)))
        elif event in ("dialDown", "touchTap"):
            await self.dial_press(ctx)
        elif event == "propertyInspectorDidAppear":
            self.schedule_refresh()
            await self.send_status(ctx)
        elif event == "sendToPlugin":
            if payload.get("refresh"):
                await self.refresh()
            await self.send_status(ctx)

    async def send_status(self, ctx: str) -> None:
        """Tell the property inspector whether the headset is reachable."""
        st = self.state
        if self.error:
            text = self.error
        elif st is None:
            text = "Reading headset…"
        elif not st.connected:
            text = "Receiver found, headset is off or out of range."
        else:
            text = f"Connected, battery {int(st.battery)}%."
        await self.ws.send({"event": "sendToPropertyInspector", "context": ctx,
                            "action": PREFIX + self.contexts[ctx]["action"],
                            "payload": {"status": text}})

    async def run(self) -> None:
        poller = asyncio.create_task(self.poll())
        watcher = asyncio.create_task(self.watch_mute())
        try:
            while (raw := await self.ws.recv()) is not None:
                try:
                    msg = json.loads(raw)
                except ValueError:
                    continue
                try:
                    await self.handle(msg)
                except Exception:
                    log("error handling", msg.get("event"), traceback.format_exc())
        finally:
            poller.cancel()
            watcher.cancel()
            self.pool.shutdown(wait=False, cancel_futures=True)
            self.mic_pool.shutdown(wait=False, cancel_futures=True)


ACTION_NAMES = {
    "anc": "Noise cancelling",
    "sidetone": "Sidetone",
    "brightness": "LED brightness",
    "autooff": "Auto power-off",
    "micled": "Mic LED",
    "wheel": "Earcup wheel",
    "battery": "Battery",
    "mute": "Mic mute",
}


def parse_args(argv: list[str]) -> dict:
    args = {}
    for i in range(0, len(argv) - 1, 2):
        args[argv[i].lstrip("-")] = argv[i + 1]
    return args


async def main() -> None:
    args = parse_args(sys.argv[1:])
    ws = await WebSocket.connect(int(args["port"]))
    await ws.send({"event": args["registerEvent"], "uuid": args["pluginUUID"]})
    log("registered with", "Stream Deck" if WINDOWS else "OpenDeck")
    await Plugin(ws).run()
    log("connection closed")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (ConnectionError, asyncio.IncompleteReadError, KeyboardInterrupt):
        pass
