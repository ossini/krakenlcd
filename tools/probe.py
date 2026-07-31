#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Interactive protocol probe: send a HID report, print what comes back.

    tools/probe.py 10 01              # firmware version
    tools/probe.py 74 01              # status
    tools/probe.py 30 04              # (guess) query display/bucket state
    tools/probe.py --listen 5         # just watch unsolicited reports for 5s

Every byte is given in hex; byte 0 is the report ID.  Nothing is interpreted,
so this is safe to use while working out what a command actually does.
"""

import argparse
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from krakenlcd.lowlevel import HidChannel, KrakenError, find_device


def hexdump(data: bytes, indent: str = "    ") -> str:
    lines = []
    for off in range(0, len(data), 16):
        row = data[off:off + 16]
        hexpart = " ".join(f"{b:02x}" for b in row)
        asciipart = "".join(chr(b) if 32 <= b < 127 else "." for b in row)
        lines.append(f"{indent}{off:04x}  {hexpart:<47}  {asciipart}")
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("bytes", nargs="*", help="report bytes in hex, e.g. 74 01")
    ap.add_argument("--listen", type=float, metavar="SEC",
                    help="only listen for incoming reports")
    ap.add_argument("--expect", help="expected reply report ID (hex)")
    ap.add_argument("--timeout", type=float, default=1.0)
    ap.add_argument("--repeat", type=int, default=1)
    args = ap.parse_args()

    paths = find_device()
    print(f"device: {paths!r}\n")

    with HidChannel(paths.hidraw) as hid:
        dropped = hid.drain()
        if dropped:
            print(f"(drained {dropped} stale report(s))\n")

        if args.listen:
            end = time.monotonic() + args.listen
            while time.monotonic() < end:
                reply = hid.read(timeout=min(0.5, max(0.01, end - time.monotonic())))
                if reply:
                    print(f"<- id 0x{reply[0]:02x}")
                    print(hexdump(reply))
            return 0

        if not args.bytes:
            ap.error("give report bytes, or --listen")

        payload = bytes(int(b, 16) for b in args.bytes)
        expect = int(args.expect, 16) if args.expect else None

        for i in range(args.repeat):
            if args.repeat > 1:
                print(f"--- attempt {i + 1}")
            print(f"-> {' '.join(f'{b:02x}' for b in payload)}")
            try:
                reply = hid.request(payload, expect_id=expect,
                                    timeout=args.timeout)
            except KrakenError as exc:
                print(f"error: {exc}", file=sys.stderr)
                return 1
            if reply is None:
                print("<- (no reply)")
            else:
                print(f"<- id 0x{reply[0]:02x}")
                print(hexdump(reply))
            # anything else queued?
            while True:
                extra = hid.read(timeout=0.15)
                if not extra:
                    break
                print(f"<- (extra) id 0x{extra[0]:02x}")
                print(hexdump(extra))
            if i + 1 < args.repeat:
                time.sleep(0.2)

    return 0


if __name__ == "__main__":
    sys.exit(main())
