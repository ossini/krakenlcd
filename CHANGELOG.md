# Changelog

## 0.2.0b1 (unreleased)

* `krakenlcd dash` — a gauge dashboard: a 270° scale from 30 to 100 °C, the
  CPU temperature as the reading, the GPU along the bottom, and the whole
  gauge turning red past `--hot`. `--big` and `--small` choose which of CPU,
  GPU and coolant goes where; `--preview out.png` draws it without hardware.
* The design is `design/kraken-2a-static.html`, transcribed into Pillow
  drawing (`design/kraken_2a.py`) rather than rendered as HTML. A browser
  would have meant ~200 MB of QtWebEngine and a resident Chromium for a
  320-pixel panel, and rendering was never the bottleneck: ~1 ms of the frame
  is drawing, ~48 ms is quantising and encoding it.
* Chakra Petch Bold and JetBrains Mono ship in `krakenlcd/fonts/` (SIL Open
  Font License, texts alongside), so the dial is set in the typefaces it was
  designed in rather than in whatever the machine happens to have.
* `krakenlcd sensors` — which hwmon nodes the readings come from, so a wrong
  number can be traced. Intel `coretemp`, AMD `k10temp`/`zenpower`, AMD and
  Intel graphics via hwmon, NVIDIA via `nvidia-smi`.
* The panel is only redrawn when a number that is actually displayed changes,
  and readings are damped (`--smooth`, 6 s by default) — a CPU package sensor
  swings 30 °C between two seconds, which on a gauge reads as a twitch rather
  than a temperature.
* `dash` is recorded like every other display command, so the systemd user
  service brings the dashboard back after a reboot.

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
