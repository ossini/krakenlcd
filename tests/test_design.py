# SPDX-License-Identifier: GPL-3.0-or-later
"""Designs: finding them, checking them, drawing them, noticing edits."""

import os
import shutil
import tempfile
import time
import unittest

from krakenlcd import design


SVG = """<svg xmlns="http://www.w3.org/2000/svg" width="320" height="320"
     data-hot="80" data-label="CPU">
  <title>Test</title>
  <rect width="320" height="320"
        fill="{{ '#ff0000' if cpu >= hot else '#0000ff' }}"/>
  <text x="160" y="160" font-family="Chakra Petch">{{ label }} {{ cpu }}</text>
</svg>
"""


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self._env = os.environ.get("XDG_CONFIG_HOME")
        os.environ["XDG_CONFIG_HOME"] = self.tmp
        self.home = design.user_dir()
        os.makedirs(self.home)

    def tearDown(self):
        if self._env is None:
            os.environ.pop("XDG_CONFIG_HOME", None)
        else:
            os.environ["XDG_CONFIG_HOME"] = self._env
        shutil.rmtree(self.tmp)

    def write(self, name, text, folder=True):
        if folder:
            path = os.path.join(self.home, name, "design.svg")
            os.makedirs(os.path.dirname(path), exist_ok=True)
        else:
            path = os.path.join(self.home, name + ".svg")
        with open(path, "w") as fh:
            fh.write(text)
        return path

    def error(self, text):
        self.write("broken", text)
        with self.assertRaises(design.DesignError) as ctx:
            design.load("broken")
        return str(ctx.exception)


class Finding(Base):
    def test_builtins_and_yours(self):
        self.write("mine", SVG)
        self.write("loose", SVG, folder=False)
        names = {e.name: e for e in design.available()}
        self.assertTrue(names["swiss"].builtin)
        self.assertFalse(names["mine"].builtin)
        self.assertIn("loose", names)

    def test_yours_replace_builtins(self):
        self.write("swiss", SVG)
        entry = design.find("swiss")
        self.assertFalse(entry.builtin)
        self.assertTrue(entry.shadows)

    def test_by_path(self):
        path = self.write("mine", SVG)
        self.assertEqual(design.find(path).name, "design")
        self.assertEqual(design.find(os.path.dirname(path)).name, "mine")

    def test_typo_suggests(self):
        with self.assertRaises(design.DesignError) as ctx:
            design.find("swis")
        self.assertIn("did you mean 'swiss'", str(ctx.exception))


class Checking(Base):
    def test_settings(self):
        self.write("mine", SVG)
        d = design.load("mine")
        self.assertEqual(d.title, "Test")
        self.assertEqual(d.settings, {"hot": 80, "label": "CPU"})
        self.assertEqual(d.configure({"hot": "75.5"})["hot"], 75.5)
        with self.assertRaises(design.DesignError) as ctx:
            d.configure({"hott": 1})
        self.assertIn("did you mean 'hot'", str(ctx.exception))

    def test_unknown_value_names_the_line(self):
        message = self.error(SVG.replace("{{ cpu }}", "{{ cpuu }}"))
        self.assertIn("line 6", message)
        self.assertIn("did you mean 'cpu'", message)

    def test_bad_xml_names_the_line(self):
        message = self.error(SVG.replace("</text>", ""))
        self.assertIn("not valid SVG", message)
        self.assertIn("line 7", message)

    def test_placeholders_may_hold_xml_characters(self):
        self.write("mine", SVG.replace("cpu >= hot", "hot < cpu"))
        design.load("mine")

    def test_not_svg(self):
        self.assertIn("<svg>", self.error("<html></html>"))


class Drawing(Base):
    def test_draws_and_skips_unchanged(self):
        self.write("mine", SVG)
        live = design.Live("mine", demo=True, warn=lambda m: None)
        image = live.frame()
        self.assertEqual(image.size, (320, 320))
        self.assertEqual(image.getpixel((5, 5)), (0, 0, 255))   # cpu 67 < 80
        self.assertIsNone(live.frame())
        live = design.Live("mine", {"hot": 50}, demo=True,
                           warn=lambda m: None)
        self.assertEqual(live.frame().getpixel((5, 5)), (255, 0, 0))

    def test_every_builtin_draws(self):
        for entry in design.available():
            if entry.builtin:
                warnings = []
                live = design.Live(entry, demo=True, warn=warnings.append)
                self.assertIsNotNone(live.frame(), entry.name)
                self.assertEqual(warnings, [], entry.name)

    def test_missing_font_is_a_warning(self):
        self.write("mine", SVG.replace("Chakra Petch", "No Such Font 123"))
        warnings = []
        design.Live("mine", demo=True, warn=warnings.append).frame()
        self.assertTrue(any("No Such Font 123" in w for w in warnings))

    def test_edits_are_picked_up(self):
        path = self.write("mine", SVG)
        warnings = []
        live = design.Live("mine", demo=True, warn=warnings.append)
        live.frame()
        time.sleep(0.01)
        with open(path, "w") as fh:
            fh.write(SVG.replace("#0000ff", "#00ff00"))
        self.assertTrue(live.reload_if_changed())
        self.assertEqual(live.frame().getpixel((5, 5)), (0, 255, 0))

        # A broken save keeps the last good version and says why.
        with open(path, "w") as fh:
            fh.write(SVG.replace("{{ cpu }}", "{{ cpu +"))
        self.assertFalse(live.reload_if_changed())
        self.assertTrue(any("previous version" in w for w in warnings))
        self.assertEqual(live.frame(force=True).getpixel((5, 5)), (0, 255, 0))


class Creating(Base):
    def test_copy_of_starter(self):
        path = design.create("mine")
        d = design.load("mine")
        self.assertEqual(d.title, "mine")
        self.assertTrue(path.endswith(os.path.join("mine", "design.svg")))
        with self.assertRaises(design.DesignError):
            design.create("mine")

    def test_around_a_picture(self):
        from PIL import Image
        picture = os.path.join(self.tmp, "photo.jpg")
        Image.new("RGB", (1200, 800), (200, 100, 50)).save(picture)
        design.create("photo", image=picture)
        live = design.Live("photo", demo=True, warn=lambda m: None)
        self.assertEqual(live.design.warnings, [])     # scaled, so no warning
        self.assertEqual(live.frame().getpixel((5, 5)), (200, 100, 50))

    def test_bad_name(self):
        with self.assertRaises(design.DesignError):
            design.create("../escape")


if __name__ == "__main__":
    unittest.main()
