# krakenlcd

Userspace driver, CLI and GUI for the LCD on **NZXT Kraken Z** coolers
(Z53 / Z63 / Z73, USB ID `1e71:3008`) on Linux.

NZXT ships no Linux software for the display, so the panel comes up with the
firmware's white liquid-temperature screen and stays there. This project talks
to it directly: flood it with any colour, play a GIF, or draw the temperature
in a colour of your choosing.

```
krakenlcd fill '#ff0000'      # any colour, full screen
krakenlcd gif party.gif       # animation, resized and rotated to fit
krakenlcd temp --color amber  # live liquid temperature
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
* [PyQt6](https://pypi.org/project/PyQt6/) — only for the GUI

The protocol layer itself (`krakenlcd.lowlevel`, `krakenlcd.device`, plus a GIF
encoder and image scaler written from scratch) uses nothing but the standard
library. If you only want `fill`, `liquid`, `brightness` and friends, you do
not need Pillow either.

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
| `krakenlcd temp` | draw the liquid temperature, updating live |
| `krakenlcd dash` | CPU/GPU gauge dashboard, updating live |
| `krakenlcd sensors` | which temperature sensors were found |
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

### The dashboard

```sh
krakenlcd dash                      # CPU on the gauge, GPU underneath
krakenlcd dash --big liquid         # coolant instead
krakenlcd dash --hot 75 --max 90    # where red starts, and the scale
krakenlcd dash --preview out.png    # draw it to a file, no hardware
```

A 270° scale from 30 to 100 °C, the reading in the middle, a second one along
the bottom, and the gauge turning red above `--hot`. `--big` and `--small`
pick from `cpu`, `gpu` and `liquid`.

`krakenlcd sensors` prints where each number comes from — Intel `coretemp`,
AMD `k10temp`/`zenpower`, AMD and Intel graphics via hwmon, NVIDIA via
`nvidia-smi` — which is the first thing to check if a reading looks wrong.

Two details worth knowing. The panel is only redrawn when a number that is
actually on it changes, so a steady machine uploads nothing. And the readings
are damped over six seconds by default (`--smooth`): a CPU package sensor
genuinely swings 30 °C between one second of idle and the next of load, and
undamped that is a flicker, not a measurement.

The design is `design/kraken-2a-static.html`, and `design/kraken_2a.py` is the
standalone transcription of it that the drawing code came from — the two are
kept around so the dial can be read against its stylesheet. It is not
rendered as HTML: that would mean shipping a browser to fill a 320-pixel
panel, and drawing was never the expensive part (about 1 ms of a frame,
against ~48 ms to quantise and encode it).

The three fonts it is set in — Chakra Petch Bold for the numerals, JetBrains
Mono for the labels — ship with the package under `krakenlcd/fonts/`, so the
dial looks the same on a machine that has never installed either. Both are
under the SIL Open Font License; the licence texts sit next to them.
`--font` and `--mono-font` override them.

Live, the dashboard costs about 1.5 % of one core at the default two-second
interval, most of it re-encoding a changed frame.

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
krakenlcd gif party.gif        # or fill, or temp, or liquid
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

With `krakenlcd temp` as the saved mode the service simply keeps running, since
that mode redraws the reading live. Everything else is a single upload and the
unit finishes as `active (exited)`.

### GUI

`krakenlcd-gui` gives you a round live preview, a colour picker with presets
and RGB sliders, a file picker for images and GIFs, and brightness and rotation
controls. Dragging a colour slider updates the panel as you go — a flat colour
encodes to about 500 bytes, so the round trip is imperceptible.

## What this does to your system

Deliberately as little as possible, and all of it reversible.

**Files in your home**, if you used `install.sh`: a virtualenv in
`~/.local/share/krakenlcd` (with a copy of the source in `src/` next to it),
two symlinks in `~/.local/bin`, a systemd **user** unit in
`~/.config/systemd/user/krakenlcd.service`, and what to show at boot in
`~/.config/krakenlcd` and `~/.cache/krakenlcd`. `--uninstall` removes all but
the last two, which are yours and are named in the output. No system
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

## Documentation

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
