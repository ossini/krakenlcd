# SPDX-License-Identifier: GPL-3.0-or-later
"""Remembering what the display was showing, so a reboot can put it back.

The cooler keeps none of this by itself: the buckets are volatile and the
firmware comes up on its own white temperature screen every time the PSU is
switched on.  So whatever was asked for last is written to

    ~/.config/krakenlcd/state.json

and ``krakenlcd restore`` -- what the systemd user service runs when you log in
-- plays it back.

Three things are stored, separately, because they are set by separate
commands and each of them should survive the others:

    argv          the display command, as an argument list ("what to show")
    brightness    last value passed to `krakenlcd brightness`
    orientation   last value passed to `krakenlcd orientation`

For an uploaded image the encoded payload is cached next to it, in
``~/.cache/krakenlcd/display.gif``.  Re-encoding a large GIF costs a few
seconds of CPU at every login, and the source file may well have moved; the
cache makes the replay a straight upload of the exact bytes that went to the
device the first time.
"""

import json
import os
import tempfile

VERSION = 1

_DISPLAY_KEYS = ("argv", "payload_rotate", "payload_size")


def _xdg(var: str, default: str) -> str:
    base = os.environ.get(var) or os.path.join(os.path.expanduser("~"), default)
    return os.path.join(base, "krakenlcd")


def config_dir() -> str:
    return _xdg("XDG_CONFIG_HOME", ".config")


def cache_dir() -> str:
    return _xdg("XDG_CACHE_HOME", ".cache")


def state_path() -> str:
    return os.path.join(config_dir(), "state.json")


def payload_path() -> str:
    return os.path.join(cache_dir(), "display.gif")


# ---------------------------------------------------------------------------
# reading and writing

def load() -> dict:
    """The saved state, or an empty dict if there is none or it is unusable."""
    try:
        with open(state_path(), "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return {}
    if not isinstance(data, dict) or data.get("version") != VERSION:
        return {}
    return data


def _write(data: dict):
    if data == load():
        return                    # dragging a colour slider re-saves a lot
    path = state_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    # Atomic, so an interrupted write cannot leave a half-file that the next
    # boot would silently discard.
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path), suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=1, sort_keys=True)
            fh.write("\n")
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def update(**fields):
    """Merge `fields` into the saved state.  Values of None are dropped."""
    data = load()
    data["version"] = VERSION
    for key, value in fields.items():
        if value is None:
            data.pop(key, None)
        else:
            data[key] = value
    _write(data)


def save_display(argv, payload: bytes | None = None, rotate: int | None = None):
    """Record the command that put something on the screen.

    `payload` is the encoded image as uploaded; it is cached only when
    re-encoding it later would be slow or impossible.
    """
    fields = dict.fromkeys(_DISPLAY_KEYS)
    fields["argv"] = list(argv)
    if payload is not None:
        try:
            path = payload_path()
            os.makedirs(os.path.dirname(path), exist_ok=True)
            fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path), suffix=".tmp")
            with os.fdopen(fd, "wb") as fh:
                fh.write(payload)
            os.replace(tmp, path)
            fields["payload_rotate"] = rotate
            fields["payload_size"] = len(payload)
        except OSError:
            pass          # the cache is an optimisation, never a requirement
    else:
        try:
            os.unlink(payload_path())
        except OSError:
            pass
    update(**fields)


def load_payload(rotate: int) -> bytes | None:
    """The cached upload, if there is one and it matches the panel rotation.

    Bucket content is not rotated by the firmware, so a payload encoded for a
    different rotation is worse than useless -- the caller re-encodes instead.
    """
    data = load()
    if data.get("payload_rotate") != rotate:
        return None
    try:
        with open(payload_path(), "rb") as fh:
            payload = fh.read()
    except OSError:
        return None
    if data.get("payload_size") not in (None, len(payload)):
        return None
    return payload or None


def clear():
    """Forget everything, so the next boot leaves the firmware screen alone."""
    for path in (state_path(), payload_path()):
        try:
            os.unlink(path)
        except OSError:
            pass


def describe(data: dict | None = None) -> str:
    """One line for humans, for `krakenlcd restore --show` and `status`."""
    data = load() if data is None else data
    if not data:
        return "nothing saved"
    parts = []
    argv = data.get("argv")
    if argv:
        parts.append(" ".join(argv))
    if data.get("brightness") is not None:
        parts.append(f"brightness {data['brightness']}%")
    if data.get("orientation") is not None:
        parts.append(f"rotated {data['orientation']}°")
    return ", ".join(parts) if parts else "nothing saved"
