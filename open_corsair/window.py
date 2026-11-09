"""Settings window: headset picture with live status, plus all settings.

The window lives in the tray process. The tray owns the device, the mic mute
and the polling; the window only shows what the tray reads and sends changes
back through it.
"""
from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from PyQt6.QtCore import QPointF, QRectF, QSize, Qt, QTimer
from PyQt6.QtGui import (
    QBrush,
    QColor,
    QFont,
    QIcon,
    QPainter,
    QPainterPath,
    QPalette,
    QPen,
    QPixmap,
    QRadialGradient,
)
from PyQt6.QtWidgets import (
    QButtonGroup,
    QCheckBox,
    QColorDialog,
    QComboBox,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSlider,
    QVBoxLayout,
    QWidget,
)

from . import lighting
from .bragi import AncMode, BatteryStatus, WheelFunction
from .headset import HeadsetSettings, HeadsetState

if TYPE_CHECKING:
    from .tray import Tray

ASSETS = Path(__file__).parent / "assets"
# model -> picture, and where the mic tip is (fraction of the picture size)
PICTURES = {
    "Virtuoso MAX Wireless": ("virtuoso_max_wireless.png", QPointF(0.266, 0.947)),
}
SIDETONE_DEFAULT = 50
AUTO_OFF_MINUTES = (5, 10, 15, 30, 60, 90)
# brightness steps iCUE offers for this headset
BRIGHTNESS_STEPS = (0, 12, 25, 37, 50, 62, 75, 87, 100)
WHEEL_CHOICES = (
    (WheelFunction.SYSTEM_VOLUME, "System volume"),
    (WheelFunction.BLUETOOTH_VOLUME, "Bluetooth volume"),
    (0, "Disabled"),
)


def is_dark(widget: QWidget) -> bool:
    return widget.palette().color(QPalette.ColorRole.Window).lightness() < 128


def battery_color(pct: float, charging: bool) -> QColor:
    if charging:
        return QColor("#27ae60") if pct >= 99 else QColor("#3daee9")
    if pct <= 10:
        return QColor("#da4453")
    if pct <= 33:
        return QColor("#f67400")
    return QColor("#27ae60")


class HeadsetView(QWidget):
    """The headset picture on a soft spotlight, with status drawn on top."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.picture = QPixmap()
        self.mic_tip = QPointF(0.5, 0.95)
        self.connected = False
        self.muted = False
        self.anc: AncMode | None = None
        self.setMinimumSize(260, 300)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

    def set_model(self, model: str) -> None:
        name, tip = PICTURES.get(model, (None, QPointF(0.5, 0.95)))
        self.picture = QPixmap(str(ASSETS / name)) if name else QPixmap()
        self.mic_tip = tip
        self.update()

    def set_status(self, connected: bool, muted: bool, anc: AncMode | None) -> None:
        if (connected, muted, anc) != (self.connected, self.muted, self.anc):
            self.connected, self.muted, self.anc = connected, muted, anc
            self.update()

    def paintEvent(self, _ev) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        r = QRectF(self.rect())

        dark = is_dark(self)
        bg = QRadialGradient(r.center().x(), r.height() * 0.45, max(r.width(), r.height()) * 0.7)
        if dark:
            bg.setColorAt(0, QColor("#5a606a"))
            bg.setColorAt(1, QColor("#25282d"))
        else:
            bg.setColorAt(0, QColor("#ffffff"))
            bg.setColorAt(1, QColor("#cfd3d9"))
        path = QPainterPath()
        path.addRoundedRect(r.adjusted(0.5, 0.5, -0.5, -0.5), 14, 14)
        p.fillPath(path, QBrush(bg))

        if self.picture.isNull():
            p.setPen(self.palette().color(QPalette.ColorRole.PlaceholderText))
            p.drawText(r, Qt.AlignmentFlag.AlignCenter, "No picture for this model")
            return

        area = r.adjusted(24, 24, -24, -24)
        size = self.picture.size().toSizeF().scaled(area.size(), Qt.AspectRatioMode.KeepAspectRatio)
        target = QRectF(QPointF(0, 0), size)
        target.moveCenter(area.center())

        # floor shadow
        shadow = QRadialGradient(target.center().x(), target.bottom() - 4, target.width() * 0.45)
        shadow.setColorAt(0, QColor(0, 0, 0, 70 if not dark else 110))
        shadow.setColorAt(1, QColor(0, 0, 0, 0))
        p.save()
        p.translate(target.center().x(), target.bottom() - 4)
        p.scale(1, 0.12)
        p.translate(-target.center().x(), -(target.bottom() - 4))
        p.fillRect(QRectF(target.left(), target.bottom() - 4 - target.width() * 0.45,
                          target.width(), target.width() * 0.9), QBrush(shadow))
        p.restore()

        if not self.connected:
            p.setOpacity(0.35)
        p.drawPixmap(target, self.picture, QRectF(self.picture.rect()))
        p.setOpacity(1.0)

        if self.connected and self.muted:
            tip = QPointF(target.left() + self.mic_tip.x() * target.width(),
                          target.top() + self.mic_tip.y() * target.height())
            self.draw_mute_badge(p, tip)
        if self.connected and self.anc is not None:
            self.draw_anc_chip(p, r)
        if not self.connected:
            self.draw_chip(p, r, "Not connected", QColor("#7f8c8d"))

    def draw_mute_badge(self, p: QPainter, tip: QPointF) -> None:
        rad = 15
        c = QPointF(tip.x() - rad - 6, tip.y())
        glow = QRadialGradient(c, rad * 2.2)
        glow.setColorAt(0, QColor(218, 68, 83, 120))
        glow.setColorAt(1, QColor(218, 68, 83, 0))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QBrush(glow))
        p.drawEllipse(c, rad * 2.2, rad * 2.2)
        p.setBrush(QColor("#da4453"))
        p.setPen(QPen(QColor("white"), 2))
        p.drawEllipse(c, rad, rad)
        # microphone glyph with a slash
        p.setPen(QPen(QColor("white"), 2, cap=Qt.PenCapStyle.RoundCap))
        p.setBrush(Qt.BrushStyle.NoBrush)
        body = QRectF(c.x() - 3.5, c.y() - 9, 7, 11)
        p.drawRoundedRect(body, 3.5, 3.5)
        p.drawArc(QRectF(c.x() - 6.5, c.y() - 4, 13, 9), 180 * 16, 180 * 16)
        p.drawLine(QPointF(c.x(), c.y() + 5), QPointF(c.x(), c.y() + 8))
        p.drawLine(QPointF(c.x() - 8, c.y() - 8), QPointF(c.x() + 8, c.y() + 8))

    def draw_anc_chip(self, p: QPainter, r: QRectF) -> None:
        text, color = {
            AncMode.OFF: ("ANC off", QColor("#7f8c8d")),
            AncMode.ON: ("Noise cancelling", QColor("#2980b9")),
            AncMode.TRANSPARENCY: ("Transparency", QColor("#16a085")),
        }[self.anc]
        self.draw_chip(p, r, text, color)

    def draw_chip(self, p: QPainter, r: QRectF, text: str, color: QColor) -> None:
        font = QFont(self.font())
        font.setPointSizeF(font.pointSizeF() * 0.9)
        font.setBold(True)
        p.setFont(font)
        w = p.fontMetrics().horizontalAdvance(text) + 24
        chip = QRectF(r.left() + 14, r.top() + 14, w, 26)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(color)
        p.drawRoundedRect(chip, 13, 13)
        p.setPen(QColor("white"))
        p.drawText(chip, Qt.AlignmentFlag.AlignCenter, text)


class BatteryBar(QWidget):
    """Wide battery gauge with the percentage and charge state."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.state: HeadsetState | None = None
        self.setFixedHeight(34)

    def set_state(self, st: HeadsetState | None) -> None:
        self.state = st
        self.update()

    def paintEvent(self, _ev) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(self.rect()).adjusted(1, 1, -1, -1)
        track = QColor(self.palette().color(QPalette.ColorRole.WindowText))
        track.setAlpha(40)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(track)
        p.drawRoundedRect(r, 8, 8)
        st = self.state
        fg = self.palette().color(QPalette.ColorRole.WindowText)
        if not st or not st.connected or st.battery is None:
            p.setPen(fg)
            p.drawText(r, Qt.AlignmentFlag.AlignCenter, "–")
            return
        pct = max(0.0, min(100.0, st.battery))
        fill = QRectF(r)
        fill.setWidth(max(16.0, r.width() * pct / 100))
        p.setBrush(battery_color(pct, st.on_charger))
        p.drawRoundedRect(fill, 8, 8)
        font = QFont(self.font())
        font.setBold(True)
        p.setFont(font)
        label = f"{int(pct)} %"
        if st.on_charger:
            label = ("⚡ " if st.status != BatteryStatus.FULL else "") + label
        # white text with a dark halo stays readable on the fill and the track
        for dx, dy in ((-1, 0), (1, 0), (0, -1), (0, 1)):
            p.setPen(QColor(0, 0, 0, 110))
            p.drawText(r.translated(dx, dy), Qt.AlignmentFlag.AlignCenter, label)
        p.setPen(QColor("white"))
        p.drawText(r, Qt.AlignmentFlag.AlignCenter, label)


class Card(QFrame):
    """Titled group of settings."""

    def __init__(self, title: str, parent=None):
        super().__init__(parent)
        self.setObjectName("card")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(16, 12, 16, 14)
        lay.setSpacing(10)
        head = QLabel(title)
        head.setObjectName("cardTitle")
        lay.addWidget(head)
        self.grid = QGridLayout()
        self.grid.setHorizontalSpacing(14)
        self.grid.setVerticalSpacing(10)
        self.grid.setColumnStretch(1, 1)
        lay.addLayout(self.grid)

    def row(self, label: str, widget: QWidget, hint: str | None = None) -> None:
        r = self.grid.rowCount()
        lbl = QLabel(label)
        lbl.setBuddy(widget)
        self.grid.addWidget(lbl, r, 0, Qt.AlignmentFlag.AlignVCenter)
        self.grid.addWidget(widget, r, 1)
        if hint:
            lbl.setToolTip(hint)
            widget.setToolTip(hint)

    def full(self, widget: QWidget) -> None:
        self.grid.addWidget(widget, self.grid.rowCount(), 0, 1, 2)


class Segmented(QWidget):
    """Row of exclusive toggle buttons."""

    def __init__(self, items, on_pick, parent=None):
        super().__init__(parent)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        self.group = QButtonGroup(self)
        self.buttons = {}
        for i, (value, label) in enumerate(items):
            b = QPushButton(label)
            b.setCheckable(True)
            b.setObjectName("seg")
            b.setProperty("pos", "first" if i == 0 else "last" if i == len(items) - 1 else "mid")
            b.clicked.connect(lambda _c=False, v=value: on_pick(v))
            self.group.addButton(b)
            lay.addWidget(b)
            self.buttons[value] = b

    def set_value(self, value) -> None:
        b = self.buttons.get(value)
        if b:
            b.setChecked(True)
        else:  # nothing known: clear the selection
            self.group.setExclusive(False)
            for x in self.buttons.values():
                x.setChecked(False)
            self.group.setExclusive(True)


class LevelSlider(QWidget):
    """Slider with a value label; reports a value once the user lets go."""

    def __init__(self, on_commit, steps=None, parent=None):
        super().__init__(parent)
        self.on_commit = on_commit
        self.steps = steps
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        self.slider = QSlider(Qt.Orientation.Horizontal)
        if steps:
            self.slider.setRange(0, len(steps) - 1)
            self.slider.setPageStep(1)
            self.slider.setTickPosition(QSlider.TickPosition.TicksBelow)
        else:
            self.slider.setRange(0, 100)
            self.slider.setPageStep(10)
        self.label = QLabel()
        self.label.setMinimumWidth(QLabel("100 %").sizeHint().width())
        self.label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        lay.addWidget(self.slider, 1)
        lay.addWidget(self.label)
        # keyboard and wheel changes are committed after a short pause
        self.commit_timer = QTimer(self, singleShot=True, interval=500, timeout=self.commit)
        self.slider.valueChanged.connect(self.on_changed)
        self.slider.sliderReleased.connect(self.commit)

    def value(self) -> int:
        v = self.slider.value()
        return self.steps[v] if self.steps else v

    def set_value(self, percent: int) -> None:
        if self.slider.isSliderDown() or self.commit_timer.isActive():
            return  # the user is busy with it
        self.slider.blockSignals(True)
        if self.steps:
            self.slider.setValue(min(range(len(self.steps)), key=lambda i: abs(self.steps[i] - percent)))
        else:
            self.slider.setValue(percent)
        self.slider.blockSignals(False)
        self.label.setText(f"{self.value()} %")

    def on_changed(self, _v) -> None:
        self.label.setText(f"{self.value()} %")
        if not self.slider.isSliderDown():
            self.commit_timer.start()

    def commit(self) -> None:
        self.commit_timer.stop()
        self.on_commit(self.value())


class ColorButton(QPushButton):
    """Colour swatch; opens a colour picker and reports the chosen #rrggbb."""

    def __init__(self, title: str, on_pick, parent=None):
        super().__init__(parent)
        self.title = title
        self.on_pick = on_pick
        self.color = QColor("#ffffff")
        self.setMinimumWidth(110)
        self.clicked.connect(self.pick)

    def set_color(self, text: str) -> None:
        self.color = QColor(text)
        self.setText(self.color.name())
        self.setIcon(self._swatch())

    def _swatch(self) -> QIcon:
        pm = QPixmap(18, 18)
        pm.fill(Qt.GlobalColor.transparent)
        p = QPainter(pm)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(QPen(QColor(0, 0, 0, 90), 1))
        p.setBrush(self.color)
        p.drawRoundedRect(QRectF(0.5, 0.5, 17, 17), 4, 4)
        p.end()
        return QIcon(pm)

    def pick(self) -> None:
        c = QColorDialog.getColor(self.color, self, self.title)
        if c.isValid():
            self.set_color(c.name())
            self.on_pick(c.name())


def combo(items, on_pick) -> QComboBox:
    c = QComboBox()
    for value, label in items:
        # plain ints: findData() does not match IntEnum members against ints
        c.addItem(label, int(value) if isinstance(value, int) else value)
    c.activated.connect(lambda i: on_pick(c.itemData(i)))
    return c


def set_combo(c: QComboBox, value, label: str | None = None) -> None:
    i = c.findData(value)
    if i < 0 and label is not None:
        c.addItem(label, value)
        i = c.count() - 1
    c.blockSignals(True)
    c.setCurrentIndex(i)
    c.blockSignals(False)


STYLE = """
QFrame#card {
    background: palette(base);
    border: 1px solid %(border)s;
    border-radius: 12px;
}
QLabel#cardTitle { font-weight: 600; font-size: %(title)spt; }
QLabel#model { font-size: %(model)spt; font-weight: 600; }
QLabel#muted { color: palette(placeholder-text); }
QPushButton#seg {
    padding: 6px 12px;
    border: 1px solid %(border)s;
    background: palette(button);
    border-radius: 0;
}
QPushButton#seg[pos="first"] { border-top-left-radius: 8px; border-bottom-left-radius: 8px; }
QPushButton#seg[pos="last"] { border-top-right-radius: 8px; border-bottom-right-radius: 8px; }
QPushButton#seg:checked {
    background: palette(highlight);
    color: palette(highlighted-text);
    border-color: palette(highlight);
}
QPushButton#seg:disabled:checked { background: %(border)s; }
QScrollArea, QScrollArea > QWidget > QWidget { background: transparent; }
"""


class SettingsWindow(QWidget):
    def __init__(self, tray: "Tray"):
        super().__init__()
        self.tray = tray
        self.setWindowTitle("OpenCorsair")
        self.setWindowIcon(QIcon.fromTheme("opencorsair", QIcon(str(ASSETS / "virtuoso_max_wireless.png"))))
        self.resize(900, 640)
        self.setMinimumSize(QSize(720, 520))
        self.model = None
        self._anc: AncMode | None = None

        root = QHBoxLayout(self)
        root.setContentsMargins(16, 16, 16, 16)
        root.setSpacing(16)

        # -- left: picture and status ------------------------------------
        left = QVBoxLayout()
        left.setSpacing(10)
        self.view = HeadsetView()
        left.addWidget(self.view, 1)
        self.model_label = QLabel("Corsair headset")
        self.model_label.setObjectName("model")
        left.addWidget(self.model_label)
        self.battery = BatteryBar()
        left.addWidget(self.battery)
        self.status_label = QLabel("Searching for headset…")
        self.status_label.setObjectName("muted")
        self.status_label.setWordWrap(True)
        left.addWidget(self.status_label)
        left_box = QWidget()
        left_box.setLayout(left)
        left_box.setMinimumWidth(300)
        root.addWidget(left_box, 5)

        # -- right: settings -----------------------------------------------
        cards = QVBoxLayout()
        cards.setSpacing(12)
        cards.setContentsMargins(0, 0, 4, 0)

        sound = Card("Sound")
        self.anc = Segmented(
            [(AncMode.OFF, "Off"), (AncMode.ON, "Noise cancelling"), (AncMode.TRANSPARENCY, "Transparency")],
            lambda v: self.apply(lambda hs, x: hs.set_anc(x), v),
        )
        sound.row("Noise control", self.anc, "Noise cancelling turns sidetone off")
        cards.addWidget(sound)

        mic = Card("Microphone")
        self.mute = QCheckBox("Muted")
        self.mute.clicked.connect(self.tray.set_muted)
        mic.row("Microphone", self.mute, "Also toggled with the button on the headset")
        self.mute_sound = combo([("soft", "Soft"), ("beeps", "Beeps"), ("off", "Off")], self.tray.set_sound)
        mic.row("Mute sound", self.mute_sound, "Played in the headset when the mic is muted or unmuted")
        self.mic_led = QCheckBox("Light up while unmuted")
        self.mic_led.clicked.connect(lambda c: self.apply(lambda hs, v: hs.set_mic_led(v), c))
        mic.row("Mic LED", self.mic_led)
        self.sidetone_on = QCheckBox("Hear your own voice")
        self.sidetone_on.clicked.connect(self.on_sidetone_toggle)
        mic.row("Sidetone", self.sidetone_on, "Turning sidetone on turns noise cancelling off")
        self.sidetone = LevelSlider(lambda v: self.apply(lambda hs, x: hs.set_sidetone(True, x), v))
        mic.row("Sidetone level", self.sidetone)
        cards.addWidget(mic)

        wheels = Card("Earcup wheels")
        self.wheel_left = combo(WHEEL_CHOICES, lambda v: self.apply(lambda hs, x: hs.set_wheel("left", x), v))
        wheels.row("Left wheel", self.wheel_left)
        self.wheel_right = combo(WHEEL_CHOICES, lambda v: self.apply(lambda hs, x: hs.set_wheel("right", x), v))
        wheels.row("Right wheel", self.wheel_right)
        cards.addWidget(wheels)

        lights = Card("Lighting")
        self.led_effect = combo(list(lighting.EFFECTS.items()),
                                lambda v: self.tray.set_lighting(led_effect=v))
        lights.row("Effect", self.led_effect,
                   "Headset default leaves the LEDs as the headset shows them. Animated "
                   "effects send updates over the radio all the time, which costs some battery.")
        self.led_left = ColorButton("Left earcup", lambda c: self.tray.set_lighting(led_left=c))
        lights.row("Left colour", self.led_left)
        self.led_right = ColorButton("Right earcup", lambda c: self.tray.set_lighting(led_right=c))
        lights.row("Right colour", self.led_right)
        self.led_speed = combo([(k, v[0]) for k, v in lighting.SPEEDS.items()],
                               lambda v: self.tray.set_lighting(led_speed=v))
        lights.row("Speed", self.led_speed)
        self.brightness = LevelSlider(lambda v: self.apply(lambda hs, x: hs.set_brightness(x), v),
                                      steps=BRIGHTNESS_STEPS)
        lights.row("Brightness", self.brightness)
        cards.addWidget(lights)

        misc = Card("Power")
        self.auto_off = combo([(0, "Never")] + [(m, f"After {m} min") for m in AUTO_OFF_MINUTES],
                              lambda v: self.apply(lambda hs, x: hs.set_sleep(x or None), v))
        misc.row("Auto power-off", self.auto_off, "Turn the headset off when it is idle")
        cards.addWidget(misc)
        cards.addStretch(1)

        self.settings_widgets = (self.anc, self.mic_led, self.sidetone_on, self.sidetone,
                                 self.wheel_left, self.wheel_right, self.brightness, self.auto_off,
                                 self.led_effect, self.led_left, self.led_right, self.led_speed)

        inner = QWidget()
        inner.setLayout(cards)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setWidget(inner)
        scroll.setMinimumWidth(380)
        root.addWidget(scroll, 6)

        self.apply_style()
        self.set_settings(None)

    def apply_style(self) -> None:
        border = self.palette().color(QPalette.ColorRole.Mid)
        border.setAlpha(110)
        base = self.font().pointSizeF()
        self.setStyleSheet(STYLE % {
            "border": border.name(QColor.NameFormat.HexArgb),
            "title": f"{base * 1.1:.1f}",
            "model": f"{base * 1.35:.1f}",
        })

    def changeEvent(self, ev) -> None:
        if ev.type() == ev.Type.PaletteChange:
            self.apply_style()
        super().changeEvent(ev)

    # -- actions ---------------------------------------------------------
    def apply(self, setter, value) -> None:
        self.tray.apply(setter, value)

    def on_sidetone_toggle(self, on: bool) -> None:
        level = self.sidetone.value() if on else None
        if on and not level:
            level = SIDETONE_DEFAULT
        self.apply(lambda hs, v: hs.set_sidetone(on, v), level)

    # -- updates from the tray ---------------------------------------------
    def set_model(self, model: str) -> None:
        if model != self.model:
            self.model = model
            self.model_label.setText(model)
            self.view.set_model(model)

    def set_state(self, st: HeadsetState | None, status: str, muted: bool,
                  mute_available: bool) -> None:
        connected = bool(st and st.connected)
        self.battery.set_state(st)
        self.status_label.setText(status)
        self.view.set_status(connected, muted, self._anc)
        self.mute.setEnabled(mute_available)
        self.mute.setChecked(muted)
        set_combo(self.mute_sound, self.tray.cfg["mute_sound"])
        self.set_lighting(self.tray.cfg)

    def set_lighting(self, cfg: dict) -> None:
        effect = cfg["led_effect"]
        set_combo(self.led_effect, effect)
        set_combo(self.led_speed, cfg["led_speed"])
        self.led_left.set_color(cfg["led_left"])
        self.led_right.set_color(cfg["led_right"])
        on = self.led_effect.isEnabled()
        self.led_left.setEnabled(on and effect in lighting.USES_COLORS)
        self.led_right.setEnabled(on and effect in lighting.USES_COLORS)
        self.led_speed.setEnabled(on and effect in lighting.ANIMATED)

    def showEvent(self, ev) -> None:
        self.apply_style()
        super().showEvent(ev)

    def set_settings(self, st: HeadsetSettings | None) -> None:
        for w in self.settings_widgets:
            w.setEnabled(st is not None)
        if st is None:
            return
        self._anc = st.anc
        self.view.set_status(self.view.connected, self.view.muted, st.anc)
        self.anc.set_value(st.anc)
        self.mic_led.setEnabled(st.mic_led is not None)
        self.mic_led.setChecked(bool(st.mic_led))
        self.sidetone_on.setChecked(st.sidetone_on)
        self.sidetone.set_value(st.sidetone_level)
        self.sidetone.setEnabled(st.sidetone_on)
        for c, v in ((self.wheel_left, st.wheel_left), (self.wheel_right, st.wheel_right)):
            c.setEnabled(v is not None)
            if v is not None:
                set_combo(c, v)
        self.brightness.setEnabled(st.brightness is not None)
        if st.brightness is not None:
            self.brightness.set_value(st.brightness)
        m = st.sleep_minutes or 0
        set_combo(self.auto_off, m, f"After {m} min")
        self.set_lighting(self.tray.cfg)
