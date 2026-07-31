# SPDX-License-Identifier: GPL-3.0-or-later
"""Where the numbers on the dashboard come from.

Everything here reads sysfs, which costs a couple of file reads and needs no
privileges.  The one exception is an NVIDIA GPU: its driver exposes no hwmon
node, so the temperature has to come from `nvidia-smi`, which is a process
launch (~60 ms).  That is why readings are taken on demand rather than in a
tight loop, and why a missing sensor is simply reported as None instead of
being an error -- a machine without a discrete GPU is not a broken machine.
"""

from __future__ import annotations

import glob
import os
import subprocess
import time

__all__ = ["Readings", "read_all", "cpu_temp", "gpu_temp", "liquid_temp"]

_HWMON = "/sys/class/hwmon"

# Chips that report a CPU package temperature, and the labels to prefer
# within them.  Intel calls it "Package id 0", AMD "Tctl" (control loop, what
# the fans follow) or "Tdie".  Falling back to the first sensor of the chip is
# better than nothing: on the ARM boards it is the only one there is.
_CPU_CHIPS = (
    ("coretemp", ("Package id 0",)),
    ("k10temp", ("Tctl", "Tdie")),
    ("zenpower", ("Tdie", "Tctl")),
    ("cpu_thermal", ()),
    ("soc_thermal", ()),
)

# AMD and Intel graphics do have hwmon nodes.  "edge" is the sensor NZXT's and
# everyone else's software shows; "junction" is the hotter internal one.
_GPU_CHIPS = (
    ("amdgpu", ("edge", "junction")),
    ("i915", ("gpu",)),
    ("xe", ("gpu",)),
)

_cache: dict[str, str | None] = {}

# How long an nvidia-smi answer stays good enough to reuse.
NVIDIA_MIN_INTERVAL = 3.0
_nvidia_last: list = [None, None]      # (monotonic time, value)


class Readings:
    """One sample of what the dashboard can show.  Any of it may be None."""

    __slots__ = ("cpu", "gpu", "liquid")

    def __init__(self, cpu=None, gpu=None, liquid=None):
        self.cpu = cpu
        self.gpu = gpu
        self.liquid = liquid

    def __repr__(self):
        def f(v):
            return "—" if v is None else f"{v:.1f}"
        return (f"<Readings cpu={f(self.cpu)} gpu={f(self.gpu)} "
                f"liquid={f(self.liquid)}>")


# ---------------------------------------------------------------------------
# hwmon

def _read_temp(path: str) -> float | None:
    try:
        with open(path) as fh:
            return int(fh.read().strip()) / 1000.0
    except (OSError, ValueError):
        return None


def _find_sensor(chips) -> str | None:
    """Path of the first temp*_input matching one of `chips`, or None.

    `chips` is a sequence of (hwmon name, preferred labels).  A chip with no
    preferred label matches its first sensor.
    """
    for want_name, want_labels in chips:
        for hwmon in sorted(glob.glob(os.path.join(_HWMON, "hwmon*"))):
            try:
                with open(os.path.join(hwmon, "name")) as fh:
                    if fh.read().strip() != want_name:
                        continue
            except OSError:
                continue
            inputs = sorted(glob.glob(os.path.join(hwmon, "temp*_input")))
            for label in want_labels:
                for path in inputs:
                    try:
                        with open(path[:-len("_input")] + "_label") as fh:
                            if fh.read().strip() == label:
                                return path
                    except OSError:
                        continue
            if not want_labels and inputs:
                return inputs[0]
    return None


def _cached_sensor(key: str, chips) -> str | None:
    """Look a sensor up once, but find it again if it disappears.

    hwmon numbers are not stable across reboots or module reloads, and this
    process can outlive both.
    """
    path = _cache.get(key, "")
    if path and os.path.exists(path):
        return path
    path = _find_sensor(chips)
    _cache[key] = path
    return path


# ---------------------------------------------------------------------------
# the readings

def cpu_temp() -> float | None:
    path = _cached_sensor("cpu", _CPU_CHIPS)
    return _read_temp(path) if path else None


def _nvidia_temp() -> float | None:
    """Ask nvidia-smi, if there is one.

    Only a missing binary is remembered as final.  Anything else -- a driver
    reloading, a timeout under load, a momentarily busy GPU -- is transient,
    and a dashboard that ran for a week would otherwise lose its GPU reading
    for good the first time one of those happened.
    """
    if _cache.get("nvidia") == "missing":
        return None

    # Unlike a sysfs read, this forks a process and costs ~60 ms of CPU, which
    # on a dashboard ticking every two seconds is more than everything else it
    # does put together.  A GPU does not change temperature meaningfully in
    # that time, so serve a recent answer instead.
    when, value = _nvidia_last
    if when is not None and time.monotonic() - when < NVIDIA_MIN_INTERVAL:
        return value

    try:
        out = subprocess.run(
            ["nvidia-smi", "--query-gpu=temperature.gpu",
             "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=5.0)
    except FileNotFoundError:
        _cache["nvidia"] = "missing"
        return None
    except (OSError, subprocess.SubprocessError):
        return _remember_nvidia(None)
    if out.returncode != 0:
        return _remember_nvidia(None)
    lines = out.stdout.strip().splitlines()
    try:
        return _remember_nvidia(float(lines[0]))
    except (IndexError, ValueError):
        return _remember_nvidia(None)


def _remember_nvidia(value):
    _nvidia_last[0] = time.monotonic()
    _nvidia_last[1] = value
    return value


def gpu_temp() -> float | None:
    path = _cached_sensor("gpu", _GPU_CHIPS)
    if path:
        value = _read_temp(path)
        if value is not None:
            return value
    return _nvidia_temp()


def liquid_temp() -> float | None:
    """The cooler's own coolant sensor, via the nzxt-kraken3 kernel driver."""
    from .device import read_hwmon_temp
    return read_hwmon_temp()


def read_all() -> Readings:
    return Readings(cpu=cpu_temp(), gpu=gpu_temp(), liquid=liquid_temp())


def describe_sources() -> list[str]:
    """Where each reading comes from, so a wrong number can be traced."""
    cpu = _cached_sensor("cpu", _CPU_CHIPS)
    gpu = _cached_sensor("gpu", _GPU_CHIPS)
    if not gpu and _nvidia_temp() is not None:
        gpu = "nvidia-smi"
    liquid = ("nzxt-kraken3 hwmon" if liquid_temp() is not None
              else "not found (the HID status report still has it)")
    return [
        f"cpu    : {cpu or 'not found'}",
        f"gpu    : {gpu or 'not found'}",
        f"liquid : {liquid}",
    ]
