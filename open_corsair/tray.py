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

from .bragi import REPORT_EVENT, AncMode, BatteryStatus, BragiError
from .headset import Headset, HeadsetSettings, HeadsetState

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


def render_icon(state: HeadsetState | None, fg: QColor) -> QIcon:
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
    p.end()
    return QIcon(pm)


class Tray:
    def __init__(self, app: QApplication):
        self.app = app
        self.headset: Headset | None = None
        self.notifier: QSocketNotifier | None = None
        self.state: HeadsetState | None = None
        self.warned_low = False

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
            if setter(self.headset, value):
                # firmware quirk: every write session unmutes the microphone
                self.tray.showMessage(
                    "Setting changed",
                    "Note: the headset unmutes the microphone when settings change.",
                    QSystemTrayIcon.MessageIcon.Information,
                    4000,
                )
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
        if pkt[0] == REPORT_EVENT:
            self.debounce.start()

    def refresh(self) -> None:
        if not self.headset:
            return
        settings = None
        try:
            self.state = self.headset.read_state()
            if self.state.connected:
                settings = self.headset.read_settings()
        except OSError:
            self.drop_device("Receiver disconnected")
            return
        except BragiError:
            self.state = HeadsetState(connected=False)
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
        self.status_action.setText(text)
        self.tray.setToolTip(text)
        self.tray.setIcon(render_icon(st, self.fg()))

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
    signal.signal(signal.SIGINT, signal.SIG_DFL)
    if not QSystemTrayIcon.isSystemTrayAvailable():
        print("no system tray available", file=sys.stderr)
    _tray = Tray(app)
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
