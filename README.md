# krakenlcd

[![tests](https://github.com/ossini/krakenlcd/actions/workflows/tests.yml/badge.svg)](https://github.com/ossini/krakenlcd/actions/workflows/tests.yml)

Userspace driver, CLI and GUI for the LCD on **NZXT Kraken Z** coolers
(Z53 / Z63 / Z73, USB ID `1e71:3008`) on Linux.

NZXT ships no Linux software for the display, so the panel comes up with the
firmware's white liquid-temperature screen and stays there. This project talks
to it directly: flood it with any colour, play a GIF, or show a live dashboard
of CPU, GPU, coolant and the rest, in a design you can make yourself.

```
krakenlcd dash                # live CPU/GPU dashboard
krakenlcd dash duo            # … in another design
krakenlcd design new mine     # start your own: it's an SVG file
krakenlcd fill '#ff0000'      # any colour, full screen
krakenlcd gif party.gif       # animation, resized and rotated to fit
krakenlcd liquid              # hand it back to the firmware
krakenlcd-gui                 # the same, with sliders
```

No kernel module, no daemon, no unbinding drivers. The whole thing is a Python
package and one udev rule.

---

## Requirements

* Linux with `usbhid` (any stock kernel)
* Python 3.10 or newer
* [Pillow](https://python-pillow.org/) — text rendering and image re-encoding
* [resvg-py](https://pypi.org/project/resvg-py/) — draws the live designs
  (a prebuilt wheel of the Rust renderer, nothing to compile)
* [PyQt6](https://pypi.org/project/PyQt6/) — only for the GUI

The protocol layer itself (`krakenlcd.lowlevel`, `krakenlcd.device`, plus a GIF
encoder and image scaler written from scratch) uses nothing but the standard
library. If you only want `fill`, `liquid`, `brightness` and friends, you do
not need Pillow or resvg either.

## Install

```sh
curl -fsSL https://raw.githubusercontent.com/ossini/krakenlcd/main/install.sh | sh
```

That is the whole thing. The script

* puts krakenlcd in its own virtualenv under `~/.local/share/krakenlcd`, so it
  cannot collide with system packages or with `pip`-managed ones,
* links `krakenlcd` and `krakenlcd-gui` into `~/.local/bin`,
* installs the udev rule (the one `sudo` prompt),
* enables the user service that puts your display back after a reboot, and
* tells you if `~/.local/bin` is missing from your `PATH`.

Replug the cooler once afterwards, or reboot, so the new rule applies to it.

Read it before you run it — `curl -fsSL .../install.sh | less` — as with any
install script.

Options go after `sh -s --`:

```sh
curl -fsSL .../install.sh | sh -s -- --no-gui      # CLI only, skips PyQt6
curl -fsSL .../install.sh | sh -s -- --no-udev     # no udev rule; needs root to run
curl -fsSL .../install.sh | sh -s -- --no-service  # no autostart at login
curl -fsSL .../install.sh | sh -s -- --ref v0.1.0  # a tag instead of main
```

`--prefix DIR` moves both directories elsewhere; `--help` lists everything.
Re-running the script upgrades an existing install in place.

### Uninstall

```sh
~/.local/share/krakenlcd/src/install.sh --uninstall
```

Removes the virtualenv, the two commands and the udev rule, restoring any
rule file that was there before.

### From a checkout

```sh
git clone https://github.com/ossini/krakenlcd
cd krakenlcd
./install.sh                     # same script, installs from the checkout
```

or, doing it by hand:

```sh
pip install --user '.[gui]'      # or just '.' without the GUI
./scripts/install-udev.sh        # grants your user access to the device
```

Note that `pip install --user` is refused by distributions that follow
PEP 668 (Arch, Debian 12+, Fedora 38+); on those, use the script or your own
virtualenv. Removing the rule again: `./scripts/uninstall-udev.sh`.

Without installing at all, you can run everything straight from a checkout:

```sh
python3 -m krakenlcd status
python3 -m krakenlcd.gui
```

## Usage

| Command | What it does |
|---|---|
| `krakenlcd status` | liquid temperature, pump and fan, LCD brightness and rotation |
| `krakenlcd fill COLOUR` | flood the panel with one colour |
| `krakenlcd gif FILE` | play an image or animated GIF, scaled to 320×320 |
| `krakenlcd dash [DESIGN]` | a live design: CPU, GPU, coolant, load, … |
| `krakenlcd design list` | the designs there are, built-in and yours |
| `krakenlcd design new NAME` | start a design of your own |
| `krakenlcd design preview NAME` | draw a design into a PNG, no cooler needed |
| `krakenlcd sensors` | what a design can show, and where it comes from |
| `krakenlcd temp` | draw the liquid temperature, updating live |
| `krakenlcd preview OUT.png` | render a screen to a file without touching hardware |
| `krakenlcd restore` | show again whatever was showing last (see below) |
| `krakenlcd liquid` | back to the firmware's own white screen |
| `krakenlcd brightness 0..100` | panel brightness |
| `krakenlcd orientation 0\|90\|180\|270` | rotate the panel |
| `krakenlcd reset` | clear all buckets and restore the firmware screen |
| `krakenlcd usb-reset` | re-enumerate the device, if it ever wedges |

Colours are names (`red`, `amber`, `cyan`, `white`, …), `#rrggbb`, or `r,g,b`.
Note that `#` starts a comment in some shells — quote it.

```sh
krakenlcd temp --color '#ff4400' --scale 0.65 --decimals 1
krakenlcd gif clip.gif --geometry contain --colors 64
krakenlcd fill white --bucket 4
```

`krakenlcd temp` keeps running and re-uploads whenever the reading changes;
stop it with Ctrl-C and the last frame stays on the panel. Add `--restore` to
hand the display back to the firmware on exit, or `--once` for a single frame.

### Live designs

```sh
krakenlcd dash                      # the default design, "swiss"
krakenlcd dash duo                  # another one
krakenlcd dash swiss --set big=liquid --set hot=40
krakenlcd design list               # what there is
```

A design is an SVG file with placeholders where the numbers go:
`{{ cpu }}`, `{{ fmt(gpu_load) }}%`, an arc that follows the CPU temperature,
a colour that turns red past 80 °C. Three come with krakenlcd, and making
your own takes a copy and a text editor (or Inkscape, Figma, an AI chat):

```sh
krakenlcd design new mine                   # a copy of "simple", in ~/.config/krakenlcd/designs
krakenlcd design new mine --image pic.jpg   # or built around a picture of yours
krakenlcd dash mine                         # on the cooler; redraws every time you save
```

**[docs/DESIGNS.md](docs/DESIGNS.md)** is the guide: the placeholders, the
functions, settings, fonts, recipes for arcs, needles and bars, and a prompt
to hand to an AI. `krakenlcd sensors` lists what can be shown, with this
machine's current values: CPU, GPU and coolant temperatures, CPU and GPU
load, memory, pump and fan speed, and the time. They come from Intel
`coretemp`, AMD `k10temp`/`zenpower`, AMD and Intel graphics via hwmon,
NVIDIA via `nvidia-smi`, and the `nzxt-kraken3` driver for the cooler. If a
reading looks wrong, `krakenlcd sensors` is also where to start.

The `data-…` attributes of a design are its settings (`--set`, or a form in
the GUI): the scale, the warning threshold, colours, which sensor goes where.
That lets a design be adjusted without opening it.

Designs are drawn with [resvg](https://github.com/linebender/resvg), not a
browser, so a live dashboard stays cheap. A refresh reads the sensors that
the design actually uses, fills in the placeholders, and stops right there
if nothing visible changed. Only then is the picture drawn (~5–15 ms) and
encoded for the cooler (~2–4 ms). The readings are damped over six seconds
by default (`--smooth`): a CPU package sensor really does swing 30 °C between
one second of idle and the next of load, and undamped that is a flicker, not
a measurement.

The fonts the built-in designs use, Chakra Petch and JetBrains Mono, ship
with the package under `krakenlcd/fonts/`, so they look the same on a
machine that has never installed either. Both are under the SIL Open Font
License; the licence texts sit next to them.

### Surviving a reboot

The cooler remembers nothing: its display memory is volatile, so every power
cycle brings back the firmware's white temperature screen. krakenlcd therefore
remembers for it. Every command that changes the display writes what it did to

```
~/.config/krakenlcd/state.json
```

and the systemd **user** service installed above runs `krakenlcd restore` when
you log in, which plays it back. Set it once —

```sh
krakenlcd dash duo             # or gif, fill, temp, liquid
krakenlcd brightness 65
```

— and that is what the panel shows after every reboot, without you touching
anything.

```sh
krakenlcd restore --show       # what is remembered right now
krakenlcd restore              # apply it immediately
krakenlcd restore --clear      # forget it; the firmware screen comes back
krakenlcd --no-save fill blue  # just for now, do not change what boots
```

Three things are stored separately, so they do not overwrite each other: the
display command, the brightness, and the rotation. `krakenlcd reset` forgets
all of it, as befits a reset.

For an image the *encoded* upload is cached in `~/.cache/krakenlcd/display.gif`
as well. Restoring is then a straight upload of the same bytes: no re-encode
at login, and it still works if the original file has since moved. Rotate the
panel and the cache no longer matches, so the source is re-encoded once and
re-cached.

The service is per user, not system-wide, and that is deliberate: the udev
rule grants access to whoever is logged in at the seat, so before login there
is nobody with permission to talk to the device. The panel therefore comes
back a second or two after you log in, not at the boot logo. `--wait 60`
covers the gap while USB settles and logind attaches the ACL.

```sh
systemctl --user status krakenlcd.service    # what it did
systemctl --user disable --now krakenlcd.service   # stop restoring at login
journalctl --user -u krakenlcd.service -b    # why it did not
```

With `krakenlcd dash` or `krakenlcd temp` as the saved mode the service simply
keeps running, since those redraw live. Everything else is a single upload and
the unit finishes as `active (exited)`.

### GUI

`krakenlcd-gui` gives you a round live preview, a design picker with a field
for each of the design's settings (and buttons to start or edit your own), a
colour picker with presets and RGB sliders, a file picker for images and GIFs,
and brightness and rotation controls. Dragging a colour slider updates the panel as you go — a flat colour
encodes to about 500 bytes, so the round trip is imperceptible.

## What this does to your system

Deliberately as little as possible, and all of it reversible.

**Files in your home**, if you used `install.sh`: a virtualenv in
`~/.local/share/krakenlcd` (with a copy of the source in `src/` next to it),
two symlinks in `~/.local/bin`, a systemd **user** unit in
`~/.config/systemd/user/krakenlcd.service`, and what to show at boot in
`~/.config/krakenlcd` (your designs too, under `designs/`) and
`~/.cache/krakenlcd`. `--uninstall` removes all but the last two, which are
yours and are named in the output. No system
service, no shell profile is edited — the script only prints the `PATH` line
for you to add if you want it.

**One udev rule**, `/etc/udev/rules.d/99-nzxt-kraken-z.rules`, which gives the
logged-in desktop user read/write access to `/dev/hidraw*` and
`/dev/bus/usb/…` **for device `1e71:3008` only**. Without it the tools have to
run as root. An existing file of that name is backed up and restored on
uninstall.

**No kernel module is loaded, blacklisted or unbound.** The driver is entirely
userspace:

* control and status go over `/dev/hidrawN` (interface 1, which stays bound to
  `usbhid` — nothing is detached)
* image data goes over `usbfs` to bulk endpoint `0x02` on interface 0, which
  is vendor-specific and has no kernel driver to begin with. It is claimed
  with `USBDEVFS_CLAIMINTERFACE` and released again on close.

**Pump and fan curves are never touched.** Only report IDs `0x10`, `0x74`,
`0x30`, `0x32`, `0x36` and `0x38` are ever written. The pump reports (`0x72`,
`0x73`) are not among them, so cooling behaviour is unaffected no matter what
the tools do.

**Device state that does change:** the cooler has ~23.75 MiB of display memory
split into 16 "buckets". Uploading an image writes buckets 0 and 1 and changes
the display mode. That is device state, not system state — `krakenlcd reset`
clears it, any other tool overwrites it, and cutting power restores firmware
defaults.

### Running alongside CoolerControl or liquidctl

They can coexist: this project only ever writes display reports, so another
tool can own the fan and pump curves at the same time. If something else is
also driving the *display*, the two will fight over it — configure only one of
them to do that.

## Troubleshooting

**`cannot open /dev/hidrawN for writing`** — the udev rule is missing or has
not been applied yet. Run
`~/.local/share/krakenlcd/src/scripts/install-udev.sh` (or
`./scripts/install-udev.sh` from a checkout), then replug the cooler.

**`interface 0 is claimed by another process`** — something else has the bulk
endpoint. Usually NZXT CAM in a VM with USB passthrough, or a second copy of
this tool.

**Uploads succeed but nothing appears** — this is the failure mode you get
when the upload sequence is subtly wrong; every status byte reads `0x01` and
the panel simply keeps the old image. See
[docs/PROTOCOL.md](docs/PROTOCOL.md), which spells out the four details that
cause it.

**The image is rotated 90°** — the firmware rotates the screens it draws
itself, but shows uploaded images exactly as sent. The tools read the panel's
rotation before every upload and pre-rotate to match; if your panel is
mis-set, fix it with `krakenlcd orientation`.

**Display black, frozen, or showing garbage:**

1. `krakenlcd reset`
2. `krakenlcd usb-reset`
3. Power off, switch the PSU off, wait ten seconds, back on. The cooler boots
   with firmware defaults.

The display is pure peripheral — the pump and fans keep running regardless, so
none of this puts your cooling at risk.

## Limitations

**The firmware's own temperature screen cannot be recoloured.** It is drawn by
the cooler, and the protocol has no colour field for it: the only command is
`0x38 0x01 0x02 0x00`, with no parameters. NZXT's own software offers no
colour choice for this mode on the Z53/Z63/Z73 either.

`krakenlcd temp` therefore *redraws* that layout on the host and uploads it as
an ordinary image. That buys you any colour you like, but the typeface is not
NZXT's — it picks the closest condensed sans installed on your system, and you
can point it at any TTF with `--font`.

Only `1e71:3008` is supported. The Kraken 2023/Elite models use a 640×640
panel and, on some firmware revisions, a different image format.

## Contributing

Bug reports, designs and code are welcome. [CONTRIBUTING.md](CONTRIBUTING.md)
says how; the tests run without a cooler:

```sh
pip install -e '.[gui]'
python -m unittest discover -s tests
```

## Documentation

* [docs/DESIGNS.md](docs/DESIGNS.md) — making your own live design
* [docs/PROTOCOL.md](docs/PROTOCOL.md) — the wire protocol, in enough detail
  to write another implementation
* `tools/probe.py` — send an arbitrary HID report and print the reply, which
  is how most of the above was worked out

## Credits

The display protocol was mapped by the [liquidctl](https://github.com/liquidctl/liquidctl)
project; their `KrakenZ3` driver is the reference this implementation was
checked against. The `nzxt-kraken3` kernel hwmon driver supplies the liquid
temperature readings used here.

Not affiliated with or endorsed by NZXT.

## License

GPL-3.0-or-later. See [LICENSE](LICENSE).

## A note on how this was made

krakenlcd stands on other people's work: the display protocol was mapped by
[liquidctl](https://github.com/liquidctl/liquidctl), designs are drawn by
[resvg](https://github.com/linebender/resvg) and
[Pillow](https://python-pillow.org/), the GUI is
[Qt](https://www.qt.io/) through [PyQt6](https://www.riverbankcomputing.com/software/pyqt/),
and the built-in designs are set in
[Chakra Petch](https://fonts.google.com/specimen/Chakra+Petch) and
[JetBrains Mono](https://www.jetbrains.com/lp/mono/).

Large parts of the code and documentation were written with the help of
LLMs. Using them for improvements is explicitly welcome. See
[CONTRIBUTING.md](CONTRIBUTING.md#using-llms) for what we ask of such
contributions, and [AGENTS.md](AGENTS.md) for the instructions coding agents
follow.
