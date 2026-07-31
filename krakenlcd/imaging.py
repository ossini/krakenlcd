# SPDX-License-Identifier: GPL-3.0-or-later
"""Rescaling and colour quantisation, implemented directly on bytearrays.

Just enough image processing to turn arbitrary RGBA frames into the indexed
320x320 frames the Kraken's GIF decoder wants, without pulling in an imaging
library.
"""

from __future__ import annotations

__all__ = ["composite_over", "resize", "quantize", "GEOMETRIES"]

GEOMETRIES = ("cover", "contain", "stretch")


def composite_over(rgba: bytes, background=(0, 0, 0)) -> bytearray:
    """Flatten RGBA onto an opaque background, returning RGB.

    The LCD has no alpha channel, so this has to happen before scaling --
    otherwise transparent pixels would bleed their (undefined) colour into
    neighbouring samples.
    """
    br, bg, bb = background
    out = bytearray(len(rgba) // 4 * 3)
    for i in range(0, len(rgba), 4):
        a = rgba[i + 3]
        o = i // 4 * 3
        if a == 255:
            out[o] = rgba[i]
            out[o + 1] = rgba[i + 1]
            out[o + 2] = rgba[i + 2]
        elif a == 0:
            out[o] = br
            out[o + 1] = bg
            out[o + 2] = bb
        else:
            ia = 255 - a
            out[o] = (rgba[i] * a + br * ia) // 255
            out[o + 1] = (rgba[i + 1] * a + bg * ia) // 255
            out[o + 2] = (rgba[i + 2] * a + bb * ia) // 255
    return out


# --------------------------------------------------------------------------
# geometry
# --------------------------------------------------------------------------

def _crop_box(sw: int, sh: int, dw: int, dh: int, geometry: str):
    """Return the source rectangle to sample from, plus the target rectangle."""
    if geometry == "stretch":
        return (0, 0, sw, sh), (0, 0, dw, dh)

    if geometry == "cover":
        # largest centred crop that has the destination's aspect ratio
        if sw * dh > dw * sh:
            new_w = max(1, round(sh * dw / dh))
            return ((sw - new_w) // 2, 0, new_w, sh), (0, 0, dw, dh)
        new_h = max(1, round(sw * dh / dw))
        return (0, (sh - new_h) // 2, sw, new_h), (0, 0, dw, dh)

    if geometry == "contain":
        scale = min(dw / sw, dh / sh)
        tw, th = max(1, round(sw * scale)), max(1, round(sh * scale))
        return (0, 0, sw, sh), ((dw - tw) // 2, (dh - th) // 2, tw, th)

    raise ValueError(f"unknown geometry {geometry!r}, expected one of {GEOMETRIES}")


def _plan_axis(src_len: int, dst_len: int, offset: int):
    """Precompute, for each destination position, the source span to average."""
    plan = []
    for i in range(dst_len):
        lo = offset + i * src_len // dst_len
        hi = offset + (i + 1) * src_len // dst_len
        if hi <= lo:
            hi = lo + 1
        plan.append((lo, hi))
    return plan


class Resizer:
    """Reusable resize plan -- computed once, applied to every frame."""

    def __init__(self, sw: int, sh: int, dw: int, dh: int,
                 geometry: str = "cover", smooth: bool = True,
                 background=(0, 0, 0)):
        self.sw, self.sh, self.dw, self.dh = sw, sh, dw, dh
        self.smooth = smooth
        self.background = background
        (cx, cy, cw, ch), (tx, ty, tw, th) = _crop_box(sw, sh, dw, dh, geometry)
        self.target = (tx, ty, tw, th)
        if smooth:
            self.xplan = _plan_axis(cw, tw, cx)
            self.yplan = _plan_axis(ch, th, cy)
        else:
            self.xmap = [cx + i * cw // tw for i in range(tw)]
            self.ymap = [cy + i * ch // th for i in range(th)]

    def apply(self, rgb: bytes) -> bytearray:
        sw, dw, dh = self.sw, self.dw, self.dh
        tx, ty, tw, th = self.target
        br, bg, bb = self.background

        out = bytearray(dw * dh * 3)
        if (br, bg, bb) != (0, 0, 0):
            out[:] = bytes((br, bg, bb)) * (dw * dh)

        if not self.smooth:
            xmap, ymap = self.xmap, self.ymap
            for j in range(th):
                srow = ymap[j] * sw
                orow = ((ty + j) * dw + tx) * 3
                for i in range(tw):
                    s = (srow + xmap[i]) * 3
                    o = orow + i * 3
                    out[o] = rgb[s]
                    out[o + 1] = rgb[s + 1]
                    out[o + 2] = rgb[s + 2]
            return out

        # Separable box filter.  Horizontal pass first into per-source-row
        # accumulators, then average those rows vertically.
        xplan, yplan = self.xplan, self.yplan

        # horizontal pass: only the source rows we actually need
        ylo_all = yplan[0][0]
        yhi_all = yplan[-1][1]
        rows: dict[int, list[int]] = {}
        for sy in range(ylo_all, yhi_all):
            base = sy * sw
            acc = [0] * (tw * 3)
            k = 0
            for xlo, xhi in xplan:
                n = xhi - xlo
                r = g = b = 0
                s = (base + xlo) * 3
                for _ in range(n):
                    r += rgb[s]
                    g += rgb[s + 1]
                    b += rgb[s + 2]
                    s += 3
                acc[k] = r // n
                acc[k + 1] = g // n
                acc[k + 2] = b // n
                k += 3
            rows[sy] = acc

        # vertical pass
        for j, (ylo, yhi) in enumerate(yplan):
            n = yhi - ylo
            orow = ((ty + j) * dw + tx) * 3
            if n == 1:
                acc = rows[ylo]
                out[orow:orow + tw * 3] = bytes(acc)
                continue
            total = [0] * (tw * 3)
            for sy in range(ylo, yhi):
                acc = rows[sy]
                for k in range(tw * 3):
                    total[k] += acc[k]
            out[orow:orow + tw * 3] = bytes(v // n for v in total)
        return out


def resize(rgb: bytes, sw: int, sh: int, dw: int, dh: int,
           geometry: str = "cover", smooth: bool = True,
           background=(0, 0, 0)) -> bytearray:
    return Resizer(sw, sh, dw, dh, geometry, smooth, background).apply(rgb)


# --------------------------------------------------------------------------
# colour quantisation (median cut)
# --------------------------------------------------------------------------

def _histogram(rgb: bytes) -> dict[tuple[int, int, int], int]:
    hist: dict[tuple[int, int, int], int] = {}
    get = hist.get
    for i in range(0, len(rgb), 3):
        key = (rgb[i], rgb[i + 1], rgb[i + 2])
        hist[key] = get(key, 0) + 1
    return hist


class _Box:
    __slots__ = ("colors", "count", "rmin", "rmax", "gmin", "gmax",
                 "bmin", "bmax")

    def __init__(self, colors: list[tuple[tuple[int, int, int], int]]):
        self.colors = colors
        self.count = sum(c for _, c in colors)
        rs = [c[0] for c, _ in colors]
        gs = [c[1] for c, _ in colors]
        bs = [c[2] for c, _ in colors]
        self.rmin, self.rmax = min(rs), max(rs)
        self.gmin, self.gmax = min(gs), max(gs)
        self.bmin, self.bmax = min(bs), max(bs)

    def priority(self) -> int:
        # split boxes that cover both many pixels and a wide colour range
        span = max(self.rmax - self.rmin, self.gmax - self.gmin,
                   self.bmax - self.bmin)
        return span * self.count

    def splittable(self) -> bool:
        return len(self.colors) > 1 and (
            self.rmax > self.rmin or self.gmax > self.gmin
            or self.bmax > self.bmin)

    def split(self):
        # longest axis, weighted by the eye's sensitivity
        spans = ((self.rmax - self.rmin) * 299,
                 (self.gmax - self.gmin) * 587,
                 (self.bmax - self.bmin) * 114)
        axis = spans.index(max(spans))
        self.colors.sort(key=lambda item: item[0][axis])
        half = self.count // 2
        acc = 0
        cut = 0
        for i, (_, cnt) in enumerate(self.colors):
            acc += cnt
            if acc >= half and i + 1 < len(self.colors):
                cut = i + 1
                break
        if cut == 0:
            cut = len(self.colors) // 2 or 1
        return _Box(self.colors[:cut]), _Box(self.colors[cut:])

    def average(self) -> tuple[int, int, int]:
        tr = tg = tb = 0
        for (r, g, b), cnt in self.colors:
            tr += r * cnt
            tg += g * cnt
            tb += b * cnt
        n = self.count or 1
        return (tr // n, tg // n, tb // n)


def quantize(rgb: bytes, max_colors: int = 256):
    """Reduce RGB pixel data to a palette plus indices.

    Returns (indices: bytearray, palette: list[(r,g,b)]).  If the image already
    has few enough distinct colours the palette is exact and nothing is lost.
    """
    if max_colors < 2:
        raise ValueError("max_colors must be >= 2")

    hist = _histogram(rgb)

    if len(hist) <= max_colors:
        palette = sorted(hist)
        lut = {c: i for i, c in enumerate(palette)}
    else:
        boxes = [_Box(list(hist.items()))]
        while len(boxes) < max_colors:
            # pick the most promising splittable box
            best_i, best_p = -1, -1
            for i, box in enumerate(boxes):
                if not box.splittable():
                    continue
                p = box.priority()
                if p > best_p:
                    best_i, best_p = i, p
            if best_i < 0:
                break
            box = boxes.pop(best_i)
            a, b = box.split()
            boxes.append(a)
            boxes.append(b)

        palette = []
        lut = {}
        for i, box in enumerate(boxes):
            palette.append(box.average())
            for color, _ in box.colors:
                lut[color] = i

    indices = bytearray(len(rgb) // 3)
    for i in range(0, len(rgb), 3):
        indices[i // 3] = lut[(rgb[i], rgb[i + 1], rgb[i + 2])]
    return indices, palette
