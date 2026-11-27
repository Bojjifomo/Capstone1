"""The Mac's health for the office header: disk, memory, swap, CPU load, uptime, Time Machine.

Everything is read from the OS with fixed commands (no shell) and cached for half a minute, so the header
can ask on every poll. On Linux (the test container) the /proc files are read instead. Nothing here is
user input; a failing probe leaves its field out rather than breaking the page.
"""
import os
import re
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path

CACHE_SECONDS = 30
_cache = {"at": 0, "data": None}
_lock = threading.Lock()
MAC = sys.platform == "darwin"


def _run(argv, timeout=4):
    try:
        p = subprocess.run(argv, capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired):
        return ""
    return p.stdout if p.returncode == 0 else ""


def disk(path=None):
    try:
        u = shutil.disk_usage(path or str(Path.home()))
    except OSError:
        return {}
    return {"total": u.total, "free": u.free, "used": u.total - u.free}


def memory():
    """Fraction of memory free (0..1) and total bytes."""
    if MAC:
        out = _run(["/usr/bin/vm_stat"])
        m = re.search(r"page size of (\d+)", out)
        page = int(m.group(1)) if m else 4096
        pages = {k.strip(): int(v.strip(" .")) for k, v in
                 (line.split(":", 1) for line in out.splitlines() if ":" in line and line.strip()[-1] == ".")}
        free = (pages.get("Pages free", 0) + pages.get("Pages inactive", 0) + pages.get("Pages speculative", 0)) * page
        total_s = _run(["/usr/sbin/sysctl", "-n", "hw.memsize"]).strip()
        total = int(total_s) if total_s.isdigit() else 0
        if not total:
            return {}
        return {"total": total, "free_pct": round(100 * free / total)}
    try:
        info = dict(line.split(":", 1) for line in Path("/proc/meminfo").read_text().splitlines() if ":" in line)
        kb = lambda k: int(info[k].split()[0]) * 1024  # noqa: E731
        total = kb("MemTotal")
        return {"total": total, "free_pct": round(100 * kb("MemAvailable") / total)} if total else {}
    except (OSError, KeyError, ValueError):
        return {}


def swap():
    """Swap in use, bytes."""
    if MAC:
        out = _run(["/usr/sbin/sysctl", "-n", "vm.swapusage"])
        m = re.search(r"used = ([\d.]+)([MGK])", out)
        if not m:
            return {}
        mult = {"K": 1e3, "M": 1e6, "G": 1e9}[m.group(2)]
        return {"used": int(float(m.group(1)) * mult)}
    try:
        info = dict(line.split(":", 1) for line in Path("/proc/meminfo").read_text().splitlines() if ":" in line)
        total = int(info["SwapTotal"].split()[0]) * 1024
        free = int(info["SwapFree"].split()[0]) * 1024
        return {"used": total - free}
    except (OSError, KeyError, ValueError):
        return {}


def cpu():
    """Load over the last minute as a percentage of the cores."""
    try:
        load = os.getloadavg()[0]
    except (OSError, AttributeError):
        return {}
    cores = os.cpu_count() or 1
    return {"pct": round(100 * load / cores, 1), "load": round(load, 2), "cores": cores}


def uptime():
    if MAC:
        out = _run(["/usr/sbin/sysctl", "-n", "kern.boottime"])
        m = re.search(r"sec = (\d+)", out)
        return {"seconds": int(time.time() - int(m.group(1)))} if m else {}
    try:
        return {"seconds": int(float(Path("/proc/uptime").read_text().split()[0]))}
    except (OSError, ValueError, IndexError):
        return {}


def backup():
    """When Time Machine last finished, if the Mac has it set up."""
    if not MAC or not os.path.exists("/usr/bin/tmutil"):
        return {}
    out = _run(["/usr/bin/tmutil", "latestbackup"], timeout=6).strip()
    m = re.search(r"(\d{4})-(\d{2})-(\d{2})-(\d{2})(\d{2})(\d{2})", out)
    if not m:
        return {}
    y, mo, d, h, mi, s = (int(x) for x in m.groups())
    try:
        ts = time.mktime((y, mo, d, h, mi, s, 0, 0, -1))
    except (OverflowError, ValueError):
        return {}
    return {"last": ts}


def temperature():
    """CPU temperature when a sensor can be read without sudo (osx-cpu-temp, if installed)."""
    exe = shutil.which("osx-cpu-temp")
    if not exe:
        return {}
    m = re.search(r"([\d.]+)\s*°?C", _run([exe]))
    return {"c": float(m.group(1))} if m else {}


def snapshot(can_free=0):
    """The cached health block. `can_free` is what the disk cleaners could reclaim, from the Vault."""
    with _lock:
        if _cache["data"] and time.time() - _cache["at"] < CACHE_SECONDS:
            data = dict(_cache["data"])
        else:
            data = {"disk": disk(), "memory": memory(), "swap": swap(), "cpu": cpu(), "uptime": uptime(),
                    "backup": backup(), "temperature": temperature(), "at": time.time()}
            _cache.update(at=time.time(), data=data)
    data["can_free"] = can_free
    return data
