# SPDX-License-Identifier: GPL-3.0-or-later
"""Draw the liquid-temperature screen ourselves, in any colour we like.

The Kraken's built-in temperature screen (0x38 0x01 0x02) is drawn by the
firmware and is always white -- there is no protocol field for its colour.  So
to get a red one we redraw the same layout on the host and push it to the LCD
as a normal image.  From the firmware's point of view it is just a picture;
from the user's point of view it is the same screen in a different colour.

Text rasterisation needs a font engine, so this module -- unlike the protocol
layer -- uses Pillow, and so does the quantising and GIF encoding after it
(`media.encode_frame`), which is the part that runs on every change.
"""

from __future__ import annotations

import os

from . import media
from .device import HEIGHT, WIDTH

__all__ = ["render", "render_gif", "parse_color", "COLORS"]

# Handful of names so the CLI can take --color red instead of --color ff0000.
COLORS = {
    "red": (255, 0, 0),
    "nzxt-red": (237, 28, 36),
    "orange": (255, 120, 0),
    "amber": (255, 176, 0),
    "yellow": (255, 214, 0),
    "green": (0, 220, 90),
    "cyan": (0, 200, 255),
    "blue": (60, 120, 255),
    "purple": (170, 90, 255),
    "magenta": (255, 0, 200),
    "white": (255, 255, 255),
    "grey": (128, 128, 128),
    "black": (0, 0, 0),
}

# Preference order for the digits.  NZXT uses a condensed geometric sans; Fira
# Sans Condensed is the closest thing usually present on a Linux box.  Each
# entry is tried in turn and the first one that loads wins.
_FONT_CANDIDATES = (
    "/usr/share/fonts/TTF/FiraSansCondensed-Medium.ttf",
    "/usr/share/fonts/fira-sans/FiraSansCondensed-Medium.ttf",
    "/usr/share/fonts/TTF/DejaVuSansCondensed-Bold.ttf",
    "/usr/share/fonts/TTF/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/liberation/LiberationSans-Bold.ttf",
    "/usr/share/fonts/TTF/LiberationSans-Bold.ttf",
)

# Supersampling factor.  The LCD is small and the digits are large, so edge
# quality is what sells the illusion that the firmware drew this.
_SS = 3

# Type sizes in display pixels, before the round-panel fit shrinks them.
_NUM_PT = 186
_UNIT_PT = 62
_GAP_PT = 6

# The panel is circular; anything outside this fraction of the radius is
# either cut off by the bezel or uncomfortably close to it.
_SAFE_RADIUS = 0.88


def parse_color(text: str) -> tuple[int, int, int]:
    """Accept a name from COLORS, '#rrggbb', 'rrggbb', or 'r,g,b'."""
    text = text.strip().lower()
    if text in COLORS:
        return COLORS[text]
    if "," in text:
        parts = [p.strip() for p in text.split(",")]
        if len(parts) != 3:
            raise ValueError(f"expected three components in {text!r}")
        rgb = tuple(int(p) for p in parts)
    else:
        hexpart = text[1:] if text.startswith("#") else text
        if len(hexpart) == 3:
            hexpart = "".join(c * 2 for c in hexpart)
        if len(hexpart) != 6:
            raise ValueError(
                f"cannot read {text!r} as a colour; try a name "
                f"({', '.join(sorted(COLORS))}), #rrggbb, or r,g,b")
        rgb = tuple(int(hexpart[i:i + 2], 16) for i in (0, 2, 4))
    for c in rgb:
        if not 0 <= c <= 255:
            raise ValueError(f"colour component out of range in {text!r}")
    return rgb  # type: ignore[return-value]


def _pillow():
    try:
        from PIL import Image, ImageDraw, ImageFont
    except ImportError:
        raise SystemExit(
            "the temperature screen needs Pillow to rasterise text.\n"
            "    pip install --user pillow\n"
            "or install your distribution's python-pillow package."
        ) from None
    return Image, ImageDraw, ImageFont


def _load_font(ImageFont, size: int, path: str | None = None):
    candidates = (path,) + _FONT_CANDIDATES if path else _FONT_CANDIDATES
    for cand in candidates:
        if cand and os.path.exists(cand):
            try:
                return ImageFont.truetype(cand, size)
            except OSError:
                continue
    raise SystemExit(
        "no usable TrueType font found. Install one (e.g. ttf-dejavu) or "
        "pass --font /path/to/font.ttf")


def _text_size(draw, text, font):
    left, top, right, bottom = draw.textbbox((0, 0), text, font=font)
    return right - left, bottom - top, left, top


def render(temp: float, color=(255, 0, 0), background=(0, 0, 0),
           decimals: int = 0, label: str | None = None,
           font_path: str | None = None, dim: float = 0.55,
           rotate: int = 0, scale: float = 1.0) -> bytes:
    """Render one temperature screen and return it as 320x320 RGB bytes.

    The layout mirrors the firmware's: the reading centred, with a smaller
    degree sign and unit hung off its top right.  ``dim`` fades the unit and
    label relative to the digits, which is what keeps the number readable at
    a glance; ``scale`` tunes the size of the whole group.

    ``rotate`` is the display's own orientation setting, in degrees.  The
    firmware rotates the screens *it* draws, but it hands uploaded images to
    the panel as they are, so anything we upload has to be pre-rotated or it
    comes out lying on its side.
    """
    Image, ImageDraw, ImageFont = _pillow()

    w, h = WIDTH * _SS, HEIGHT * _SS
    img = Image.new("RGB", (w, h), background)
    draw = ImageDraw.Draw(img)

    text = f"{temp:.{decimals}f}" if decimals else f"{temp:.0f}"
    unit = "°C"

    dim_color = tuple(max(0, min(255, int(c * dim + b * (1 - dim))))
                      for c, b in zip(color, background))

    # Font metrics put the pen on the ascender line, which is nowhere near the
    # top of the ink -- and how far off it is depends on the digits and on the
    # font.  textbbox gives the ink box, so lay the group out in ink
    # coordinates: the number's top-left is the origin and the unit hangs off
    # its top right, tops aligned.
    def layout(fit: float):
        """Return a draw callback and the size of the ink it will produce."""
        s = fit * scale
        num_font = _load_font(ImageFont, max(8, int(_NUM_PT * _SS * s)),
                              font_path)
        unit_font = _load_font(ImageFont, max(6, int(_UNIT_PT * _SS * s)),
                               font_path)
        nw, nh, nx0, ny0 = _text_size(draw, text, num_font)
        uw, uh, ux0, uy0 = _text_size(draw, unit, unit_font)
        gap = int(_GAP_PT * _SS * s)

        def place(target, ox, oy, num_fill, unit_fill):
            d = ImageDraw.Draw(target)
            d.text((ox - nx0, oy - ny0), text, font=num_font, fill=num_fill)
            d.text((ox + nw + gap - ux0, oy - uy0), unit, font=unit_font,
                   fill=unit_fill)

        return place, (nw + gap + uw, max(nh, uh))

    # The Kraken's panel is round, so a three-digit reading that would fit a
    # square canvas can still run past the edge of the glass.  Shrink until
    # the group's corners sit inside the safe radius.
    fit = 1.0
    place, (gw, gh) = layout(fit)
    limit = (WIDTH * _SS / 2) * _SAFE_RADIUS
    for _ in range(6):
        half_diag = ((gw / 2) ** 2 + (gh / 2) ** 2) ** 0.5
        if half_diag <= limit:
            break
        fit *= limit / half_diag
        place, (gw, gh) = layout(fit)

    place(img, (w - gw) // 2, (h - gh) // 2, color, dim_color)

    if label:
        label_font = _load_font(ImageFont, int(24 * _SS), font_path)
        lw, _, lx0, ly0 = _text_size(draw, label, label_font)
        draw.text(((w - lw) // 2 - lx0, h - int(52 * _SS) - ly0), label,
                  font=label_font, fill=dim_color)

    img = img.resize((WIDTH, HEIGHT), Image.LANCZOS)
    if rotate % 360:
        img = img.rotate(-(rotate % 360), resample=Image.BICUBIC,
                         fillcolor=background)
    return img.tobytes()


def render_gif(temp: float, **kwargs) -> bytes:
    """Render a screen and wrap it in a single-frame GIF.

    A GIF of flat-coloured text on black is a couple of kilobytes, so pushing
    a fresh one every few seconds costs almost nothing on the bus -- far less
    than the 400 KiB a raw RGBA frame would take.
    """
    Image, _, _ = _pillow()
    rgb = render(temp, **kwargs)
    # Already rotated by render(); encode_frame only quantises and wraps it.
    return media.encode_frame(Image.frombytes("RGB", (WIDTH, HEIGHT), rgb))


def save_png(path: str, temp: float, **kwargs):
    """Write the same image to a file, for checking the layout off-device."""
    Image, _, _ = _pillow()
    rgb = render(temp, **kwargs)
    Image.frombytes("RGB", (WIDTH, HEIGHT), rgb).save(path)
