"""Web Push (VAPID + RFC 8291 aes128gcm) with the standard library only.

P-256, ECDSA, AES-128-GCM and HKDF are written out here because Python's stdlib has none
of them. Payloads are tiny (a title and a line of text), so speed doesn't matter.
"""
import base64
import hashlib
import hmac
import json
import secrets
import struct
import time
import urllib.error
import urllib.request
from urllib.parse import urlsplit

import store

# Only real browser push services; the server never POSTs anywhere else.
PUSH_HOSTS = ("web.push.apple.com", "fcm.googleapis.com", "updates.push.services.mozilla.com",
              "push.services.mozilla.com", ".notify.windows.com")


def b64u(data):
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def unb64u(text):
    text = str(text)
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


# ---- P-256 --------------------------------------------------------------

P = 0xFFFFFFFF00000001000000000000000000000000FFFFFFFFFFFFFFFFFFFFFFFF
A = P - 3
B = 0x5AC635D8AA3A93E7B3EBBD55769886BC651D06B0CC53B0F63BCE3C3E27D2604B
N = 0xFFFFFFFF00000000FFFFFFFFFFFFFFFFBCE6FAADA7179E84F3B9CAC2FC632551
G = (0x6B17D1F2E12C4247F8BCE6E563A440F277037D812DEB33A0F4A13945D898C296,
     0x4FE342E2FE1A7F9B8EE7EB4A7C0F9E162BCE33576B315ECECBB6406837BF51F5)


def _add(p1, p2):
    if p1 is None:
        return p2
    if p2 is None:
        return p1
    (x1, y1), (x2, y2) = p1, p2
    if x1 == x2 and (y1 + y2) % P == 0:
        return None
    if p1 == p2:
        lam = (3 * x1 * x1 + A) * pow(2 * y1, -1, P) % P
    else:
        lam = (y2 - y1) * pow(x2 - x1, -1, P) % P
    x3 = (lam * lam - x1 - x2) % P
    return x3, (lam * (x1 - x3) - y1) % P


def _mul(k, point):
    result = None
    while k:
        if k & 1:
            result = _add(result, point)
        point = _add(point, point)
        k >>= 1
    return result


def encode_point(pt):
    return b"\x04" + pt[0].to_bytes(32, "big") + pt[1].to_bytes(32, "big")


def decode_point(raw):
    if len(raw) != 65 or raw[0] != 4:
        raise ValueError("not an uncompressed P-256 point")
    x, y = int.from_bytes(raw[1:33], "big"), int.from_bytes(raw[33:], "big")
    if (y * y - (x * x * x + A * x + B)) % P:
        raise ValueError("point is not on the curve")
    return x, y


def new_private():
    return secrets.randbelow(N - 1) + 1


def public_of(priv):
    return encode_point(_mul(priv, G))


def ecdh(priv, peer_raw):
    return _mul(priv, decode_point(peer_raw))[0].to_bytes(32, "big")


def ecdsa_sign(priv, message):
    z = int.from_bytes(hashlib.sha256(message).digest(), "big")
    while True:
        k = secrets.randbelow(N - 1) + 1
        r = _mul(k, G)[0] % N
        s = pow(k, -1, N) * (z + r * priv) % N
        if r and s:
            return r.to_bytes(32, "big") + s.to_bytes(32, "big")


# ---- AES-128-GCM ----------------------------------------------------------

def _xtime(a):
    return ((a << 1) ^ 0x1B) & 0xFF if a & 0x80 else a << 1


def _make_sbox():
    sbox = [0] * 256
    p = q = 1
    rotl = lambda x, s: ((x << s) | (x >> (8 - s))) & 0xFF  # noqa: E731
    while True:
        p = p ^ _xtime(p)  # p * 3
        q ^= q << 1
        q ^= q << 2
        q ^= q << 4
        q &= 0xFF
        if q & 0x80:
            q ^= 0x09
        sbox[p] = q ^ rotl(q, 1) ^ rotl(q, 2) ^ rotl(q, 3) ^ rotl(q, 4) ^ 0x63
        if p == 1:
            break
    sbox[0] = 0x63
    return sbox


SBOX = _make_sbox()


def _expand(key):
    w = [list(key[i:i + 4]) for i in range(0, 16, 4)]
    rcon = 1
    for i in range(4, 44):
        t = list(w[i - 1])
        if i % 4 == 0:
            t = [SBOX[x] for x in t[1:] + t[:1]]
            t[0] ^= rcon
            rcon = _xtime(rcon)
        w.append([a ^ b for a, b in zip(w[i - 4], t)])
    return [sum(w[4 * r:4 * r + 4], []) for r in range(11)]


def _encrypt_block(rk, block):
    s = [b ^ k for b, k in zip(block, rk[0])]
    for r in range(1, 11):
        s = [SBOX[b] for b in s]
        s = [s[((i // 4 + i % 4) % 4) * 4 + i % 4] for i in range(16)]
        if r != 10:
            mixed = []
            for c in range(0, 16, 4):
                a0, a1, a2, a3 = s[c:c + 4]
                x = a0 ^ a1 ^ a2 ^ a3
                mixed += [a0 ^ x ^ _xtime(a0 ^ a1), a1 ^ x ^ _xtime(a1 ^ a2),
                          a2 ^ x ^ _xtime(a2 ^ a3), a3 ^ x ^ _xtime(a3 ^ a0)]
            s = mixed
        s = [b ^ k for b, k in zip(s, rk[r])]
    return bytes(s)


def _gf_mult(x, y):
    z, v = 0, y
    for i in range(127, -1, -1):
        if (x >> i) & 1:
            z ^= v
        v = (v >> 1) ^ (0xE1 << 120) if v & 1 else v >> 1
    return z


def aes_gcm_encrypt(key, iv, plaintext, aad=b""):
    rk = _expand(key)
    h = int.from_bytes(_encrypt_block(rk, bytes(16)), "big")
    j0 = iv + b"\x00\x00\x00\x01"
    counter = int.from_bytes(j0, "big")
    out = bytearray()
    for i in range(0, len(plaintext), 16):
        counter = (counter & ~0xFFFFFFFF) | ((counter + 1) & 0xFFFFFFFF)
        stream = _encrypt_block(rk, counter.to_bytes(16, "big"))
        out += bytes(a ^ b for a, b in zip(plaintext[i:i + 16], stream))
    pad = lambda b: b + bytes(-len(b) % 16)  # noqa: E731
    data = pad(aad) + pad(bytes(out)) + struct.pack(">QQ", len(aad) * 8, len(out) * 8)
    s = 0
    for i in range(0, len(data), 16):
        s = _gf_mult(s ^ int.from_bytes(data[i:i + 16], "big"), h)
    tag = int.from_bytes(_encrypt_block(rk, j0), "big") ^ s
    return bytes(out) + tag.to_bytes(16, "big")


# ---- RFC 8291 + VAPID -----------------------------------------------------

def hkdf(salt, ikm, info, length):
    prk = hmac.new(salt, ikm, hashlib.sha256).digest()
    return hmac.new(prk, info + b"\x01", hashlib.sha256).digest()[:length]


def encrypt(payload, ua_public, auth_secret, as_private=None, salt=None):
    as_private = as_private or new_private()
    as_public = public_of(as_private)
    salt = salt or secrets.token_bytes(16)
    secret = ecdh(as_private, ua_public)
    ikm = hkdf(auth_secret, secret, b"WebPush: info\x00" + ua_public + as_public, 32)
    cek = hkdf(salt, ikm, b"Content-Encoding: aes128gcm\x00", 16)
    nonce = hkdf(salt, ikm, b"Content-Encoding: nonce\x00", 12)
    body = aes_gcm_encrypt(cek, nonce, payload + b"\x02")
    return salt + struct.pack(">IB", 4096, 65) + as_public + body


def vapid_header(endpoint, priv, subject):
    parts = urlsplit(endpoint)
    header = b64u(json.dumps({"typ": "JWT", "alg": "ES256"}).encode())
    claims = b64u(json.dumps({"aud": "%s://%s" % (parts.scheme, parts.netloc),
                              "exp": int(time.time()) + 12 * 3600, "sub": subject}).encode())
    signing = (header + "." + claims).encode()
    token = header + "." + claims + "." + b64u(ecdsa_sign(priv, signing))
    return "vapid t=%s, k=%s" % (token, b64u(public_of(priv)))


def endpoint_allowed(endpoint):
    parts = urlsplit(str(endpoint))
    host = (parts.hostname or "").lower()
    return parts.scheme == "https" and any(host == h or (h.startswith(".") and host.endswith(h)) for h in PUSH_HOSTS)


class Pusher:
    """One subscription (your phone). Keys live in vapid.json, the subscription in push.json."""

    def __init__(self, subject):
        self.subject = subject
        keys = store.load("vapid.json", None)
        if not keys:
            priv = new_private()
            keys = {"private": "%064x" % priv, "public": b64u(public_of(priv))}
            store.save("vapid.json", keys, private=True)
        self.priv = int(keys["private"], 16)
        self.public = keys["public"]
        self.sub = store.load("push.json", {}).get("subscription")
        self.last = None

    @property
    def subscribed(self):
        return bool(self.sub)

    def subscribe(self, sub):
        endpoint = sub.get("endpoint") if isinstance(sub, dict) else None
        keys = sub.get("keys") if isinstance(sub, dict) else None
        if not endpoint_allowed(endpoint) or not isinstance(keys, dict):
            raise ValueError("not a browser push subscription")
        decode_point(unb64u(keys.get("p256dh", "")))
        if len(unb64u(keys.get("auth", ""))) != 16:
            raise ValueError("bad auth secret")
        self.sub = {"endpoint": endpoint, "keys": {"p256dh": keys["p256dh"], "auth": keys["auth"]}}
        store.save("push.json", {"subscription": self.sub, "since": time.time()}, private=True)

    def unsubscribe(self):
        self.sub = None
        store.save("push.json", {}, private=True)

    def send(self, title, body, tag="office", url="/", urgency="normal"):
        """Returns True if the push service accepted it."""
        self.last = {"title": title, "body": body, "tag": tag, "url": url, "ts": time.time()}
        sub = self.sub
        if not sub:
            return False
        payload = json.dumps(self.last).encode()[:3000]
        data = encrypt(payload, unb64u(sub["keys"]["p256dh"]), unb64u(sub["keys"]["auth"]))
        req = urllib.request.Request(sub["endpoint"], data=data, method="POST", headers={
            "Authorization": vapid_header(sub["endpoint"], self.priv, self.subject),
            "Content-Encoding": "aes128gcm", "Content-Type": "application/octet-stream",
            "TTL": "3600", "Urgency": urgency, "Topic": "".join(c for c in tag if c.isalnum())[:32] or "office",
        })
        try:
            with urllib.request.urlopen(req, timeout=10) as res:
                return 200 <= res.status < 300
        except urllib.error.HTTPError as e:
            if e.code in (404, 410):  # the phone dropped the subscription
                self.unsubscribe()
            return False
        except (urllib.error.URLError, OSError):
            return False
