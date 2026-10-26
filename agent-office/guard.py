"""One-time codes sent to the phone for risky actions. Memory only, hashed, short-lived."""
import hashlib
import hmac
import secrets
import sys
import threading
import time

TTL = 120
MAX_TRIES = 3
MAX_PER_10MIN = 6


class Codes:
    def __init__(self, deliver, dev=False):
        self.deliver = deliver  # deliver(title, body) -> bool
        self.dev = dev
        self.pending = {}
        self.issued = []
        self.lock = threading.Lock()

    def _digest(self, salt, code):
        return hashlib.sha256(salt + code.encode()).digest()

    def request(self, purpose, label):
        """Send a fresh code for `purpose`. Returns a code id, or raises RuntimeError."""
        now = time.time()
        with self.lock:
            self.issued = [t for t in self.issued if now - t < 600]
            if len(self.issued) >= MAX_PER_10MIN:
                raise RuntimeError("too many codes asked for; wait a few minutes")
            for cid in [c for c, e in self.pending.items() if e["expires"] < now]:
                del self.pending[cid]
            code = "%06d" % secrets.randbelow(10 ** 6)
            cid = secrets.token_urlsafe(12)
            salt = secrets.token_bytes(16)
            self.pending[cid] = {"hash": self._digest(salt, code), "salt": salt, "purpose": purpose,
                                 "expires": now + TTL, "tries": 0}
            self.issued.append(now)
        if self.dev:
            sys.stderr.write("[dev] seal code for %s: %s\n" % (purpose, code))
        if not self.deliver("Guild seal: " + code, "Use it within 2 minutes to " + label + ".") and not self.dev:
            with self.lock:
                self.pending.pop(cid, None)
            raise RuntimeError("could not reach your phone; turn on ravens (push) first")
        return cid

    def verify(self, cid, code, purpose):
        with self.lock:
            entry = self.pending.get(str(cid))
            if not entry:
                return False
            if entry["expires"] < time.time() or entry["purpose"] != purpose:
                del self.pending[cid]
                return False
            entry["tries"] += 1
            ok = hmac.compare_digest(entry["hash"], self._digest(entry["salt"], str(code).strip()))
            if ok or entry["tries"] >= MAX_TRIES:
                del self.pending[cid]
            return ok
