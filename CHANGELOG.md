# Changelog

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
