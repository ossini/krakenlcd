# SPDX-License-Identifier: GPL-3.0-or-later
"""Command line front end for the Kraken Z LCD.

    krakenlcd status                 what the cooler reports
    krakenlcd fill white             flood the panel with one colour
    krakenlcd gif party.gif          play an image or animation
    krakenlcd temp                   temperature screen in colour, live
    krakenlcd temp --color amber     ... in some other colour
    krakenlcd temp --once            draw it once and exit
    krakenlcd preview out.png        render to a file, touch no hardware
    krakenlcd dash                   a live design: CPU, GPU, … updating
    krakenlcd dash duo -s hot=90     another design, one setting changed
    krakenlcd design list            the designs there are
    krakenlcd design new mine        start your own
    krakenlcd sensors                what a design can show
    krakenlcd liquid                 back to the firmware's white screen
    krakenlcd brightness 80
    krakenlcd restore                put back whatever was showing last
    krakenlcd reset                  clear everything, firmware screen
    krakenlcd usb-reset              last resort: re-enumerate the device

Every command that changes the display also records what it did, so that
`krakenlcd restore` -- run by the systemd user service at login -- can put it
back after a reboot.  `--no-save` applies something once without changing
what comes back next time.
"""

import argparse
import os
import signal
import subprocess
import sys
import time

from . import __version__, design, media, sensors, state, tempscreen
from .device import DoubleBuffer, Kraken, read_hwmon_temp
from .lowlevel import KrakenError, find_device


_stop = False


def _save_display(args, argv, payload=None, rotate=None):
    """Record a display command for `krakenlcd restore`, unless --no-save."""
    if getattr(args, "no_save", False):
        return
    state.save_display(argv, payload=payload, rotate=rotate)


def _save_panel(args, **fields):
    if getattr(args, "no_save", False):
        return
    state.update(**fields)


def _install_signal_handlers():
    def handler(signum, frame):
        global _stop
        _stop = True
    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, handler)


# ---------------------------------------------------------------------------
# subcommands
# ---------------------------------------------------------------------------

def cmd_status(args):
    with Kraken(args.serial) as k:
        print(f"device      : {k.paths!r}")
        major, minor, patch = k.firmware()
        print(f"firmware    : {major}.{minor}.{patch}")
        st = k.status()
        print(f"liquid      : {st.liquid_temp:.1f} °C  (HID)")
        hw = read_hwmon_temp()
        if hw is not None:
            print(f"              {hw:.1f} °C  (kernel hwmon)")
        print(f"pump        : {st.pump_rpm} rpm at {st.pump_duty}%")
        print(f"fan         : {st.fan_rpm} rpm at {st.fan_duty}%")
        info = k.lcd_info()
        print(f"lcd         : {info['width']}x{info['height']}, "
              f"brightness {info['brightness']}%, "
              f"rotated {info['orientation']}°")
    print(f"at boot     : {state.describe()}")
    return 0


def cmd_orientation(args):
    with Kraken(args.serial) as k:
        k.set_orientation(args.degrees)
    _save_panel(args, orientation=args.degrees)
    print(f"display rotation set to {args.degrees}°")
    return 0


def _render_kwargs(args, rotate=0):
    return {
        "color": tempscreen.parse_color(args.color),
        "background": tempscreen.parse_color(args.background),
        "decimals": args.decimals,
        "label": args.label,
        "font_path": args.font,
        "dim": args.dim,
        "scale": args.scale,
        "rotate": rotate,
    }


def cmd_preview(args):
    kwargs = _render_kwargs(args, rotate=args.rotate)
    tempscreen.save_png(args.path, args.value, **kwargs)
    print(f"wrote {args.path} ({args.value:g} °C, no device was touched)")
    return 0


def _temp_argv(args):
    """The `temp` command as an argument list, for restore-at-boot."""
    argv = ["temp",
            "--color", args.color,
            "--background", args.background,
            "--decimals", str(args.decimals),
            "--dim", str(args.dim),
            "--scale", str(args.scale),
            "--interval", str(args.interval),
            "--bucket", str(args.bucket)]
    if args.label:
        argv += ["--label", args.label]
    if args.font:
        argv += ["--font", os.path.abspath(args.font)]
    if args.rotate is not None:
        argv += ["--rotate", str(args.rotate)]
    # --once and --restore are part of what the user asked for: replaying them
    # keeps a one-shot one-shot.  --verbose is not, it would only fill the log.
    if args.once:
        argv.append("--once")
    if args.restore:
        argv.append("--restore")
    return argv


def cmd_temp(args):
    _install_signal_handlers()

    with Kraken(args.serial) as k:
        if args.brightness is not None:
            k.set_brightness(args.brightness)
            _save_panel(args, brightness=args.brightness)
        # Saved once the device is open: this command runs until Ctrl-C, so
        # waiting for it to finish would mean never recording it at all.
        _save_display(args, _temp_argv(args))

        # Uploaded images are shown exactly as sent, so we have to apply the
        # panel's own rotation ourselves.
        rotate = k.lcd_info()["orientation"] if args.rotate is None \
            else args.rotate
        kwargs = _render_kwargs(args, rotate=rotate)
        if args.verbose:
            print(f"panel rotation {rotate}°, pre-rotating the image to match")

        buf = DoubleBuffer(k, buckets=(args.bucket, args.bucket + 1))
        buf.prepare()

        shown = None
        while True:
            temp = k.liquid_temp()
            # Quantise to what will actually be drawn, so a reading that
            # wobbles in the hundredths does not cause a pointless upload.
            key = round(temp, args.decimals)
            if key != shown:
                data = tempscreen.render_gif(temp, **kwargs)
                buf.push(data)
                shown = key
                if args.verbose:
                    print(f"[{time.strftime('%H:%M:%S')}] {key} °C  "
                          f"({len(data)} B)", flush=True)

            if args.once or _stop:
                break

            # Sleep in slices so Ctrl-C is responsive.
            end = time.monotonic() + args.interval
            while not _stop and time.monotonic() < end:
                time.sleep(min(0.2, end - time.monotonic()))
            if _stop:
                break

        if args.restore:
            k.show_liquid_screen()
            print("restored the firmware temperature screen")
        elif not args.once:
            print("\nstopped; the last frame stays on the display.\n"
                  "Run 'krakenlcd liquid' to hand it back to the firmware.")
    return 0


# The 0.2 beta's dashboard flags, which were the swiss design's settings
# before there were designs.  Still accepted -- a saved state.json from the
# beta replays them at login -- and passed on as settings.
_LEGACY_DASH = (("big", "big"), ("small", "small"), ("min", "scale_min"),
                ("max", "scale_max"), ("hot", "hot"), ("decimals", "decimals"))


def _settings(args) -> dict:
    """`--set NAME=VALUE`, plus the beta's flags spelled as settings."""
    out = {}
    for flag, setting in _LEGACY_DASH:
        value = getattr(args, "legacy_" + flag, None)
        if value is not None:
            out[setting] = value
    for item in getattr(args, "set", None) or []:
        key, sep, value = item.partition("=")
        if not sep or not key.strip():
            raise ValueError(f"--set wants NAME=VALUE, e.g. --set hot=75 "
                             f"(got {item!r})")
        out[key.strip()] = value.strip()
    return out


def _design_ref(ref: str) -> str:
    """A design as it should be remembered: by name, or by absolute path."""
    entry = design.find(ref)
    return ref if entry.name == ref and os.sep not in ref \
        and not ref.lower().endswith(".svg") else os.path.abspath(entry.path)


def _dash_argv(args, settings):
    """The `dash` command as an argument list, for restore-at-boot."""
    argv = ["dash", _design_ref(args.design),
            "--interval", str(args.interval),
            "--smooth", str(args.smooth),
            "--bucket", str(args.bucket)]
    for name, value in settings.items():
        argv += ["--set", f"{name}={value}"]
    if args.rotate is not None:
        argv += ["--rotate", str(args.rotate)]
    if args.once:
        argv.append("--once")
    return argv


def _now():
    return time.strftime("%H:%M:%S")


def cmd_dash(args):
    """A live design, redrawn whenever something on it changes."""
    settings = _settings(args)
    live = design.Live(args.design, settings, smooth=args.smooth)

    if args.preview:
        image = live.frame()
        if args.rotate:
            image = image.rotate(-(args.rotate % 360))
        image.save(args.preview)
        print(f"wrote {args.preview} (no device was touched)")
        return 0

    _install_signal_handlers()

    with Kraken(args.serial) as k:
        _save_display(args, _dash_argv(args, settings))

        rotate = k.lcd_info()["orientation"] if args.rotate is None \
            else args.rotate
        buf = DoubleBuffer(k, buckets=(args.bucket, args.bucket + 1))
        buf.prepare()
        print(f"showing '{live.design.title}' from {live.design.where}\n"
              f"edit and save that file, and the display follows", flush=True)

        first = True
        failed = None
        while True:
            if live.reload_if_changed():
                print(f"[{_now()}] reloaded {live.design.where}", flush=True)
            # Only what ends up on the panel decides whether it is worth an
            # upload: unless a number on it changed, frame() returns None.
            try:
                image = live.frame()
                failed = None
            except design.DesignError as exc:
                if first:
                    raise
                # Mid-edit breakage keeps the last good frame up.
                if str(exc) != failed:
                    print(f"[{_now()}] {exc}", file=sys.stderr, flush=True)
                failed, image = str(exc), None
            first = False
            if image is not None:
                data = media.encode_frame(image, rotate=rotate)
                buf.push(data)
                if args.verbose:
                    print(f"[{_now()}] redrawn "
                          f"({len(data) / 1024:.1f} KiB)", flush=True)

            if args.once or _stop:
                break

            end = time.monotonic() + args.interval
            while not _stop and time.monotonic() < end:
                time.sleep(min(0.2, end - time.monotonic()))
            if _stop:
                break

        if args.restore:
            k.show_liquid_screen()
            print("restored the firmware temperature screen")
        elif not args.once:
            print("\nstopped; the last frame stays on the display.")
    return 0


def cmd_design_list(args):
    entries = design.available()
    print(f"Your designs go in {design.pretty_path(design.user_dir())}\n")
    width = max(len(e.name) for e in entries)
    for entry in entries:
        kind = "built-in" if entry.builtin else "yours"
        if entry.shadows:
            kind = "yours, replaces the built-in one"
        if entry.builtin and entry.name == design.DEFAULT:
            kind += ", the default"
        try:
            loaded = design.load(entry)
            about = loaded.description or loaded.title
            if loaded.author:
                kind += f", by {loaded.author}"
        except design.DesignError as exc:
            about = f"BROKEN: {str(exc).splitlines()[0]}"
        if len(about) > 72:
            about = about[:71] + "…"
        print(f"  {entry.name:{width}}  {about}")
        print(f"  {'':{width}}  ({kind})")
    print("\nShow one:       krakenlcd dash NAME"
          "\nTry it out:     krakenlcd design preview NAME"
          "\nMake your own:  krakenlcd design new NAME")
    return 0


def cmd_design_new(args):
    path = design.create(args.name, source=args.source, image=args.image)
    if args.image:
        origin = f"around {args.image}, scaled to 320x320"
    else:
        origin = f"a copy of '{args.source or design.STARTER}'"
    print(f"created {design.pretty_path(path)}  ({origin})\n")
    print("Next:\n"
          f"  1. open it in Inkscape, Figma or any text editor and make it "
          f"yours\n"
          f"  2. krakenlcd dash {args.name}\n"
          f"     puts it on the cooler, and redraws it every time you save\n"
          f"     (or: krakenlcd design preview {args.name} --watch, "
          f"without a cooler)\n\n"
          f"What can go between {{{{ }}}}: krakenlcd sensors, and "
          f"docs/DESIGNS.md")
    return 0


def cmd_design_preview(args):
    live = design.Live(args.design, _settings(args), smooth=0.0,
                       demo=args.demo)
    out = args.output or f"{live.design.name}-preview.png"
    t0 = time.perf_counter()
    image = live.frame(force=True)
    took = (time.perf_counter() - t0) * 1000
    image.save(out)
    values = "made-up values" if args.demo else "this machine's readings"
    print(f"wrote {out}: '{live.design.title}' with {values}, "
          f"drawn in {took:.0f} ms")
    if not args.watch:
        return 0

    _install_signal_handlers()
    print("watching for changes to the design; Ctrl-C to stop", flush=True)
    failed = None
    while not _stop:
        time.sleep(0.5)
        reloaded = live.reload_if_changed()
        try:
            image = live.frame(force=reloaded)
            failed = None
        except design.DesignError as exc:
            if str(exc) != failed:
                print(f"[{_now()}] {exc}", file=sys.stderr, flush=True)
            failed = str(exc)
            continue
        if image is not None:
            image.save(out)
            if reloaded:
                print(f"[{_now()}] redrawn {out}", flush=True)
    return 0


def cmd_design_folder(args):
    path = design.user_dir()
    os.makedirs(path, exist_ok=True)
    print(path)
    if args.open:
        try:
            subprocess.Popen(["xdg-open", path], stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL)
        except OSError as exc:
            print(f"could not open a file manager: {exc}", file=sys.stderr)
            return 1
    return 0


def cmd_sensors(args):
    for line in sensors.describe_sources():
        print(line)
    print()
    for name, sensor in sensors.SENSORS.items():
        value = sensor.read()
        if value is None:
            shown = "not available"
        elif sensor.step is None or sensor.step < 1:
            shown = f"{value:.1f} {sensor.unit}"
        else:
            shown = f"{value:.0f} {sensor.unit}"
        print(f"  {name:10s} {shown:15s} {sensor.label}")
    for name, read in sensors.TEXT.items():
        print(f"  {name:10s} {read():15s}")
    print("\nIn a design, write any of these names between {{ }}, "
          "e.g. {{ cpu }} or {{ fmt(ram_used, 1) }}.")
    return 0


def cmd_fill(args):
    color = tempscreen.parse_color(args.color)
    data = media.solid(color)
    with Kraken(args.serial) as k:
        DoubleBuffer(k, buckets=(args.bucket, args.bucket + 1)).push(data)
    # A flat colour re-encodes in microseconds, so there is nothing to cache.
    _save_display(args, ["fill", args.color, "--bucket", str(args.bucket)])
    print(f"display filled with rgb{color} ({len(data)} B)")
    return 0


def cmd_gif(args):
    with Kraken(args.serial) as k:
        rotate = k.lcd_info()["orientation"] if args.rotate is None \
            else args.rotate
        print(f"re-encoding {args.path} for 320x320, rotation {rotate}° ...")
        data = media.prepare_gif(args.path, rotate=rotate,
                                 geometry=args.geometry,
                                 max_frames=args.max_frames,
                                 colors=args.colors)
        print(f"uploading {len(data) / 1024:.0f} KiB ...")
        t0 = time.time()
        DoubleBuffer(k, buckets=(args.bucket, args.bucket + 1)).push(data)
        print(f"done in {time.time() - t0:.1f}s")

    argv = ["gif", os.path.abspath(args.path),
            "--geometry", args.geometry,
            "--colors", str(args.colors),
            "--bucket", str(args.bucket)]
    if args.max_frames is not None:
        argv += ["--max-frames", str(args.max_frames)]
    if args.rotate is not None:
        argv += ["--rotate", str(args.rotate)]
    # Keep the encoded bytes: re-encoding costs seconds at every login, and
    # the source file may not survive as long as the setting does.
    _save_display(args, argv, payload=data, rotate=rotate)
    return 0


def cmd_liquid(args):
    with Kraken(args.serial) as k:
        k.show_liquid_screen()
    _save_display(args, ["liquid"])
    print("display is back on the firmware's own temperature screen")
    return 0


def cmd_brightness(args):
    with Kraken(args.serial) as k:
        k.set_brightness(args.percent)
    _save_panel(args, brightness=args.percent)
    print(f"brightness set to {args.percent}%")
    return 0


def cmd_reset(args):
    with Kraken(args.serial) as k:
        k.reset()
    if not getattr(args, "no_save", False):
        state.clear()
    print("all buckets cleared, firmware temperature screen restored\n"
          "the saved boot-time display was forgotten as well")
    return 0


def _wait_for_device(seconds: float, serial=None):
    """Wait until the cooler is there *and* openable.

    At login both halves take a moment: USB enumeration, and logind attaching
    the uaccess ACL to the hidraw node.  Discovery alone is not enough — the
    device turns up in sysfs before we are allowed to talk to it — so this
    opens it for real and lets go again.
    """
    deadline = time.monotonic() + max(0.0, seconds)
    while True:
        try:
            find_device(serial)
            Kraken(serial).close()
            return
        except (KrakenError, OSError) as exc:
            if _stop or time.monotonic() >= deadline:
                raise KrakenError(f"gave up waiting for the cooler: {exc}")
            time.sleep(1.0)


def cmd_restore(args):
    """Put back whatever was on the display before the machine went off."""
    if args.clear:
        state.clear()
        print("forgot the saved display; the firmware screen stays at boot")
        return 0

    saved = state.load()
    if args.show:
        print(state.describe(saved))
        print(f"stored in {state.state_path()}")
        return 0
    if not saved:
        print("nothing saved yet — run e.g. 'krakenlcd fill red' once")
        return 0

    _install_signal_handlers()
    if args.wait:
        _wait_for_device(args.wait, args.serial)

    argv = saved.get("argv")
    replay = None
    if argv:
        try:
            replay = build_parser().parse_args(list(argv))
        except SystemExit:
            print(f"error: saved command is not valid any more: "
                  f"{' '.join(argv)}", file=sys.stderr)
            return 2
        replay.serial = args.serial
        # A replay reproduces what is already saved, so re-saving it writes
        # nothing new -- except that a re-encoded image refreshes the cached
        # payload, which is exactly what we want after a rotation change.
        replay.no_save = args.no_save

    with Kraken(args.serial) as k:
        # Orientation first: it decides how an image has to be pre-rotated.
        if saved.get("orientation") is not None:
            k.set_orientation(int(saved["orientation"]))
        if saved.get("brightness") is not None:
            k.set_brightness(int(saved["brightness"]))

        if replay is None:
            print(f"restored {state.describe(saved)}")
            return 0

        # An uploaded image is replayed from the cached payload when the panel
        # is still rotated the way it was, which skips the re-encode entirely.
        if replay.func is cmd_gif:
            rotate = k.lcd_info()["orientation"] if replay.rotate is None \
                else replay.rotate
            payload = state.load_payload(rotate)
            if payload is not None:
                DoubleBuffer(k, buckets=(replay.bucket,
                                         replay.bucket + 1)).push(payload)
                print(f"restored {os.path.basename(replay.path)} from cache "
                      f"({len(payload) / 1024:.0f} KiB)")
                return 0
            if not os.path.exists(replay.path):
                print(f"error: {replay.path} is gone and the cached upload "
                      f"does not match the current rotation", file=sys.stderr)
                return 1

    print(f"restoring: {' '.join(argv)}")
    return replay.func(replay)


def cmd_usb_reset(args):
    with Kraken(args.serial) as k:
        k.usb_reset()
    print("USB reset sent; the device re-enumerates in a moment")
    return 0


# ---------------------------------------------------------------------------

def build_parser():
    ap = argparse.ArgumentParser(
        prog="krakenlcd",
        description="Talk to an NZXT Kraken Z LCD from userspace.")
    ap.add_argument("--version", action="version",
                    version=f"krakenlcd {__version__}")
    ap.add_argument("--serial", help="pick a specific cooler by serial number")
    ap.add_argument("--no-save", action="store_true",
                    help="apply this once without changing what comes back "
                         "at the next boot")

    # The same flags again for every subcommand, so that both
    # `krakenlcd --no-save fill red` and `krakenlcd fill red --no-save` work.
    # SUPPRESS is what keeps the subparser from overwriting a value the main
    # parser already put in the namespace.
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--serial", default=argparse.SUPPRESS,
                        help=argparse.SUPPRESS)
    common.add_argument("--no-save", action="store_true",
                        default=argparse.SUPPRESS, help=argparse.SUPPRESS)

    subparsers = ap.add_subparsers(dest="cmd", required=True)

    def add(name, **kw):
        return subparsers.add_parser(name, parents=[common], **kw)

    add("status", help="print sensors and LCD info"
                   ).set_defaults(func=cmd_status)

    def add_render_options(p):
        p.add_argument("--color", "-c", default="red",
                       help="digit colour: a name (%s), #rrggbb or r,g,b"
                            % ", ".join(sorted(tempscreen.COLORS)))
        p.add_argument("--background", "-b", default="black",
                       help="background colour (default black)")
        p.add_argument("--decimals", type=int, default=0, choices=(0, 1),
                       help="digits after the decimal point (default 0)")
        p.add_argument("--label", help="small caption under the reading, "
                                       "e.g. LIQUID")
        p.add_argument("--font", help="path to a .ttf to use for the digits")
        p.add_argument("--dim", type=float, default=0.55,
                       help="how much to fade the unit and label (0..1)")
        p.add_argument("--scale", type=float, default=1.0,
                       help="size of the reading, 1.0 = default")

    p = add("temp", help="show the liquid temperature in colour")
    add_render_options(p)
    p.add_argument("--interval", "-i", type=float, default=2.0,
                   help="seconds between readings (default 2)")
    p.add_argument("--once", action="store_true",
                   help="draw a single frame and exit")
    p.add_argument("--restore", action="store_true",
                   help="put the firmware screen back when stopping")
    p.add_argument("--brightness", type=int,
                   help="also set the LCD brightness, 0..100")
    p.add_argument("--bucket", type=int, default=0,
                   help="first of the two buckets to use (default 0)")
    p.add_argument("--rotate", type=int, choices=(0, 90, 180, 270),
                   help="override the panel rotation read from the device")
    p.add_argument("--verbose", "-v", action="store_true")
    p.set_defaults(func=cmd_temp)

    def add_settings_option(p):
        p.add_argument("--set", "-s", action="append", metavar="NAME=VALUE",
                       help="change one of the design's settings, e.g. "
                            "-s hot=75 (repeatable; 'krakenlcd design list' "
                            "and the design file say which there are)")

    p = add("dash", help="show a live design: CPU, GPU, … updating")
    p.add_argument("design", nargs="?", default=design.DEFAULT,
                   help="a design from 'krakenlcd design list', or the path "
                        "of a .svg or design folder (default %(default)s)")
    add_settings_option(p)
    p.add_argument("--interval", "-i", type=float, default=2.0,
                   help="seconds between readings (default 2)")
    p.add_argument("--smooth", type=float, default=6.0, metavar="SECONDS",
                   help="damp the readings over this long; a CPU package "
                        "sensor is far too twitchy to show raw (default 6, "
                        "0 turns it off)")
    p.add_argument("--once", action="store_true",
                   help="draw a single frame and exit")
    p.add_argument("--restore", action="store_true",
                   help="put the firmware screen back when stopping")
    p.add_argument("--preview", metavar="OUT.png",
                   help="render to a file instead, touching no hardware")
    p.add_argument("--bucket", type=int, default=0,
                   help="first of the two buckets to use (default 0)")
    p.add_argument("--rotate", type=int, choices=(0, 90, 180, 270),
                   help="override the panel rotation read from the device")
    p.add_argument("--verbose", "-v", action="store_true")
    for flag, _ in _LEGACY_DASH:
        p.add_argument("--" + flag, dest="legacy_" + flag, help=argparse.SUPPRESS)
    p.set_defaults(func=cmd_dash)

    p = add("design", help="list, start and try out live designs")
    actions = p.add_subparsers(dest="action", required=True, metavar="ACTION")

    q = actions.add_parser("list", parents=[common],
                           help="the designs there are, and where yours go")
    q.set_defaults(func=cmd_design_list)

    q = actions.add_parser("new", parents=[common],
                           help="start a design of your own")
    q.add_argument("name", help="what to call it")
    q.add_argument("--from", dest="source", metavar="DESIGN",
                   help="the design to copy (default %s)" % design.STARTER)
    q.add_argument("--image", metavar="PICTURE",
                   help="build it around a picture of yours instead, with "
                        "the numbers on top")
    q.set_defaults(func=cmd_design_new)

    q = actions.add_parser("preview", parents=[common],
                           help="draw a design into a PNG, no cooler needed")
    q.add_argument("design", nargs="?", default=design.DEFAULT,
                   help="name or path (default %(default)s)")
    q.add_argument("--output", "-o", metavar="OUT.png",
                   help="where to write it (default NAME-preview.png)")
    q.add_argument("--demo", action="store_true",
                   help="use made-up values instead of this machine's")
    q.add_argument("--watch", "-w", action="store_true",
                   help="keep redrawing as the design file changes")
    add_settings_option(q)
    q.set_defaults(func=cmd_design_preview)

    q = actions.add_parser("folder", parents=[common],
                           help="print the folder your designs go in")
    q.add_argument("--open", action="store_true",
                   help="and open it in the file manager")
    q.set_defaults(func=cmd_design_folder)

    add("designs", help="same as 'design list'"
        ).set_defaults(func=cmd_design_list)

    add("sensors", help="what a design can show, and where it comes from"
        ).set_defaults(func=cmd_sensors)

    p = add("preview", help="render to a PNG without a device")
    add_render_options(p)
    p.add_argument("path", help="output .png")
    p.add_argument("--value", type=float, default=28.0,
                   help="temperature to draw (default 28)")
    p.add_argument("--rotate", type=int, default=0, choices=(0, 90, 180, 270),
                   help="pre-rotate, as the device would need it")
    p.set_defaults(func=cmd_preview)

    p = add("fill", help="flood the display with one colour")
    p.add_argument("color", help="a name, #rrggbb or r,g,b")
    p.add_argument("--bucket", type=int, default=0)
    p.set_defaults(func=cmd_fill)

    p = add("gif", help="play an image or animated GIF")
    p.add_argument("path", help="any image or GIF file")
    p.add_argument("--geometry", default="cover",
                   choices=("cover", "contain", "stretch"),
                   help="how to fit a non-square source (default cover)")
    p.add_argument("--max-frames", type=int,
                   help="stop after this many frames")
    p.add_argument("--colors", type=int, default=256,
                   help="palette size; lower makes the upload smaller")
    p.add_argument("--rotate", type=int, choices=(0, 90, 180, 270),
                   help="override the panel rotation read from the device")
    p.add_argument("--bucket", type=int, default=0)
    p.set_defaults(func=cmd_gif)

    add("liquid", help="show the firmware's white temperature screen"
                   ).set_defaults(func=cmd_liquid)

    p = add("brightness", help="set LCD brightness (0..100)")
    p.add_argument("percent", type=int)
    p.set_defaults(func=cmd_brightness)

    p = add("orientation", help="rotate the display")
    p.add_argument("degrees", type=int, choices=(0, 90, 180, 270))
    p.set_defaults(func=cmd_orientation)

    p = add("restore",
                       help="show again what was showing before the reboot")
    p.add_argument("--wait", type=float, default=0.0, metavar="SECONDS",
                   help="wait up to this long for the cooler to appear, "
                        "for starting before USB has settled")
    p.add_argument("--show", action="store_true",
                   help="print what is saved and exit")
    p.add_argument("--clear", action="store_true",
                   help="forget it, and leave the firmware screen at boot")
    p.set_defaults(func=cmd_restore)

    add("reset", help="clear all buckets, restore firmware screen"
                   ).set_defaults(func=cmd_reset)
    add("usb-reset", help="re-enumerate the device"
                   ).set_defaults(func=cmd_usb_reset)
    return ap


def main():
    args = build_parser().parse_args()
    try:
        return args.func(args)
    except KrakenError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    sys.exit(main())
