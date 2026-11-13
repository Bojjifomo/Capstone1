"""tools/after-ssd: the SSD wait wrapper and its opt-in PREFLIGHT check."""
import os
import shutil
import subprocess
import tempfile
import time
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent.parent / "tools" / "after-ssd"

BROKEN = """#!/bin/sh
if [ "$1" = "--version" ]; then
  echo "dyld[123]: Library not loaded: /opt/homebrew/opt/simdjson/lib/libsimdjson.30.dylib" >&2
  echo "  Referenced from: /opt/homebrew/opt/node@22/bin/node" >&2
  echo "  Reason: tried: '/opt/homebrew/opt/simdjson/lib/libsimdjson.30.dylib' (no such file)" >&2
  exit 1
fi
echo ran > "$MARKER"
"""

HEALTHY = """#!/bin/sh
if [ "$1" = "--version" ]; then echo v22.0.0; exit 0; fi
echo "$PWD|$*" > "$MARKER"
"""


class AfterSsd(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp()).resolve()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        (self.tmp / "vol" / "work" / "Capstone1").mkdir(parents=True)
        self.work = self.tmp / "work"
        self.work.mkdir()
        self.marker = self.tmp / "marker"

    def fake(self, body):
        p = self.tmp / "fakebin"
        p.write_text(body)
        p.chmod(0o755)
        return p

    def run_wrapper(self, fake, preflight):
        env = dict(os.environ, AFTER_SSD_VOL=str(self.tmp / "vol"),
                   PREFLIGHT_BACKOFF="1", MARKER=str(self.marker))
        env.pop("PREFLIGHT", None)
        if preflight:
            env["PREFLIGHT"] = "1"
        t = time.time()
        r = subprocess.run(["sh", str(SCRIPT), str(self.work), str(fake), "run", "serve"],
                           env=env, capture_output=True, text=True, timeout=30)
        return r, time.time() - t

    def test_broken_binary_logs_one_line_and_backs_off(self):
        r, took = self.run_wrapper(self.fake(BROKEN), True)
        self.assertNotEqual(r.returncode, 0)
        lines = (r.stderr + r.stdout).splitlines()
        self.assertEqual(len(lines), 1, lines)
        self.assertIn("dyld", lines[0])
        self.assertIn("brew reinstall node@22", lines[0])
        self.assertGreaterEqual(took, 0.9)
        self.assertFalse(self.marker.exists())

    def test_healthy_binary_runs_in_workdir_with_args(self):
        r, _ = self.run_wrapper(self.fake(HEALTHY), True)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.marker.read_text().strip(), f"{self.work}|run serve")

    def test_without_preflight_no_version_check(self):
        r, took = self.run_wrapper(self.fake(BROKEN), False)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stderr, "")
        self.assertTrue(self.marker.exists())
        self.assertLess(took, 1)


if __name__ == "__main__":
    unittest.main()
