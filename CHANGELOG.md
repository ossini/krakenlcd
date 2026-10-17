# Changelog

## 0.2.0b1 (unreleased)

* **Live designs.** `krakenlcd dash [DESIGN]` shows a live dashboard, and a
  design is an SVG file with placeholders like `{{ cpu }}` where the numbers
  go: made in Inkscape, Figma, a text editor or an AI chat, and dropped into
  `~/.config/krakenlcd/designs/`. Three ship with the package: `swiss` (the
  default), `simple` and `duo`. [docs/DESIGNS.md](docs/DESIGNS.md) is the
  guide.
* Placeholders are a small expression language (arithmetic, comparisons,
  `a if c else b`, and functions such as `fmt`, `map`, `arc`, `ramp`) that is
  parsed, never executed as Python, so a design from someone else can only
  ever draw a picture. Mistakes are reported with the file, the line and
  usually a suggestion.
* A design's `data-*` attributes are its settings: changeable with
  `--set NAME=VALUE`, or in a form the GUI builds for each design.
* `krakenlcd design list | new | preview | folder`. `new` copies a design to
  start from, or builds one around a picture (`--image`); `preview` draws one
  into a PNG without a cooler, `--watch` keeps it current while editing.
* `dash` redraws the moment the design file is saved, and keeps the last good
  version on screen while an edit is broken.
* More to show: CPU and GPU load, memory, pump and fan speed, the time and
  date, besides CPU, GPU and coolant temperatures. `krakenlcd sensors` lists
  them with their current values. Only the sensors a design uses are read, so
  one without the GPU never starts `nvidia-smi`.
* Designs are drawn with resvg (Rust, via the `resvg-py` wheel) rather than a
  browser, and only when something visible changes: an unchanged refresh
  costs well under a millisecond. Frames are now quantised and encoded with
  Pillow, ~3 ms instead of ~50 ms with the pure-Python encoder, which also
  speeds up `krakenlcd temp`.
* The GUI has a *Live design* mode: design picker, settings form with colour
  pickers, *New design …*, *Edit*, *Open folder*, and a preview that follows
  the sensors. It shows only the controls the current mode uses.
* Readings are damped (`--smooth`, 6 s by default): a CPU package sensor
  swings 30 °C between two seconds, which on a gauge reads as a twitch rather
  than a temperature.
* `dash` is recorded like every other display command, so the systemd user
  service brings it back after a reboot. The flags of earlier beta builds
  (`--big`, `--small`, `--min`, `--max`, `--hot`, `--decimals`) are still
  accepted and become the `swiss` design's settings.
* Chakra Petch and JetBrains Mono ship in `krakenlcd/fonts/` (SIL Open Font
  License, texts alongside). A design's own fonts sit next to it; system
  fonts are found through fontconfig.
* `krakenlcd --version`.
* Unit tests (`python -m unittest discover -s tests`), run on every push by
  GitHub Actions, along with the GUI offscreen and the install script.

## 0.1.0

First release.

* Userspace transport for the Kraken Z (`1e71:3008`): 64-byte HID reports over
  `hidraw`, image data over `usbfs` to bulk endpoint `0x02`. No kernel module,
  no driver unbinding.
* GIF encoder/decoder, image scaler and colour quantiser written against the
  GIF89a spec, so the protocol layer has no third-party dependencies.
* `krakenlcd` command: `status`, `fill`, `gif`, `temp`, `preview`, `liquid`,
  `brightness`, `orientation`, `reset`, `usb-reset`.
* `krakenlcd-gui`: live colour picker, image and GIF playback, brightness and
  rotation, with a round preview of what the panel will show.
* Liquid temperature is read from the `nzxt-kraken3` kernel hwmon driver when
  available, falling back to a HID status report.
* Display updates are double-buffered across two buckets, so a bucket is never
  rewritten while it is on screen.
* Uploaded images are pre-rotated to match the panel's orientation, which the
  firmware does not do for bucket content.
* Brightness and orientation share one report; both setters read the other
  value back rather than writing a default over it.
* udev rule scoped to the single device, with install and uninstall scripts.
* The display survives a reboot: every command that changes it records what it
  did in `~/.config/krakenlcd/state.json`, `krakenlcd restore` plays it back,
  and a systemd user service runs it at login. Display command, brightness and
  rotation are stored separately. Uploaded images keep their encoded payload in
  `~/.cache/krakenlcd`, so a login is an upload rather than a re-encode.
* `krakenlcd restore --show/--clear` to inspect and forget it, `--no-save` on
  any command to change the panel without changing what comes back.
* Fixed: `fill` and `gif` failed with "bucket 0 setup rejected by the firmware
  (result 0x04)" whenever that bucket was the one already on screen, which is
  the normal state after any previous upload. `DoubleBuffer` now frees the
  buckets and retries; only the live temperature display did this before.
* One-line installer, `curl -fsSL .../install.sh | sh`: isolated virtualenv in
  `~/.local/share/krakenlcd`, commands linked into `~/.local/bin`, udev rule,
  and `--uninstall` to undo all of it. Sidesteps PEP 668, which refuses
  `pip install --user` on current distributions.
* Protocol documented in `docs/PROTOCOL.md`.
