# SPDX-License-Identifier: GPL-3.0-or-later
"""The Kraken Z protocol: status, LCD buckets, display modes.

Everything the firmware understands is a 64-byte HID report whose first two
bytes are an ID and a sub-command.  Replies come back with the ID incremented
by one, the same sub-command in byte 1, and a result code in byte 14 (0x01 =
accepted).

Display content lives in up to 16 "buckets" carved out of ~23.75 MiB of device
memory.  Putting something on screen is three steps:

    1. 0x32 0x01 -- allocate a bucket (address and size, both in KiB)
    2. 0x36 0x01 -- arm it, push the payload over bulk endpoint 0x02,
       0x36 0x02 -- close the transfer
    3. 0x38 0x01 0x04 <bucket> -- display it

The payload is not bare pixels: it carries a 20-byte header (a 12-byte magic
plus a type/length descriptor) that tells the firmware whether the rest is a
GIF file or raw RGBA.

Mode 0x02 of the 0x38 command is the *firmware's own* liquid-temperature
screen; that one needs no bucket at all.
"""

from __future__ import annotations

import glob
import os
import time

from .lowlevel import (
    BulkChannel,
    DeviceNotFound,
    HidChannel,
    KrakenError,
    find_device,
)

__all__ = ["Kraken", "Status", "MODE_LIQUID", "MODE_BUCKET"]

# ---------------------------------------------------------------------------
# protocol constants
# ---------------------------------------------------------------------------

WIDTH = HEIGHT = 320

# 0x38 0x01 <mode> <bucket>
MODE_LIQUID = 0x02  # firmware-drawn liquid temperature (white, not tunable)
MODE_BUCKET = 0x04  # show the image/GIF stored in <bucket>

# Payload header.  12 bytes of magic, then a 4-byte type word and the length
# of the data that follows, little-endian.
_MAGIC = bytes([0x12, 0xFA, 0x01, 0xE8, 0xAB, 0xCD, 0xEF, 0x98,
                0x76, 0x54, 0x32, 0x10])
KIND_GIF = 0x01
KIND_RGBA = 0x02

BUCKET_COUNT = 16
TOTAL_MEMORY_KIB = 24320

# The bulk endpoint's max packet size.  Sending image data in exactly this
# unit is what NZXT's own tooling does.
_BULK_CHUNK = 512

# Byte 14 of every reply is the result code.
_OK = 0x01


def wrap_payload(data: bytes, kind: int = KIND_GIF) -> bytes:
    """Prefix image data with the header the LCD firmware expects."""
    return _MAGIC + bytes([kind, 0, 0, 0]) + len(data).to_bytes(4, "little")


class Status:
    """A decoded 0x75 status report."""

    __slots__ = ("liquid_temp", "pump_rpm", "pump_duty", "fan_rpm", "fan_duty",
                 "raw")

    def __init__(self, raw: bytes):
        self.raw = raw
        self.liquid_temp = raw[15] + raw[16] / 10.0
        self.pump_rpm = int.from_bytes(raw[17:19], "little")
        self.pump_duty = raw[19]
        self.fan_rpm = int.from_bytes(raw[23:25], "little")
        self.fan_duty = raw[25]

    def __repr__(self):
        return (f"<Status liquid={self.liquid_temp:.1f}C "
                f"pump={self.pump_rpm}rpm/{self.pump_duty}% "
                f"fan={self.fan_rpm}rpm/{self.fan_duty}%>")


def read_hwmon_temp() -> float | None:
    """Liquid temperature from the in-kernel nzxt_kraken3 driver.

    Preferred over asking over HID: it is a plain sysfs read, so it never
    competes with another process (CoolerControl, say) for the report queue.
    """
    for path in glob.glob("/sys/class/hwmon/hwmon*"):
        try:
            with open(os.path.join(path, "name")) as fh:
                if fh.read().strip() not in ("z53", "z63", "z73", "kraken3",
                                             "nzxt_kraken3"):
                    continue
            with open(os.path.join(path, "temp1_input")) as fh:
                return int(fh.read().strip()) / 1000.0
        except OSError:
            continue
    return None


class Kraken:
    """An open Kraken Z.  The bulk channel is only claimed when needed."""

    def __init__(self, serial: str | None = None):
        self.paths = find_device(serial)
        self.hid = HidChannel(self.paths.hidraw)
        self.hid.drain()
        self._bulk: BulkChannel | None = None

    # -- lifecycle ---------------------------------------------------------

    def close(self):
        if self._bulk is not None:
            self._bulk.close()
            self._bulk = None
        self.hid.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    @property
    def bulk(self) -> BulkChannel:
        if self._bulk is None:
            self._bulk = BulkChannel(self.paths.usbfs, interface=0,
                                     endpoint=0x02)
        return self._bulk

    # -- request/reply -----------------------------------------------------

    def command(self, cmd, timeout: float = 3.0, retries: int = 2) -> bytes:
        """Send a report and wait for the matching reply.

        Matching is on both the report ID and the sub-command, because the
        device (and any other process talking to it) sprays unrelated status
        reports into the same queue.
        """
        cmd = bytes(cmd)
        want_id, want_sub = (cmd[0] + 1) & 0xFF, cmd[1]
        for attempt in range(retries + 1):
            self.hid.drain()
            self.hid.write(cmd)
            end = time.monotonic() + timeout
            while True:
                remaining = end - time.monotonic()
                if remaining <= 0:
                    break
                reply = self.hid.read(timeout=remaining)
                if reply is None:
                    break
                if reply[0] == want_id and reply[1] == want_sub:
                    return reply
        raise KrakenError(
            f"no reply to {cmd[:4].hex(' ')} after {retries + 1} attempts")

    def checked(self, cmd, what: str, timeout: float = 3.0) -> bytes:
        reply = self.command(cmd, timeout=timeout)
        if reply[14] != _OK:
            raise KrakenError(f"{what} rejected by the firmware "
                              f"(result 0x{reply[14]:02x})")
        return reply

    # -- information -------------------------------------------------------

    def firmware(self) -> tuple[int, int, int]:
        r = self.command([0x10, 0x01])
        return (r[16], r[17], r[18])

    def status(self) -> Status:
        return Status(self.command([0x74, 0x01]))

    def liquid_temp(self) -> float:
        """Liquid temperature, from sysfs if the kernel driver is loaded."""
        temp = read_hwmon_temp()
        return temp if temp is not None else self.status().liquid_temp

    def lcd_info(self) -> dict:
        r = self.command([0x30, 0x01])
        return {
            "brightness": r[0x18],
            "orientation": r[0x1A] * 90,
            "width": int.from_bytes(r[20:22], "little"),
            "height": int.from_bytes(r[22:24], "little"),
            "raw": r[14:32].hex(" "),
        }

    def bucket_info(self, index: int) -> dict:
        """Where a bucket's data lives, or None-ish zeros if it is empty.

        Layout after the 14-byte preamble: bucket index, asset index, 0x02,
        start address and size (both 16-bit little-endian, in KiB), then two
        flag bytes.
        """
        r = self.command([0x30, 0x04, index])
        return {
            "index": r[14],
            "occupied": any(r[15:32]),
            "start_kib": int.from_bytes(r[17:19], "little"),
            "size_kib": int.from_bytes(r[19:21], "little"),
            "raw": r[14:32].hex(" "),
        }

    # -- display settings --------------------------------------------------

    def set_brightness(self, percent: int, orientation: int | None = None):
        """Set brightness in percent, and optionally rotation in quarters.

        Brightness and orientation share one report, so writing either means
        writing both -- and passing a default for the one you did not mean to
        touch silently rotates the user's display.  Read it back first.
        """
        if orientation is None:
            orientation = self.lcd_info()["orientation"] // 90
        percent = max(0, min(100, int(percent)))
        self.hid.write(bytes([0x30, 0x02, 0x01, percent, 0x00, 0x00, 0x01,
                              orientation & 0x03]))
        time.sleep(0.1)

    def set_orientation(self, degrees: int):
        if degrees not in (0, 90, 180, 270):
            raise ValueError("orientation must be 0, 90, 180 or 270 degrees")
        self.set_brightness(self.lcd_info()["brightness"], degrees // 90)

    def show_liquid_screen(self):
        """Hand the display back to the firmware's own temperature screen."""
        self.checked([0x38, 0x01, MODE_LIQUID, 0x00], "liquid screen")

    def show_bucket(self, index: int):
        self.checked([0x38, 0x01, MODE_BUCKET, index], f"show bucket {index}")

    # -- buckets -----------------------------------------------------------

    def clear_bucket(self, index: int):
        # Deleting the bucket that is currently on screen is refused, so the
        # caller has to move the display away first.
        self.checked([0x32, 0x02, index], f"delete bucket {index}")

    def clear_all_buckets(self):
        self.show_liquid_screen()
        time.sleep(0.2)
        for i in range(BUCKET_COUNT):
            try:
                self.clear_bucket(i)
            except KrakenError:
                pass  # already empty

    def upload(self, index: int, data: bytes, kind: int = KIND_GIF,
               start_kib: int | None = None, slot_kib: int | None = None) -> int:
        """Put one image into a bucket.  Returns the size used, in KiB.

        ``start_kib`` defaults to a fixed slot per bucket index so that two
        buckets can be written alternately without their memory overlapping.
        """
        if not 0 <= index < BUCKET_COUNT:
            raise ValueError(f"bucket index {index} out of range")

        header = wrap_payload(data, kind)
        size_kib = (len(header) + len(data) + 1023) // 1024

        slot = slot_kib or TOTAL_MEMORY_KIB // BUCKET_COUNT
        if start_kib is None:
            # Every bucket gets an equal, non-overlapping slice of memory.
            start_kib = index * slot
        if size_kib > slot:
            raise KrakenError(
                f"payload of {size_kib} KiB does not fit in its {slot} KiB "
                f"slot; use a smaller or shorter image")
        if start_kib + size_kib > TOTAL_MEMORY_KIB:
            raise KrakenError(
                f"bucket {index} would run past the end of the device's "
                f"{TOTAL_MEMORY_KIB} KiB of display memory")

        self.command([0x36, 0x03])  # purpose unknown, but the firmware wants it

        # A bucket has to be empty before it can be set up, and one that still
        # holds data needs deleting twice before it lets go.
        for _ in range(2):
            try:
                self.clear_bucket(index)
            except KrakenError:
                break

        self.checked([0x32, 0x01, index, index + 1,
                      start_kib & 0xFF, start_kib >> 8,
                      size_kib & 0xFF, size_kib >> 8, 0x01],
                     f"bucket {index} setup")
        self.checked([0x36, 0x01, index], f"bucket {index} write start")

        # The 20-byte header goes out as a transfer of its own: it is short,
        # so it ends with a short packet, and that is how the firmware knows
        # where the descriptor stops and the payload starts.  Concatenating
        # the two makes the upload succeed at the USB level and show nothing.
        self.bulk.write(header, chunk_size=_BULK_CHUNK)
        self.bulk.write(data, chunk_size=_BULK_CHUNK)

        self.hid.write(bytes([0x36, 0x02]))
        time.sleep(0.05)
        return size_kib

    def display(self, data: bytes, kind: int = KIND_GIF, index: int = 0):
        """Upload and show in one go."""
        self.upload(index, data, kind)
        self.show_bucket(index)

    # -- recovery ----------------------------------------------------------

    def reset(self):
        """Empty every bucket and give the display back to the firmware."""
        self.show_liquid_screen()
        time.sleep(0.2)
        for i in range(BUCKET_COUNT):
            try:
                self.clear_bucket(i)
            except KrakenError:
                pass
        self.show_liquid_screen()

    def usb_reset(self):
        self.bulk.usb_reset()
        self._bulk = None


class DoubleBuffer:
    """Alternates between two buckets so live updates never flicker.

    A bucket that is currently on screen is locked against being rewritten,
    so an updating display needs at least two of them: write the idle one,
    then switch.
    """

    def __init__(self, kraken: Kraken, buckets=(0, 1)):
        self.kraken = kraken
        self.buckets = tuple(buckets)
        self._next = 0
        self._prepared = False
        # Only these buckets are ever used, so they may as well split the
        # whole of display memory between them -- that is what lets a long
        # GIF through instead of capping everything at 1/16th.
        self.slot_kib = TOTAL_MEMORY_KIB // len(self.buckets)

    def prepare(self):
        """Free the buckets we are about to use."""
        self.kraken.show_liquid_screen()
        time.sleep(0.2)
        for b in self.buckets:
            try:
                self.kraken.clear_bucket(b)
            except KrakenError:
                pass
        self._prepared = True
        self._next = 0

    def push(self, data: bytes, kind: int = KIND_GIF):
        try:
            return self._push(data, kind)
        except KrakenError:
            if self._prepared:
                raise
            # First push of a fresh process, and the bucket we picked is the
            # one already on screen -- left there by a previous run, which the
            # firmware locks against being rewritten.  Move the display away
            # and try once more.  (Costs a blink of the firmware screen, so it
            # is worth avoiding: a caller that pushes repeatedly should call
            # prepare() up front, as the live temperature display does.)
            self.prepare()
            return self._push(data, kind)

    def _push(self, data: bytes, kind: int):
        slot = self._next
        index = self.buckets[slot]
        self.kraken.upload(index, data, kind,
                           start_kib=slot * self.slot_kib,
                           slot_kib=self.slot_kib)
        self.kraken.show_bucket(index)
        self._next = (slot + 1) % len(self.buckets)
        self._prepared = True
        return index
