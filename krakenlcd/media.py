# SPDX-License-Identifier: GPL-3.0-or-later
"""Turning colours and image files into something the LCD will accept.

The firmware takes exactly two payload flavours: a GIF file, or a raw RGBA
frame.  A GIF is far the better deal -- a flat colour compresses to a few
hundred bytes where the same frame as RGBA is 400 KiB -- so everything here
produces GIFs.

Flat fills go through the project's own encoder and need nothing but the
stdlib.  Re-encoding an arbitrary GIF, though, means decoding, scaling and
quantising dozens of frames, and the pure-Python versions of those take about
a minute for a 45-frame clip.  That path therefore uses Pillow, which is
already required for text rendering.
"""

from __future__ import annotations

import io

from . import gif
from .device import HEIGHT, WIDTH

__all__ = ["solid", "prepare_gif", "prepare_image"]


def solid(color, width: int = WIDTH, height: int = HEIGHT) -> bytes:
    """A single-frame GIF of one flat colour.

    Rotation is irrelevant for a flat fill, so there is no parameter for it.
    """
    r, g, b = color
    indices = bytes(width * height)  # every pixel index 0
    return gif.encode(width, height,
                      [(indices, [(r, g, b)], 1000, -1)], loop=0)


def _pillow():
    try:
        from PIL import Image, ImageSequence
    except ImportError:
        raise SystemExit(
            "re-encoding image files needs Pillow.\n"
            "    pip install --user pillow\n"
            "or install your distribution's python-pillow package."
        ) from None
    return Image, ImageSequence


def _fit(Image, frame, geometry: str, background):
    """Scale one frame to the panel, cropping or padding as asked."""
    frame = frame.convert("RGB")
    sw, sh = frame.size
    if geometry == "stretch":
        return frame.resize((WIDTH, HEIGHT), Image.LANCZOS)
    if geometry == "cover":
        scale = max(WIDTH / sw, HEIGHT / sh)
    else:  # contain
        scale = min(WIDTH / sw, HEIGHT / sh)
    tw, th = max(1, round(sw * scale)), max(1, round(sh * scale))
    frame = frame.resize((tw, th), Image.LANCZOS)
    canvas = Image.new("RGB", (WIDTH, HEIGHT), background)
    canvas.paste(frame, ((WIDTH - tw) // 2, (HEIGHT - th) // 2))
    return canvas


def prepare_gif(source, rotate: int = 0, geometry: str = "cover",
                background=(0, 0, 0), max_frames: int | None = None,
                colors: int = 256) -> bytes:
    """Re-encode an animated GIF to 320x320 for the LCD.

    ``rotate`` is the panel's orientation in degrees: uploaded content is
    displayed exactly as sent, so the rotation has to be baked in here.
    """
    Image, ImageSequence = _pillow()

    if isinstance(source, (bytes, bytearray)):
        img = Image.open(io.BytesIO(bytes(source)))
    else:
        img = Image.open(source)

    frames = []
    durations = []
    for frame in ImageSequence.Iterator(img):
        out = _fit(Image, frame, geometry, background)
        if rotate % 360:
            out = out.rotate(-(rotate % 360), resample=Image.BICUBIC,
                             fillcolor=background)
        if colors < 256:
            out = out.quantize(colors=colors).convert("RGB")
        frames.append(out)
        durations.append(frame.info.get("duration", 100) or 100)
        if max_frames and len(frames) >= max_frames:
            break

    if not frames:
        raise ValueError("no frames in that file")

    buf = io.BytesIO()
    frames[0].save(buf, format="GIF", save_all=True,
                   append_images=frames[1:], duration=durations,
                   loop=0, interlace=False, optimize=True)
    return buf.getvalue()


def prepare_image(source, rotate: int = 0, geometry: str = "cover",
                  background=(0, 0, 0)) -> bytes:
    """Same as prepare_gif but for a still: one frame, no animation."""
    return prepare_gif(source, rotate=rotate, geometry=geometry,
                       background=background, max_frames=1)
