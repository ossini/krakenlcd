# SPDX-License-Identifier: GPL-3.0-or-later
"""Live designs: an SVG with placeholders, drawn with what the sensors say.

A design is a folder with a `design.svg` in it -- or just a lone .svg file --
in one of two places:

    ~/.config/krakenlcd/designs/     yours: drop folders or .svg files here
    krakenlcd/designs/               the ones that ship with the package

The SVG is drawn by resvg, a small renderer written in Rust (~8 ms for a
320x320 dial), so a design can come out of Inkscape, Figma, Illustrator, an
AI chat or a text editor: anything that writes SVG.  Where a number belongs
it says `{{ cpu }}`; template.py has what else can go between the braces,
and docs/DESIGNS.md is the guide.

Settings are the `data-*` attributes on the root <svg> element:

    <svg ... data-hot="80" data-scale-min="30">

Each one becomes a variable the placeholders can use (`hot`, `scale_min`),
with that value as its default, and can be changed without touching the file:
`krakenlcd dash swiss --set hot=75`.  That is how a downloaded design gets
adjusted by someone who never opens it.

Fonts are looked for in the design's folder first, then among the ones the
package ships, then on the system through fontconfig.  System fonts are
resolved once, by name, the first time a design is drawn: letting resvg scan
every font on the machine itself costs about half a second per frame.
"""

from __future__ import annotations

import difflib
import io
import math
import os
import re
import shutil
import subprocess
import sys
import xml.etree.ElementTree as ET

from . import state
from .device import HEIGHT, WIDTH
from .sensors import SENSORS, TEXT, Smoother
from .template import _TOKENS, FUNCTIONS, MISSING, Template, TemplateError

__all__ = ["Design", "DesignError", "Live", "Entry", "available", "find",
           "load", "create", "user_dir", "pretty_path", "DEFAULT"]

_HERE = os.path.dirname(os.path.abspath(__file__))
BUILTIN_DIR = os.path.join(_HERE, "designs")
FONT_DIR = os.path.join(_HERE, "fonts")

DEFAULT = "swiss"           # what `krakenlcd dash` shows when not told
STARTER = "simple"          # what `krakenlcd design new` copies by default
SVG_NAME = "design.svg"

_FONT_EXT = (".ttf", ".otf", ".ttc")
_IMAGE_EXT = (".png", ".jpg", ".jpeg", ".webp", ".gif")
_GENERIC = ("serif", "sans-serif", "monospace", "cursive", "fantasy")
_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}")


class DesignError(ValueError):
    """Something wrong with a design, worded for whoever made it."""


def user_dir() -> str:
    return os.path.join(state.config_dir(), "designs")


def pretty_path(path: str) -> str:
    """A path as people write it, with ~ for the home directory."""
    home = os.path.expanduser("~")
    return "~" + path[len(home):] if path.startswith(home + os.sep) else path


def _resvg():
    try:
        import resvg_py
    except ImportError:
        raise SystemExit(
            "live designs need resvg to draw SVG.\n"
            "    pip install resvg-py\n"
            "(install.sh does this for you)."
        ) from None
    return resvg_py


def _pillow():
    try:
        from PIL import Image, ImageFont
    except ImportError:
        raise SystemExit(
            "live designs need Pillow.\n"
            "    pip install --user pillow\n"
            "or install your distribution's python-pillow package."
        ) from None
    return Image, ImageFont


# ---------------------------------------------------------------------------
# finding designs

class Entry:
    """A design on disk, before it is loaded."""

    __slots__ = ("name", "path", "builtin", "shadows")

    def __init__(self, name, path, builtin, shadows=False):
        self.name = name
        self.path = path
        self.builtin = builtin
        self.shadows = shadows      # a design of yours hiding a built-in one


def _svg_in(folder: str) -> str | None:
    """The design file in a folder: design.svg, or the only .svg there is."""
    path = os.path.join(folder, SVG_NAME)
    if os.path.isfile(path):
        return path
    try:
        svgs = [f for f in os.listdir(folder) if f.lower().endswith(".svg")]
    except OSError:
        return None
    return os.path.join(folder, svgs[0]) if len(svgs) == 1 else None


def _scan(directory: str, builtin: bool) -> dict:
    found = {}
    try:
        names = sorted(os.listdir(directory))
    except OSError:
        return found
    for name in names:
        if name.startswith((".", "_")):
            continue
        path = os.path.join(directory, name)
        if os.path.isdir(path) and _svg_in(path):
            found[name] = Entry(name, path, builtin)
        elif os.path.isfile(path) and name.lower().endswith(".svg"):
            found.setdefault(name[:-4], Entry(name[:-4], path, builtin))
    return found


def available() -> list[Entry]:
    """Every design there is, built-in ones first.  Yours win on a clash."""
    builtin = _scan(BUILTIN_DIR, True)
    yours = _scan(user_dir(), False)
    for name, entry in yours.items():
        entry.shadows = name in builtin
    merged = {**builtin, **yours}
    return sorted(merged.values(), key=lambda e: (not e.builtin, e.name))


def find(ref: str) -> Entry:
    """A design by name, or by path to its folder or .svg file."""
    if os.sep in ref or ref.lower().endswith(".svg") or ref in (".", ".."):
        path = os.path.abspath(os.path.expanduser(ref))
        if os.path.isfile(path):
            return Entry(os.path.splitext(os.path.basename(path))[0], path,
                         False)
        if os.path.isdir(path):
            if not _svg_in(path):
                raise DesignError(f"{pretty_path(path)} has no {SVG_NAME} in it")
            return Entry(os.path.basename(path), path, False)
        raise DesignError(f"{pretty_path(path)} does not exist")
    entries = available()
    for entry in entries:
        if entry.name == ref:
            return entry
    close = difflib.get_close_matches(ref, [e.name for e in entries], n=1)
    hint = f" -- did you mean '{close[0]}'?" if close else ""
    raise DesignError(f"there is no design called '{ref}'{hint}\n"
                      f"'krakenlcd design list' shows the ones there are")


def load(ref) -> "Design":
    entry = ref if isinstance(ref, Entry) else find(ref)
    return Design(entry)


# ---------------------------------------------------------------------------
# fonts

_family_cache: dict[str, str | None] = {}
_system_cache: dict[str, list[str]] = {}
_generic_cache: dict[str, tuple[str, str] | None] = {}


def _family_of(path: str) -> str | None:
    if path not in _family_cache:
        _, ImageFont = _pillow()
        try:
            _family_cache[path] = ImageFont.truetype(path, 10).getname()[0]
        except OSError:
            _family_cache[path] = None
    return _family_cache[path]


def _fc_escape(name: str) -> str:
    return re.sub(r"([\\\-:,])", r"\\\1", name)


def _system_fonts(family: str) -> list[str]:
    """Every installed file of one family (all weights), via fontconfig."""
    key = family.lower()
    if key not in _system_cache:
        try:
            out = subprocess.run(
                ["fc-list", "--format", "%{file}\n",
                 f":family={_fc_escape(family)}"],
                capture_output=True, text=True, timeout=10)
            files = [f for f in out.stdout.splitlines()
                     if f.lower().endswith(_FONT_EXT)]
        except (OSError, subprocess.SubprocessError):
            files = []
        _system_cache[key] = sorted(set(files))[:32]
    return _system_cache[key]


def _generic_font(generic: str) -> tuple[str, str] | None:
    """(family, file) fontconfig picks for 'sans-serif' and friends."""
    if generic not in _generic_cache:
        try:
            out = subprocess.run(
                ["fc-match", "--format", "%{family[0]}\n%{file}", generic],
                capture_output=True, text=True, timeout=10)
            family, _, path = out.stdout.partition("\n")
            ok = out.returncode == 0 and family and os.path.isfile(path)
            _generic_cache[generic] = (family, path) if ok else None
        except (OSError, subprocess.SubprocessError):
            _generic_cache[generic] = None
    return _generic_cache[generic]


def _font_declarations(text: str) -> list[list[str]]:
    """The font-family lists a design asks for, in attributes and CSS."""
    found = []
    for m in re.finditer(r"font-family\s*([:=])\s*", text):
        rest = text[m.end():m.end() + 300]
        if m.group(1) == "=" and rest[:1] in "\"'":
            value = rest[1:].split(rest[0], 1)[0]
        else:
            value = re.split(r"[;}\n<>\"]", rest, maxsplit=1)[0]
        value = value.replace("&quot;", "").replace("&apos;", "")
        names = [n.strip().strip("'\"").strip() for n in value.split(",")]
        names = [n for n in names if n and "{{" not in n]
        if names:
            found.append(names)
    return found


class _Fonts:
    """What resvg gets told about fonts for one design."""

    def __init__(self, folder: str | None, text: str):
        self.warnings: list[str] = []
        local = []
        if folder:
            for sub in (folder, os.path.join(folder, "fonts")):
                try:
                    local += sorted(os.path.join(sub, f) for f in os.listdir(sub)
                                    if f.lower().endswith(_FONT_EXT))
                except OSError:
                    pass
        bundled = sorted(os.path.join(FONT_DIR, f) for f in os.listdir(FONT_DIR)
                         if f.lower().endswith(_FONT_EXT))
        self.files = local + bundled
        known = {(_family_of(f) or "").lower() for f in self.files}

        self.generic = {}
        for generic in _GENERIC:
            if generic != "sans-serif" and generic not in text:
                continue
            picked = _generic_font(generic)
            if picked:
                self.generic[generic] = picked[0]
                if picked[1] not in self.files:
                    self.files.append(picked[1])
        # Without fontconfig, fall back on what ships with the package.
        self.generic.setdefault("sans-serif", "Chakra Petch")
        self.generic.setdefault("monospace", "JetBrains Mono")
        self.generic.setdefault("serif", self.generic["sans-serif"])

        for names in _font_declarations(text):
            resolved = False
            for name in names:
                low = name.lower()
                if low in _GENERIC or low in known:
                    resolved = True
                    break
                system = _system_fonts(name)
                if system:
                    self.files += [f for f in system if f not in self.files]
                    known.add(low)
                    resolved = True
                    break
            if not resolved:
                self.warnings.append(
                    f"font '{names[0]}' is not installed, so text set in it "
                    f"uses {self.generic['sans-serif']} instead -- put the "
                    f".ttf next to the design to fix that")

    def kwargs(self) -> dict:
        return {
            "skip_system_fonts": True,
            "font_files": self.files,
            "font_family": self.generic["sans-serif"],
            "sans_serif_family": self.generic["sans-serif"],
            "serif_family": self.generic["serif"],
            "monospace_family": self.generic["monospace"],
        }


# ---------------------------------------------------------------------------
# a design

def _coerce(text: str):
    """A setting as typed: a number if it reads as one, text otherwise."""
    t = str(text).strip()
    try:
        return int(t)
    except ValueError:
        pass
    try:
        value = float(t)
        return value if math.isfinite(value) else t
    except ValueError:
        return t


def _mask(text: str) -> str:
    """The file with placeholders blanked out, so it parses as plain XML.

    Line breaks are kept, so a parse error still points at the right line.
    """
    def blank(m):
        if m.group(1) is not None:
            return m.group(0)
        return re.sub(r"[^\n]", "x", m.group(0))
    return _TOKENS.sub(blank, text)


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _sensor_placeholder(name):
    """Stands in for sensor() while compiling, so its arity can be checked."""
    return MISSING


_COMPILE_FUNCTIONS = dict(FUNCTIONS, sensor=_sensor_placeholder)


class Design:
    """One design, parsed and checked, ready to fill in and draw."""

    def __init__(self, entry: Entry):
        self.name = entry.name
        self.builtin = entry.builtin
        self.path = entry.path
        # A lone .svg may sit among unrelated files, in Downloads say: its
        # folder still resolves relative image links, but is not searched for
        # fonts or checked for oversized pictures.
        self.own_folder = os.path.isdir(entry.path)
        if self.own_folder:
            self.folder = entry.path
            self.svg_path = _svg_in(entry.path)
        else:
            self.folder = os.path.dirname(entry.path)
            self.svg_path = entry.path
        self.where = pretty_path(self.svg_path)
        self.warnings: list[str] = []
        self._fonts = None

        try:
            with open(self.svg_path, encoding="utf-8-sig") as fh:
                self.text = fh.read()
        except UnicodeDecodeError:
            raise DesignError(f"{self.where}: not a text file (is it really "
                              f"an SVG?)") from None
        except OSError as exc:
            raise DesignError(f"{self.where}: {exc.strerror}") from None

        try:
            self.template = Template(self.text, functions=_COMPILE_FUNCTIONS)
        except TemplateError as exc:
            raise DesignError(f"{self.where}, {exc}") from None

        try:
            root = ET.fromstring(_mask(self.text))
        except ET.ParseError as exc:
            line, col = exc.position
            reason = str(exc).split(":")[0]
            raise DesignError(f"{self.where}, line {line}: this is not valid "
                              f"SVG ({reason}, column {col + 1})") from None
        if _local(root.tag) != "svg":
            raise DesignError(f"{self.where}: the file does not start with "
                              f"an <svg> element")

        self.title = self.name
        self.description = ""
        for child in root:
            tag = _local(child.tag)
            if tag == "title" and (child.text or "").strip():
                self.title = child.text.strip()
            elif tag == "desc" and (child.text or "").strip():
                self.description = " ".join(child.text.split())

        self.author = ""
        self.settings: dict = {}
        for key, value in root.attrib.items():
            if not key.startswith("data-"):
                continue
            name = key[5:].replace("-", "_").lower()
            if name == "author":
                self.author = value
                continue
            if not name.isidentifier():
                self.warnings.append(f"ignored setting {key}: names may only "
                                     f"use letters, digits, - and _")
                continue
            if name in SENSORS or name in TEXT:
                self.warnings.append(f"setting {key} has the same name as a "
                                     f"sensor, and hides it")
            self.settings[name] = _coerce(value)

        known = set(self.settings) | set(SENSORS) | set(TEXT)
        for expr in self.template.expressions:
            for name in sorted(expr.names - known):
                close = difflib.get_close_matches(name, sorted(known), n=1)
                hint = f" -- did you mean '{close[0]}'?" if close else ""
                raise DesignError(
                    f"{self.where}, line {expr.line}: there is no value "
                    f"called '{name}'{hint}\n"
                    f"'krakenlcd sensors' lists what can be shown; a "
                    f"data-{name.replace('_', '-')}=\"…\" attribute on <svg> "
                    f"would make it a setting")

        self._check_images()

    def _check_images(self):
        """A 4000-pixel photo is decoded on every redraw.  Say so."""
        if not self.own_folder:
            return
        Image, _ = _pillow()
        try:
            names = os.listdir(self.folder)
        except OSError:
            return
        for name in names:
            if not name.lower().endswith(_IMAGE_EXT):
                continue
            try:
                with Image.open(os.path.join(self.folder, name)) as im:
                    w, h = im.size
            except OSError:
                continue
            if max(w, h) > 2 * WIDTH:
                self.warnings.append(
                    f"{name} is {w}x{h}: scale it down to {WIDTH}x{HEIGHT}, "
                    f"or every redraw has to decode all of it")

    # -- settings ---------------------------------------------------------

    def configure(self, overrides=None) -> dict:
        """The settings, with `overrides` (name -> value) applied."""
        values = dict(self.settings)
        for key, value in (overrides or {}).items():
            name = key.replace("-", "_").lower()
            if name not in self.settings:
                close = difflib.get_close_matches(name, list(self.settings),
                                                  n=1)
                hint = f" -- did you mean '{close[0]}'?" if close else ""
                have = ", ".join(sorted(self.settings)) or "none at all"
                raise DesignError(f"the design '{self.name}' has no setting "
                                  f"'{key}'{hint} (its settings: {have})")
            values[name] = _coerce(value)
        return values

    # -- drawing ----------------------------------------------------------

    def fonts(self) -> _Fonts:
        if self._fonts is None:
            self._fonts = _Fonts(self.folder if self.own_folder else None,
                                 self.text)
            self.warnings += self._fonts.warnings
        return self._fonts

    def rasterize(self, svg: str):
        """Filled-in SVG text -> 320x320 RGB image."""
        resvg = _resvg()
        Image, _ = _pillow()
        try:
            png = resvg.svg_to_bytes(svg_string=svg, width=WIDTH,
                                     height=HEIGHT,
                                     resources_dir=self.folder,
                                     **self.fonts().kwargs())
        except ValueError as exc:
            # Placeholders never span lines, so resvg's line numbers are the
            # file's line numbers too.
            raise DesignError(f"{self.where}: cannot draw this SVG ({exc})") \
                from None
        image = Image.open(io.BytesIO(bytes(png)))
        image.load()
        flat = Image.new("RGB", image.size, (0, 0, 0))
        rgba = image.convert("RGBA")
        flat.paste(rgba, (0, 0), rgba)
        return flat

    def stamp(self) -> tuple:
        """Changes whenever any file of the design does."""
        if self.own_folder:
            files = []
            for sub in (self.path, os.path.join(self.path, "fonts")):
                try:
                    files += [os.path.join(sub, f) for f in os.listdir(sub)]
                except OSError:
                    pass
        else:
            files = [self.path]
        out = []
        for path in sorted(files):
            try:
                st = os.stat(path)
            except OSError:
                continue
            out.append((path, st.st_mtime_ns, st.st_size))
        return tuple(out)


# ---------------------------------------------------------------------------
# a design on screen

def _stepped(value: float, step, decimals: int):
    """Round a reading to the resolution the design gets to see."""
    if step is None:                        # a temperature
        return round(value) if decimals <= 0 else round(value, decimals)
    if step >= 1:
        return int(round(value / step) * step)
    digits = max(0, -math.floor(math.log10(step)))
    return round(round(value / step) * step, digits)


class Live:
    """A design being shown: reads what it uses, redraws only on change.

    Each `frame()` fills the template in afresh.  Sensors are read lazily, the
    first time a placeholder asks, so a design without a GPU reading never
    runs nvidia-smi.  Readings are smoothed and then rounded to whole steps
    (whole degrees, by default) before the design sees them, and the
    filled-in SVG is compared with the last one drawn: if the text is the
    same, so is the picture, and nothing is rendered or uploaded at all.
    """

    def __init__(self, ref, settings=None, smooth: float = 6.0,
                 demo: bool = False, warn=None):
        self.overrides = dict(settings or {})
        self.demo = demo
        self.smoother = Smoother(smooth)
        self.warn = warn or (lambda message: print(f"warning: {message}",
                                                   file=sys.stderr))
        self.entry = ref if isinstance(ref, Entry) else find(ref)
        self.design = load(self.entry)
        self.values = self.design.configure(self.overrides)
        self._stamp = self.design.stamp()
        self._shown = None
        self._reported: set = set()
        self._report_warnings()

    def _report_warnings(self):
        for message in self.design.warnings:
            if message not in self._reported:
                self._reported.add(message)
                self.warn(message)

    def reload_if_changed(self) -> bool:
        """Pick up edits to the design's files.  True if it was reloaded.

        A broken edit -- a typo while someone is still typing -- keeps the
        previous version on screen and says why, once.
        """
        stamp = self.design.stamp()
        if stamp == self._stamp:
            return False
        self._stamp = stamp
        try:
            design = load(self.entry)
            values = design.configure(self.overrides)
        except DesignError as exc:
            self.warn(f"{exc}\n(the previous version stays on screen until "
                      f"this is fixed)")
            return False
        self.design, self.values = design, values
        self._shown = None
        self._reported.clear()
        return True

    def _decimals(self) -> int:
        value = self.values.get("decimals", 0)
        return max(0, min(2, int(value))) if isinstance(value, (int, float)) \
            else 0

    def _read(self, name: str):
        if name in TEXT:
            return TEXT[name]()
        sensor = SENSORS.get(name)
        if sensor is None:
            return MISSING
        raw = sensor.demo if self.demo else sensor.read()
        value = self.smoother.feed(name, raw)
        if value is None:
            return MISSING
        return _stepped(value, sensor.step, self._decimals())

    def svg(self) -> str:
        """The design filled in with what the sensors say right now."""
        cache: dict = {}

        def lookup(name):
            if name in self.values:
                return self.values[name]
            if name not in cache:
                cache[name] = self._read(name)
            return cache[name]

        def sensor(name):
            name = str(name)
            return lookup(name) if name in SENSORS or name in TEXT \
                else MISSING

        def report(error):
            message = f"{self.design.where}, {error}"
            if message not in self._reported:
                self._reported.add(message)
                self.warn(message)

        functions = dict(FUNCTIONS, sensor=sensor)
        return self.design.template.render(lookup, functions, on_error=report)

    def frame(self, force: bool = False):
        """The picture to show now, or None when it would match the last."""
        svg = self.svg()
        if svg == self._shown and not force:
            return None
        image = self.design.rasterize(svg)
        self._shown = svg
        self._report_warnings()       # font warnings surface on first draw
        return image


# ---------------------------------------------------------------------------
# making a new one

_PICTURE = """\
<svg xmlns="http://www.w3.org/2000/svg" width="320" height="320"
     viewBox="0 0 320 320" data-text-color="#ffffff">
  <title>@NAME@</title>
  <!-- Your picture, filling the round panel.  Swap background.png for
       another one whenever you like: the display picks it up on save. -->
  <image href="background.png" x="0" y="0" width="320" height="320"
         preserveAspectRatio="xMidYMid slice"/>

  <!-- The numbers.  The soft shadow keeps them readable on any picture.
       Everything between {{ and }} is filled in live; see docs/DESIGNS.md. -->
  <defs>
    <filter id="shadow" x="-20%" y="-20%" width="140%" height="140%">
      <feDropShadow dx="0" dy="2" stdDeviation="3" flood-color="#000000"
                    flood-opacity="0.75"/>
    </filter>
  </defs>
  <g fill="{{ text_color }}" filter="url(#shadow)" text-anchor="middle">
    <text x="160" y="180" font-family="Chakra Petch" font-weight="700"
          font-size="100">{{ fmt(cpu) }}°</text>
    <text x="160" y="222" font-family="JetBrains Mono" font-size="16"
          letter-spacing="4">CPU</text>
    <text x="160" y="266" font-family="JetBrains Mono" font-weight="700"
          font-size="18">GPU {{ fmt(gpu) }}°</text>
  </g>
</svg>
"""


def create(name: str, source: str | None = None,
           image: str | None = None) -> str:
    """A new design of yours, copied from another one or built on a picture.

    Returns the path of its design.svg.
    """
    if not _NAME.fullmatch(name):
        raise DesignError("a design name may use letters, digits, '.', '-' "
                          "and '_', and must start with a letter or digit")
    dest = os.path.join(user_dir(), name)
    if os.path.exists(dest) or os.path.exists(dest + ".svg"):
        raise DesignError(f"{pretty_path(dest)} already exists")

    if image:
        Image, _ = _pillow()
        try:
            picture = Image.open(image)
            picture.load()
        except OSError as exc:
            raise DesignError(f"cannot read {image}: {exc}") from None
        # Scaled once, here, so the renderer never decodes a big photo.
        picture = picture.convert("RGB")
        scale = max(WIDTH / picture.width, HEIGHT / picture.height)
        size = (max(WIDTH, round(picture.width * scale)),
                max(HEIGHT, round(picture.height * scale)))
        picture = picture.resize(size, Image.LANCZOS)
        left, top = (size[0] - WIDTH) // 2, (size[1] - HEIGHT) // 2
        picture = picture.crop((left, top, left + WIDTH, top + HEIGHT))
        os.makedirs(dest)
        picture.save(os.path.join(dest, "background.png"))
        path = os.path.join(dest, SVG_NAME)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(_PICTURE.replace("@NAME@", name))
        return path

    origin = find(source or STARTER)
    if os.path.isdir(origin.path):
        shutil.copytree(origin.path, dest)
        path = _svg_in(dest)
    else:
        os.makedirs(dest)
        path = os.path.join(dest, SVG_NAME)
        shutil.copyfile(origin.path, path)

    # Name the copy after itself, so the two are told apart in lists.
    with open(path, encoding="utf-8-sig") as fh:
        text = fh.read()
    text = re.sub(r"<title>.*?</title>", f"<title>{name}</title>", text,
                  count=1, flags=re.DOTALL)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)
    return path
