"""install.sh end to end in a sandbox: fake HOME, fake launchctl and pgrep. Run: python3 -m unittest discover -s lp_call_bot/tests"""
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]


class InstallTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="lpbot-install-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        # a repo on an "external disk" with a space in the path, like /Volumes/SSD 1TB/work/Capstone1
        self.repo = self.tmp / "SSD 1TB" / "work" / "Capstone1"
        shutil.copytree(REPO / "lp_call_bot", self.repo / "lp_call_bot", ignore=shutil.ignore_patterns("__pycache__"))
        shutil.copytree(REPO / "rh_wallet_finder", self.repo / "rh_wallet_finder",
                        ignore=shutil.ignore_patterns("__pycache__", "data", "web", "ios", "assets", "tests"))
        (self.repo / "lp_call_bot" / ".env").write_text("TELEGRAM_BOT_TOKEN=secret-token\nOWNER_IDS=1\n")
        (self.repo / "lp_call_bot" / "state.json").write_text('{"subscribers": [7, 8]}')
        (self.repo / "rh_wallet_finder" / ".env").write_text("RPC_URL=do-not-copy-me\n")
        self.home = self.tmp / "home"
        self.installed = self.home / "Projects" / "lp_call_bot"
        bin_dir = self.tmp / "bin"
        bin_dir.mkdir()
        for name, code in (("launchctl", 'case "$1" in print) exit 1;; esac; exit 0'), ("pgrep", "exit 1")):
            (bin_dir / name).write_text("#!/bin/sh\n" + code + "\n")
            (bin_dir / name).chmod(0o755)
        self.env = dict(os.environ, HOME=str(self.home), PATH=str(bin_dir) + os.pathsep + os.environ["PATH"])
        self.env.pop("LP_CALL_BOT_HOME", None)

    def install(self, *args):
        return subprocess.run(["sh", str(self.repo / "lp_call_bot" / "install.sh"), *args], env=self.env,
                              capture_output=True, text=True, timeout=60)

    def test_install_runs_from_home_and_imports(self):
        r = self.install()
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("Jalan dari: " + str(self.installed), r.stdout)
        plist = (self.home / "Library" / "LaunchAgents" / "com.bojjifomo.lp-call-bot.plist").read_text()
        self.assertNotIn("SSD 1TB", plist, "launchd must not point at the external disk")
        self.assertIn(str(self.installed / "bot.py"), plist)
        # the four shared modules came along, and nothing secret from rh_wallet_finder did
        shared = sorted(p.name for p in (self.installed / "rh_wallet_finder").iterdir())
        self.assertEqual(shared, ["chain.py", "config.py", "keccak.py", "rpc.py"])
        # the bot starts: this is the ModuleNotFoundError: No module named 'chain' from the Mac mini
        out = subprocess.run([sys.executable, "-c", "import watcher, bot; print(watcher.C.__name__, watcher.RH.__name__)"],
                             cwd=self.installed, capture_output=True, text=True, timeout=30)
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertIn("chain config", out.stdout)

    def test_update_keeps_secrets_state_and_heartbeat(self):
        self.assertEqual(self.install().returncode, 0)
        (self.installed / ".env").write_text("TELEGRAM_BOT_TOKEN=changed\n")
        (self.installed / "state.json").write_text('{"subscribers": [7, 8, 9]}')
        (self.installed / "heartbeat").write_text("")
        self.assertEqual(self.install().returncode, 0)
        self.assertEqual((self.installed / ".env").read_text(), "TELEGRAM_BOT_TOKEN=changed\n")
        self.assertEqual((self.installed / "state.json").read_text(), '{"subscribers": [7, 8, 9]}')
        self.assertTrue((self.installed / "heartbeat").exists())
        self.assertEqual(oct((self.installed / ".env").stat().st_mode & 0o777), "0o600")

    def test_first_install_copies_state_once(self):
        self.install()
        self.assertEqual((self.installed / "state.json").read_text(), '{"subscribers": [7, 8]}')

    def test_needs_the_repo_layout(self):
        shutil.rmtree(self.repo / "rh_wallet_finder")
        r = self.install()
        self.assertEqual(r.returncode, 1)
        self.assertIn("rh_wallet_finder", r.stderr)

    def test_uninstall(self):
        self.install()
        r = self.install("uninstall")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertFalse((self.home / "Library" / "LaunchAgents" / "com.bojjifomo.lp-call-bot.plist").exists())


if __name__ == "__main__":
    unittest.main()
