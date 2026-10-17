# SPDX-License-Identifier: GPL-3.0-or-later
"""A small Qt front end for the Kraken Z display.

Pick a colour and the panel follows the sliders live; drop in a GIF and it
plays; pick a live design and it shows CPU, GPU and the rest as they change,
redrawing whenever its file is saved; switch back to the firmware's own
screen with one button.

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
                              QUrl, QWaitCondition, pyqtSignal)
    from PyQt6.QtGui import (QBrush, QColor, QDesktopServices, QImage,
                             QPainter, QPixmap)
    from PyQt6.QtWidgets import (QApplication, QButtonGroup, QCheckBox,
                                 QColorDialog, QComboBox, QDoubleSpinBox,
                                 QFileDialog, QFormLayout, QFrame, QGridLayout,
                                 QGroupBox, QHBoxLayout, QInputDialog, QLabel,
                                 QLineEdit, QMainWindow, QMessageBox,
                                 QPushButton, QRadioButton, QSlider,
                                 QVBoxLayout, QWidget)
except ImportError:
    raise SystemExit(
        "the GUI needs PyQt6.\n"
        "    pip install --user PyQt6\n"
        "or install your distribution's python-pyqt6 package.\n"
        "Everything the GUI does is also available from the krakenlcd command."
    ) from None

from . import design, media, state, tempscreen
from .device import HEIGHT, WIDTH, DoubleBuffer, Kraken, read_hwmon_temp
from .lowlevel import KrakenError

MODE_SOLID, MODE_TEMP, MODE_GIF, MODE_FIRMWARE, MODE_DESIGN = range(5)

# How often a live design is looked at: sensors read, file checked for edits.
DESIGN_INTERVAL = 2.0


def _hex(color) -> str:
    return "#%02x%02x%02x" % tuple(color)


def _html(text: str) -> str:
    return (text.replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;").replace("\n", "<br>"))


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
        self._live = None           # design.Live while a design is shown
        self._last_design_tick = 0.0
        self._design_failed = None
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
                if self._live is not None:
                    self._tick_design(buffer)
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

    def _tick_design(self, buffer, force=False):
        now = time.monotonic()
        if not force and now - self._last_design_tick < DESIGN_INTERVAL:
            return
        self._last_design_tick = now
        live = self._live
        try:
            if live.reload_if_changed():
                self.info.emit(f"reloaded {live.design.where}")
            image = live.frame(force=force)
            self._design_failed = None
        except design.DesignError as exc:
            # Said once, not every two seconds while it stays broken.
            if str(exc) != self._design_failed:
                self._design_failed = str(exc)
                self.failed.emit(str(exc))
            return
        if image is not None:
            try:
                buffer.push(media.encode_frame(image, rotate=self.orientation))
            except KrakenError as exc:
                self.failed.emit(str(exc))

    def _run_job(self, kraken, buffer, job):
        kind, kw = job
        self.busy.emit(True)
        try:
            if kind not in ("brightness", "orientation"):
                self._live = None
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
            elif kind == "design":
                self._temp_mode = None
                self._live = design.Live(kw["ref"], kw["settings"],
                                         warn=self.info.emit)
                self._design_failed = None
                self._tick_design(buffer, force=True)
                argv = ["dash", kw["ref"]]
                for name, value in kw["settings"].items():
                    argv += ["--set", f"{name}={value}"]
                state.save_display(argv)
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
                if self._live is not None:
                    self._tick_design(buffer, force=True)
                state.update(orientation=kw["degrees"])
            elif kind == "reset":
                self._temp_mode = None
                kraken.reset()
                buffer.prepare()
                state.clear()
                self.info.emit("buckets cleared, firmware screen restored")
        except (KrakenError, ValueError, OSError) as exc:
            # design.DesignError is a ValueError: a broken design lands here.
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
        self.design_name = None
        self.design_settings = {}   # only what was changed from the defaults
        self._preview_live = None

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

        # The preview of a live design follows the sensors on its own clock,
        # so it works with no cooler attached as well.
        self._design_timer = QTimer(self)
        self._design_timer.setInterval(int(DESIGN_INTERVAL * 1000))
        self._design_timer.timeout.connect(self.tick_design_preview)

        self._build()
        self.refresh_designs()
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
        self.design_group = self._design_box()
        self.color_group = self._color_box()
        self.content_group = self._content_box()
        right.addWidget(self._mode_box())
        right.addWidget(self.design_group)
        right.addWidget(self.color_group)
        right.addWidget(self.content_group)
        right.addWidget(self._panel_box())
        right.addStretch(1)
        root.addLayout(right, 1)

        self.setCentralWidget(central)
        self.statusBar().showMessage("connecting …")
        self._sync_groups()

    def _mode_box(self):
        box = QGroupBox("What to show")
        layout = QVBoxLayout(box)
        self.mode_group = QButtonGroup(self)
        for value, text in ((MODE_SOLID, "Solid colour"),
                            (MODE_DESIGN, "Live design (CPU, GPU, …)"),
                            (MODE_TEMP, "Liquid temperature"),
                            (MODE_GIF, "Image or GIF"),
                            (MODE_FIRMWARE, "Firmware screen (white)")):
            button = QRadioButton(text)
            self.mode_group.addButton(button, value)
            layout.addWidget(button)
        self.mode_group.button(MODE_SOLID).setChecked(True)
        self.mode_group.idClicked.connect(self.on_mode_changed)
        return box

    def _design_box(self):
        box = QGroupBox("Live design")
        layout = QVBoxLayout(box)

        self.design_combo = QComboBox()
        self.design_combo.activated.connect(self.on_design_chosen)
        layout.addWidget(self.design_combo)

        self.design_about = QLabel()
        self.design_about.setWordWrap(True)
        layout.addWidget(self.design_about)

        # One field per setting of the chosen design, rebuilt on every change
        # of design: the data-… attributes on its <svg> decide what is here.
        self.settings_box = QWidget()
        self.settings_form = QFormLayout(self.settings_box)
        self.settings_form.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.settings_box)

        buttons = QHBoxLayout()
        new = QPushButton("New design …")
        new.setToolTip("Start your own, as a copy of the selected design")
        new.clicked.connect(self.on_new_design)
        buttons.addWidget(new)
        edit = QPushButton("Edit")
        edit.setToolTip("Open the design's SVG file; the display redraws "
                        "whenever you save it")
        edit.clicked.connect(self.on_edit_design)
        buttons.addWidget(edit)
        folder = QPushButton("Open folder")
        folder.setToolTip(f"Your designs live in "
                          f"{design.pretty_path(design.user_dir())}: drop "
                          f"folders or .svg files in there")
        folder.clicked.connect(self.on_open_design_folder)
        buttons.addWidget(folder)
        layout.addLayout(buttons)
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
        self._sync_groups()
        self._sync_design_timer()
        self.update_preview()
        self.apply_mode()

    # -- live designs ---------------------------------------------------

    def refresh_designs(self, select=None):
        """Fill the design list again, e.g. after a new one was dropped in."""
        select = select or self.design_name or design.DEFAULT
        self.design_combo.clear()
        for entry in design.available():
            label = entry.name if entry.builtin else f"{entry.name}  (yours)"
            self.design_combo.addItem(label, entry.name)
        index = self.design_combo.findData(select)
        self.design_combo.setCurrentIndex(max(0, index))
        self.load_design(self.design_combo.currentData())

    def load_design(self, name):
        self.design_name = name
        self.design_settings = {}
        self._preview_live = None
        while self.settings_form.rowCount():
            self.settings_form.removeRow(0)
        try:
            loaded = design.load(name)
        except design.DesignError as exc:
            self.design_about.setText(f"<span style='color:#d8342a'>"
                                      f"{_html(str(exc))}</span>")
            return
        self.design_about.setText(_html(loaded.description or loaded.title))
        for setting, value in loaded.settings.items():
            self.settings_form.addRow(setting.replace("_", " "),
                                      self._setting_field(setting, value))

    def _setting_field(self, name, value):
        text = str(value)
        field = QLineEdit(text)
        field.editingFinished.connect(
            lambda: self.on_setting_changed(name, field.text()))
        if not text.startswith("#") or len(text) not in (4, 7):
            return field
        row = QWidget()
        layout = QHBoxLayout(row)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(field, 1)
        swatch = QPushButton()
        swatch.setFixedSize(26, 22)

        def paint(colour):
            swatch.setStyleSheet(f"background:{colour};border:1px solid #666;"
                                 f"border-radius:3px")

        def pick():
            chosen = QColorDialog.getColor(QColor(field.text()), self, name)
            if chosen.isValid():
                field.setText(chosen.name())
                paint(chosen.name())
                self.on_setting_changed(name, chosen.name())

        paint(text)
        swatch.clicked.connect(pick)
        layout.addWidget(swatch)
        return row

    def on_design_chosen(self, index):
        self.load_design(self.design_combo.itemData(index))
        # Picking a design means wanting to see it.
        self.mode_group.button(MODE_DESIGN).setChecked(True)
        self.on_mode_changed(MODE_DESIGN)

    def on_setting_changed(self, name, text):
        if self.design_settings.get(name) == text:
            return
        self.design_settings[name] = text
        self._preview_live = None
        if self.mode == MODE_DESIGN:
            self.update_preview()
            self.schedule_apply()

    def on_new_design(self):
        name, ok = QInputDialog.getText(
            self, "New design", "Name for your design (letters, digits, - "
            "and _):")
        if not ok or not name.strip():
            return
        try:
            path = design.create(name.strip(), source=self.design_name)
        except (design.DesignError, OSError) as exc:
            QMessageBox.warning(self, "New design", str(exc))
            return
        self.refresh_designs(select=name.strip())
        self.on_design_chosen(self.design_combo.currentIndex())
        QDesktopServices.openUrl(QUrl.fromLocalFile(path))
        self.statusBar().showMessage(
            f"created {design.pretty_path(path)} -- save it and the display "
            f"follows", 10000)

    def on_edit_design(self):
        try:
            entry = design.find(self.design_name)
        except design.DesignError as exc:
            self.on_failed(str(exc))
            return
        if entry.builtin:
            answer = QMessageBox.question(
                self, "Edit design",
                f"'{entry.name}' comes with krakenlcd and is replaced on the "
                f"next update.  Make a copy of your own to edit instead?")
            if answer == QMessageBox.StandardButton.Yes:
                self.on_new_design()
            return
        path = entry.path if not os.path.isdir(entry.path) \
            else design.load(entry).svg_path
        QDesktopServices.openUrl(QUrl.fromLocalFile(path))

    def on_open_design_folder(self):
        path = design.user_dir()
        os.makedirs(path, exist_ok=True)
        QDesktopServices.openUrl(QUrl.fromLocalFile(path))

    def _sync_groups(self):
        """Only the controls that do something in the current mode."""
        self.design_group.setVisible(self.mode == MODE_DESIGN)
        self.color_group.setVisible(self.mode in (MODE_SOLID, MODE_TEMP))
        self.content_group.setVisible(self.mode in (MODE_GIF, MODE_TEMP))
        self.adjustSize()

    def _sync_design_timer(self):
        if self.mode == MODE_DESIGN:
            self._design_timer.start()
        else:
            self._design_timer.stop()

    def _design_preview_live(self):
        if self._preview_live is None and self.design_name:
            self._preview_live = design.Live(
                self.design_name, self.design_settings,
                warn=lambda message: self.on_info(message))
        return self._preview_live

    def tick_design_preview(self):
        try:
            live = self._design_preview_live()
            if live is None:
                return
            if live.reload_if_changed():
                self.load_design_about(live)
            image = live.frame()
        except design.DesignError as exc:
            self.on_failed(str(exc))
            return
        if image is not None:
            self.preview.show_rgb(image.tobytes())

    def load_design_about(self, live):
        self.design_about.setText(
            _html(live.design.description or live.design.title))

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
        self.on_mode_changed(MODE_GIF)

    def schedule_apply(self):
        self._debounce.start()

    def apply_mode(self):
        if self.mode == MODE_SOLID:
            self.worker.submit("solid", color=self.color)
        elif self.mode == MODE_DESIGN:
            if self.design_name:
                self.worker.submit("design", ref=self.design_name,
                                   settings=dict(self.design_settings))
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
            elif self.mode == MODE_DESIGN:
                live = self._design_preview_live()
                if live is None:
                    self.preview.show_solid((0, 0, 0))
                else:
                    self.preview.show_rgb(live.frame(force=True).tobytes())
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
        except design.DesignError as exc:
            self.on_failed(str(exc))
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
