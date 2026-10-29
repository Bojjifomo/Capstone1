"""Where the watts come from.

macmon (brew install macmon) reads the SMC like iStat does, needs no sudo, and gives
`sys_power`: the whole Mac mini, close to what a wall meter shows. powermetrics ships
with macOS but needs root and only knows the chip (CPU + GPU + ANE), so it reads lower
than the wall. "fake" is for tests and for trying the page on a machine that is not a Mac.
"""
import json
import math
import os
import plistlib
import random
import shutil
import subprocess
import time

MACMON_PATHS = ["/opt/homebrew/bin/macmon", "/usr/local/bin/macmon"]


def _num(v):
    """macmon gives usage as [freq_mhz, fraction] and power as a float; take what's there."""
    if isinstance(v, (list, tuple)):
        v = v[-1] if v else None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) else None


def parse_macmon(line):
    """One `macmon pipe` JSON line -> sample dict (watts), or None for junk."""
    try:
        d = json.loads(line)
    except (ValueError, TypeError):
        return None
    if not isinstance(d, dict):
        return None
    cpu, gpu, ane = _num(d.get("cpu_power")), _num(d.get("gpu_power")), _num(d.get("ane_power"))
    chip = _num(d.get("all_power"))
    if chip is None and cpu is not None:
        chip = cpu + (gpu or 0) + (ane or 0)
    system = _num(d.get("sys_power"))
    total = system if system and system > 0 else chip
    if total is None:
        return None
    temp = d.get("temp") or {}
    usage = [_num(d.get(k)) for k in ("ecpu_usage", "pcpu_usage")]
    usage = [u for u in usage if u is not None]
    return {
        "watts": total,
        "system_w": system,
        "chip_w": chip,
        "cpu_w": cpu,
        "gpu_w": gpu,
        "ane_w": ane,
        "ram_w": _num(d.get("ram_power")),
        "cpu_temp": _num(temp.get("cpu_temp_avg")) if isinstance(temp, dict) else None,
        "cpu_usage": max(usage) if usage else None,
        "gpu_usage": _num(d.get("gpu_usage")),
    }


def parse_powermetrics(blob):
    """One powermetrics plist document (bytes) -> sample dict (watts), or None."""
    blob = blob.strip(b"\0\n\r\t ")
    if not blob:
        return None
    try:
        d = plistlib.loads(blob)
    except Exception:
        return None
    p = d.get("processor") or {}
    mw = lambda k: (_num(p.get(k)) / 1000.0) if _num(p.get(k)) is not None else None  # noqa: E731
    cpu, gpu, ane, chip = mw("cpu_power"), mw("gpu_power"), mw("ane_power"), mw("combined_power")
    if gpu is None and isinstance(d.get("gpu"), dict):
        g = _num(d["gpu"].get("gpu_energy"))
        gpu = g / 1000.0 if g is not None else None
    if chip is None and cpu is not None:
        chip = cpu + (gpu or 0) + (ane or 0)
    if chip is None:
        return None
    return {"watts": chip, "system_w": None, "chip_w": chip, "cpu_w": cpu, "gpu_w": gpu,
            "ane_w": ane, "ram_w": None, "cpu_temp": None, "cpu_usage": None, "gpu_usage": None}


def find_macmon():
    found = shutil.which("macmon")
    if found:
        return found
    for p in MACMON_PATHS:
        if shutil.which(p):
            return p
    return None


def macmon_stream(interval_ms, binary=None):
    binary = binary or find_macmon()
    proc = subprocess.Popen([binary, "pipe", "-i", str(interval_ms)], stdout=subprocess.PIPE,
                            stderr=subprocess.DEVNULL, text=True, bufsize=1)
    try:
        for line in proc.stdout:
            s = parse_macmon(line)
            if s:
                yield s
    finally:
        proc.kill()
    raise RuntimeError("macmon exited (code %s)" % proc.wait())


def powermetrics_stream(interval_ms):
    cmd = ["/usr/bin/powermetrics", "--samplers", "cpu_power,gpu_power", "-i", str(interval_ms),
           "-f", "plist"]
    if os.geteuid() != 0:
        cmd = ["sudo", "-n"] + cmd   # needs the sudoers line from the README
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    buf = b""
    try:
        while True:
            chunk = proc.stdout.read1(65536) if hasattr(proc.stdout, "read1") else proc.stdout.read(4096)
            if not chunk:
                break
            buf += chunk
            while b"\0" in buf:
                doc, buf = buf.split(b"\0", 1)
                s = parse_powermetrics(doc)
                if s:
                    yield s
    finally:
        proc.kill()
    err = (proc.stderr.read() or b"").decode(errors="replace").strip()
    raise RuntimeError("powermetrics exited (code %s) %s" % (proc.wait(), err[:200]))


def fake_stream(interval_ms):
    """Idle around 6 W with bursts of work up to ~40 W."""
    t0 = time.time()
    while True:
        t = time.time() - t0
        busy = 0.5 + 0.5 * math.sin(t / 40.0)
        cpu = max(0.1, busy ** 3 * 28 + random.uniform(-0.5, 0.5))
        gpu = max(0.0, busy ** 4 * 8 + random.uniform(-0.2, 0.2))
        chip = cpu + gpu
        yield {"watts": chip + 5.2, "system_w": chip + 5.2, "chip_w": chip, "cpu_w": cpu,
               "gpu_w": gpu, "ane_w": 0.0, "ram_w": 0.3, "cpu_temp": 40 + busy * 30,
               "cpu_usage": busy, "gpu_usage": busy ** 2}
        time.sleep(interval_ms / 1000.0)


def open_stream(name, interval_ms):
    """-> (resolved source name, generator). 'auto' picks macmon, then powermetrics."""
    if name == "auto":
        name = "macmon" if find_macmon() else "powermetrics"
    if name == "macmon":
        return name, macmon_stream(interval_ms)
    if name == "powermetrics":
        return name, powermetrics_stream(interval_ms)
    if name == "fake":
        return name, fake_stream(interval_ms)
    raise ValueError("unknown source: %s" % name)
