# SPDX-License-Identifier: GPL-3.0-or-later
"""A self-contained GIF87a/89a decoder and encoder.

Written from scratch against the GIF89a specification so the driver has no
image-library dependency at all.  Only the stdlib is used.

Decoding yields fully composited RGBA frames (honouring transparency and all
four disposal methods), which is what you need before rescaling: individual
sub-frames of an optimised GIF are meaningless on their own.

Encoding writes a GIF with one local colour table per frame, a NETSCAPE2.0
looping extension, and proper LZW compression.
"""

from __future__ import annotations

import struct

__all__ = ["GifError", "Frame", "decode", "encode"]


class GifError(Exception):
    pass


class Frame:
    """One fully composited frame."""

    __slots__ = ("rgba", "delay_ms", "width", "height")

    def __init__(self, rgba: bytearray, width: int, height: int, delay_ms: int):
        self.rgba = rgba
        self.width = width
        self.height = height
        self.delay_ms = delay_ms

    def __repr__(self):
        return f"<Frame {self.width}x{self.height} {self.delay_ms}ms>"


# ==========================================================================
# LZW
# ==========================================================================

def _lzw_decode(min_code_size: int, data: bytes, max_out: int) -> bytearray:
    if not 2 <= min_code_size <= 11:
        raise GifError(f"bad LZW minimum code size {min_code_size}")

    clear_code = 1 << min_code_size
    end_code = clear_code + 1

    # table[i] is the byte string for code i.  Kept as a list of bytes objects.
    def fresh_table():
        return [bytes((i,)) for i in range(clear_code)] + [b"", b""]

    table = fresh_table()
    code_size = min_code_size + 1
    next_code = end_code + 1

    out = bytearray()
    prev = -1
    bitpos = 0
    total_bits = len(data) * 8

    while bitpos + code_size <= total_bits:
        idx = bitpos >> 3
        # up to 12-bit code at up to 7-bit offset -> at most 19 bits -> 3 bytes
        window = int.from_bytes(data[idx:idx + 3].ljust(3, b"\x00"), "little")
        code = (window >> (bitpos & 7)) & ((1 << code_size) - 1)
        bitpos += code_size

        if code == clear_code:
            table = fresh_table()
            code_size = min_code_size + 1
            next_code = end_code + 1
            prev = -1
            continue
        if code == end_code:
            break

        if prev < 0:
            if code >= clear_code:
                raise GifError("first LZW code is not a literal")
            entry = table[code]
        else:
            if code < next_code:
                entry = table[code]
                if not entry and code >= clear_code:
                    raise GifError(f"reference to undefined LZW code {code}")
            elif code == next_code:
                # the classic self-referential KwKwK case
                entry = table[prev] + table[prev][:1]
            else:
                raise GifError(f"LZW code {code} out of range (next={next_code})")

            if next_code < 4096:
                table.append(table[prev] + entry[:1])
                next_code += 1
                if next_code == (1 << code_size) and code_size < 12:
                    code_size += 1

        out += entry
        prev = code
        if len(out) >= max_out:
            break

    return out


class _BitWriter:
    __slots__ = ("_buf", "_acc", "_nbits")

    def __init__(self):
        self._buf = bytearray()
        self._acc = 0
        self._nbits = 0

    def write(self, value: int, nbits: int) -> None:
        self._acc |= (value & ((1 << nbits) - 1)) << self._nbits
        self._nbits += nbits
        while self._nbits >= 8:
            self._buf.append(self._acc & 0xFF)
            self._acc >>= 8
            self._nbits -= 8

    def finish(self) -> bytearray:
        if self._nbits:
            self._buf.append(self._acc & 0xFF)
            self._acc = 0
            self._nbits = 0
        return self._buf


def _lzw_encode(indices: bytes, min_code_size: int) -> bytearray:
    clear_code = 1 << min_code_size
    end_code = clear_code + 1

    bw = _BitWriter()
    code_size = min_code_size + 1
    table: dict[bytes, int] = {bytes((i,)): i for i in range(clear_code)}
    next_code = end_code + 1

    bw.write(clear_code, code_size)

    prefix = b""
    for byte in indices:
        candidate = prefix + bytes((byte,))
        if candidate in table:
            prefix = candidate
            continue

        bw.write(table[prefix], code_size)

        if next_code < 4096:
            table[candidate] = next_code
            next_code += 1
            # The encoder adds a table entry on every emitted code, but the
            # decoder only adds one from its *second* code onwards -- it is
            # always exactly one entry behind.  So the encoder has to widen one
            # step later than the decoder's `next_code == 1 << code_size` for
            # the two to stay in lockstep.
            if next_code == (1 << code_size) + 1 and code_size < 12:
                code_size += 1
        else:
            bw.write(clear_code, code_size)
            table = {bytes((i,)): i for i in range(clear_code)}
            next_code = end_code + 1
            code_size = min_code_size + 1

        prefix = bytes((byte,))

    if prefix:
        bw.write(table[prefix], code_size)
    bw.write(end_code, code_size)
    return bw.finish()


# ==========================================================================
# decoding
# ==========================================================================

class _Reader:
    __slots__ = ("data", "pos")

    def __init__(self, data: bytes):
        self.data = data
        self.pos = 0

    def take(self, n: int) -> bytes:
        if self.pos + n > len(self.data):
            raise GifError("truncated GIF")
        chunk = self.data[self.pos:self.pos + n]
        self.pos += n
        return chunk

    def u8(self) -> int:
        return self.take(1)[0]

    def u16(self) -> int:
        return struct.unpack("<H", self.take(2))[0]

    def sub_blocks(self) -> bytes:
        """Read a chain of length-prefixed sub-blocks up to the 0x00 terminator."""
        out = bytearray()
        while True:
            n = self.u8()
            if n == 0:
                return bytes(out)
            out += self.take(n)


def _palette(raw: bytes) -> list[tuple[int, int, int]]:
    return [(raw[i], raw[i + 1], raw[i + 2]) for i in range(0, len(raw), 3)]


_INTERLACE_PASSES = ((0, 8), (4, 8), (2, 4), (1, 2))


def decode(data: bytes) -> tuple[int, int, list[Frame]]:
    """Decode a GIF into composited RGBA frames.

    Returns (width, height, frames).
    """
    r = _Reader(data)
    magic = r.take(6)
    if magic[:3] != b"GIF":
        raise GifError("not a GIF file (bad magic)")
    if magic[3:] not in (b"87a", b"89a"):
        raise GifError(f"unsupported GIF version {magic[3:]!r}")

    width = r.u16()
    height = r.u16()
    if width == 0 or height == 0:
        raise GifError("GIF has zero dimensions")
    packed = r.u8()
    bg_index = r.u8()
    r.u8()  # pixel aspect ratio, ignored

    gct: list[tuple[int, int, int]] = []
    if packed & 0x80:
        gct = _palette(r.take(3 * (2 << (packed & 0x07))))

    # canvas starts fully transparent
    canvas = bytearray(width * height * 4)
    frames: list[Frame] = []

    # pending graphic control extension state
    delay_cs = 0
    transparent_index = -1
    disposal = 0

    while True:
        try:
            block = r.u8()
        except GifError:
            break  # some encoders omit the trailer

        if block == 0x3B:  # trailer
            break

        if block == 0x21:  # extension
            label = r.u8()
            if label == 0xF9:  # graphic control
                size = r.u8()
                body = r.take(size)
                if size >= 4:
                    flags = body[0]
                    disposal = (flags >> 2) & 0x07
                    transparent_index = body[3] if (flags & 0x01) else -1
                    delay_cs = struct.unpack("<H", body[1:3])[0]
                while r.u8() != 0:
                    r.pos -= 1
                    r.take(r.u8())
            else:
                r.sub_blocks()
            continue

        if block != 0x2C:
            raise GifError(f"unexpected block 0x{block:02x} at offset {r.pos - 1}")

        # ---- image descriptor
        fx, fy, fw, fh = r.u16(), r.u16(), r.u16(), r.u16()
        ipacked = r.u8()
        interlaced = bool(ipacked & 0x40)
        if ipacked & 0x80:
            lct = _palette(r.take(3 * (2 << (ipacked & 0x07))))
        else:
            lct = gct
        if not lct:
            raise GifError("frame has no colour table")

        min_code_size = r.u8()
        lzw_data = r.sub_blocks()
        pixels = _lzw_decode(min_code_size, lzw_data, fw * fh)
        if len(pixels) < fw * fh:
            # tolerate truncated frames by padding with the transparent index
            pad = 0 if transparent_index < 0 else transparent_index
            pixels += bytes([pad]) * (fw * fh - len(pixels))

        saved = bytes(canvas) if disposal == 3 else None

        # ---- composite onto the canvas
        ncolors = len(lct)
        if interlaced:
            rows = []
            for start, step in _INTERLACE_PASSES:
                rows.extend(range(start, fh, step))
        else:
            rows = range(fh)

        for src_row, dst_row in enumerate(rows):
            y = fy + dst_row
            if not (0 <= y < height):
                continue
            src = src_row * fw
            base = (y * width + fx) * 4
            for col in range(fw):
                x = fx + col
                if not (0 <= x < width):
                    continue
                idx = pixels[src + col]
                if idx == transparent_index:
                    continue
                if idx >= ncolors:
                    continue
                rr, gg, bb = lct[idx]
                o = base + col * 4
                canvas[o] = rr
                canvas[o + 1] = gg
                canvas[o + 2] = bb
                canvas[o + 3] = 255

        delay_ms = delay_cs * 10
        if delay_ms <= 10:
            delay_ms = 100  # what browsers do with 0/1cs frames
        frames.append(Frame(bytearray(canvas), width, height, delay_ms))

        # ---- apply disposal for the next frame
        if disposal == 2:
            fill = (0, 0, 0, 0)
            if gct and 0 <= bg_index < len(gct):
                fill = gct[bg_index] + (0,)
            for row in range(fy, min(fy + fh, height)):
                o = (row * width + fx) * 4
                span = max(0, min(fw, width - fx))
                canvas[o:o + span * 4] = bytes(fill) * span
        elif disposal == 3 and saved is not None:
            canvas = bytearray(saved)

        delay_cs = 0
        transparent_index = -1
        disposal = 0

    if not frames:
        raise GifError("GIF contains no frames")
    return width, height, frames


# ==========================================================================
# encoding
# ==========================================================================

def _bits_for(ncolors: int) -> int:
    bits = 1
    while (1 << bits) < ncolors:
        bits += 1
    return max(2, min(8, bits))


def _sub_blocks(data: bytes) -> bytearray:
    out = bytearray()
    for off in range(0, len(data), 255):
        chunk = data[off:off + 255]
        out.append(len(chunk))
        out += chunk
    out.append(0)
    return out


def encode(width: int, height: int, frames, loop: int = 0) -> bytes:
    """Build a GIF89a from indexed frames.

    Each item of `frames` is a tuple ``(indices, palette, delay_ms,
    transparent_index)``:

      indices           bytes, width*height palette indices
      palette           list of (r, g, b), at most 256 entries
      delay_ms          frame duration in milliseconds
      transparent_index index to treat as transparent, or -1 for none
    """
    frames = list(frames)
    if not frames:
        raise GifError("no frames to encode")

    out = bytearray(b"GIF89a")
    # Logical screen descriptor.  No global colour table: every frame carries
    # its own, which keeps quality high when frames differ a lot.
    out += struct.pack("<HHBBB", width, height, 0x70, 0, 0)

    if loop >= 0 and len(frames) > 1:
        out += b"\x21\xff\x0bNETSCAPE2.0\x03\x01"
        out += struct.pack("<H", loop)
        out += b"\x00"

    for indices, palette, delay_ms, transparent_index in frames:
        if len(indices) != width * height:
            raise GifError(
                f"frame has {len(indices)} pixels, expected {width * height}")
        if not palette:
            raise GifError("frame has an empty palette")
        if len(palette) > 256:
            raise GifError(f"palette too large: {len(palette)}")

        bits = _bits_for(len(palette))
        table_size = 1 << bits

        # graphic control extension
        flags = (1 << 2)  # disposal 1 = leave the frame in place
        if transparent_index is not None and transparent_index >= 0:
            flags |= 0x01
            tidx = transparent_index
        else:
            tidx = 0
        delay_cs = max(1, min(65535, round(delay_ms / 10)))
        out += b"\x21\xf9\x04" + bytes((flags,)) + struct.pack("<H", delay_cs) \
            + bytes((tidx,)) + b"\x00"

        # image descriptor with a local colour table
        out += b"\x2c" + struct.pack("<HHHH", 0, 0, width, height)
        out += bytes((0x80 | (bits - 1),))
        for i in range(table_size):
            if i < len(palette):
                rr, gg, bb = palette[i]
                out += bytes((rr & 0xFF, gg & 0xFF, bb & 0xFF))
            else:
                out += b"\x00\x00\x00"

        out += bytes((bits,))
        out += _sub_blocks(bytes(_lzw_encode(bytes(indices), bits)))

    out += b"\x3b"
    return bytes(out)
