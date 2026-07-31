# SPDX-License-Identifier: GPL-3.0-or-later
"""The gauge dashboard: a 270° scale, a big reading, a second one below.

The drawing is `kraken_2a.py`, the 1:1 Pillow transcription of
`design/kraken-2a-static.html`, moved into the package.  Constants, geometry
and typography are unchanged from it; what is added here is everything the
panel needs and a standalone script does not: the fonts as package data, the
pre-rotation an uploaded image requires, compositing the transparent corners
onto a background, GIF encoding, and the damping and change detection that
keep a twitchy sensor from redrawing the screen every second.

The three fonts ship in `fonts/` (SIL Open Font License, texts alongside) so
the design looks the same on a machine that has never heard of Chakra Petch.
"""

from __future__ import annotations

import math
import os
import sys

from . import gif, imaging
from .device import HEIGHT, WIDTH

__all__ = ["render", "render_gif", "save_png", "state_key", "state_text",
           "Smoother"]

# ------------------------------------------------------------------ constants
SIZE = WIDTH             # the panel edge, 320 px
S = 4                    # supersampling, for soft edges
PAPER = (244, 244, 241, 255)      # #f4f4f1
INK = (17, 18, 20, 255)           # #111214
TRACK = (220, 220, 215, 255)      # #dcdcd7
RED = (216, 52, 42, 255)          # #d8342a
TICK_MIN = (169, 172, 168, 255)   # #a9aca8
GREY_SUB = (92, 95, 101, 255)     # #5c5f65
GREY_TOP = (154, 157, 159, 255)   # #9a9d9f

# Outside the dial.  The panel is round, so this is only ever the four corners
# nobody sees -- but it is what the antialiased rim blends into, so it matters
# at the edge.  Same value as the page background in the HTML.
OUTSIDE = (12, 13, 15)

T_MIN, T_MAX = 30, 100   # scale range
HOT = 80                 # red from here up
SWEEP = 270.0            # total arc
START = 225.0            # compass angle (0 = 12 o'clock, clockwise) for T_MIN

_FONT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fonts")
FONT_BIG = os.path.join(_FONT_DIR, "ChakraPetch-Bold.ttf")
FONT_MONO = os.path.join(_FONT_DIR, "JetBrainsMono-Regular.ttf")
FONT_MONOB = os.path.join(_FONT_DIR, "JetBrainsMono-Bold.ttf")

# What the line at the top says.  Thresholds are the reading in °C.
STATES = ((HOT, "THERMAL LIMIT"), (68, "UNTER LAST"), (52, "NORMAL"),
          (float("-inf"), "LEERLAUF"))


def _pillow():
    try:
        from PIL import Image, ImageDraw, ImageFont
    except ImportError:
        raise SystemExit(
            "the dashboard needs Pillow to rasterise text.\n"
            "    pip install --user pillow\n"
            "or install your distribution's python-pillow package."
        ) from None
    return Image, ImageDraw, ImageFont


def _font(ImageFont, path, px, override=None):
    for candidate in ((override, path) if override else (path,)):
        try:
            return ImageFont.truetype(candidate, px)
        except OSError:
            continue
    print(f"! font {os.path.basename(path)} not found – falling back",
          file=sys.stderr)
    return ImageFont.load_default()


# ------------------------------------------------------------------ geometry

def norm(t):
    return max(0.0, min(1.0, (t - T_MIN) / (T_MAX - T_MIN)))


def compass(t):
    """Temperature -> compass angle (0 = 12 o'clock, clockwise)."""
    return START + norm(t) * SWEEP


def pil_angle(a):
    """Compass angle -> PIL angle (0 = 3 o'clock, clockwise)."""
    return a - 90.0


def spaced_text(draw, xy, text, fnt, fill, tracking, anchor_center=True,
                baseline_mid=True):
    """Draw text with letter-spacing, optionally centred horizontally."""
    widths = [draw.textlength(ch, font=fnt) for ch in text]
    total = sum(widths) + tracking * (len(text) - 1)
    x, y = xy
    if anchor_center:
        x -= total / 2
    for ch, w in zip(text, widths):
        draw.text((x, y), ch, font=fnt, fill=fill,
                  anchor="lm" if baseline_mid else "la")
        x += w + tracking
    return total


def state_text(value) -> str:
    """The caption at the top for a reading."""
    if value is None:
        return "KEIN SENSOR"
    for threshold, text in STATES:
        if value >= threshold:
            return text
    return STATES[-1][1]


# ------------------------------------------------------------------ smoothing

class Smoother:
    """Exponential moving average, one per reading.

    A CPU package sensor swings by 30 °C between two seconds of idle and one
    of load, which on a gauge reads as a twitch rather than a temperature.
    `tau` is the time constant in seconds: after that long, roughly two thirds
    of a step has been followed.  `tau = 0` passes the raw value through.
    """

    def __init__(self, tau: float = 5.0):
        self.tau = max(0.0, tau)
        self._values: dict[str, float] = {}

    def feed(self, name: str, value, dt: float):
        if value is None:
            self._values.pop(name, None)
            return None
        if self.tau <= 0 or dt <= 0:
            return value
        previous = self._values.get(name)
        if previous is None:
            self._values[name] = value
            return value
        alpha = 1.0 - math.exp(-dt / self.tau)
        smoothed = previous + (value - previous) * alpha
        self._values[name] = smoothed
        return smoothed


def state_key(big, small, hot_at: float = HOT, decimals: int = 0):
    """What the panel would show, reduced to a comparable value.

    Only worth re-uploading when one of these changes: a reading wobbling in
    the hundredths would otherwise cost an upload every couple of seconds for
    a picture nobody can tell apart.
    """
    def q(v):
        return None if v is None else round(v, decimals)
    hot = big is not None and big >= hot_at
    return (q(big), q(small), state_text(big), hot)


# ------------------------------------------------------------------ rendering

def render_rgba(cpu=67, gpu=71, state=None, fill_pct=None, *,
                label="CPU °C", sub_label="GPU", hot_at=HOT,
                scale_min=T_MIN, scale_max=T_MAX, decimals=0,
                font_path=None, mono_path=None):
    """The dial, 320x320 RGBA, transparent outside the circle."""
    Image, ImageDraw, ImageFont = _pillow()

    W = SIZE * S
    img = Image.new("RGBA", (W, W), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    c = W / 2

    span = max(1e-6, scale_max - scale_min)

    def unit(t):
        return max(0.0, min(1.0, (t - scale_min) / span))

    def angle(t):
        return START + unit(t) * SWEEP

    if state is None:
        state = state_text(cpu)

    # --- dial -----------------------------------------------------------
    d.ellipse([0, 0, W - 1, W - 1], fill=PAPER)

    # --- arc: inset 9px, ring 6px wide ----------------------------------
    inset, ring = 9 * S, 6 * S
    r_out = c - inset
    box = [c - r_out + ring / 2, c - r_out + ring / 2,
           c + r_out - ring / 2, c + r_out - ring / 2]

    if fill_pct is None:
        pct = 0.0 if cpu is None else unit(cpu) * 100
    else:
        pct = max(0.0, min(100.0, fill_pct))
    a_fill = START + pct / 100 * SWEEP
    hot_now = cpu is not None and cpu >= hot_at

    d.arc(box, pil_angle(a_fill), pil_angle(START + SWEEP), fill=TRACK,
          width=int(ring))
    if pct > 0:
        d.arc(box, pil_angle(START), pil_angle(a_fill),
              fill=RED if hot_now else INK, width=int(ring))

    # --- scale, long ticks every ten degrees ----------------------------
    step = (scale_max - scale_min) / 14.0
    for i in range(15):
        t = scale_min + i * step
        major = i % 2 == 0
        h = (13 if major else 7) * S
        w = (1.5 if major else 1.0) * S
        col = RED if t >= hot_at else (INK if major else TICK_MIN)
        r1 = c - 14 * S          # outer end
        r0 = r1 - h              # inner end
        rad = math.radians(angle(t) - 90)
        dx, dy = math.cos(rad), math.sin(rad)
        d.line([c + dx * r0, c + dy * r0, c + dx * r1, c + dy * r1],
               fill=col, width=max(1, int(round(w))))

    # --- typography -----------------------------------------------------
    f_big = _font(ImageFont, FONT_BIG, 112 * S, font_path)
    f_cap = _font(ImageFont, FONT_MONO, 10 * S, mono_path)
    f_state = _font(ImageFont, FONT_MONO, 9 * S, mono_path)
    f_sub = _font(ImageFont, FONT_MONO, 11 * S, mono_path)
    f_subb = _font(ImageFont, FONT_MONOB, 11 * S, mono_path)

    # centre block: number (line-height .78) + 20px + caption row
    num_h = 112 * 0.78 * S
    cap_h = 13 * S
    block = num_h + 20 * S + cap_h
    top = (W - block) / 2

    text = "--" if cpu is None else f"{cpu:.{decimals}f}"
    d.text((c, top + num_h / 2), text, font=f_big, fill=INK, anchor="mm")

    cap_y = top + num_h + 20 * S + cap_h / 2
    cap_w = spaced_text(d, (c, cap_y), label, f_cap, INK, 0.28 * 10 * S)
    for sign in (-1, 1):
        x0 = c + sign * (cap_w / 2 + 8 * S)
        x1 = x0 + sign * 26 * S
        d.line([x0, cap_y, x1, cap_y], fill=INK, width=max(1, S))

    # status line, top 44px
    spaced_text(d, (c, 44 * S + 6.5 * S), state, f_state, GREY_TOP,
                0.30 * 9 * S)

    # footer, bottom 44px: label in grey, value in bold black
    if sub_label:
        y_sub = W - 44 * S - 6.5 * S
        lbl = sub_label + " "
        val = "--" if gpu is None else f"{gpu:.0f}"
        w_lbl = (sum(d.textlength(ch, font=f_sub) for ch in lbl)
                 + 0.10 * 11 * S * len(lbl))
        w_val = d.textlength(val, font=f_subb)
        x = c - (w_lbl + w_val) / 2
        x += spaced_text(d, (x, y_sub), lbl, f_sub, GREY_SUB, 0.10 * 11 * S,
                         anchor_center=False)
        d.text((x, y_sub), val, font=f_subb, fill=INK, anchor="lm")

    return img.resize((SIZE, SIZE), Image.LANCZOS)


def render(cpu, gpu=None, *, rotate: int = 0, background=OUTSIDE,
           **kwargs) -> bytes:
    """The dial as 320x320 RGB bytes, ready for the panel.

    `rotate` is the display's own orientation: the firmware rotates the
    screens it draws itself but hands uploaded images to the panel as they
    are, so anything we upload has to be pre-rotated to match.
    """
    Image, _, _ = _pillow()
    dial = render_rgba(cpu, gpu, **kwargs)
    flat = Image.new("RGB", dial.size, background)
    flat.paste(dial, (0, 0), dial)
    if rotate % 360:
        flat = flat.rotate(-(rotate % 360), resample=Image.BICUBIC,
                           fillcolor=background)
    return flat.tobytes()


def render_gif(cpu, gpu=None, **kwargs) -> bytes:
    """The same frame as a single-frame GIF, ready to upload.

    The design is flat -- five colours and their antialiased edges -- so 256
    entries are ample and there is nothing here for the quantiser to spoil.
    """
    rgb = render(cpu, gpu, **kwargs)
    indices, palette = imaging.quantize(rgb, 256)
    return gif.encode(WIDTH, HEIGHT, [(bytes(indices), palette, 1000, -1)],
                      loop=0)


def save_png(path: str, cpu, gpu=None, **kwargs):
    """Write the frame to a file, for checking the layout without hardware."""
    Image, _, _ = _pillow()
    rgb = render(cpu, gpu, **kwargs)
    Image.frombytes("RGB", (WIDTH, HEIGHT), rgb).save(path)
