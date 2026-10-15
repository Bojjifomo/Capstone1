"""One tracking session (leader + optional follower) shared by the CLI and the web UI."""

from . import copytrade
from .alerts import AlertFormatter, Notifier, fmt_usd
from .compare import Comparator
from .copier import Copier, load_keypair
from .prices import PriceOracle
from .rpc import Rpc
from .tgcontrol import TelegramControl
from .tokens import TokenRegistry
from .vault import VaultTracker, resolve_vault


class Session:
    def __init__(self, vault, follower=None, rpc_url=None, rpc_delay=0.05, copy_mode="mirror",
                 history=300, use_gpa=False, state_dir=".vault_tracker", alert_types=None, min_usd=5.0,
                 name=None, copy_settings=None, copy_keypair=None, compare=None, compare_name=None,
                 compare_window=600, compare_sol_only=False):
        copytrade.parse_mode(copy_mode)  # raises ValueError on a bad mode
        self.copy_mode = copy_mode
        self.history = history
        self.rpc = Rpc(rpc_url, min_interval=rpc_delay)
        vault, self.vault_note = resolve_vault(self.rpc, vault)
        tokens = TokenRegistry(self.rpc)
        prices = PriceOracle(self.rpc, tokens)
        self.leader = VaultTracker(self.rpc, tokens, prices, vault, "leader", state_dir, use_gpa=use_gpa)
        if name:
            self.leader.display = name.upper()
        self.follower = None
        if follower:
            self.follower = VaultTracker(self.rpc, tokens, prices, follower, "follower", state_dir,
                                         use_gpa=use_gpa)
        # a second wallet to hold the leader against (e.g. the wallet you copy instead of the vault)
        self.compare, self.comparator = None, None
        if compare and compare != self.leader.address:
            self.compare = VaultTracker(self.rpc, tokens, prices, compare, "compare", state_dir, use_gpa=use_gpa)
            self.compare.display = (compare_name or "WALLET").upper()
            self.comparator = Comparator(self.leader, self.compare, compare_window, state_dir, compare_sol_only)
        # follower first, so leader alerts can say whether you already copied
        self.trackers = [t for t in (self.follower, self.leader, self.compare) if t]
        self.notifier = Notifier(alert_types, min_usd=min_usd)
        self.formatter = AlertFormatter(self.leader, self.follower, copy_mode)
        self.copier = None
        self.tg_control = None
        if copy_settings and copy_settings.get("run", "off") != "off":
            kp = load_keypair(copy_keypair) if copy_keypair else None
            self.copier = Copier(self.rpc, tokens, prices, copy_settings, copy_mode, state_dir,
                                 keypair=kp, sim_wallet=follower)
            if self.copier.s["tg_commands"] and self.notifier.tg_token and self.notifier.tg_chat:
                self.tg_control = TelegramControl(self.copier, self.notifier.tg_token, self.notifier.tg_chat)
                self.tg_control.start()

    def close(self):
        if self.tg_control:
            self.tg_control.stop()

    @property
    def follower_label(self):
        return self.follower.label if self.follower else None

    def cycle(self, first=False, on_progress=None):
        """Poll + refresh every tracker, send alerts. Returns [(title, lines, event)] sent."""
        pending = []
        for t in self.trackers:
            if first and not t.bootstrapped:
                t.bootstrap(self.history, (lambda i, n, t=t: on_progress(t, i, n)) if on_progress else None)
            else:
                pending += t.poll_transactions()
        for t in self.trackers:
            _, ev = t.refresh(emit=not first)
            pending += ev
            t.save()
        sent = []
        for ev in sorted(pending, key=lambda e: e.get("ts") or 0):
            if ev.get("wallet") == "compare":
                continue  # the comparator reports on this wallet
            title, lines = self.formatter.format(ev)
            if not self.notifier.wants(ev, self.follower_label):
                continue
            self.notifier.send(title, lines, ev)
            sent.append((title, lines, ev))
        if self.copier:  # copy the leader into the bot wallet, then your own exits (TP / SL / out of range)
            for title, lines in self.copier.handle(pending, self.leader) + self.copier.check():
                self.notifier.send(title, lines, {"type": "COPY"})
                sent.append((title, lines, {"type": "COPY"}))
        if self.comparator:
            for title, lines in self.comparator.update():
                self.notifier.send(title, lines, {"type": "COMPARE"})
                sent.append((title, lines, {"type": "COMPARE"}))
        return sent

    def announce(self):
        if not self.notifier.channels:
            return
        snap = self.leader.snapshot or {"positions": [], "total_usd": 0}
        self.notifier.send("👀 Vault tracker aktif", [
            f"{self.leader.display}: {self.leader.address}",
            f"Copy mode: {self.copy_mode}",
            f"Copytrade bot: {self.copier.s['run'].upper() if self.copier else 'OFF'}"
            + (f" · wallet {self.copier.wallet}" if self.copier and self.copier.wallet else ""),
            *(["Kontrol dari Telegram aktif: kirim /help ke bot ini."] if self.tg_control else []),
            f"Posisi terbuka: {len(snap['positions'])} · nilai {fmt_usd(snap['total_usd'])}",
        ])
