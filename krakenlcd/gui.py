# SPDX-License-Identifier: GPL-3.0-or-later
"""A small Qt front end for the Kraken Z display.

Pick a colour and the panel follows the sliders live; drop in a GIF and it
plays; switch back to the firmware's own screen with one button.

All device I/O happens on a single worker thread.  The USB bulk endpoint is
not reentrant and a 2 MB GIF takes seconds to push, so doing this on the Qt
thread would freeze the window solid.  The worker keeps room for exactly one
pending job: dragging a slider therefore replaces the queued upload instead
of piling up hundreds of them.
"""

import os
import sys
import time

try:
    from PyQt6.QtCore import (QMutex, QMutexLocker, Qt, QThread, QTimer,
                              QWaitCondition, pyqtSignal)
    from PyQt6.QtGui import QBrush, QColor, QImage, QPainter, QPixmap
    from PyQt6.QtWidgets import (QApplication, QButtonGroup, QCheckBox,
                                 QColorDialog, QComboBox, QDoubleSpinBox,
                                 QFileDialog, QFrame, QGridLayout, QGroupBox,
                                 QHBoxLayout, QLabel, QMainWindow,
                                 QMessageBox, QPushButton, QRadioButton,
                                 QSlider, QVBoxLayout, QWidget)
except ImportError:
    raise SystemExit(
        "the GUI needs PyQt6.\n"
        "    pip install --user PyQt6\n"
        "or install your distribution's python-pyqt6 package.\n"
        "Everything the GUI does is also available from the krakenlcd command."
    ) from None

from . import media, state, tempscreen
from .device import HEIGHT, WIDTH, DoubleBuffer, Kraken, read_hwmon_temp
from .lowlevel import KrakenError

MODE_SOLID, MODE_TEMP, MODE_GIF, MODE_FIRMWARE = range(4)


def _hex(color) -> str:
    return "#%02x%02x%02x" % tuple(color)


# ---------------------------------------------------------------------------
# worker
# ---------------------------------------------------------------------------

class DeviceWorker(QThread):
    """Owns the Kraken.  Everything else asks it for things."""

    status = pyqtSignal(dict)
    failed = pyqtSignal(str)
    busy = pyqtSignal(bool)
    info = pyqtSignal(str)

    def __init__(self):
        super().__init__()
        self._mutex = QMutex()
        self._wake = QWaitCondition()
        self._job = None            # at most one; a newer job replaces it
        self._running = True
        self._temp_mode = None      # kwargs while the temperature screen is live
        self._temp_interval = 2.0
        self._last_temp_push = 0.0
        self._last_shown = None
        self.orientation = 0

    # -- called from the GUI thread ------------------------------------

    def submit(self, kind: str, **kwargs):
        with QMutexLocker(self._mutex):
            self._job = (kind, kwargs)
            self._wake.wakeAll()

    def stop(self):
        with QMutexLocker(self._mutex):
            self._running = False
            self._wake.wakeAll()

    # -- the thread itself ---------------------------------------------

    def run(self):
        try:
            kraken = Kraken()
        except Exception as exc:
            self.failed.emit(str(exc))
            return

        try:
            self.orientation = kraken.lcd_info()["orientation"]
        except KrakenError:
            self.orientation = 0
        buffer = DoubleBuffer(kraken, buckets=(0, 1))
        try:
            buffer.prepare()
        except KrakenError as exc:
            self.failed.emit(f"could not free the display buckets: {exc}")

        last_status = 0.0
        try:
            while True:
                with QMutexLocker(self._mutex):
                    if not self._running:
                        break
                    job = self._job
                    self._job = None
                    if job is None:
                        # Wake up regularly anyway: the status readout and the
                        # live temperature screen both need a heartbeat.
                        self._wake.wait(self._mutex, 500)
                        job = self._job
                        self._job = None
                    if not self._running:
                        break

                if job is not None:
                    self._run_job(kraken, buffer, job)

                now = time.monotonic()
                if now - last_status > 2.0:
                    last_status = now
                    self._emit_status(kraken)
                if self._temp_mode is not None:
                    self._tick_temp(kraken, buffer)
        finally:
            kraken.close()

    def _emit_status(self, kraken):
        try:
            st = kraken.status()
            self.status.emit({
                "liquid": read_hwmon_temp() or st.liquid_temp,
                "pump_rpm": st.pump_rpm,
                "pump_duty": st.pump_duty,
                "fan_rpm": st.fan_rpm,
            })
        except KrakenError:
            pass  # a dropped status report is not worth bothering the user

    def _tick_temp(self, kraken, buffer):
        now = time.monotonic()
        if now - self._last_temp_push < self._temp_interval:
            return
        self._last_temp_push = now
        kwargs = dict(self._temp_mode)
        decimals = kwargs.get("decimals", 0)
        temp = read_hwmon_temp()
        if temp is None:
            temp = kraken.liquid_temp()
        key = round(temp, decimals)
        if key == self._last_shown:
            return
        try:
            buffer.push(tempscreen.render_gif(
                temp, rotate=self.orientation, **kwargs))
            self._last_shown = key
        except KrakenError as exc:
            self.failed.emit(str(exc))

    def _run_job(self, kraken, buffer, job):
        kind, kw = job
        self.busy.emit(True)
        try:
            if kind == "solid":
                self._temp_mode = None
                buffer.push(media.solid(kw["color"]))
                state.save_display(["fill", _hex(kw["color"])])
            elif kind == "temp":
                self._temp_interval = kw.pop("interval", 2.0)
                self._temp_mode = kw
                self._last_shown = None
                self._last_temp_push = 0.0
                state.save_display([
                    "temp",
                    "--color", _hex(kw["color"]),
                    "--scale", str(kw.get("scale", 1.0)),
                    "--decimals", str(kw.get("decimals", 0)),
                    "--interval", str(self._temp_interval)])
            elif kind == "gif":
                self._temp_mode = None
                self.info.emit(f"re-encoding {os.path.basename(kw['path'])} …")
                data = media.prepare_gif(kw["path"], rotate=self.orientation,
                                         geometry=kw.get("geometry", "cover"),
                                         colors=kw.get("colors", 256))
                self.info.emit(f"uploading {len(data) / 1024:.0f} KiB …")
                t0 = time.time()
                buffer.push(data)
                self.info.emit(f"uploaded {len(data) / 1024:.0f} KiB "
                               f"in {time.time() - t0:.1f} s")
                state.save_display(
                    ["gif", os.path.abspath(kw["path"]),
                     "--geometry", kw.get("geometry", "cover")],
                    payload=data, rotate=self.orientation)
            elif kind == "firmware":
                self._temp_mode = None
                kraken.show_liquid_screen()
                state.save_display(["liquid"])
                self.info.emit("firmware temperature screen")
            elif kind == "brightness":
                kraken.set_brightness(kw["percent"])
                state.update(brightness=kw["percent"])
            elif kind == "orientation":
                kraken.set_orientation(kw["degrees"])
                self.orientation = kw["degrees"]
                self._last_shown = None
                state.update(orientation=kw["degrees"])
            elif kind == "reset":
                self._temp_mode = None
                kraken.reset()
                buffer.prepare()
                state.clear()
                self.info.emit("buckets cleared, firmware screen restored")
        except (KrakenError, ValueError, OSError) as exc:
            self.failed.emit(str(exc))
        finally:
            self.busy.emit(False)


# ---------------------------------------------------------------------------
# widgets
# ---------------------------------------------------------------------------

class Preview(QLabel):
    """Shows what the panel is being asked to display, upright."""

    def __init__(self):
        super().__init__()
        self.setFixedSize(240, 240)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.show_rgb(bytes(WIDTH * HEIGHT * 3))

    def show_rgb(self, rgb: bytes):
        image = QImage(rgb, WIDTH, HEIGHT, WIDTH * 3,
                       QImage.Format.Format_RGB888).copy()
        pix = QPixmap.fromImage(image).scaled(
            self.width(), self.height(),
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation)
        # The panel is round, so mask the corners: otherwise the preview
        # promises pixels the glass will never show.
        rounded = QPixmap(pix.size())
        rounded.fill(QColor(0, 0, 0, 0))
        painter = QPainter(rounded)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setBrush(QBrush(pix))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawEllipse(0, 0, pix.width(), pix.height())
        painter.end()
        self.setPixmap(rounded)

    def show_solid(self, color):
        self.show_rgb(bytes(color) * (WIDTH * HEIGHT))


class Window(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Kraken Z Display")
        self.color = (255, 255, 255)
        self.gif_path = None
        self.mode = MODE_SOLID

        self.worker = DeviceWorker()
        self.worker.status.connect(self.on_status)
        self.worker.failed.connect(self.on_failed)
        self.worker.busy.connect(self.on_busy)
        self.worker.info.connect(self.on_info)
        self.worker.start()

        # Uploads are coalesced here as well as in the worker: there is no
        # point encoding a frame for every pixel the slider passes over.
        self._debounce = QTimer(self)
        self._debounce.setSingleShot(True)
        self._debounce.setInterval(150)
        self._debounce.timeout.connect(self.apply_mode)

        self._build()
        self.update_preview()

    # -- construction ---------------------------------------------------

    def _build(self):
        central = QWidget()
        root = QHBoxLayout(central)
        root.setContentsMargins(16, 16, 16, 16)
        root.setSpacing(20)

        left = QVBoxLayout()
        left.setSpacing(10)
        self.preview = Preview()
        left.addWidget(self.preview, alignment=Qt.AlignmentFlag.AlignHCenter)
        self.readout = QLabel("—")
        self.readout.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        left.addWidget(self.readout)
        left.addStretch(1)
        root.addLayout(left)

        right = QVBoxLayout()
        right.setSpacing(12)
        right.addWidget(self._mode_box())
        right.addWidget(self._color_box())
        right.addWidget(self._content_box())
        right.addWidget(self._panel_box())
        right.addStretch(1)
        root.addLayout(right, 1)

        self.setCentralWidget(central)
        self.statusBar().showMessage("connecting …")

    def _mode_box(self):
        box = QGroupBox("What to show")
        layout = QVBoxLayout(box)
        self.mode_group = QButtonGroup(self)
        for value, text in ((MODE_SOLID, "Solid colour"),
                            (MODE_TEMP, "Liquid temperature"),
                            (MODE_GIF, "Image or GIF"),
                            (MODE_FIRMWARE, "Firmware screen (white)")):
            button = QRadioButton(text)
            self.mode_group.addButton(button, value)
            layout.addWidget(button)
        self.mode_group.button(MODE_SOLID).setChecked(True)
        self.mode_group.idClicked.connect(self.on_mode_changed)
        return box

    def _color_box(self):
        box = QGroupBox("Colour")
        grid = QGridLayout(box)
        self.channels = []
        for row, (name, value) in enumerate(zip("RGB", self.color)):
            slider = QSlider(Qt.Orientation.Horizontal)
            slider.setRange(0, 255)
            slider.setValue(value)
            slider.valueChanged.connect(self.on_color_changed)
            label = QLabel(str(value))
            label.setMinimumWidth(30)
            grid.addWidget(QLabel(name), row, 0)
            grid.addWidget(slider, row, 1)
            grid.addWidget(label, row, 2)
            self.channels.append((slider, label))

        self.swatch = QPushButton("Pick …")
        self.swatch.clicked.connect(self.on_pick_color)
        grid.addWidget(self.swatch, 3, 0, 1, 3)

        presets = QHBoxLayout()
        for name in ("white", "red", "orange", "green", "cyan", "blue",
                     "magenta", "black"):
            rgb = tempscreen.COLORS[name]
            button = QPushButton()
            button.setFixedSize(26, 26)
            button.setToolTip(name)
            button.setStyleSheet(
                f"background:rgb{rgb};border:1px solid #666;border-radius:4px")
            button.clicked.connect(lambda _, c=rgb: self.set_color(c))
            presets.addWidget(button)
        presets.addStretch(1)
        grid.addLayout(presets, 4, 0, 1, 3)
        return box

    def _content_box(self):
        box = QGroupBox("Content options")
        grid = QGridLayout(box)

        self.gif_button = QPushButton("Choose image or GIF …")
        self.gif_button.clicked.connect(self.on_pick_gif)
        grid.addWidget(self.gif_button, 0, 0, 1, 2)

        self.gif_label = QLabel("no file chosen")
        self.gif_label.setWordWrap(True)
        grid.addWidget(self.gif_label, 1, 0, 1, 2)

        grid.addWidget(QLabel("Fit"), 2, 0)
        self.geometry_box = QComboBox()
        self.geometry_box.addItems(["cover", "contain", "stretch"])
        self.geometry_box.currentTextChanged.connect(self.schedule_apply)
        grid.addWidget(self.geometry_box, 2, 1)

        grid.addWidget(QLabel("Digit size"), 3, 0)
        self.scale_spin = QDoubleSpinBox()
        self.scale_spin.setRange(0.2, 1.5)
        self.scale_spin.setSingleStep(0.05)
        self.scale_spin.setValue(0.65)
        self.scale_spin.valueChanged.connect(self.schedule_apply)
        grid.addWidget(self.scale_spin, 3, 1)

        self.decimal_box = QCheckBox("Show one decimal")
        self.decimal_box.stateChanged.connect(self.schedule_apply)
        grid.addWidget(self.decimal_box, 4, 0, 1, 2)
        return box

    def _panel_box(self):
        box = QGroupBox("Panel")
        grid = QGridLayout(box)

        grid.addWidget(QLabel("Brightness"), 0, 0)
        self.brightness = QSlider(Qt.Orientation.Horizontal)
        self.brightness.setRange(0, 100)
        self.brightness.setValue(50)
        self.brightness.sliderReleased.connect(
            lambda: self.worker.submit("brightness",
                                       percent=self.brightness.value()))
        grid.addWidget(self.brightness, 0, 1)

        grid.addWidget(QLabel("Rotation"), 1, 0)
        self.rotation = QComboBox()
        self.rotation.addItems(["0°", "90°", "180°", "270°"])
        self.rotation.activated.connect(
            lambda i: self.worker.submit("orientation", degrees=i * 90))
        grid.addWidget(self.rotation, 1, 1)

        reset = QPushButton("Clear buckets and restore firmware screen")
        reset.clicked.connect(lambda: self.worker.submit("reset"))
        grid.addWidget(reset, 2, 0, 1, 2)
        return box

    # -- events ---------------------------------------------------------

    def on_mode_changed(self, value):
        self.mode = value
        self.update_preview()
        self.apply_mode()

    def on_color_changed(self):
        self.color = tuple(s.value() for s, _ in self.channels)
        for (slider, label) in self.channels:
            label.setText(str(slider.value()))
        self.update_preview()
        self.schedule_apply()

    def set_color(self, rgb):
        for (slider, _), value in zip(self.channels, rgb):
            slider.blockSignals(True)
            slider.setValue(value)
            slider.blockSignals(False)
        self.on_color_changed()

    def on_pick_color(self):
        initial = QColor(*self.color)
        chosen = QColorDialog.getColor(initial, self, "Display colour")
        if chosen.isValid():
            self.set_color((chosen.red(), chosen.green(), chosen.blue()))

    def on_pick_gif(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Choose an image or GIF", os.getcwd(),
            "Images (*.gif *.png *.jpg *.jpeg *.bmp *.webp);;All files (*)")
        if not path:
            return
        self.gif_path = path
        self.gif_label.setText(path)
        self.mode_group.button(MODE_GIF).setChecked(True)
        self.mode = MODE_GIF
        self.update_preview()
        self.apply_mode()

    def schedule_apply(self):
        self._debounce.start()

    def apply_mode(self):
        if self.mode == MODE_SOLID:
            self.worker.submit("solid", color=self.color)
        elif self.mode == MODE_TEMP:
            self.worker.submit("temp", color=self.color,
                               scale=self.scale_spin.value(),
                               decimals=1 if self.decimal_box.isChecked() else 0,
                               interval=2.0)
        elif self.mode == MODE_GIF:
            if self.gif_path:
                self.worker.submit("gif", path=self.gif_path,
                                   geometry=self.geometry_box.currentText())
        elif self.mode == MODE_FIRMWARE:
            self.worker.submit("firmware")

    # -- preview --------------------------------------------------------

    def update_preview(self):
        try:
            if self.mode == MODE_SOLID:
                self.preview.show_solid(self.color)
            elif self.mode == MODE_TEMP:
                temp = read_hwmon_temp() or 30.0
                self.preview.show_rgb(tempscreen.render(
                    temp, color=self.color, scale=self.scale_spin.value(),
                    decimals=1 if self.decimal_box.isChecked() else 0))
            elif self.mode == MODE_GIF and self.gif_path:
                from PIL import Image
                frame = Image.open(self.gif_path).convert("RGB").resize(
                    (WIDTH, HEIGHT))
                self.preview.show_rgb(frame.tobytes())
            elif self.mode == MODE_FIRMWARE:
                self.preview.show_rgb(tempscreen.render(
                    read_hwmon_temp() or 30.0, color=(255, 255, 255),
                    scale=0.65))
            else:
                self.preview.show_solid((0, 0, 0))
        except Exception:
            self.preview.show_solid((0, 0, 0))

    # -- worker signals -------------------------------------------------

    def on_status(self, data):
        self.readout.setText(
            f"{data['liquid']:.1f} °C   ·   pump {data['pump_rpm']} rpm")
        if self.mode == MODE_TEMP:
            self.update_preview()

    def on_failed(self, message):
        self.statusBar().showMessage(f"error: {message}", 8000)

    def on_info(self, message):
        self.statusBar().showMessage(message, 5000)

    def on_busy(self, busy):
        self.statusBar().showMessage("working …" if busy else "ready")

    def closeEvent(self, event):
        self.worker.stop()
        self.worker.wait(3000)
        super().closeEvent(event)


def main():
    app = QApplication(sys.argv)
    try:
        window = Window()
    except KrakenError as exc:
        QMessageBox.critical(None, "Kraken Z", str(exc))
        return 1
    window.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
