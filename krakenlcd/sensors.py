# SPDX-License-Identifier: GPL-3.0-or-later
"""Where the numbers in a design come from.

Everything here reads sysfs or /proc, which costs a couple of file reads and
needs no privileges.  The one exception is an NVIDIA GPU: its driver exposes
no hwmon node, so temperature and load have to come from `nvidia-smi`, which
is a process launch (~60 ms).  That is why readings are taken on demand --
a design that never mentions the GPU never starts nvidia-smi -- and why a
missing sensor is simply reported as None instead of being an error: a
machine without a discrete GPU is not a broken machine.

SENSORS is the list a design can use by name.  `krakenlcd sensors` prints it
with the current values, which doubles as the reference for designers.
"""

from __future__ import annotations

import glob
import math
import os
import subprocess
import time

__all__ = ["SENSORS", "TEXT", "Sensor", "Smoother", "read", "describe_sources",
           "cpu_temp", "gpu_temp", "liquid_temp"]

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

# What the nzxt-kraken3 kernel driver calls itself, depending on the model.
_KRAKEN_CHIPS = ("z53", "z63", "z73", "kraken3", "nzxt_kraken3")

_cache: dict[str, str | None] = {}

# How long an nvidia-smi answer stays good enough to reuse.
NVIDIA_MIN_INTERVAL = 3.0
_nvidia_last: list = [None, (None, None)]   # (monotonic time, (temp, load))


# ---------------------------------------------------------------------------
# hwmon

def _read_number(path: str, scale: float = 1.0) -> float | None:
    try:
        with open(path) as fh:
            return int(fh.read().strip()) / scale
    except (OSError, ValueError):
        return None


def _read_temp(path: str) -> float | None:
    return _read_number(path, 1000.0)


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


def _find_kraken() -> str | None:
    for hwmon in sorted(glob.glob(os.path.join(_HWMON, "hwmon*"))):
        try:
            with open(os.path.join(hwmon, "name")) as fh:
                if fh.read().strip() in _KRAKEN_CHIPS:
                    return hwmon
        except OSError:
            continue
    return None


def _cached(key: str, find) -> str | None:
    """Look a path up once, but find it again if it disappears.

    hwmon numbers are not stable across reboots or module reloads, and this
    process can outlive both.
    """
    path = _cache.get(key, "")
    if path and os.path.exists(path):
        return path
    path = find()
    _cache[key] = path
    return path


def _cached_sensor(key: str, chips) -> str | None:
    return _cached(key, lambda: _find_sensor(chips))


# ---------------------------------------------------------------------------
# NVIDIA

def _nvidia() -> tuple:
    """(temperature, load) from nvidia-smi, either of them possibly None.

    Only a missing binary is remembered as final.  Anything else -- a driver
    reloading, a timeout under load, a momentarily busy GPU -- is transient,
    and a dashboard that ran for a week would otherwise lose its GPU reading
    for good the first time one of those happened.
    """
    if _cache.get("nvidia") == "missing":
        return None, None

    # Unlike a sysfs read, this forks a process and costs ~60 ms of CPU, which
    # on a dashboard ticking every two seconds is more than everything else it
    # does put together.  A GPU does not change temperature meaningfully in
    # that time, so serve a recent answer instead.
    when, value = _nvidia_last
    if when is not None and time.monotonic() - when < NVIDIA_MIN_INTERVAL:
        return value

    try:
        out = subprocess.run(
            ["nvidia-smi", "--query-gpu=temperature.gpu,utilization.gpu",
             "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=5.0)
    except FileNotFoundError:
        _cache["nvidia"] = "missing"
        return None, None
    except (OSError, subprocess.SubprocessError):
        return _remember_nvidia(None, None)
    if out.returncode != 0:
        return _remember_nvidia(None, None)
    lines = out.stdout.strip().splitlines()
    fields = [f.strip() for f in lines[0].split(",")] if lines else []

    def number(i):
        try:
            return float(fields[i])
        except (IndexError, ValueError):
            return None
    return _remember_nvidia(number(0), number(1))


def _remember_nvidia(temp, load):
    _nvidia_last[0] = time.monotonic()
    _nvidia_last[1] = (temp, load)
    return temp, load


# ---------------------------------------------------------------------------
# the readings

def cpu_temp() -> float | None:
    path = _cached_sensor("cpu", _CPU_CHIPS)
    return _read_temp(path) if path else None


def gpu_temp() -> float | None:
    path = _cached_sensor("gpu", _GPU_CHIPS)
    if path:
        value = _read_temp(path)
        if value is not None:
            return value
    return _nvidia()[0]


def liquid_temp() -> float | None:
    """The cooler's own coolant sensor, via the nzxt-kraken3 kernel driver."""
    hwmon = _cached("kraken", _find_kraken)
    return _read_temp(os.path.join(hwmon, "temp1_input")) if hwmon else None


def pump_rpm() -> float | None:
    hwmon = _cached("kraken", _find_kraken)
    return _read_number(os.path.join(hwmon, "fan1_input")) if hwmon else None


def fan_rpm() -> float | None:
    hwmon = _cached("kraken", _find_kraken)
    return _read_number(os.path.join(hwmon, "fan2_input")) if hwmon else None


_cpu_last: list = [None]        # (busy, total) jiffies at the previous read


def _cpu_jiffies():
    try:
        with open("/proc/stat") as fh:
            fields = [int(v) for v in fh.readline().split()[1:]]
    except (OSError, ValueError):
        return None
    idle = fields[3] + (fields[4] if len(fields) > 4 else 0)   # idle + iowait
    total = sum(fields[:8])     # guest time is already counted in user
    return total - idle, total


def cpu_load() -> float | None:
    """Percent of all cores busy since the previous call.

    The very first call has nothing to compare with, so it takes a second
    sample a moment later rather than showing a blank.
    """
    now = _cpu_jiffies()
    if now is None:
        return None
    previous = _cpu_last[0]
    if previous is None:
        time.sleep(0.05)
        previous, now = now, _cpu_jiffies()
        if now is None:
            return None
    _cpu_last[0] = now
    busy, total = now[0] - previous[0], now[1] - previous[1]
    return 100.0 * busy / total if total > 0 else 0.0


def gpu_load() -> float | None:
    """AMD reports this in sysfs; NVIDIA through nvidia-smi."""
    def find():
        found = sorted(glob.glob("/sys/class/drm/card*/device/gpu_busy_percent"))
        return found[0] if found else None
    path = _cached("gpu_load", find)
    if path:
        value = _read_number(path)
        if value is not None:
            return value
    return _nvidia()[1]


def _meminfo() -> dict:
    out = {}
    try:
        with open("/proc/meminfo") as fh:
            for line in fh:
                key, _, rest = line.partition(":")
                if key in ("MemTotal", "MemAvailable"):
                    out[key] = int(rest.split()[0])     # KiB
    except (OSError, ValueError, IndexError):
        pass
    return out


def ram_percent() -> float | None:
    info = _meminfo()
    if not info.get("MemTotal") or "MemAvailable" not in info:
        return None
    return 100.0 * (info["MemTotal"] - info["MemAvailable"]) / info["MemTotal"]


def ram_used() -> float | None:
    info = _meminfo()
    if "MemTotal" not in info or "MemAvailable" not in info:
        return None
    return (info["MemTotal"] - info["MemAvailable"]) / (1024 * 1024)   # GiB


def ram_total() -> float | None:
    total = _meminfo().get("MemTotal")
    return total / (1024 * 1024) if total else None


# ---------------------------------------------------------------------------
# the registry designs use

class Sensor:
    """A number a design can show.

    `step` is how finely it is passed on: a design is only redrawn when a
    value changes by a whole step, so this is what keeps 1650 rpm and 1653
    rpm from costing an upload.  None means "a temperature": whole degrees,
    or tenths when the design asks for `decimals`.
    """

    __slots__ = ("name", "label", "unit", "read", "step", "demo")

    def __init__(self, name, label, unit, read, step, demo):
        self.name = name
        self.label = label
        self.unit = unit
        self.read = read
        self.step = step
        self.demo = demo


SENSORS = {s.name: s for s in (
    Sensor("cpu", "CPU temperature", "°C", cpu_temp, None, 67),
    Sensor("gpu", "GPU temperature", "°C", gpu_temp, None, 71),
    Sensor("liquid", "coolant temperature", "°C", liquid_temp, None, 31),
    Sensor("cpu_load", "CPU load", "%", cpu_load, 1, 42),
    Sensor("gpu_load", "GPU load", "%", gpu_load, 1, 35),
    Sensor("ram", "memory in use", "%", ram_percent, 1, 58),
    Sensor("ram_used", "memory in use", "GiB", ram_used, 0.1, 18.4),
    Sensor("ram_total", "memory installed", "GiB", ram_total, 0.1, 31.2),
    Sensor("pump", "pump speed", "rpm", pump_rpm, 10, 1650),
    Sensor("fan", "radiator fan speed", "rpm", fan_rpm, 10, 900),
)}

# Text a design can show.  Not smoothed, not stepped: it changes when it
# changes, which for the clock is once a minute.
TEXT = {
    "time": lambda: time.strftime("%H:%M"),
    "date": lambda: time.strftime("%Y-%m-%d"),
}


def read(name: str) -> float | None:
    return SENSORS[name].read()


# ---------------------------------------------------------------------------
# smoothing

class Smoother:
    """Exponential moving average, one per reading.

    A CPU package sensor swings by 30 °C between two seconds of idle and one
    of load, which on a gauge reads as a twitch rather than a temperature.
    `tau` is the time constant in seconds: after that long, roughly two thirds
    of a step has been followed.  `tau = 0` passes the raw value through.
    """

    def __init__(self, tau: float = 5.0):
        self.tau = max(0.0, tau)
        self._values: dict[str, tuple[float, float]] = {}  # name: (when, value)

    def feed(self, name: str, value, now: float | None = None):
        if value is None:
            self._values.pop(name, None)
            return None
        now = time.monotonic() if now is None else now
        previous = self._values.get(name)
        if self.tau <= 0 or previous is None:
            self._values[name] = (now, value)
            return value
        then, old = previous
        dt = now - then
        if dt <= 0:
            return old
        alpha = 1.0 - math.exp(-dt / self.tau)
        smoothed = old + (value - old) * alpha
        self._values[name] = (now, smoothed)
        return smoothed


def describe_sources() -> list[str]:
    """Where each reading comes from, so a wrong number can be traced."""
    cpu = _cached_sensor("cpu", _CPU_CHIPS)
    gpu = _cached_sensor("gpu", _GPU_CHIPS)
    if not gpu and _nvidia()[0] is not None:
        gpu = "nvidia-smi"
    kraken = _cached("kraken", _find_kraken)
    return [
        f"cpu      : {cpu or 'not found'}",
        f"gpu      : {gpu or 'not found'}",
        f"cooler   : {kraken + ' (nzxt-kraken3)' if kraken else 'not found'}",
    ]
