"""System tray battery indicator (PyQt6, works with KDE/GNOME StatusNotifier)."""
from __future__ import annotations

import configparser
import os
import signal
import sys
import time
from pathlib import Path

from PyQt6.QtCore import QPointF, QProcess, QRectF, Qt, QSocketNotifier, QTimer
from PyQt6.QtGui import (
    QAction,
    QActionGroup,
    QColor,
    QFont,
    QFontMetricsF,
    QIcon,
    QPainter,
    QPainterPath,
    QPen,
    QPixmap,
)
from PyQt6.QtWidgets import QApplication, QMenu, QSystemTrayIcon

from .bragi import KEY_CUSTOM, KEY_MIC, REPORT_EVENT, AncMode, BatteryStatus, BragiError, Event, Mode
from .headset import Headset, HeadsetSettings, HeadsetState
from . import config, lighting
from .mic import MicControl

# In software mode the headset pushes battery changes itself, so polling is
# only a fallback. Keep radio traffic low: it costs headset battery.
POLL_CONNECTED_MS = 60_000
POLL_DISCONNECTED_MS = 20_000
RECONNECT_MS = 5_000
LOW_BATTERY = 15
SIDETONE_LEVELS = (10, 20, 30, 40, 50, 60, 70, 80, 90, 100)
AUTO_OFF_MINUTES = (5, 10, 15, 30, 60, 90)
FRAME_MS = 50  # LED animation: 20 frames per second


class Lights:
    """Sends the configured LED effect to the headset.

    Static colours are sent once (again after a reconnect), animated effects
    every FRAME_MS while the headset is connected.
    """

    def __init__(self, tray: "Tray"):
        self.tray = tray
        self.timer = QTimer(interval=FRAME_MS, timeout=self.tick)
        self.started = time.monotonic()
        self.active = False

    def restart(self) -> None:
        self.timer.stop()
        self.active = True
        self.started = time.monotonic()
        effect = self.tray.cfg["led_effect"]
        if self.tick() and effect in lighting.ANIMATED:
            self.timer.start()

    def stop(self) -> None:
        self.timer.stop()
        self.active = False

    def tick(self) -> bool:
        hs = self.tray.headset
        cfg = self.tray.cfg
        if not hs:
            self.stop()
            return False
        period = lighting.SPEEDS.get(cfg["led_speed"], lighting.SPEEDS["medium"])[1]
        colors = lighting.render(
            cfg["led_effect"],
            lighting.parse_color(cfg["led_left"]),
            lighting.parse_color(cfg["led_right"]),
            time.monotonic() - self.started,
            period,
        )
        if colors is None:
            self.stop()
            return False
        try:
            hs.set_leds(colors)
        except OSError:
            self.tray.drop_device("Receiver disconnected")
            return False
        except BragiError as e:
            # headset gone or busy: the next refresh restarts the effect
            print(f"lighting: {e}", flush=True)
            self.stop()
            return False
        return True


def _read_ini(path: Path) -> configparser.ConfigParser | None:
    cp = configparser.ConfigParser(interpolation=None, strict=False)
    try:
        cp.read(path, encoding="utf-8")
    except (OSError, configparser.Error):
        return None
    return cp


def plasma_panel_fg() -> QColor | None:
    """Text colour of the Plasma panel, or None when it can't be told.

    KDE can pair light apps with a dark panel (Plasma theme breeze-dark), so
    the app palette is no guide for the tray. The theme name comes from
    plasmarc, falling back to the look-and-feel defaults; a theme with its own
    colors file fixes the panel colours, otherwise the panel follows the
    system colour scheme like the apps do.
    """
    if "KDE" not in os.environ.get("XDG_CURRENT_DESKTOP", "").upper():
        return None
    cfg = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")
    name = None
    for f in (cfg / "plasmarc", cfg / "kdedefaults" / "plasmarc"):
        cp = _read_ini(f)
        if cp is not None and cp.has_option("Theme", "name"):
            name = cp.get("Theme", "name").strip()
            break
    if not name or name == "default":
        return None
    data_home = Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local/share")
    data_dirs = [data_home] + [Path(d) for d in
                               os.environ.get("XDG_DATA_DIRS", "/usr/local/share:/usr/share").split(":") if d]
    for d in data_dirs:
        colors = d / "plasma" / "desktoptheme" / name / "colors"
        if colors.is_file():
            cp = _read_ini(colors)
            if cp is None:
                return None
            for section in ("Colors:Window", "Colors:View"):
                if cp.has_option(section, "ForegroundNormal"):
                    try:
                        r, g, b = (int(v) for v in cp.get(section, "ForegroundNormal").split(",")[:3])
                    except ValueError:
                        return None
                    return QColor(r, g, b)
            return None
    return None


def draw_outlined(p: QPainter, path: QPainterPath, fill: QColor, halo: QColor, width: float) -> None:
    """Fill a path with a solid contrasting outline around it."""
    pen = QPen(halo, width)
    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    p.setPen(pen)
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.drawPath(path)
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(fill)
    p.drawPath(path)


def battery_color(pct: float, charging: bool) -> QColor:
    if charging:
        return QColor("#27ae60") if pct >= 99 else QColor("#3daee9")
    if pct <= 10:
        return QColor("#da4453")
    if pct <= 33:
        return QColor("#f67400")
    return QColor("#27ae60")


def bolt_path(body: QRectF) -> QPainterPath:
    """Lightning bolt centred in the battery body."""
    cx, cy, h = body.center().x(), body.center().y(), body.height() * 0.92
    pts = [(0.12, -0.5), (-0.30, 0.06), (-0.02, 0.06), (-0.12, 0.5), (0.30, -0.08), (0.02, -0.08)]
    path = QPainterPath()
    path.moveTo(cx + pts[0][0] * h, cy + pts[0][1] * h)
    for x, y in pts[1:]:
        path.lineTo(cx + x * h, cy + y * h)
    path.closeSubpath()
    return path


def draw_mute_badge(p: QPainter) -> None:
    """Red disc with a white bar in the top-left corner: mic muted."""
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QColor("#da4453"))
    p.drawEllipse(QRectF(0, 0, 30, 30))
    p.setBrush(QColor("white"))
    p.drawRoundedRect(QRectF(6, 12, 18, 6), 2, 2)


def render_icon(state: HeadsetState | None, fg: QColor, muted: bool = False) -> QIcon:
    """Battery glyph with the percentage (or a bolt while charging) inside."""
    size = 64
    pm = QPixmap(size, size)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)

    body = QRectF(1, 9, 56, 46)
    tip = QRectF(57, 23, 6, 18)
    connected = state is not None and state.connected and state.battery is not None

    outline = QColor(fg)
    if not connected:
        outline.setAlphaF(0.45)
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(outline)
    path = QPainterPath()
    path.addRoundedRect(body, 8, 8)
    inner = QPainterPath()
    inner.addRoundedRect(body.adjusted(5, 5, -5, -5), 4, 4)
    p.drawPath(path.subtracted(inner))
    p.drawRoundedRect(tip, 2, 2)

    if connected:
        pct = max(0.0, min(100.0, state.battery))
        fill = body.adjusted(7, 7, -7, -7)
        fill.setWidth(fill.width() * pct / 100)
        p.setBrush(battery_color(pct, state.on_charger))
        p.drawRoundedRect(fill, 2, 2)
        halo = QColor("black") if fg.lightness() > 128 else QColor("white")

        if state.on_charger:
            draw_outlined(p, bolt_path(body), fg, halo, 4)
            if muted:
                draw_mute_badge(p)
            p.end()
            return QIcon(pm)

        text = f"{int(pct)}"  # truncate like iCUE
        font = QFont()
        font.setWeight(QFont.Weight.Bold)
        font.setPixelSize(36 if len(text) < 3 else 27)
        # centre the glyphs on the body and give them a solid halo: the icon
        # is shown at ~22 px, where a 1 px offset halo disappears
        fm = QFontMetricsF(font)
        tw = fm.horizontalAdvance(text)
        base = QPointF(body.center().x() - tw / 2, body.center().y() + (fm.ascent() - fm.descent()) / 2)
        path = QPainterPath()
        path.addText(base, font, text)
        draw_outlined(p, path, fg, halo, 3)
    else:
        p.setPen(outline)
        font = QFont()
        font.setBold(True)
        font.setPixelSize(28)
        p.setFont(font)
        p.drawText(body.toRect(), Qt.AlignmentFlag.AlignCenter, "–")
    if muted and connected:
        draw_mute_badge(p)
    p.end()
    return QIcon(pm)


class Tray:
    def __init__(self, app: QApplication):
        self.app = app
        self.headset: Headset | None = None
        self.notifier: QSocketNotifier | None = None
        self.state: HeadsetState | None = None
        self.cfg = config.load()
        self.mic: MicControl | None = None
        self.muted = False
        self.warned_low = False
        self.key_bits = 0
        self.settings: HeadsetSettings | None = None
        self.window = None  # SettingsWindow, created on first use
        self.lights = Lights(self)

        self.tray = QSystemTrayIcon()
        self.menu = QMenu()
        self.status_action = QAction("Searching for headset…")
        self.status_action.setEnabled(False)
        quit_ = QAction("Quit", triggered=app.quit)
        self.menu.addAction(self.status_action)
        open_window = QAction("Settings…", triggered=self.show_window)
        self.menu.addAction(open_window)
        self.menu.addSeparator()
        self.mute_action = QAction("Microphone muted", self.menu, checkable=True)
        self.mute_action.triggered.connect(self.set_muted)
        self.menu.addAction(self.mute_action)
        self.sound_menu, self.sound_actions = self._radio_menu(
            "Mute sound",
            [("soft", "Soft"), ("beeps", "Beeps"), ("off", "Off")],
            None,
            handler=self.set_sound,
        )
        self.sound_actions.get(self.cfg["mute_sound"], self.sound_actions["soft"]).setChecked(True)
        self.menu.addSeparator()
        self.settings_menus = self.build_settings_menus()
        self.menu.addSeparator()
        self.menu.addAction(quit_)
        self._actions = (quit_, open_window)
        self.tray.setContextMenu(self.menu)
        self.tray.activated.connect(self.on_activated)

        self.poll = QTimer(interval=POLL_DISCONNECTED_MS, timeout=self.refresh)
        self.menu.aboutToShow.connect(lambda: self.refresh(with_settings=True))
        self.reconnect_timer = QTimer(interval=RECONNECT_MS, timeout=self.try_open)
        self.debounce = QTimer(singleShot=True, interval=300, timeout=self.refresh)
        app.aboutToQuit.connect(self.shutdown)
        # follow mute changes made elsewhere (OpenDeck key, KDE applet) at once
        self.mute_check = QTimer(singleShot=True, interval=100, timeout=self.on_mute_changed)
        self.pactl: QProcess | None = None
        self.watch_pipewire()

        self.update_ui()
        self.tray.show()
        self.try_open()

    # -- settings menus --------------------------------------------------
    def _radio_menu(self, title: str, items, setter, handler=None) -> tuple[QMenu, dict]:
        menu = self.menu.addMenu(title)
        group = QActionGroup(menu)
        actions = {}
        for value, label in items:
            a = QAction(label, menu, checkable=True)
            if handler:
                a.triggered.connect(lambda _c=False, v=value: handler(v))
            else:
                a.triggered.connect(lambda _c=False, v=value: self.apply(setter, v))
            group.addAction(a)
            menu.addAction(a)
            actions[value] = a
        return menu, actions

    def build_settings_menus(self) -> list[QMenu]:
        self.anc_menu, self.anc_actions = self._radio_menu(
            "Noise cancelling",
            [(AncMode.OFF, "Off"), (AncMode.ON, "On"), (AncMode.TRANSPARENCY, "Transparency")],
            lambda hs, v: hs.set_anc(v),
        )
        self.sidetone_menu, self.sidetone_actions = self._radio_menu(
            "Sidetone",
            [(0, "Off")] + [(lvl, f"{lvl}%") for lvl in SIDETONE_LEVELS],
            lambda hs, v: hs.set_sidetone(v > 0, v or None),
        )
        self.sleep_menu, self.sleep_actions = self._radio_menu(
            "Auto power-off",
            [(0, "Never")] + [(m, f"After {m} min") for m in AUTO_OFF_MINUTES],
            lambda hs, v: hs.set_sleep(v or None),
        )
        self.led_menu, self.led_actions = self._radio_menu(
            "Lighting",
            list(lighting.EFFECTS.items()),
            None,
            handler=lambda v: self.set_lighting(led_effect=v),
        )
        self.mic_led_action = QAction("Mic LED while unmuted", self.menu, checkable=True)
        self.mic_led_action.triggered.connect(lambda c: self.apply(lambda hs, v: hs.set_mic_led(v), c))
        self.menu.addAction(self.mic_led_action)
        return [self.anc_menu, self.sidetone_menu, self.sleep_menu, self.led_menu]

    def set_lighting(self, **changes) -> None:
        """Change LED settings (config keys led_*) and show them right away."""
        was = self.cfg["led_effect"]
        self.cfg.update(changes)
        config.save(self.cfg)
        if self.state and self.state.connected:
            if self.cfg["led_effect"] == "device" and was != "device":
                self.lights.stop()
                self.restore_device_lighting()
            else:
                self.lights.restart()
        self.update_ui()

    def restore_device_lighting(self) -> None:
        """A short trip through hardware mode hands the LEDs back to the
        headset's own effect."""
        if not self.headset:
            return
        try:
            self.headset.leave_software_mode()
            self.headset.enter_software_mode()
            self.key_bits = 0
        except OSError:
            self.drop_device("Receiver disconnected")
        except BragiError as e:
            print(f"lighting: {e}", flush=True)

    # -- settings window ---------------------------------------------------
    def on_activated(self, reason) -> None:
        if reason == QSystemTrayIcon.ActivationReason.Trigger:
            if self.window and self.window.isVisible() and self.window.isActiveWindow():
                self.window.hide()
            else:
                self.show_window()
        else:
            self.refresh(with_settings=True)

    def show_window(self) -> None:
        if self.window is None:
            from .window import SettingsWindow
            self.window = SettingsWindow(self)
        # settings may have changed elsewhere, e.g. on the OpenDeck plugin
        self.refresh(with_settings=True)
        self.window.set_settings(self.settings)
        self.update_ui()
        self.window.show()
        self.window.raise_()
        self.window.activateWindow()
        self.refresh(with_settings=True)

    def apply(self, setter, value) -> None:
        if not self.headset:
            return
        try:
            setter(self.headset, value)
        except OSError:
            self.drop_device("Receiver disconnected")
            return
        except BragiError as e:
            self.tray.showMessage("Could not change setting", str(e),
                                  QSystemTrayIcon.MessageIcon.Warning)
        self.refresh(with_settings=True)

    def update_settings_ui(self, st: HeadsetSettings | None) -> None:
        self.settings = st
        if self.window:
            self.window.set_settings(st)
        enabled = st is not None
        for m in self.settings_menus:
            m.setEnabled(enabled)
        self.mic_led_action.setEnabled(enabled and st.mic_led is not None)
        if not st:
            return
        effect = self.cfg["led_effect"]
        if effect in self.led_actions:
            self.led_actions[effect].setChecked(True)
        if st.anc in self.anc_actions:
            self.anc_actions[st.anc].setChecked(True)
        level = st.sidetone_level if st.sidetone_on else 0
        # snap to the closest menu entry
        closest = min(self.sidetone_actions, key=lambda v: abs(v - level))
        self.sidetone_actions[closest].setChecked(True)
        minutes = st.sleep_minutes or 0
        closest = min(self.sleep_actions, key=lambda v: abs(v - minutes))
        self.sleep_actions[closest].setChecked(True)
        self.mic_led_action.setChecked(bool(st.mic_led))

    # -- device handling -------------------------------------------------
    def try_open(self) -> None:
        try:
            self.headset = Headset.open()
        except PermissionError:
            self.set_error("No permission – install the udev rule")
            self.reconnect_timer.start()
            return
        except OSError:
            self.set_error("Receiver not found")
            self.reconnect_timer.start()
            return
        self.reconnect_timer.stop()
        self.mic = MicControl(self.headset.usb_name, self.cfg["mute_sound"])
        self.headset.bragi.on_event = self.on_event
        self.notifier = QSocketNotifier(self.headset.bragi.fileno(), QSocketNotifier.Type.Read)
        self.notifier.activated.connect(self.on_readable)
        self.poll.start()
        self.refresh()

    def drop_device(self, reason: str) -> None:
        if self.notifier:
            self.notifier.setEnabled(False)
            self.notifier.deleteLater()
            self.notifier = None
        if self.headset:
            self.headset.close()
            self.headset = None
        self.lights.stop()
        self.poll.stop()
        self.set_error(reason)
        self.reconnect_timer.start()

    def on_readable(self) -> None:
        if not self.headset:
            return
        try:
            self.headset.bragi.drain()
        except OSError:
            self.drop_device("Receiver disconnected")

    def on_event(self, pkt: bytes) -> None:
        if pkt[0] != REPORT_EVENT or len(pkt) < 4:
            return
        if pkt[2] == Event.KEY:
            pressed = pkt[3] & ~self.key_bits
            self.key_bits = pkt[3]
            if pkt[3]:
                print(f"key: {pkt[3]:#04x}", flush=True)
            if pressed & KEY_MIC:
                self.toggle_mute()
            if pressed & ~(KEY_MIC | KEY_CUSTOM):
                print(f"unknown key bits: {pressed:#04x}", flush=True)
        elif pkt[2] == Event.PROPERTY:
            self.debounce.start()

    # -- software mode / mute ---------------------------------------------
    def ensure_software_mode(self) -> bool:
        """Hold the headset in software mode so settings can be written
        without mode switches (a mode switch resets the headset's own mute).
        Returns True if the mode had to be switched."""
        if self.headset and self.headset.mode() not in (Mode.SOFTWARE, None):
            self.headset.enter_software_mode()
            self.key_bits = 0
            return True
        return False

    def set_sound(self, style: str) -> None:
        self.cfg["mute_sound"] = style
        config.save(self.cfg)
        if self.mic:
            self.mic.sound = style
            self.mic.play_feedback(self.muted)  # preview
        self.update_ui()

    def toggle_mute(self) -> None:
        if self.mic:
            self.set_muted(not self.muted)

    def set_muted(self, muted: bool) -> None:
        if self.mic and self.mic.set_muted(muted):
            self.muted = muted
        self.update_ui()

    def sync_mute(self) -> None:
        """Pick up mute changes made elsewhere (e.g. KDE volume applet)."""
        if self.mic:
            cur = self.mic.is_muted()
            if cur is not None:
                self.muted = cur

    def watch_pipewire(self) -> None:
        self.pactl = QProcess()
        self.pactl.readyReadStandardOutput.connect(self.on_pipewire_event)
        # PipeWire restarted or pactl missing: try again later
        self.pactl.finished.connect(lambda *_: QTimer.singleShot(5000, self.watch_pipewire))
        self.pactl.start("pactl", ["subscribe"])

    def on_pipewire_event(self) -> None:
        data = bytes(self.pactl.readAllStandardOutput())
        if b" on source " in data or b" on server " in data:
            self.mute_check.start()

    def on_mute_changed(self) -> None:
        was = self.muted
        self.sync_mute()
        if self.muted != was:
            self.update_ui()

    def shutdown(self) -> None:
        """Hand the headset back to hardware mode on exit.

        The PipeWire mute is left as it is, so a muted mic stays muted.
        """
        self.lights.stop()
        if self.pactl:
            self.pactl.finished.disconnect()
            self.pactl.kill()
            self.pactl.waitForFinished(1000)
        if self.headset:
            try:
                if self.headset.is_connected():
                    self.headset.leave_software_mode()
            except (OSError, BragiError):
                pass

    def refresh(self, with_settings: bool = False) -> None:
        if not self.headset:
            return
        settings = None
        was_connected = bool(self.state and self.state.connected)
        relight = False
        try:
            self.state = self.headset.read_state()
            if self.state.connected:
                # (re-)take control, e.g. after the headset was power-cycled
                switched = self.ensure_software_mode()
                relight = switched or not was_connected or not self.lights.active
                if with_settings or not was_connected:
                    settings = self.headset.read_settings()
        except OSError:
            self.drop_device("Receiver disconnected")
            return
        except BragiError:
            self.state = HeadsetState(connected=False)
        if not self.state.connected:
            self.lights.stop()
            self.headset.forget_leds()
        elif relight and self.cfg["led_effect"] != "device":
            self.lights.restart()
        self.poll.setInterval(POLL_CONNECTED_MS if self.state.connected else POLL_DISCONNECTED_MS)
        self.log_state_change()
        self.sync_mute()
        if settings is not None or not self.state.connected:
            self.update_settings_ui(settings)
        self.update_ui()

    def log_state_change(self) -> None:
        st = self.state
        key = None if st is None else (st.connected, st.battery, st.status, st.external_power)
        if key != getattr(self, "_last_logged", object()):
            self._last_logged = key
            print(f"state: {st}", flush=True)

    # -- UI --------------------------------------------------------------
    def set_error(self, text: str) -> None:
        self.state = None
        self.update_settings_ui(None)
        self.status_action.setText(text)
        self.tray.setIcon(render_icon(None, self.fg()))
        self.tray.setToolTip(f"OpenCorsair: {text}")
        self.update_window(self.headset.model if self.headset else "Corsair headset", text)

    def fg(self) -> QColor:
        return plasma_panel_fg() or self.app.palette().windowText().color()

    def update_ui(self) -> None:
        st = self.state
        model = self.headset.model if self.headset else "Corsair headset"
        if st is None:
            self.tray.setIcon(render_icon(None, self.fg()))
            self.tray.setToolTip("OpenCorsair")
            self.update_window(model, self.status_action.text())
            return
        status = ""
        if not st.connected:
            text = f"{model}: not connected"
            status = "Not connected"
            self.warned_low = False
        else:
            status = "charging" if st.on_charger and st.status != BatteryStatus.FULL else {
                BatteryStatus.CHARGING: "charging",
                BatteryStatus.DISCHARGING: "on battery",
                BatteryStatus.FULL: "fully charged",
            }.get(st.status, "")
            text = f"{model}: {int(st.battery)}%" + (f" ({status})" if status else "")
            status = status.capitalize()
            self.check_low_battery(st, model)
        if st.connected and self.muted:
            text += " · mic muted"
        self.mute_action.setEnabled(self.mic is not None and self.mic.source() is not None)
        self.mute_action.setChecked(self.muted)
        self.status_action.setText(text)
        self.tray.setToolTip(text)
        self.tray.setIcon(render_icon(st, self.fg(), self.muted and st.connected))
        self.update_window(model, status)

    def update_window(self, model: str, status: str) -> None:
        if not self.window:
            return
        self.window.set_model(model)
        self.window.set_state(self.state, status, self.muted, self.mute_action.isEnabled())

    def check_low_battery(self, st: HeadsetState, model: str) -> None:
        low = st.battery is not None and st.battery <= LOW_BATTERY and not st.on_charger
        if low and not self.warned_low:
            self.tray.showMessage(
                "Headset battery low",
                f"{model}: {int(st.battery)}% remaining",
                QSystemTrayIcon.MessageIcon.Warning,
            )
        self.warned_low = low


def main(show_settings: bool = False) -> None:
    app = QApplication(sys.argv[:1])
    app.setApplicationName("OpenCorsair")
    app.setDesktopFileName("opencorsair")
    app.setQuitOnLastWindowClosed(False)
    # quit cleanly on Ctrl+C / systemctl stop, so the headset gets handed back
    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, lambda *_: app.quit())
    tick = QTimer(interval=500, timeout=lambda: None)  # let Python see signals
    tick.start()
    if not QSystemTrayIcon.isSystemTrayAvailable():
        print("no system tray available", file=sys.stderr)
    _tray = Tray(app)
    if show_settings:
        _tray.show_window()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
