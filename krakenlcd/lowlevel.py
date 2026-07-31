# SPDX-License-Identifier: GPL-3.0-or-later
"""Raw USB/HID transport for the Kraken Z, built on nothing but the stdlib.

Two channels are needed to talk to a Kraken Z:

  * Interface 1 is a HID interface.  The kernel binds ``usbhid`` to it and
    exposes it as ``/dev/hidrawN``.  All control and status traffic is 64-byte
    reports: byte 0 is the report ID, bytes 1..63 the payload.  We just
    read()/write() the character device -- no library required.

  * Interface 0 is vendor-specific with a single bulk OUT endpoint (0x02,
    512-byte packets).  This is where display image data goes.  No kernel
    driver claims it, so we drive it through ``usbfs`` ioctls on
    ``/dev/bus/usb/BBB/DDD``.  ``ctypes`` + ``fcntl`` is all that takes.

Nothing here is Kraken-specific; see device.py for the protocol.
"""

from __future__ import annotations

import ctypes
import errno
import fcntl
import glob
import os
import select
import struct
import time

VENDOR_ID = 0x1E71
PRODUCT_IDS = (0x3008,)  # Kraken Z53 / Z63 / Z73

REPORT_SIZE = 64


class KrakenError(Exception):
    pass


class DeviceNotFound(KrakenError):
    pass


class PermissionProblem(KrakenError):
    pass


# --------------------------------------------------------------------------
# device discovery via sysfs
# --------------------------------------------------------------------------

def _read_sysfs(path: str) -> str:
    try:
        with open(path) as fh:
            return fh.read().strip()
    except OSError:
        return ""


def _usb_devices() -> list[str]:
    """sysfs paths of every USB device node (not interface) on the system."""
    out = []
    for path in glob.glob("/sys/bus/usb/devices/*"):
        if _read_sysfs(os.path.join(path, "idVendor")):
            out.append(path)
    return sorted(out)


def find_device(serial: str | None = None) -> "DevicePaths":
    """Locate a Kraken Z and return the paths needed to talk to it."""
    candidates = []
    for path in _usb_devices():
        vid = _read_sysfs(os.path.join(path, "idVendor"))
        pid = _read_sysfs(os.path.join(path, "idProduct"))
        if not vid or int(vid, 16) != VENDOR_ID:
            continue
        if int(pid, 16) not in PRODUCT_IDS:
            continue
        dev_serial = _read_sysfs(os.path.join(path, "serial"))
        if serial and dev_serial != serial:
            continue
        candidates.append((path, int(pid, 16), dev_serial))

    if not candidates:
        raise DeviceNotFound(
            "no NZXT Kraken Z (1e71:3008) found on the USB bus.\n"
            "Is the pump's USB header connected to the mainboard?"
        )

    syspath, pid, dev_serial = candidates[0]
    busnum = int(_read_sysfs(os.path.join(syspath, "busnum")))
    devnum = int(_read_sysfs(os.path.join(syspath, "devnum")))
    usbfs = f"/dev/bus/usb/{busnum:03d}/{devnum:03d}"

    # The HID interface is the one with bInterfaceClass == 3.  Find its hidraw.
    hidraw = None
    base = os.path.basename(syspath)
    for iface in sorted(glob.glob(os.path.join(syspath, base + ":*"))):
        if _read_sysfs(os.path.join(iface, "bInterfaceClass")) != "03":
            continue
        found = glob.glob(os.path.join(iface, "*", "hidraw", "hidraw*"))
        if found:
            hidraw = "/dev/" + os.path.basename(found[0])
            break

    if hidraw is None:
        raise DeviceNotFound(
            f"found the Kraken at {syspath} but no hidraw node for its HID "
            "interface. Is the 'usbhid' module loaded?"
        )

    return DevicePaths(
        syspath=syspath,
        usbfs=usbfs,
        hidraw=hidraw,
        product_id=pid,
        serial=dev_serial,
        busnum=busnum,
        devnum=devnum,
    )


class DevicePaths:
    __slots__ = ("syspath", "usbfs", "hidraw", "product_id", "serial",
                 "busnum", "devnum")

    def __init__(self, **kw):
        for k, v in kw.items():
            setattr(self, k, v)

    def __repr__(self):
        return (f"<Kraken 1e71:{self.product_id:04x} serial={self.serial} "
                f"hidraw={self.hidraw} usbfs={self.usbfs}>")


def _explain_permission_error(path: str) -> str:
    return (
        f"cannot open {path} for writing.\n\n"
        "Install the udev rule that grants your user access to this device:\n"
        "    ./scripts/install-udev.sh\n"
        "(it is removed again with ./scripts/uninstall-udev.sh)\n\n"
        "Alternatively run this command with sudo."
    )


# --------------------------------------------------------------------------
# HID channel
# --------------------------------------------------------------------------

class HidChannel:
    """64-byte report I/O on a hidraw node."""

    def __init__(self, path: str):
        self.path = path
        try:
            self.fd = os.open(path, os.O_RDWR | os.O_NONBLOCK)
        except PermissionError:
            raise PermissionProblem(_explain_permission_error(path)) from None
        except FileNotFoundError:
            raise DeviceNotFound(f"{path} disappeared") from None

    def close(self):
        if self.fd is not None:
            os.close(self.fd)
            self.fd = None

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    def drain(self):
        """Throw away reports the device queued up before we started."""
        dropped = 0
        while True:
            r, _, _ = select.select([self.fd], [], [], 0)
            if not r:
                return dropped
            try:
                os.read(self.fd, REPORT_SIZE)
                dropped += 1
            except BlockingIOError:
                return dropped
            except OSError:
                return dropped

    def write(self, data: bytes) -> None:
        """Send one output report. data[0] is the report ID.

        Short buffers are zero-padded to the full report length, which is what
        the firmware expects.
        """
        if not data:
            raise ValueError("empty report")
        buf = bytes(data).ljust(REPORT_SIZE, b"\x00")
        if len(buf) != REPORT_SIZE:
            raise ValueError(f"report too long: {len(data)} > {REPORT_SIZE}")
        try:
            n = os.write(self.fd, buf)
        except PermissionError:
            raise PermissionProblem(_explain_permission_error(self.path)) from None
        if n != REPORT_SIZE:
            raise KrakenError(f"short HID write: {n} of {REPORT_SIZE}")

    def read(self, timeout: float = 1.0) -> bytes | None:
        """Wait for one input report. Returns None on timeout."""
        deadline = time.monotonic() + timeout
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return None
            r, _, _ = select.select([self.fd], [], [], remaining)
            if not r:
                return None
            try:
                data = os.read(self.fd, REPORT_SIZE)
            except BlockingIOError:
                continue
            if data:
                return data

    def request(self, data: bytes, expect_id: int | None = None,
                timeout: float = 1.0, attempts: int = 12) -> bytes | None:
        """Send a report and return the matching reply.

        The device also emits unsolicited status reports, so replies with an
        unexpected report ID are skipped rather than returned.
        """
        self.write(data)
        if expect_id is None:
            expect_id = (data[0] + 1) & 0xFF
        for _ in range(attempts):
            reply = self.read(timeout)
            if reply is None:
                return None
            if reply[0] == expect_id:
                return reply
        return None


# --------------------------------------------------------------------------
# usbfs bulk channel
# --------------------------------------------------------------------------

def _ioc(direction: int, typ: str, nr: int, size: int) -> int:
    # asm-generic/ioctl.h layout: dir(2) size(14) type(8) nr(8)
    return (direction << 30) | (size << 16) | (ord(typ) << 8) | nr


_IOC_WRITE = 1
_IOC_READ = 2


class _BulkTransfer(ctypes.Structure):
    # struct usbdevfs_bulktransfer
    _fields_ = [
        ("ep", ctypes.c_uint),
        ("len", ctypes.c_uint),
        ("timeout", ctypes.c_uint),
        ("data", ctypes.c_void_p),
    ]


USBDEVFS_BULK = _ioc(_IOC_READ | _IOC_WRITE, "U", 2, ctypes.sizeof(_BulkTransfer))
USBDEVFS_RESET = _ioc(0, "U", 20, 0)
USBDEVFS_CLAIMINTERFACE = _ioc(_IOC_READ, "U", 15, ctypes.sizeof(ctypes.c_uint))
USBDEVFS_RELEASEINTERFACE = _ioc(_IOC_READ, "U", 16, ctypes.sizeof(ctypes.c_uint))

# usbfs refuses single transfers larger than this on many kernels; also keeps
# each URB short enough that a stall is reported promptly.
MAX_BULK_CHUNK = 16 * 1024


class BulkChannel:
    """Bulk OUT transfers on a usbfs device node."""

    def __init__(self, path: str, interface: int, endpoint: int):
        self.path = path
        self.interface = interface
        self.endpoint = endpoint
        self._claimed = False
        try:
            self.fd = os.open(path, os.O_RDWR)
        except PermissionError:
            raise PermissionProblem(_explain_permission_error(path)) from None
        except FileNotFoundError:
            raise DeviceNotFound(f"{path} disappeared") from None
        self.claim()

    def claim(self):
        if self._claimed:
            return
        arg = struct.pack("I", self.interface)
        try:
            fcntl.ioctl(self.fd, USBDEVFS_CLAIMINTERFACE, arg)
        except OSError as exc:
            if exc.errno == errno.EBUSY:
                raise KrakenError(
                    f"interface {self.interface} is claimed by another process "
                    "or kernel driver. Is NZXT CAM running in a VM, or another "
                    "Kraken tool active?"
                ) from None
            raise KrakenError(
                f"USBDEVFS_CLAIMINTERFACE failed: {exc}") from None
        self._claimed = True

    def release(self):
        if not self._claimed:
            return
        try:
            fcntl.ioctl(self.fd, USBDEVFS_RELEASEINTERFACE,
                        struct.pack("I", self.interface))
        except OSError:
            pass
        self._claimed = False

    def close(self):
        if self.fd is not None:
            self.release()
            os.close(self.fd)
            self.fd = None

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    def write(self, data: bytes, timeout_ms: int = 5000,
              chunk_size: int | None = None) -> int:
        """Write all of data to the bulk endpoint, chunking as needed.

        Each call is one USB transfer per chunk, so a caller that needs the
        firmware to see a transfer boundary -- a short packet -- has to make a
        separate call rather than concatenating buffers.
        """
        limit = min(chunk_size or MAX_BULK_CHUNK, MAX_BULK_CHUNK)
        mv = memoryview(bytes(data))
        total = 0
        while total < len(mv):
            chunk = mv[total:total + limit]
            buf = ctypes.create_string_buffer(bytes(chunk), len(chunk))
            xfer = _BulkTransfer(
                ep=self.endpoint,
                len=len(chunk),
                timeout=timeout_ms,
                data=ctypes.cast(buf, ctypes.c_void_p),
            )
            try:
                written = fcntl.ioctl(self.fd, USBDEVFS_BULK, xfer)
            except OSError as exc:
                if exc.errno == errno.EPIPE:
                    raise KrakenError(
                        "bulk endpoint stalled -- the firmware rejected the "
                        "transfer (wrong size or no bucket armed)"
                    ) from None
                if exc.errno == errno.ETIMEDOUT:
                    raise KrakenError(
                        "bulk transfer timed out; the device is not accepting "
                        "data (is a bucket armed with 0x36 0x01?)"
                    ) from None
                raise KrakenError(f"USBDEVFS_BULK failed: {exc}") from None
            if written <= 0:
                raise KrakenError(f"bulk write returned {written}")
            total += written
        return total

    def usb_reset(self):
        """Ask the kernel to re-enumerate the device (like replugging it)."""
        self.release()
        try:
            fcntl.ioctl(self.fd, USBDEVFS_RESET, 0)
        except OSError as exc:
            raise KrakenError(f"USBDEVFS_RESET failed: {exc}") from None
