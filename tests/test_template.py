# SPDX-License-Identifier: GPL-3.0-or-later
"""The placeholder language: what it computes, and what it refuses."""

import unittest

from krakenlcd.template import MISSING, Template, TemplateError, to_text


def fill(text, **values):
    return Template(text).render(lambda name: values.get(name, MISSING))


class Values(unittest.TestCase):
    def test_numbers_and_text(self):
        self.assertEqual(fill("{{ cpu }}°", cpu=67), "67°")
        self.assertEqual(fill("{{cpu}}", cpu=67.25), "67.25")
        self.assertEqual(fill("{{ 'GPU ' + gpu }}", gpu=71), "GPU 71")
        self.assertEqual(fill("{{ 2 ** 10 }}"), "1024")
        self.assertEqual(fill("{{ 1 / 3 }}"), "0.333")
        self.assertEqual(fill("{{ -0.0001 }}"), "0")

    def test_conditions(self):
        t = "{{ 'hot' if cpu >= hot else 'ok' }}"
        self.assertEqual(fill(t, cpu=85, hot=80), "hot")
        self.assertEqual(fill(t, cpu=60, hot=80), "ok")
        self.assertEqual(fill("{{ 50 < cpu <= 80 }}", cpu=80), "true")
        self.assertEqual(fill("{{ small == 'none' }}", small="none"), "true")

    def test_entities_from_editors(self):
        # Inkscape writes > and & inside attributes as entities.
        self.assertEqual(fill("{{ 'a' if cpu &gt;= 80 else 'b' }}", cpu=90),
                         "a")

    def test_output_is_escaped(self):
        self.assertEqual(fill("{{ x }}", x='<b> & "c"'),
                         "&lt;b&gt; &amp; &quot;c&quot;")

    def test_comments_are_left_alone(self):
        self.assertEqual(fill("<!-- {{ nonsense( }} -->{{ 1 }}"),
                         "<!-- {{ nonsense( }} -->1")


class Missing(unittest.TestCase):
    def test_reads_as_dashes(self):
        self.assertEqual(fill("{{ gpu }}"), "--")
        self.assertEqual(fill("{{ fmt(gpu, 1) }}"), "--")
        self.assertEqual(to_text(MISSING), "--")

    def test_swallows_arithmetic_and_comparisons(self):
        self.assertEqual(fill("{{ gpu * 2 + 1 }}"), "--")
        self.assertEqual(fill("{{ 'hot' if gpu >= 80 else 'cool' }}"), "cool")
        self.assertEqual(fill("{{ gpu != 3 }}"), "true")

    def test_falls_back_with_or(self):
        self.assertEqual(fill("{{ gpu or 0 }}"), "0")

    def test_arc_of_missing_draws_nothing(self):
        self.assertEqual(fill("{{ arc(160, 160, 100, -135, map(gpu, 0, 100, "
                              "-135, 135)) }}"), "")


class Functions(unittest.TestCase):
    def test_map_clamps(self):
        self.assertEqual(fill("{{ map(cpu, 30, 100, 0, 1) }}", cpu=65), "0.5")
        self.assertEqual(fill("{{ map(cpu, 30, 100, 0, 1) }}", cpu=10), "0")
        self.assertEqual(fill("{{ map(cpu, 30, 100, 0, 1) }}", cpu=200), "1")

    def test_fmt(self):
        self.assertEqual(fill("{{ fmt(v, 1) }}", v=67), "67.0")
        self.assertEqual(fill("{{ fmt(v) }}", v=-0.3), "0")

    def test_arc(self):
        self.assertEqual(fill("{{ arc(160, 160, 100, 0, 90) }}"),
                         "M160 60 A100 100 0 0 1 260 160")
        self.assertEqual(fill("{{ arc(160, 160, 100, 90, 0) }}"),
                         "M160 60 A100 100 0 0 1 260 160")
        self.assertEqual(fill("{{ arc(160, 160, 100, 0, 0) }}"), "")
        self.assertIn(" Z", fill("{{ arc(160, 160, 100, 0, 360) }}"))

    def test_colours(self):
        self.assertEqual(fill("{{ mix('#000000', '#ffffff', 0.5) }}"),
                         "#808080")
        self.assertEqual(fill("{{ mix('#000', '#fff', 2) }}"), "#ffffff")
        self.assertEqual(fill("{{ ramp(0.5, '#00f', '#0f0', '#f00') }}"),
                         "#00ff00")
        self.assertEqual(fill("{{ ramp(gpu, '#00f', '#f00') }}"), "#0000ff")

    def test_text_helpers(self):
        self.assertEqual(fill("{{ upper(x) }}{{ len(x) }}", x="cpu"), "CPU3")


class Refused(unittest.TestCase):
    """Designs get shared.  None of this may get past the compiler."""

    def assertRefused(self, text):
        with self.assertRaises(TemplateError):
            Template(text)

    def test_python_escapes(self):
        self.assertRefused("{{ ().__class__ }}")
        self.assertRefused("{{ cpu.real }}")
        self.assertRefused("{{ x[0] }}")
        self.assertRefused("{{ __import__('os') }}")
        self.assertRefused("{{ open('/etc/passwd') }}")
        self.assertRefused("{{ (lambda: 1)() }}")
        self.assertRefused("{{ [x for x in y] }}")
        self.assertRefused("{{ f'{cpu}' }}")
        self.assertRefused("{{ (x := 1) }}")
        self.assertRefused("{{ eval('1') }}")

    def test_unknown_function_suggests(self):
        with self.assertRaises(TemplateError) as ctx:
            Template("\n\n{{ fmtt(cpu) }}")
        self.assertIn("fmt", str(ctx.exception))
        self.assertEqual(ctx.exception.line, 3)

    def test_wrong_argument_count(self):
        with self.assertRaises(TemplateError) as ctx:
            Template("{{ map(cpu, 1) }}")
        self.assertIn("map(value, in_lo, in_hi, out_lo, out_hi)",
                      str(ctx.exception))

    def test_syntax_error(self):
        self.assertRefused("{{ cpu + }}")
        self.assertRefused("{{ }}")

    def test_runaway_numbers(self):
        with self.assertRaises(TemplateError):
            fill("{{ 9 ** 9 ** 9 }}")
        with self.assertRaises(TemplateError):
            fill("{{ 'x' * 100000000 }}")

    def test_runtime_errors_reported_not_raised(self):
        errors = []
        out = Template("{{ 1 / zero }}").render(
            lambda name: 0, on_error=errors.append)
        self.assertEqual(out, "--")
        self.assertEqual(len(errors), 1)


if __name__ == "__main__":
    unittest.main()
