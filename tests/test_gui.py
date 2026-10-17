# SPDX-License-Identifier: GPL-3.0-or-later
"""The GUI's live design mode, offscreen and without a cooler."""

import importlib.util
import os
import shutil
import tempfile
import unittest

HAVE_QT = importlib.util.find_spec("PyQt6") is not None

if HAVE_QT:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PyQt6.QtWidgets import QApplication

    from krakenlcd import gui

    class NoDevice(gui.DeviceWorker):
        """Never opens the cooler: a test must not take it from the user."""

        def run(self):
            self.failed.emit("no device in tests")


@unittest.skipUnless(HAVE_QT, "PyQt6 is not installed")
class LiveDesignMode(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self._env = os.environ.get("XDG_CONFIG_HOME")
        os.environ["XDG_CONFIG_HOME"] = self.tmp
        self._worker = gui.DeviceWorker
        gui.DeviceWorker = NoDevice
        self.window = gui.Window()

    def tearDown(self):
        self.window.close()
        gui.DeviceWorker = self._worker
        if self._env is None:
            os.environ.pop("XDG_CONFIG_HOME", None)
        else:
            os.environ["XDG_CONFIG_HOME"] = self._env
        shutil.rmtree(self.tmp)

    def test_choosing_a_design(self):
        w = self.window
        names = [w.design_combo.itemData(i)
                 for i in range(w.design_combo.count())]
        self.assertIn("swiss", names)
        w.design_combo.setCurrentIndex(names.index("simple"))
        w.on_design_chosen(w.design_combo.currentIndex())
        self.assertEqual(w.mode, gui.MODE_DESIGN)
        self.assertTrue(w._design_timer.isActive())
        self.assertFalse(w.design_group.isHidden())
        self.assertTrue(w.color_group.isHidden())
        # One row per data-… setting of the design.
        self.assertEqual(w.settings_form.rowCount(), 5)
        self.assertFalse(w.preview.pixmap().isNull())

    def test_settings_reach_the_preview(self):
        w = self.window
        w.on_design_chosen(w.design_combo.findData("swiss"))
        w.on_setting_changed("hot", "10")
        self.assertEqual(w._design_preview_live().values["hot"], 10)

    def test_leaving_the_mode_stops_the_timer(self):
        w = self.window
        w.on_design_chosen(w.design_combo.findData("duo"))
        w.mode_group.button(gui.MODE_SOLID).click()
        self.assertFalse(w._design_timer.isActive())
        self.assertTrue(w.design_group.isHidden())


if __name__ == "__main__":
    unittest.main()
