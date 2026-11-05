"""Hardware discovery and per-user settings; no GUI dependencies."""

import csv
import json
import math
import os
import re
from pathlib import Path
import subprocess
import tempfile
from dataclasses import dataclass

SMI = "/usr/bin/nvidia-smi"
HELPER = "/usr/local/libexec/v100set-power"
FIELDS = "uuid,name,pci.bus_id,power.draw,power.limit,power.min_limit,power.max_limit,temperature.gpu"


def number(value):
    try:
        result = float(value)
        return result if math.isfinite(result) else None
    except (ValueError, TypeError):
        return None


@dataclass(frozen=True)
class GPU:
    uuid: str
    name: str
    bus: str
    draw: float | None
    limit: float | None
    minimum: float | None
    maximum: float | None
    temperature: float | None

    @property
    def adjustable(self):
        return (self.minimum is not None and self.maximum is not None
                and self.limit is not None and 0 < self.minimum < self.maximum)

    def validate(self, watts):
        value = number(watts)
        if not self.adjustable or value is None or not self.minimum <= value <= self.maximum:
            raise ValueError(f"{self.name}: power limit is outside the supported range")
        return value


def is_v100(gpu):
    return re.search(r"\bV100(?:S)?\b", gpu.name, re.IGNORECASE) is not None


def snap_limit(gpu, watts):
    """Round user selections to a 5 W grid inside the driver's range."""
    low = math.ceil(gpu.minimum / 5) * 5
    high = math.floor(gpu.maximum / 5) * 5
    if low > high:
        raise ValueError("No supported 5 W power limit")
    return max(low, min(high, math.floor(watts / 5 + 0.5) * 5))


def parse_gpus(output):
    result = []
    for row in csv.reader(output.splitlines(), skipinitialspace=True):
        if not row:
            continue
        if len(row) != 8:
            raise ValueError("Unexpected NVIDIA telemetry format")
        row = [v.strip() for v in row]
        result.append(GPU(*row[:3], *(number(v) for v in row[3:])))
    return result


def discover():
    result = subprocess.run([SMI, f"--query-gpu={FIELDS}", "--format=csv,noheader,nounits"],
                            capture_output=True, text=True, timeout=8, check=False)
    if result.returncode:
        raise RuntimeError(result.stderr.strip() or result.stdout.strip() or "NVIDIA driver unavailable")
    return parse_gpus(result.stdout)


def apply_limit(uuid, watts):
    result = subprocess.run(["/usr/bin/sudo", "-n", HELPER, uuid, str(watts)],
                            capture_output=True, text=True, timeout=25, check=False)
    if result.returncode:
        raise RuntimeError(result.stderr.strip() or result.stdout.strip() or "Power limit was rejected")
    return number(result.stdout.strip())


def atomic_write(path, content, mode=0o600):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp = tempfile.mkstemp(dir=path.parent, prefix=".v100set-")
    try:
        with os.fdopen(fd, "w") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temp, mode)
        os.replace(temp, path)
    finally:
        if os.path.exists(temp):
            os.unlink(temp)


class Settings:
    def __init__(self, root=None):
        self.root = Path(root or os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
        self.path = self.root / "v100set/settings.json"
        self.autostart = self.root / "autostart/v100set.desktop"

    def read(self):
        if not self.path.exists():
            return {"version": 1, "start_at_login": False, "limits": {}}
        data = json.loads(self.path.read_text())
        if (not isinstance(data, dict) or data.get("version") != 1
                or not isinstance(data.get("limits"), dict)
                or not isinstance(data.get("start_at_login"), bool)):
            raise ValueError("Invalid v100set settings file")
        for uuid, watts in data["limits"].items():
            if not isinstance(uuid, str) or isinstance(watts, bool) or number(watts) is None:
                raise ValueError("Invalid saved power limit")
        return data

    def write(self, data):
        atomic_write(self.path, json.dumps(data, indent=2) + "\n")

    def set_startup(self, enabled):
        data = self.read()
        previous = data.copy()
        data["start_at_login"] = enabled
        self.write(data)
        try:
            if enabled:
                atomic_write(self.autostart, "[Desktop Entry]\nType=Application\nName=v100set\n"
                             "Comment=Restore saved NVIDIA power limits\n"
                             "Exec=/usr/local/bin/v100set --startup\nIcon=v100set\n"
                             "StartupWMClass=v100set\nTerminal=false\nX-GNOME-Autostart-enabled=true\n")
            else:
                self.autostart.unlink(missing_ok=True)
        except OSError:
            self.write(previous)
            raise

    def save_limits(self, limits):
        data = self.read()
        data["limits"].update(limits)
        self.write(data)


def load_targets(gpus, saved):
    targets, errors = {}, []
    for gpu in gpus:
        if not is_v100(gpu) or gpu.uuid not in saved:
            continue
        try:
            targets[gpu.uuid] = snap_limit(gpu, gpu.validate(saved[gpu.uuid]))
        except ValueError as exc:
            errors.append(str(exc))
    missing = set(saved) - {g.uuid for g in gpus}
    if missing:
        errors.append(f"{len(missing)} saved GPU(s) are not connected")
    return targets, errors
