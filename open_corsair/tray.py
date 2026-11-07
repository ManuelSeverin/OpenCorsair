"""System tray battery indicator (PyQt6, works with KDE/GNOME StatusNotifier)."""
from __future__ import annotations

import signal
import sys
import time

from PyQt6.QtCore import QRectF, Qt, QSocketNotifier, QTimer
from PyQt6.QtGui import (
    QAction,
    QActionGroup,
    QColor,
    QFont,
    QIcon,
    QPainter,
    QPainterPath,
    QPen,
    QPixmap,
)
from PyQt6.QtWidgets import QApplication, QMenu, QSystemTrayIcon

from .bragi import KEY_MIC, REPORT_EVENT, AncMode, BatteryStatus, BragiError, Event, Mode
from .headset import Headset, HeadsetSettings, HeadsetState
from .mic import MicControl

HEARTBEAT_MS = 5_000
POLL_MS = 20_000  # the headset sends no events in hardware mode, so poll
RECONNECT_MS = 5_000
LOW_BATTERY = 15
SIDETONE_LEVELS = (10, 20, 30, 40, 50, 60, 70, 80, 90, 100)
AUTO_OFF_MINUTES = (5, 10, 15, 30, 60, 90)


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
            bolt = bolt_path(body)
            pen = QPen(halo, 3)
            pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
            p.setPen(pen)
            p.setBrush(fg)
            p.drawPath(bolt)
            if muted:
                draw_mute_badge(p)
            p.end()
            return QIcon(pm)

        text = f"{pct:.0f}"
        font = QFont()
        font.setBold(True)
        font.setPixelSize(36 if len(text) < 3 else 27)
        p.setFont(font)
        # draw text with a contrasting halo so it stays readable on the fill
        rect = body.toRect()
        p.setPen(halo)
        for dx, dy in ((-1, 0), (1, 0), (0, -1), (0, 1)):
            p.drawText(rect.translated(dx, dy), Qt.AlignmentFlag.AlignCenter, text)
        p.setPen(fg)
        p.drawText(rect, Qt.AlignmentFlag.AlignCenter, text)
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
        self.mic: MicControl | None = None
        self.muted = False
        self.warned_low = False
        self.key_bits = 0

        self.tray = QSystemTrayIcon()
        self.menu = QMenu()
        self.status_action = QAction("Searching for headset…")
        self.status_action.setEnabled(False)
        self.detail_action = QAction("")
        self.detail_action.setEnabled(False)
        self.detail_action.setVisible(False)
        refresh = QAction("Refresh", triggered=self.refresh)
        quit_ = QAction("Quit", triggered=app.quit)
        for a in (self.status_action, self.detail_action):
            self.menu.addAction(a)
        self.menu.addSeparator()
        self.mute_action = QAction("Microphone muted", self.menu, checkable=True)
        self.mute_action.triggered.connect(self.set_muted)
        self.menu.addAction(self.mute_action)
        self.menu.addSeparator()
        self.settings_menus = self.build_settings_menus()
        self.menu.addSeparator()
        self.menu.addAction(refresh)
        self.menu.addAction(quit_)
        self._actions = (refresh, quit_)
        self.tray.setContextMenu(self.menu)
        self.tray.activated.connect(lambda _reason: self.refresh())

        self.poll = QTimer(interval=POLL_MS, timeout=self.refresh)
        self.reconnect_timer = QTimer(interval=RECONNECT_MS, timeout=self.try_open)
        self.debounce = QTimer(singleShot=True, interval=300, timeout=self.refresh)
        self.heartbeat_timer = QTimer(interval=HEARTBEAT_MS, timeout=self.heartbeat)
        app.aboutToQuit.connect(self.shutdown)

        self.update_ui()
        self.tray.show()
        self.try_open()

    # -- settings menus --------------------------------------------------
    def _radio_menu(self, title: str, items, setter) -> tuple[QMenu, dict]:
        menu = self.menu.addMenu(title)
        group = QActionGroup(menu)
        actions = {}
        for value, label in items:
            a = QAction(label, menu, checkable=True)
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
        self.mic_led_action = QAction("Mic mute LED", self.menu, checkable=True)
        self.mic_led_action.triggered.connect(lambda c: self.apply(lambda hs, v: hs.set_mic_led(v), c))
        self.menu.addAction(self.mic_led_action)
        return [self.anc_menu, self.sidetone_menu, self.sleep_menu]

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
        self.refresh()

    def update_settings_ui(self, st: HeadsetSettings | None) -> None:
        enabled = st is not None
        for m in self.settings_menus:
            m.setEnabled(enabled)
        self.mic_led_action.setEnabled(enabled and st.mic_led is not None)
        if not st:
            return
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
        self.mic = MicControl(self.headset.usb_name)
        self.headset.bragi.on_event = self.on_event
        self.notifier = QSocketNotifier(self.headset.bragi.fileno(), QSocketNotifier.Type.Read)
        self.notifier.activated.connect(self.on_readable)
        self.poll.start()
        self.heartbeat_timer.start()
        self.refresh()

    def drop_device(self, reason: str) -> None:
        if self.notifier:
            self.notifier.setEnabled(False)
            self.notifier.deleteLater()
            self.notifier = None
        if self.headset:
            self.headset.close()
            self.headset = None
        self.poll.stop()
        self.heartbeat_timer.stop()
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
            if pressed & KEY_MIC:
                self.toggle_mute()
        elif pkt[2] == Event.PROPERTY:
            self.debounce.start()

    # -- software mode / mute ---------------------------------------------
    def ensure_software_mode(self) -> None:
        """Hold the headset in software mode so settings can be written
        without mode switches (a mode switch resets the headset's own mute)."""
        if self.headset and self.headset.mode() not in (Mode.SOFTWARE, None):
            self.headset.enter_software_mode()
            self.key_bits = 0

    def heartbeat(self) -> None:
        if not self.headset or not (self.state and self.state.connected):
            return
        try:
            # also re-takes control quickly after the headset was power-cycled
            self.ensure_software_mode()
            self.headset.heartbeat()
        except OSError:
            self.drop_device("Receiver disconnected")
        except BragiError:
            pass

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

    def shutdown(self) -> None:
        """Hand the headset back to hardware mode on exit.

        The PipeWire mute is left as it is, so a muted mic stays muted.
        """
        self.heartbeat_timer.stop()
        if self.headset:
            try:
                if self.headset.is_connected():
                    self.headset.leave_software_mode()
            except (OSError, BragiError):
                pass

    def refresh(self) -> None:
        if not self.headset:
            return
        settings = None
        try:
            self.state = self.headset.read_state()
            if self.state.connected:
                self.ensure_software_mode()
                settings = self.headset.read_settings()
        except OSError:
            self.drop_device("Receiver disconnected")
            return
        except BragiError:
            self.state = HeadsetState(connected=False)
        self.sync_mute()
        self.update_settings_ui(settings)
        self.update_ui()

    # -- UI --------------------------------------------------------------
    def set_error(self, text: str) -> None:
        self.state = None
        self.update_settings_ui(None)
        self.status_action.setText(text)
        self.detail_action.setVisible(False)
        self.tray.setIcon(render_icon(None, self.fg()))
        self.tray.setToolTip(f"open-corsair: {text}")

    def fg(self) -> QColor:
        return self.app.palette().windowText().color()

    def update_ui(self) -> None:
        st = self.state
        model = self.headset.model if self.headset else "Corsair headset"
        if st is None:
            self.tray.setIcon(render_icon(None, self.fg()))
            self.tray.setToolTip("open-corsair")
            return
        if not st.connected:
            text = f"{model}: not connected"
            self.detail_action.setVisible(False)
            self.warned_low = False
        else:
            status = "charging" if st.on_charger and st.status != BatteryStatus.FULL else {
                BatteryStatus.CHARGING: "charging",
                BatteryStatus.DISCHARGING: "on battery",
                BatteryStatus.FULL: "fully charged",
            }.get(st.status, "unknown")
            text = f"{model}: {st.battery:.0f}% ({status})"
            if st.voltage_mv:
                self.detail_action.setText(
                    f"{st.battery:.1f}% · {st.voltage_mv / 1000:.2f} V · updated {time.strftime('%H:%M')}"
                )
                self.detail_action.setVisible(True)
            self.check_low_battery(st, model)
        if st.connected and self.muted:
            text += " · mic muted"
        self.mute_action.setEnabled(self.mic is not None and self.mic.source() is not None)
        self.mute_action.setChecked(self.muted)
        self.status_action.setText(text)
        self.tray.setToolTip(text)
        self.tray.setIcon(render_icon(st, self.fg(), self.muted and st.connected))

    def check_low_battery(self, st: HeadsetState, model: str) -> None:
        low = st.battery is not None and st.battery <= LOW_BATTERY and not st.on_charger
        if low and not self.warned_low:
            self.tray.showMessage(
                "Headset battery low",
                f"{model}: {st.battery:.0f}% remaining",
                QSystemTrayIcon.MessageIcon.Warning,
            )
        self.warned_low = low


def main() -> None:
    app = QApplication(sys.argv)
    app.setApplicationName("open-corsair")
    app.setDesktopFileName("open-corsair")
    app.setQuitOnLastWindowClosed(False)
    # quit cleanly on Ctrl+C / systemctl stop, so the headset gets handed back
    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, lambda *_: app.quit())
    tick = QTimer(interval=500, timeout=lambda: None)  # let Python see signals
    tick.start()
    if not QSystemTrayIcon.isSystemTrayAvailable():
        print("no system tray available", file=sys.stderr)
    _tray = Tray(app)
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
