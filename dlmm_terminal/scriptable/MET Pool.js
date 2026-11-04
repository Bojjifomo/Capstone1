// Variables used by Scriptable.
// These must be at the very top of the file. Do not edit.
// icon-color: deep-orange; icon-glyph: meteor;
// ============================================================================
//  MET Pool : Meteora DLMM pool finder and SOL-only zap : Solana
//
//  *** WITH A PRIVATE KEY IN "MET Secrets", THIS SCRIPT SPENDS MONEY. ***
//
//  Ranks Meteora DLMM pools paired with SOL by fee yield, and gives every row
//  zap buttons that open a SOL-only ladder with a preset you set once. With no
//  key it is a read-only pool finder.
//
//  Fee yield = the pool's 24h fees / TVL, per day, straight from Meteora's
//  data API, which also supplies TVL, volume, bin step and fees. DexScreener
//  adds the price changes behind the 24h sparkline. Nothing here needs a key
//  to read; Helius (or any Solana RPC) is only needed to open positions.
//
//  A zap is SOL only: SOL can only sit on its own side of the price, so the
//  ladder waits below the current price and buys the token as it falls. You
//  pick the shape when you confirm: Spot (flat), Curve (heaviest next to the
//  price) or Bid-Ask (heaviest furthest out). Slices split one deposit into
//  several positions stepping 1:2:...:n away from the price, like RH, with
//  the shape drawn inside every slice (MET.ladderShape).
//
//  SETUP:
//   1. Put "MET Secrets" in Scriptable and fill in your keys (see that file).
//   2. Put this file in Scriptable as "MET Pool".
//   3. Home Screen > add a Scriptable widget (medium or large) running it.
//      Parameter is optional: a market-cap floor such as 300K or 2M.
//   4. Tap the widget for the full table. Tap a row for DexScreener.
// ============================================================================

// Ultra-early crash net: a widget that shows the failure instead of a blank.
if (config.runsInWidget) {
  try {
    const _f = new ListWidget();
    const _g = new LinearGradient();
    _g.colors = [new Color("#1E1F35"), new Color("#12121C")];
    _g.locations = [0, 1];
    _f.backgroundGradient = _g;
    _f.setPadding(16, 17, 16, 17);
    const _h = _f.addText("☄️ SOL Pools");
    _h.font = Font.boldSystemFont(14);
    _h.textColor = new Color("#F5F5FF");
    _f.addSpacer(6);
    const _m = _f.addText("Script crashed before the widget was built. Tap to open in the app.");
    _m.font = Font.systemFont(11);
    _m.textColor = new Color("#FF453A");
    _f.url = "scriptable:///run/" + encodeURIComponent(Script.name());
    Script.setWidget(_f);
  } catch (e) {}
}

// ----------------------------- CONFIG ----------------------------------------
// Keys and presets live in a separate "MET Secrets" script, so pasting a new
// build of this file over the old one never touches them.
const SECRETS = (() => {
  if (typeof importModule !== "function") return {};
  for (const name of ["MET Secrets", "METSecrets"]) {
    try {
      const m = importModule(name);
      if (m && typeof m === "object") return m;
    } catch (e) {}
  }
  return {};
})();
const secret = name => {
  const v = SECRETS[name];
  return v == null ? "" : String(v).trim();
};

const VERSION = "met-pool-v7";

// Zap presets, in SOL. Checked rather than trusted: a typo that reached the
// planner would place a ladder nobody asked for, and on Solana every slice is
// its own rent-paying account.
//   amount    SOL to deposit
//   slices    how many positions to split it into (1 = one position)
//   top       where the ladder starts, % below the price (0 = at the price)
//   range     where it ends, % below the price
//   strategy  "spot", "curve" or "bidask": the shape offered first
const STRATEGIES = ["spot", "curve", "bidask"];
const STRATEGY_NAME = { spot: "Spot", curve: "Curve", bidask: "Bid-Ask" };
const STRATEGY_SHORT = { spot: "Spot", curve: "Curve", bidask: "BA" };
function normStrategy(s) {
  const t = String(s || "").toLowerCase().replace(/[^a-z]/g, "");
  if (t === "spot") return "spot";
  if (t === "curve" || t === "curved") return "curve";
  if (t === "bidask" || t === "ba") return "bidask";
  return null;
}
const MAX_SLICES = 20;
const MIN_SLICE_SOL = 0.01;
const ZAP_PRESETS_DEFAULT = [
  { amount: 0.5, slices: 1, top: 0, range: 50, strategy: "bidask" },
  { amount: 1, slices: 3, top: 0, range: 70, strategy: "spot" },
];
const ZAP_PRESETS = (() => {
  const raw = SECRETS.MET_ZAP_PRESETS;
  if (!Array.isArray(raw) || !raw.length) return ZAP_PRESETS_DEFAULT;
  const ok = raw.map(p => p && ({
    amount: Number(p.amount), slices: Math.floor(Number(p.slices == null ? 1 : p.slices)),
    top: Number(p.top) || 0, range: Number(p.range), strategy: normStrategy(p.strategy || "spot"),
  })).filter(p => p && p.amount > 0 && p.slices >= 1 && p.slices <= MAX_SLICES
    && p.range > 0 && p.range < 100 && p.top >= 0 && p.top < p.range && p.strategy
    && p.amount / p.slices >= MIN_SLICE_SOL);
  return ok.length ? ok.slice(0, 3) : ZAP_PRESETS_DEFAULT;
})();

// true  = work the whole thing out and simulate it, send nothing.
// false = really open positions.
const PRACTICE_MODE = typeof SECRETS.PRACTICE_MODE === "boolean" ? SECRETS.PRACTICE_MODE : true;
const DRY_RUN = PRACTICE_MODE;

// =============================================================================
//  MET core: Solana and Meteora DLMM in plain JavaScript, for Scriptable.
//
//  Spliced into MET Pool and MET Portfolio at the //__MET_CORE__ marker by
//  tools/build.mjs, so both scripts share one implementation of everything that
//  can lose money if it is wrong: key handling, signing, account derivation,
//  transaction layout and the DLMM instruction encodings.
//
//  No npm, no WebCrypto: Scriptable runs JavaScriptCore with neither. Every
//  primitive here is checked against Node's own crypto and against mainnet in
//  tests/ (run `node tests/run.mjs`), and nothing is sent anywhere without a
//  simulation of the exact bytes first.
//
//  Sources the encodings follow: @meteora-ag/dlmm 1.9.14 (program 0.12.0 IDL),
//  RFC 8032 (ed25519), FIPS 180-4 (SHA-2), Solana's legacy message format.
// =============================================================================
const MET = (() => {
  "use strict";

  // ------------------------------------------------------------------ bytes --
  function concat(arrs) {
    let n = 0;
    for (const a of arrs) n += a.length;
    const o = new Uint8Array(n);
    let k = 0;
    for (const a of arrs) { o.set(a, k); k += a.length; }
    return o;
  }
  const toHex = b => Array.from(b, x => x.toString(16).padStart(2, "0")).join("");
  function fromHex(h) {
    const s = String(h || "").replace(/^0x/, "");
    const o = new Uint8Array(s.length >> 1);
    for (let i = 0; i < o.length; i++) o[i] = parseInt(s.substr(i * 2, 2), 16);
    return o;
  }
  // TextEncoder is not guaranteed inside Scriptable, and every seed here is
  // ASCII anyway; this covers the rest of UTF-8 for completeness.
  function utf8(s) {
    const out = [];
    for (const ch of String(s)) {
      const c = ch.codePointAt(0);
      if (c < 0x80) out.push(c);
      else if (c < 0x800) out.push(0xc0 | (c >> 6), 0x80 | (c & 63));
      else if (c < 0x10000) out.push(0xe0 | (c >> 12), 0x80 | ((c >> 6) & 63), 0x80 | (c & 63));
      else out.push(0xf0 | (c >> 18), 0x80 | ((c >> 12) & 63), 0x80 | ((c >> 6) & 63), 0x80 | (c & 63));
    }
    return Uint8Array.from(out);
  }
  const eqBytes = (a, b) => a.length === b.length && a.every((x, i) => x === b[i]);

  // Little-endian writers, the way Borsh and the Solana runtime lay numbers out.
  const u8 = v => Uint8Array.of(v & 255);
  const u16 = v => Uint8Array.of(v & 255, (v >>> 8) & 255);
  function u32(v) {
    v = v >>> 0;
    return Uint8Array.of(v & 255, (v >>> 8) & 255, (v >>> 16) & 255, (v >>> 24) & 255);
  }
  const i32 = v => u32(v | 0);
  function u64(v) {
    let n = BigInt(v);
    if (n < 0n) n += 1n << 64n;
    if (n < 0n || n >= (1n << 64n)) throw new Error("u64 out of range: " + v);
    const o = new Uint8Array(8);
    for (let i = 0; i < 8; i++) { o[i] = Number(n & 255n); n >>= 8n; }
    return o;
  }
  const i64 = v => u64(BigInt(v));   // u64() wraps negatives into two's complement

  const rdU16 = (b, o) => b[o] | (b[o + 1] << 8);
  const rdU32 = (b, o) => (b[o] | (b[o + 1] << 8) | (b[o + 2] << 16) | (b[o + 3] << 24)) >>> 0;
  const rdI32 = (b, o) => b[o] | (b[o + 1] << 8) | (b[o + 2] << 16) | (b[o + 3] << 24);
  function rdUint(b, o, n) {
    let v = 0n;
    for (let i = n - 1; i >= 0; i--) v = (v << 8n) | BigInt(b[o + i]);
    return v;
  }
  const rdU64 = (b, o) => rdUint(b, o, 8);
  const rdI64 = (b, o) => { const v = rdU64(b, o); return v >= (1n << 63n) ? v - (1n << 64n) : v; };
  const rdU128 = (b, o) => rdUint(b, o, 16);

  // Solana's compact-u16: seven bits a byte, high bit set on all but the last.
  function shortvec(n) {
    const out = [];
    let v = n;
    for (;;) {
      let e = v & 0x7f;
      v >>= 7;
      if (v === 0) { out.push(e); break; }
      out.push(e | 0x80);
    }
    return Uint8Array.from(out);
  }
  function readShortvec(b, o) {
    let v = 0, size = 0;
    for (;;) {
      const e = b[o + size];
      v |= (e & 0x7f) << (size * 7);
      size++;
      if (!(e & 0x80)) break;
      if (size > 3) throw new Error("bad compact-u16");
    }
    return { value: v, size };
  }

  // ---------------------------------------------------------------- base58 --
  const B58 = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz";
  const B58_MAP = {};
  for (let i = 0; i < B58.length; i++) B58_MAP[B58[i]] = i;
  function b58(bytes) {
    const digits = [];
    for (let i = 0; i < bytes.length; i++) {
      let carry = bytes[i];
      for (let j = 0; j < digits.length; j++) {
        carry += digits[j] * 256;
        digits[j] = carry % 58;
        carry = Math.floor(carry / 58);
      }
      while (carry > 0) { digits.push(carry % 58); carry = Math.floor(carry / 58); }
    }
    let s = "";
    for (let k = 0; k < bytes.length && bytes[k] === 0; k++) s += "1";
    for (let q = digits.length - 1; q >= 0; q--) s += B58[digits[q]];
    return s;
  }
  function unb58(str) {
    const s = String(str || "").trim();
    const bytes = [];
    for (const c of s) {
      const v = B58_MAP[c];
      if (v === undefined) throw new Error("not base58: contains \"" + c + "\"");
      let carry = v;
      for (let j = 0; j < bytes.length; j++) {
        carry += bytes[j] * 58;
        bytes[j] = carry & 255;
        carry >>= 8;
      }
      while (carry > 0) { bytes.push(carry & 255); carry >>= 8; }
    }
    for (let k = 0; k < s.length && s[k] === "1"; k++) bytes.push(0);
    return Uint8Array.from(bytes.reverse());
  }
  // A public key as 32 bytes, from either representation.
  function keyBytes(k) {
    if (k instanceof Uint8Array) {
      if (k.length !== 32) throw new Error("public key must be 32 bytes");
      return k;
    }
    const b = unb58(k);
    if (b.length !== 32) throw new Error("not a 32-byte address: " + String(k).slice(0, 12) + "…");
    return b;
  }
  const isAddress = s => {
    try { return /^[1-9A-HJ-NP-Za-km-z]{32,44}$/.test(String(s)) && unb58(s).length === 32; }
    catch (e) { return false; }
  };

  // ---------------------------------------------------------------- base64 --
  const B64 = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";
  const B64_MAP = {};
  for (let i = 0; i < 64; i++) B64_MAP[B64[i]] = i;
  function b64(bytes) {
    let s = "", i = 0;
    for (; i + 2 < bytes.length; i += 3) {
      const n = (bytes[i] << 16) | (bytes[i + 1] << 8) | bytes[i + 2];
      s += B64[n >> 18] + B64[(n >> 12) & 63] + B64[(n >> 6) & 63] + B64[n & 63];
    }
    const rem = bytes.length - i;
    if (rem === 1) {
      const n = bytes[i] << 16;
      s += B64[n >> 18] + B64[(n >> 12) & 63] + "==";
    } else if (rem === 2) {
      const n = (bytes[i] << 16) | (bytes[i + 1] << 8);
      s += B64[n >> 18] + B64[(n >> 12) & 63] + B64[(n >> 6) & 63] + "=";
    }
    return s;
  }
  function unb64(str) {
    const s = String(str || "").replace(/[^A-Za-z0-9+/]/g, "");
    const out = new Uint8Array(Math.floor((s.length * 3) / 4));
    let buf = 0, bits = 0, k = 0;
    for (let i = 0; i < s.length; i++) {
      buf = (buf << 6) | B64_MAP[s[i]];
      bits += 6;
      if (bits >= 8) {
        bits -= 8;
        out[k++] = (buf >> bits) & 255;
        buf &= (1 << bits) - 1;
      }
    }
    return out.subarray(0, k);
  }

  // ------------------------------------------------------ SHA-2 constants --
  // Derived rather than typed in: a single mistyped hex digit in 80 round
  // constants would produce a hash that is wrong only for some inputs. The
  // spec defines them as fractional parts of square and cube roots of the first
  // primes, so they are computed from that definition with integer roots.
  function iroot(n, k) {
    if (n < 2n) return n;
    let x = 1n << (BigInt(n.toString(2).length) / BigInt(k) + 1n);
    const K = BigInt(k);
    for (;;) {
      const y = ((K - 1n) * x + n / (x ** (K - 1n))) / K;
      if (y >= x) return x;
      x = y;
    }
  }
  const PRIMES = (() => {
    const p = [];
    for (let n = 2; p.length < 80; n++) if (p.every(q => n % q)) p.push(n);
    return p;
  })();
  const fracBits = (p, k, bits) => {
    const r = iroot(BigInt(p) << BigInt(bits * k), k);   // floor(p^(1/k) * 2^bits)
    return r & ((1n << BigInt(bits)) - 1n);
  };
  const K512 = PRIMES.map(p => fracBits(p, 3, 64));
  const IV512 = PRIMES.slice(0, 8).map(p => fracBits(p, 2, 64));
  const K256 = Uint32Array.from(K512.slice(0, 64), v => Number(v >> 32n));
  const IV256 = Uint32Array.from(IV512, v => Number(v >> 32n));
  // SHA-512 runs on 32-bit halves: Scriptable interprets JavaScript, and Int32
  // arithmetic there is far cheaper than BigInt.
  const K512_32 = new Int32Array(160);
  K512.forEach((v, i) => { K512_32[2 * i] = Number(v >> 32n) | 0; K512_32[2 * i + 1] = Number(v & 0xffffffffn) | 0; });
  const IV512_32 = new Int32Array(16);
  IV512.forEach((v, i) => { IV512_32[2 * i] = Number(v >> 32n) | 0; IV512_32[2 * i + 1] = Number(v & 0xffffffffn) | 0; });

  // ------------------------------------------------------------- SHA-256 --
  function sha256(msg) {
    const len = msg.length;
    const total = ((len + 9 + 63) >> 6) << 6;
    const buf = new Uint8Array(total);
    buf.set(msg);
    buf[len] = 0x80;
    const dv = new DataView(buf.buffer);
    dv.setUint32(total - 8, Math.floor(len / 0x20000000));
    dv.setUint32(total - 4, (len * 8) >>> 0);
    const h = Uint32Array.from(IV256);
    const w = new Uint32Array(64);
    const rotr = (x, n) => (x >>> n) | (x << (32 - n));
    for (let off = 0; off < total; off += 64) {
      for (let i = 0; i < 16; i++) w[i] = dv.getUint32(off + i * 4);
      for (let i = 16; i < 64; i++) {
        const a = w[i - 15], b = w[i - 2];
        const s0 = rotr(a, 7) ^ rotr(a, 18) ^ (a >>> 3);
        const s1 = rotr(b, 17) ^ rotr(b, 19) ^ (b >>> 10);
        w[i] = (w[i - 16] + s0 + w[i - 7] + s1) | 0;
      }
      let a = h[0], b = h[1], c = h[2], d = h[3], e = h[4], f = h[5], g = h[6], hh = h[7];
      for (let i = 0; i < 64; i++) {
        const S1 = rotr(e, 6) ^ rotr(e, 11) ^ rotr(e, 25);
        const ch = (e & f) ^ (~e & g);
        const t1 = (hh + S1 + ch + K256[i] + w[i]) | 0;
        const S0 = rotr(a, 2) ^ rotr(a, 13) ^ rotr(a, 22);
        const maj = (a & b) ^ (a & c) ^ (b & c);
        const t2 = (S0 + maj) | 0;
        hh = g; g = f; f = e; e = (d + t1) | 0; d = c; c = b; b = a; a = (t1 + t2) | 0;
      }
      h[0] = (h[0] + a) | 0; h[1] = (h[1] + b) | 0; h[2] = (h[2] + c) | 0; h[3] = (h[3] + d) | 0;
      h[4] = (h[4] + e) | 0; h[5] = (h[5] + f) | 0; h[6] = (h[6] + g) | 0; h[7] = (h[7] + hh) | 0;
    }
    const out = new Uint8Array(32);
    const ov = new DataView(out.buffer);
    for (let i = 0; i < 8; i++) ov.setUint32(i * 4, h[i]);
    return out;
  }

  // ------------------------------------------------------------- SHA-512 --
  // 64-bit words as (hi, lo) Int32 pairs. Rotation amounts here are never 0 or
  // 32, so the shift-by-32 corner of JavaScript never arises.
  function sha512(msg) {
    const len = msg.length;
    const total = ((len + 17 + 127) >> 7) << 7;
    const buf = new Uint8Array(total);
    buf.set(msg);
    buf[len] = 0x80;
    const dv = new DataView(buf.buffer);
    dv.setUint32(total - 8, Math.floor(len / 0x20000000));
    dv.setUint32(total - 4, (len * 8) >>> 0);
    const H = Int32Array.from(IV512_32);
    const W = new Int32Array(160);
    const TWO32 = 4294967296;
    for (let off = 0; off < total; off += 128) {
      for (let i = 0; i < 32; i++) W[i] = dv.getInt32(off + i * 4);
      for (let i = 16; i < 80; i++) {
        // sigma0 = rotr1 ^ rotr8 ^ shr7 of W[i-15]
        let xh = W[(i - 15) * 2], xl = W[(i - 15) * 2 + 1];
        const s0h = ((xh >>> 1) | (xl << 31)) ^ ((xh >>> 8) | (xl << 24)) ^ (xh >>> 7);
        const s0l = ((xl >>> 1) | (xh << 31)) ^ ((xl >>> 8) | (xh << 24)) ^ ((xl >>> 7) | (xh << 25));
        // sigma1 = rotr19 ^ rotr61 ^ shr6 of W[i-2]
        xh = W[(i - 2) * 2]; xl = W[(i - 2) * 2 + 1];
        const s1h = ((xh >>> 19) | (xl << 13)) ^ ((xl >>> 29) | (xh << 3)) ^ (xh >>> 6);
        const s1l = ((xl >>> 19) | (xh << 13)) ^ ((xh >>> 29) | (xl << 3)) ^ ((xl >>> 6) | (xh << 26));
        const lo = (W[(i - 16) * 2 + 1] >>> 0) + (s0l >>> 0) + (W[(i - 7) * 2 + 1] >>> 0) + (s1l >>> 0);
        const hi = W[(i - 16) * 2] + s0h + W[(i - 7) * 2] + s1h + Math.floor(lo / TWO32);
        W[i * 2] = hi | 0;
        W[i * 2 + 1] = lo | 0;
      }
      let ah = H[0], al = H[1], bh = H[2], bl = H[3], ch = H[4], cl = H[5], dh = H[6], dl = H[7];
      let eh = H[8], el = H[9], fh = H[10], fl = H[11], gh = H[12], gl = H[13], hh = H[14], hl = H[15];
      for (let i = 0; i < 80; i++) {
        // Sigma1(e) = rotr14 ^ rotr18 ^ rotr41
        const S1h = ((eh >>> 14) | (el << 18)) ^ ((eh >>> 18) | (el << 14)) ^ ((el >>> 9) | (eh << 23));
        const S1l = ((el >>> 14) | (eh << 18)) ^ ((el >>> 18) | (eh << 14)) ^ ((eh >>> 9) | (el << 23));
        const chh = (eh & fh) ^ (~eh & gh);
        const chl = (el & fl) ^ (~el & gl);
        const t1l = (hl >>> 0) + (S1l >>> 0) + (chl >>> 0) + (K512_32[i * 2 + 1] >>> 0) + (W[i * 2 + 1] >>> 0);
        const t1h = hh + S1h + chh + K512_32[i * 2] + W[i * 2] + Math.floor(t1l / TWO32);
        // Sigma0(a) = rotr28 ^ rotr34 ^ rotr39
        const S0h = ((ah >>> 28) | (al << 4)) ^ ((al >>> 2) | (ah << 30)) ^ ((al >>> 7) | (ah << 25));
        const S0l = ((al >>> 28) | (ah << 4)) ^ ((ah >>> 2) | (al << 30)) ^ ((ah >>> 7) | (al << 25));
        const majh = (ah & bh) ^ (ah & ch) ^ (bh & ch);
        const majl = (al & bl) ^ (al & cl) ^ (bl & cl);
        const t2l = (S0l >>> 0) + (majl >>> 0);
        const t2h = S0h + majh + Math.floor(t2l / TWO32);
        hh = gh; hl = gl; gh = fh; gl = fl; fh = eh; fl = el;
        const el2 = (dl >>> 0) + (t1l >>> 0);
        eh = (dh + t1h + Math.floor(el2 / TWO32)) | 0; el = el2 | 0;
        dh = ch; dl = cl; ch = bh; cl = bl; bh = ah; bl = al;
        const al2 = (t1l >>> 0) + (t2l >>> 0);
        ah = (t1h + t2h + Math.floor(al2 / TWO32)) | 0; al = al2 | 0;
      }
      const acc = [ah, al, bh, bl, ch, cl, dh, dl, eh, el, fh, fl, gh, gl, hh, hl];
      for (let i = 0; i < 16; i += 2) {
        const lo = (H[i + 1] >>> 0) + (acc[i + 1] >>> 0);
        H[i] = (H[i] + acc[i] + Math.floor(lo / TWO32)) | 0;
        H[i + 1] = lo | 0;
      }
    }
    const out = new Uint8Array(64);
    const ov = new DataView(out.buffer);
    for (let i = 0; i < 16; i++) ov.setInt32(i * 4, H[i]);
    return out;
  }

  // ------------------------------------------------------------- ed25519 --
  const P = (1n << 255n) - 19n;
  const L = (1n << 252n) + 27742317777372353535851937790883648493n;
  const md = a => { const r = a % P; return r >= 0n ? r : r + P; };
  function powmod(b, e) {
    let r = 1n;
    b = md(b);
    while (e > 0n) {
      if (e & 1n) r = (r * b) % P;
      b = (b * b) % P;
      e >>= 1n;
    }
    return r;
  }
  const inv = a => powmod(a, P - 2n);
  const Dc = md(-121665n * inv(121666n));
  const D2 = md(2n * Dc);
  const SQRT_M1 = powmod(2n, (P - 1n) / 4n);
  const leBig = b => { let v = 0n; for (let i = b.length - 1; i >= 0; i--) v = (v << 8n) | BigInt(b[i]); return v; };
  function big32le(n) {
    const o = new Uint8Array(32);
    for (let i = 0; i < 32; i++) { o[i] = Number(n & 255n); n >>= 8n; }
    return o;
  }
  // sqrt(u/v) if it exists, per RFC 8032 section 5.1.3.
  function sqrtRatio(u, v) {
    const v3 = (v * v % P) * v % P;
    const v7 = (v3 * v3 % P) * v % P;
    let x = (u * v3 % P) * powmod(u * v7 % P, (P - 5n) / 8n) % P;
    const vx2 = v * x % P * x % P;
    if (vx2 === md(u)) return x;
    if (vx2 === md(-u)) return x * SQRT_M1 % P;
    return null;
  }
  const ZERO = { X: 0n, Y: 1n, Z: 1n, T: 0n };
  function padd(p, q) {
    const A = md((p.Y - p.X) * (q.Y - q.X));
    const B = md((p.Y + p.X) * (q.Y + q.X));
    const C = md(p.T * D2 % P * q.T);
    const D = md(2n * p.Z * q.Z);
    const E = B - A, F = D - C, G = D + C, H = B + A;
    return { X: md(E * F), Y: md(G * H), Z: md(F * G), T: md(E * H) };
  }
  function pdbl(p) {
    const A = p.X * p.X % P;
    const B = p.Y * p.Y % P;
    const C = 2n * (p.Z * p.Z % P) % P;
    const Dd = md(-A);
    const E = md((p.X + p.Y) * (p.X + p.Y) - A - B);
    const G = Dd + B, F = G - C, H = Dd - B;
    return { X: md(E * F), Y: md(G * H), Z: md(F * G), T: md(E * H) };
  }
  function encodePoint(p) {
    const zi = inv(p.Z);
    const x = p.X * zi % P, y = p.Y * zi % P;
    const out = big32le(y);
    if (x & 1n) out[31] |= 0x80;
    return out;
  }
  // Matches curve25519-dalek's CompressedEdwardsY::decompress, which is what
  // the runtime uses to decide whether a program address is off the curve: y
  // is reduced mod p with the sign bit ignored, and only the square root can
  // fail. A different notion of "on curve" would derive a different address.
  function decodePoint(bytes) {
    const yb = Uint8Array.from(bytes);
    const sign = yb[31] >> 7;
    yb[31] &= 0x7f;
    const y = md(leBig(yb));
    const yy = y * y % P;
    let x = sqrtRatio(md(yy - 1n), md(Dc * yy + 1n));
    if (x === null) return null;
    if (Number(x & 1n) !== sign) x = md(-x);
    return { X: x, Y: y, Z: 1n, T: x * y % P };
  }
  const onCurve = bytes => decodePoint(bytes) !== null;
  const BASE = (() => {
    const y = md(4n * inv(5n));
    let x = sqrtRatio(md(y * y - 1n), md(Dc * y * y + 1n));
    if (x & 1n) x = md(-x);
    return { X: x, Y: y, Z: 1n, T: x * y % P };
  })();
  // 2^i x B for every bit, built once on first use, so each fixed-base
  // multiplication is additions only: about half the work of double-and-add.
  let _baseTable = null;
  function mulBase(n) {
    if (!_baseTable) {
      _baseTable = [BASE];
      for (let i = 1; i < 256; i++) _baseTable.push(pdbl(_baseTable[i - 1]));
    }
    let r = ZERO;
    for (let i = 0; n > 0n; i++, n >>= 1n) if (n & 1n) r = padd(r, _baseTable[i]);
    return r;
  }
  const modL = n => { const r = n % L; return r >= 0n ? r : r + L; };
  // An expanded signing key. The public half is computed once here, not per
  // signature.
  function keypairFromSeed(seed) {
    if (!(seed instanceof Uint8Array) || seed.length !== 32) throw new Error("ed25519 seed must be 32 bytes");
    const h = sha512(seed);
    const a = h.slice(0, 32);
    a[0] &= 248; a[31] &= 127; a[31] |= 64;
    const scalar = leBig(a);
    const pub = encodePoint(mulBase(scalar));
    return { seed: Uint8Array.from(seed), scalar, prefix: h.slice(32), pub, address: b58(pub) };
  }
  function sign(msg, kp) {
    const r = modL(leBig(sha512(concat([kp.prefix, msg]))));
    const R = encodePoint(mulBase(r));
    const k = modL(leBig(sha512(concat([R, kp.pub, msg]))));
    const S = modL(r + k * kp.scalar);
    return concat([R, big32le(S)]);
  }
  // For tests and for checking a pasted key; never on the send path.
  function verify(sig, msg, pub) {
    const A = decodePoint(pub), R = decodePoint(sig.subarray(0, 32));
    if (!A || !R) return false;
    const S = leBig(sig.subarray(32));
    if (S >= L) return false;
    const k = modL(leBig(sha512(concat([sig.subarray(0, 32), pub, msg]))));
    let kA = ZERO, q = A, n = k;
    while (n > 0n) { if (n & 1n) kA = padd(kA, q); q = pdbl(q); n >>= 1n; }
    return eqBytes(encodePoint(mulBase(S)), encodePoint(padd(R, kA)));
  }

  // A wallet key as pasted from a wallet app. Accepted forms:
  //   base58 of 64 bytes (Phantom / Solflare "export private key")
  //   base58 of the 32-byte seed
  //   [1,2,3,...] JSON array of 64 numbers (solana-keygen's id.json)
  //   64 or 128 hex characters
  // A 64-byte key carries its own public half, which must match the seed:
  // a mismatch means a corrupted paste, and signing with it would fail anyway.
  function parseSecretKey(input) {
    const s = String(input || "").trim();
    if (!s) return null;
    let bytes;
    if (s[0] === "[") {
      let arr;
      try { arr = JSON.parse(s); } catch (e) { throw new Error("private key looks like a JSON array but does not parse"); }
      if (!Array.isArray(arr) || !arr.every(n => Number.isInteger(n) && n >= 0 && n < 256)) {
        throw new Error("private key array must hold numbers 0-255");
      }
      bytes = Uint8Array.from(arr);
    } else if (/^(0x)?[0-9a-fA-F]{64}$|^(0x)?[0-9a-fA-F]{128}$/.test(s)) {
      bytes = fromHex(s);
    } else {
      try { bytes = unb58(s); } catch (e) { throw new Error("private key is not base58, hex or a number array"); }
    }
    if (bytes.length !== 64 && bytes.length !== 32) {
      throw new Error(`private key decodes to ${bytes.length} bytes; expected 64 (or a 32-byte seed)`);
    }
    const kp = keypairFromSeed(bytes.slice(0, 32));
    if (bytes.length === 64 && !eqBytes(kp.pub, bytes.slice(32))) {
      throw new Error("private key's public half does not match its seed; re-copy it from the wallet");
    }
    return kp;
  }

  // ------------------------------------------------------- addresses/PDAs --
  const PROGRAM = {
    SYSTEM: "11111111111111111111111111111111",
    TOKEN: "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA",
    TOKEN_2022: "TokenzQdBNbLqP5VEhdkAS6EPFLC1PHnBqCXEpPxuEb",
    ATA: "ATokenGPvbdGVxr1b2hvZbsiqW5xWH25efTNsLJA8knL",
    COMPUTE_BUDGET: "ComputeBudget111111111111111111111111111111",
    MEMO: "MemoSq4gqABAXKb96qnH8TysNcWxMyWCqXgDLGmfcHr",
    RENT: "SysvarRent111111111111111111111111111111111",
    DLMM: "LBUZKhRxPF3XUpBCjp4YzTKgLccjZhTSDM9YuVaPwxo",
  };
  const WSOL = "So11111111111111111111111111111111111111112";
  const PDA_MARKER = utf8("ProgramDerivedAddress");
  const _pdaCache = new Map();
  function findPda(seeds, programId) {
    const seedBytes = seeds.map(s => (typeof s === "string" ? utf8(s) : s));
    const pid = keyBytes(programId);
    const cacheKey = seedBytes.map(toHex).join("|") + "@" + toHex(pid);
    const hit = _pdaCache.get(cacheKey);
    if (hit) return hit;
    for (let bump = 255; bump >= 0; bump--) {
      const h = sha256(concat([...seedBytes, Uint8Array.of(bump), pid, PDA_MARKER]));
      if (!onCurve(h)) {
        const r = { address: b58(h), bump };
        _pdaCache.set(cacheKey, r);
        return r;
      }
    }
    throw new Error("no program address found");
  }
  const ata = (owner, mint, tokenProgram) =>
    findPda([keyBytes(owner), keyBytes(tokenProgram || PROGRAM.TOKEN), keyBytes(mint)], PROGRAM.ATA).address;

  // ------------------------------------------------------ transactions ------
  // A legacy transaction. Every transaction these scripts build fits the
  // 1,232-byte limit without lookup tables; compile() measures and says so
  // rather than letting the RPC refuse an oversized one.
  const TX_LIMIT = 1232;
  const meta = (pubkey, isSigner, isWritable) => ({ pubkey, isSigner: !!isSigner, isWritable: !!isWritable });

  function compile({ payer, blockhash, instructions }) {
    const order = [];
    const byKey = new Map();
    const touch = (pubkey, s, w) => {
      const k = typeof pubkey === "string" ? pubkey : b58(pubkey);
      let e = byKey.get(k);
      if (!e) { e = { key: k, isSigner: false, isWritable: false }; byKey.set(k, e); order.push(e); }
      e.isSigner = e.isSigner || !!s;
      e.isWritable = e.isWritable || !!w;
    };
    touch(payer, true, true);
    for (const ix of instructions) {
      for (const k of ix.keys) touch(k.pubkey, k.isSigner, k.isWritable);
      touch(ix.programId, false, false);
    }
    // Fee payer first, then writable signers, readonly signers, writable
    // non-signers, readonly non-signers: the order the header's three counts
    // describe. Within a group the order is free, so insertion order is kept.
    const rank = e => (e.key === payer ? -1 : 0) + (e.isSigner ? 0 : 2) + (e.isWritable ? 0 : 1);
    const keys = order.slice().sort((a, b) => rank(a) - rank(b));
    const index = new Map(keys.map((e, i) => [e.key, i]));
    const numSigners = keys.filter(e => e.isSigner).length;
    const header = Uint8Array.of(
      numSigners,
      keys.filter(e => e.isSigner && !e.isWritable).length,
      keys.filter(e => !e.isSigner && !e.isWritable).length,
    );
    const parts = [header, shortvec(keys.length), ...keys.map(e => keyBytes(e.key)), keyBytes(blockhash), shortvec(instructions.length)];
    for (const ix of instructions) {
      parts.push(u8(index.get(ix.programId)));
      parts.push(shortvec(ix.keys.length));
      parts.push(Uint8Array.from(ix.keys.map(k => index.get(typeof k.pubkey === "string" ? k.pubkey : b58(k.pubkey)))));
      parts.push(shortvec(ix.data.length));
      parts.push(ix.data);
    }
    const message = concat(parts);
    const signers = keys.slice(0, numSigners).map(e => e.key);
    const size = shortvec(numSigners).length + 64 * numSigners + message.length;
    return { message, signers, size, accounts: keys.length };
  }
  // keypairs: every keypair that may be asked to sign, looked up by address.
  // A { address, watchOnly: true } entry signs with zeros: only ever useful
  // for a simulation with signature checks off, and refused by any real send.
  function signCompiled(compiled, keypairs) {
    const byAddr = new Map(keypairs.map(kp => [kp.address, kp]));
    const sigs = compiled.signers.map(addr => {
      const kp = byAddr.get(addr);
      if (!kp) throw new Error("no key to sign for " + addr.slice(0, 8) + "…");
      return kp.watchOnly ? new Uint8Array(64) : sign(compiled.message, kp);
    });
    const bytes = concat([shortvec(sigs.length), ...sigs, compiled.message]);
    return { bytes, base64: b64(bytes), signature: b58(sigs[0]) };
  }
  // A transaction built elsewhere (Jupiter), v0 or legacy. Only the fee payer
  // slot is signed here, and only when the transaction names this wallet as
  // its fee payer and needs no other signature: anything else is refused, since
  // a second required signer would mean a transaction shaped unlike a swap.
  function signForeign(base64, kp) {
    const tx = unb64(base64);
    const n = readShortvec(tx, 0);
    const msgStart = n.size + 64 * n.value;
    const message = tx.subarray(msgStart);
    let o = 0;
    if (message[0] & 0x80) o = 1;   // versioned: prefix byte, then the legacy header
    const numRequired = message[o];
    const keysLen = readShortvec(message, o + 3);
    const payer = b58(message.subarray(o + 3 + keysLen.size, o + 3 + keysLen.size + 32));
    if (payer !== kp.address) throw new Error("swap transaction is not paid by this wallet");
    if (numRequired !== 1 || n.value !== 1) throw new Error(`swap transaction wants ${numRequired} signatures`);
    const sig = kp.watchOnly ? new Uint8Array(64) : sign(message, kp);
    const out = Uint8Array.from(tx);
    out.set(sig, n.size);
    return { bytes: out, base64: b64(out), signature: b58(sig), version: (message[0] & 0x80) ? (message[0] & 0x7f) : "legacy" };
  }

  // ------------------------------------------------------------ transport --
  // Scriptable's Request when running on the phone, fetch() under Node for the
  // tests. Node also has a global Request (the fetch one), so the check is for
  // Scriptable's loadString rather than for the name.
  const SCRIPTABLE = typeof Request === "function" && Request.prototype && typeof Request.prototype.loadString === "function";
  function sleep(ms) {
    return new Promise(r => (typeof Timer !== "undefined" && Timer.schedule) ? Timer.schedule(ms, false, r) : setTimeout(r, ms));
  }
  async function http(url, opts) {
    const o = opts || {};
    const headers = Object.assign({ Accept: "application/json" }, o.headers || {});
    const body = o.body == null ? null : (typeof o.body === "string" ? o.body : JSON.stringify(o.body));
    if (body != null && !headers["Content-Type"]) headers["Content-Type"] = "application/json";
    if (SCRIPTABLE) {
      const r = new Request(url);
      r.method = o.method || (body != null ? "POST" : "GET");
      r.headers = headers;
      if (body != null) r.body = body;
      r.timeoutInterval = o.timeout || 20;
      // asJson: let iOS's own JSON reader decode the body (the fallback when
      // a reply read as text will not parse).
      if (o.asJson) {
        const json = await r.loadJSON();
        return { status: (r.response && r.response.statusCode) || 0, text: "", json };
      }
      const text = await r.loadString();
      return { status: (r.response && r.response.statusCode) || 0, text: text || "" };
    }
    const ctl = typeof AbortController === "function" ? new AbortController() : null;
    const t = ctl ? setTimeout(() => ctl.abort(), (o.timeout || 20) * 1000) : null;
    try {
      const res = await fetch(url, { method: o.method || (body != null ? "POST" : "GET"), headers, body: body == null ? undefined : body, signal: ctl ? ctl.signal : undefined });
      return { status: res.status, text: await res.text() };
    } finally { if (t) clearTimeout(t); }
  }
  // A reply's text as JSON, forgiving what a phone's network stack can leave
  // around it: a byte-order mark, NULs or whitespace at either end, or bytes
  // after the closing brace. Null when it still is not JSON.
  function parseReply(text) {
    const s = String(text == null ? "" : text).replace(/^[\uFEFF\u0000\s]+|[\u0000\s]+$/g, "");
    if (!s) return null;
    try { return JSON.parse(s); } catch (e) {}
    const end = s.lastIndexOf("}");
    if (end > 0 && end < s.length - 1) { try { return JSON.parse(s.slice(0, end + 1)); } catch (e) {} }
    return null;
  }
  async function getJson(url, opts) {
    const r = await http(url, opts);
    let j = null;
    try { j = JSON.parse(r.text); } catch (e) {}
    if (r.status >= 400 || j == null) {
      const e = new Error(`HTTP ${r.status} from ${url.replace(/\?.*$/, "").slice(0, 60)}${r.text ? ": " + r.text.slice(0, 80) : ""}`);
      e.status = r.status;
      throw e;
    }
    return j;
  }

  // JSON-RPC with endpoint rotation. Reads are retried through throttling and
  // network faults; a broadcast is sent once per call, because a lost reply may
  // belong to a transaction that already landed. Re-sending the SAME signed
  // bytes is harmless (it has one signature) and is done by the confirmation
  // loop, not here.
  function makeRpc(urls, opts) {
    const list = (urls || []).filter(Boolean);
    if (!list.length) throw new Error("no Solana RPC configured");
    const o = opts || {};
    let at = 0;
    const retries = o.retries == null ? 4 : o.retries;
    async function call(method, params, callOpts) {
      const co = callOpts || {};
      const isSend = method === "sendTransaction";
      let lastErr = null;
      const tries = isSend ? 1 : retries + 1;
      for (let attempt = 0; attempt < tries * list.length; attempt++) {
        const url = list[at % list.length];
        if (attempt && !isSend) await sleep(Math.min(4000, 300 * Math.pow(2, Math.floor(attempt / list.length))));
        let r;
        try {
          r = await http(url, { body: { jsonrpc: "2.0", id: 1, method, params }, timeout: co.timeout || o.timeout || 20 });
        } catch (e) {
          lastErr = e;
          if (isSend) throw e;
          at++;
          continue;
        }
        let j = parseReply(r.text);
        // A reply that arrived but will not parse as text: on the phone, ask
        // again once and let iOS decode it, with compression off. Reads only;
        // a broadcast is never repeated here.
        if (!j && SCRIPTABLE && !isSend && r.status === 200) {
          try {
            const r2 = await http(url, { body: { jsonrpc: "2.0", id: 1, method, params }, asJson: true,
              headers: { "Accept-Encoding": "identity" }, timeout: co.timeout || o.timeout || 20 });
            if (r2.json && typeof r2.json === "object") j = r2.json;
          } catch (e) {}
        }
        if (!j) {
          const text = String(r.text || "");
          lastErr = new Error(text ? `the RPC's reply to ${method} could not be read (HTTP ${r.status}, ${text.length} characters)`
            : `the RPC sent an empty reply to ${method} (HTTP ${r.status})`);
          if (typeof o.onBadReply === "function") {
            try {
              o.onBadReply({ at: new Date().toISOString(), host: String(url).replace(/^https?:\/\/([^/?#]+).*$/, "$1"), method,
                status: r.status, length: text.length, head: text.slice(0, 300), tail: text.slice(-300),
                codes: [...text.slice(0, 4), ...text.slice(-4)].map(c => c.charCodeAt(0)) });
            } catch (e) {}
          }
          if (isSend && r.status && r.status < 500 && r.status !== 429) throw lastErr;
          at++;
          if (isSend) throw lastErr;
          continue;
        }
        if (j.error) {
          const m = String(j.error.message || JSON.stringify(j.error));
          // Rate limits and exhausted credits move to the next endpoint; any
          // other error is the chain's answer and is returned as such.
          if (/rate|limit|429|max usage|credits|timed? ?out|busy|unavailable/i.test(m) && !isSend) {
            lastErr = new Error(m);
            at++;
            continue;
          }
          const err = new Error(m);
          err.rpc = j.error;
          throw err;
        }
        return j.result;
      }
      throw lastErr || new Error("RPC unavailable");
    }
    return { call, urls: list };
  }

  // Account data as bytes, or null for an account that does not exist.
  async function getAccounts(rpc, addresses, commitment) {
    const out = new Map();
    for (let i = 0; i < addresses.length; i += 100) {
      const chunk = addresses.slice(i, i + 100);
      const r = await rpc.call("getMultipleAccounts", [chunk, { encoding: "base64", commitment: commitment || "confirmed" }]);
      (r && r.value || []).forEach((acc, k) => {
        out.set(chunk[k], acc ? { data: unb64(acc.data[0]), lamports: acc.lamports, owner: acc.owner } : null);
      });
    }
    return out;
  }

  // ------------------------------------------------------ instructions ------
  const ix = (programId, keys, data) => ({ programId, keys, data });
  const computeLimit = units => ix(PROGRAM.COMPUTE_BUDGET, [], concat([u8(2), u32(units)]));
  const computePrice = microLamports => ix(PROGRAM.COMPUTE_BUDGET, [], concat([u8(3), u64(Math.max(0, Math.floor(microLamports)))]));
  const transfer = (from, to, lamports) => ix(PROGRAM.SYSTEM, [meta(from, true, true), meta(to, false, true)], concat([u32(2), u64(lamports)]));
  const syncNative = account => ix(PROGRAM.TOKEN, [meta(account, false, true)], u8(17));
  const closeTokenAccount = (account, dest, owner, tokenProgram) =>
    ix(tokenProgram || PROGRAM.TOKEN, [meta(account, false, true), meta(dest, false, true), meta(owner, true, false)], u8(9));
  // Token-2022 HarvestWithheldTokensToMint (TransferFeeExtension 26, sub 4):
  // moves withheld transfer fees from token accounts to their mint. Anyone
  // may call it; afterwards an empty account can be closed.
  const harvestWithheldToMint = (mint, sources) =>
    ix(PROGRAM.TOKEN_2022, [meta(mint, false, true), ...sources.map(a => meta(a, false, true))], Uint8Array.of(26, 4));
  const createAtaIdempotent = (payer, owner, mint, tokenProgram) => ix(PROGRAM.ATA, [
    meta(payer, true, true), meta(ata(owner, mint, tokenProgram), false, true), meta(owner, false, false),
    meta(mint, false, false), meta(PROGRAM.SYSTEM, false, false), meta(tokenProgram || PROGRAM.TOKEN, false, false),
  ], u8(1));

  // ---------------------------------------------------------------- DLMM ----
  const DISC = {
    initialize_position: [219, 192, 234, 71, 190, 191, 102, 80],
    initialize_bin_array: [35, 86, 19, 185, 78, 212, 75, 211],
    initialize_bin_array_bitmap_extension: [47, 157, 226, 180, 12, 240, 33, 71],
    add_liquidity_by_strategy2: [3, 221, 149, 218, 111, 141, 118, 213],
    remove_liquidity_by_range2: [204, 2, 195, 145, 53, 145, 145, 205],
    claim_fee2: [112, 191, 101, 171, 28, 144, 127, 187],
    claim_reward2: [190, 3, 127, 119, 178, 87, 157, 183],
    close_position_if_empty: [59, 124, 212, 118, 91, 152, 110, 157],
  };
  const ACCOUNT_DISC = {
    LbPair: [33, 11, 49, 98, 181, 101, 177, 13],
    PositionV2: [117, 176, 212, 199, 245, 180, 133, 182],
    BinArray: [92, 142, 92, 220, 5, 148, 70, 181],
  };
  const disc = name => Uint8Array.from(DISC[name]);
  const hasDisc = (data, name) => data && data.length >= 8 && ACCOUNT_DISC[name].every((b, i) => data[i] === b);

  const BINS_PER_ARRAY = 70;            // MAX_BIN_PER_ARRAY
  const BINS_PER_POSITION = 70;         // DEFAULT_BIN_PER_POSITION: no extension rent
  const BITMAP_HALF = 512;              // BIN_ARRAY_BITMAP_SIZE: default bitmap covers -512..511
  const POSITION_BASE = 8 + 8112;       // PositionV2 without extension
  const POSITION_BIN_EXT = 112;         // per extra bin beyond 70
  // Account sizes, for rent. The SDK carries rent as constants (0.0574 SOL a
  // position), but Solana's rent per byte has since been cut: on 2026-09-30 a
  // position locked 0.0419 SOL. So rent is asked of the chain (rentRates) and
  // these are only the fallback, at the current rate of 5,080 lamports a byte.
  const SIZE = { position: 8120, binArray: 10136, bitmap: 1568, tokenAccount: 165, tokenAccount2022: 182 };
  const rentAt = bytes => (128 + bytes) * 5080;
  const POSITION_RENT = rentAt(SIZE.position);
  const BIN_ARRAY_RENT = rentAt(SIZE.binArray);
  const TOKEN_ACCOUNT_RENT = rentAt(SIZE.tokenAccount);
  let _rent = null;
  async function rentRates(rpc) {
    if (_rent) return _rent;
    const keys = Object.keys(SIZE);
    try {
      const vals = await Promise.all(keys.map(k => rpc.call("getMinimumBalanceForRentExemption", [SIZE[k]])));
      _rent = Object.fromEntries(keys.map((k, i) => [k, Number(vals[i])]));
    } catch (e) {
      _rent = Object.fromEntries(keys.map(k => [k, rentAt(SIZE[k])]));
    }
    return _rent;
  }
  const STRATEGY = { spot: 6, curve: 7, bidask: 8 };   // *ImBalanced variants, as the SDK sends

  const eventAuthority = () => findPda(["__event_authority"], PROGRAM.DLMM).address;
  const binArrayIndex = binId => Math.floor(binId / BINS_PER_ARRAY);
  const binArrayAddress = (lbPair, index) => findPda(["bin_array", keyBytes(lbPair), i64(index)], PROGRAM.DLMM).address;
  const bitmapExtensionAddress = lbPair => findPda(["bitmap", keyBytes(lbPair)], PROGRAM.DLMM).address;
  const bitmapOverflow = index => index < -BITMAP_HALF || index > BITMAP_HALF - 1;
  function binArraysCovering(lbPair, lo, hi) {
    const out = [];
    for (let i = binArrayIndex(lo); i <= binArrayIndex(hi); i++) out.push({ index: i, address: binArrayAddress(lbPair, i) });
    return out;
  }
  // Transfer-hook account lists for token X and Y, always two entries: the
  // program reads the slice list positionally, and empty slices are how "no
  // hook" is said. Pools whose token carries a hook are refused before this.
  const REMAINING_NONE_LIQUIDITY = Uint8Array.of(2, 0, 0, 0, 0, 0, 1, 0);
  const REMAINING_NONE_REWARD = Uint8Array.of(1, 0, 0, 0, 2, 0);

  function parseLbPair(data) {
    if (!hasDisc(data, "LbPair")) throw new Error("not a DLMM pool account");
    // Each reward keeps its slot: claim_reward2 names the slot, and slot 1 can
    // be set while slot 0 is empty.
    const reward = i => {
      const o = 264 + i * 144;
      return { index: i, mint: b58(data.subarray(o, o + 32)), vault: b58(data.subarray(o + 32, o + 64)) };
    };
    return {
      baseFactor: rdU16(data, 8),
      variableFeeControl: rdU32(data, 16),
      protocolShare: rdU16(data, 32),
      baseFeePowerFactor: data[34],
      functionType: data[35],
      collectFeeMode: data[36],
      pairType: data[75],
      activeId: rdI32(data, 76),
      binStep: rdU16(data, 80),
      status: data[82],
      activationType: data[86],
      tokenXMint: b58(data.subarray(88, 120)),
      tokenYMint: b58(data.subarray(120, 152)),
      reserveX: b58(data.subarray(152, 184)),
      reserveY: b58(data.subarray(184, 216)),
      rewards: [reward(0), reward(1)].filter(r => r.mint !== PROGRAM.SYSTEM),
      oracle: b58(data.subarray(552, 584)),
      activationPoint: rdU64(data, 816),
      tokenXProgramFlag: data[880],
      tokenYProgramFlag: data[881],
    };
  }
  // Base fee as a fraction: base_factor x bin_step x 10 x 10^power / 1e9.
  const baseFeeRate = lp => lp.baseFactor * lp.binStep * 10 * Math.pow(10, lp.baseFeePowerFactor || 0) / 1e9;

  function parsePosition(data) {
    if (!hasDisc(data, "PositionV2")) throw new Error("not a DLMM position account");
    const lowerBinId = rdI32(data, 7912), upperBinId = rdI32(data, 7916);
    const width = upperBinId - lowerBinId + 1;
    const bins = [];
    for (let i = 0; i < width; i++) {
      let so, fo, ro;
      if (i < 70) {
        so = 72 + i * 16;
        ro = 1192 + i * 48;
        fo = 4552 + i * 48;
      } else {
        const b = POSITION_BASE + (i - 70) * POSITION_BIN_EXT;
        if (b + POSITION_BIN_EXT > data.length) break;
        so = b; ro = b + 16; fo = b + 64;
      }
      bins.push({
        binId: lowerBinId + i,
        share: rdU128(data, so),
        feeXPending: rdU64(data, fo + 32),
        feeYPending: rdU64(data, fo + 40),
        rewardPending: [rdU64(data, ro + 32), rdU64(data, ro + 40)],
      });
    }
    const liquid = bins.filter(b => b.share > 0n);
    const owed = bins.filter(b => b.share > 0n || b.feeXPending > 0n || b.feeYPending > 0n
                                 || b.rewardPending[0] > 0n || b.rewardPending[1] > 0n);
    return {
      lbPair: b58(data.subarray(8, 40)),
      owner: b58(data.subarray(40, 72)),
      lowerBinId, upperBinId, width, bins,
      liquidRange: liquid.length ? [liquid[0].binId, liquid[liquid.length - 1].binId] : null,
      owedRange: owed.length ? [owed[0].binId, owed[owed.length - 1].binId] : null,
      totalClaimedFeeX: rdU64(data, 7928),
      totalClaimedFeeY: rdU64(data, 7936),
      lockReleasePoint: rdU64(data, 7992),
      feeOwner: b58(data.subarray(8001, 8033)),
    };
  }

  function parseBinArray(data) {
    if (!hasDisc(data, "BinArray")) throw new Error("not a DLMM bin array");
    const index = Number(rdI64(data, 8));
    const bins = [];
    for (let i = 0; i < 70; i++) {
      const o = 56 + i * 144;
      bins.push({
        binId: index * 70 + i,
        amountX: rdU64(data, o),
        amountY: rdU64(data, o + 8),
        liquiditySupply: rdU128(data, o + 32),
      });
    }
    return { index, lbPair: b58(data.subarray(24, 56)), bins };
  }

  // SPL mint: decimals, and for Token-2022 the extensions that matter here. A
  // transfer hook needs extra accounts resolved from the hook program, which
  // this code does not do, so such a token is reported and refused upstream.
  function parseMint(data, ownerProgram) {
    if (!data || data.length < 82) throw new Error("not a token mint");
    const out = {
      decimals: data[44],
      supply: rdU64(data, 36),
      program: ownerProgram,
      is2022: ownerProgram === PROGRAM.TOKEN_2022,
      transferHook: null,
      transferFeeBps: 0,
    };
    if (out.is2022 && data.length > 166 && data[165] === 1) {
      let o = 166;
      while (o + 4 <= data.length) {
        const type = rdU16(data, o), len = rdU16(data, o + 2);
        const v = o + 4;
        if (type === 0 && len === 0) break;
        if (type === 14 && len >= 64) {
          const prog = data.subarray(v + 32, v + 64);
          if (prog.some(x => x !== 0)) out.transferHook = b58(prog);
        }
        if (type === 1 && len >= 108) {
          // TransferFeeConfig: newer_transfer_fee.transfer_fee_basis_points is the
          // last u16 of the struct.
          out.transferFeeBps = rdU16(data, v + 106);
        }
        o = v + len;
      }
    }
    return out;
  }

  function ixInitializePosition({ payer, position, lbPair, owner, lowerBinId, width }) {
    return ix(PROGRAM.DLMM, [
      meta(payer, true, true), meta(position, true, true), meta(lbPair, false, false), meta(owner, true, false),
      meta(PROGRAM.SYSTEM, false, false), meta(PROGRAM.RENT, false, false),
      meta(eventAuthority(), false, false), meta(PROGRAM.DLMM, false, false),
    ], concat([disc("initialize_position"), i32(lowerBinId), i32(width)]));
  }
  function ixInitializeBinArray({ lbPair, index, funder }) {
    return ix(PROGRAM.DLMM, [
      meta(lbPair, false, false), meta(binArrayAddress(lbPair, index), false, true),
      meta(funder, true, true), meta(PROGRAM.SYSTEM, false, false),
    ], concat([disc("initialize_bin_array"), i64(index)]));
  }
  function ixInitializeBitmapExtension({ lbPair, funder }) {
    return ix(PROGRAM.DLMM, [
      meta(lbPair, false, false), meta(bitmapExtensionAddress(lbPair), false, true),
      meta(funder, true, true), meta(PROGRAM.SYSTEM, false, false), meta(PROGRAM.RENT, false, false),
    ], disc("initialize_bin_array_bitmap_extension"));
  }
  // An optional Anchor account that is absent is passed as the program id.
  const optional = (addr, writable) => addr ? meta(addr, false, !!writable) : meta(PROGRAM.DLMM, false, false);
  const tokenProgramOf = flag => (flag === 1 ? PROGRAM.TOKEN_2022 : PROGRAM.TOKEN);

  // pool: { address, lb (parsed LbPair) }.
  function ixAddLiquidityByStrategy2({ pool, position, sender, amountX, amountY, activeId, maxActiveBinSlippage,
                                       minBinId, maxBinId, strategy, favorX, bitmapExtension }) {
    const lb = pool.lb;
    const code = STRATEGY[strategy];
    if (code == null) throw new Error("unknown strategy " + strategy);
    const params = new Uint8Array(64);
    params[0] = favorX ? 1 : 0;
    const px = tokenProgramOf(lb.tokenXProgramFlag), py = tokenProgramOf(lb.tokenYProgramFlag);
    return ix(PROGRAM.DLMM, [
      meta(position, false, true), meta(pool.address, false, true), optional(bitmapExtension, true),
      meta(ata(sender, lb.tokenXMint, px), false, true), meta(ata(sender, lb.tokenYMint, py), false, true),
      meta(lb.reserveX, false, true), meta(lb.reserveY, false, true),
      meta(lb.tokenXMint, false, false), meta(lb.tokenYMint, false, false),
      meta(sender, true, false), meta(px, false, false), meta(py, false, false),
      meta(eventAuthority(), false, false), meta(PROGRAM.DLMM, false, false),
      ...binArraysCovering(pool.address, minBinId, maxBinId).map(b => meta(b.address, false, true)),
    ], concat([
      disc("add_liquidity_by_strategy2"),
      u64(amountX), u64(amountY), i32(activeId), i32(maxActiveBinSlippage),
      i32(minBinId), i32(maxBinId), u8(code), params,
      REMAINING_NONE_LIQUIDITY,
    ]));
  }
  function ixRemoveLiquidityByRange2({ pool, position, sender, fromBinId, toBinId, bps, bitmapExtension }) {
    const lb = pool.lb;
    const px = tokenProgramOf(lb.tokenXProgramFlag), py = tokenProgramOf(lb.tokenYProgramFlag);
    return ix(PROGRAM.DLMM, [
      meta(position, false, true), meta(pool.address, false, true), optional(bitmapExtension, true),
      meta(ata(sender, lb.tokenXMint, px), false, true), meta(ata(sender, lb.tokenYMint, py), false, true),
      meta(lb.reserveX, false, true), meta(lb.reserveY, false, true),
      meta(lb.tokenXMint, false, false), meta(lb.tokenYMint, false, false),
      meta(sender, true, false), meta(px, false, false), meta(py, false, false),
      meta(PROGRAM.MEMO, false, false), meta(eventAuthority(), false, false), meta(PROGRAM.DLMM, false, false),
      ...binArraysCovering(pool.address, fromBinId, toBinId).map(b => meta(b.address, false, true)),
    ], concat([disc("remove_liquidity_by_range2"), i32(fromBinId), i32(toBinId), u16(bps), REMAINING_NONE_LIQUIDITY]));
  }
  function ixClaimFee2({ pool, position, sender, minBinId, maxBinId }) {
    const lb = pool.lb;
    const px = tokenProgramOf(lb.tokenXProgramFlag), py = tokenProgramOf(lb.tokenYProgramFlag);
    return ix(PROGRAM.DLMM, [
      meta(pool.address, false, true), meta(position, false, true), meta(sender, true, false),
      meta(lb.reserveX, false, true), meta(lb.reserveY, false, true),
      meta(ata(sender, lb.tokenXMint, px), false, true), meta(ata(sender, lb.tokenYMint, py), false, true),
      meta(lb.tokenXMint, false, false), meta(lb.tokenYMint, false, false),
      meta(px, false, false), meta(py, false, false),
      meta(PROGRAM.MEMO, false, false), meta(eventAuthority(), false, false), meta(PROGRAM.DLMM, false, false),
      ...binArraysCovering(pool.address, minBinId, maxBinId).map(b => meta(b.address, false, true)),
    ], concat([disc("claim_fee2"), i32(minBinId), i32(maxBinId), REMAINING_NONE_LIQUIDITY]));
  }
  function ixClaimReward2({ pool, position, sender, rewardIndex, rewardMint, rewardVault, rewardProgram, minBinId, maxBinId }) {
    return ix(PROGRAM.DLMM, [
      meta(pool.address, false, true), meta(position, false, true), meta(sender, true, false),
      meta(rewardVault, false, true), meta(rewardMint, false, false),
      meta(ata(sender, rewardMint, rewardProgram), false, true), meta(rewardProgram, false, false),
      meta(PROGRAM.MEMO, false, false), meta(eventAuthority(), false, false), meta(PROGRAM.DLMM, false, false),
      ...binArraysCovering(pool.address, minBinId, maxBinId).map(b => meta(b.address, false, true)),
    ], concat([disc("claim_reward2"), u64(rewardIndex), i32(minBinId), i32(maxBinId), REMAINING_NONE_REWARD]));
  }
  function ixClosePositionIfEmpty({ position, sender, rentReceiver }) {
    return ix(PROGRAM.DLMM, [
      meta(position, false, true), meta(sender, true, false), meta(rentReceiver || sender, false, true),
      meta(eventAuthority(), false, false), meta(PROGRAM.DLMM, false, false),
    ], disc("close_position_if_empty"));
  }

  // Price of one whole token X in token Y at a bin: (1 + step)^id, rescaled
  // from raw units by the two decimals.
  const binPrice = (binId, binStep, decX, decY) => Math.pow(1 + binStep / 10000, binId) * Math.pow(10, decX - decY);
  // Bins between the price and a point pct percent below it (the price of the
  // non-SOL token falling by pct).
  const binsForDrop = (pct, binStep) => {
    const f = Math.min(99.9, Math.max(0, pct)) / 100;
    return f <= 0 ? 0 : Math.log(1 / (1 - f)) / Math.log(1 + binStep / 10000);
  };
  const dropForBins = (bins, binStep) => (1 - Math.pow(1 + binStep / 10000, -bins)) * 100;

  // ------------------------------------------------------------- ladders ---
  // How a ladder of `n` slices spends its SOL, for one strategy.
  //
  // Measured on mainnet (tests/mainnet.test.mjs): inside ONE position the
  // program draws the strategy against that position's own two edges. Spot is
  // flat; Curve falls in a straight line from the edge next to the price to
  // nearly nothing at the far edge; Bid-Ask is the mirror image.
  //
  // Returns { weights, inner }:
  //   weights  share of the deposit for each slice, slice 1 next to the price
  //   inner    the DLMM shape each slice is opened with ("spot"|"curve"|"bidask")
  //
  // The slices are the RH ladder whatever the strategy: slice i from the price
  // holds i parts, 1:2:...:n, so the further the price falls the more SOL is
  // waiting. The strategy is the shape inside every slice: Spot gives a rising
  // staircase, Bid-Ask rising triangles (each slice climbs from nearly zero at
  // its near edge), Curve the mirror (each slice tallest at its near edge).
  // One slice is the pool's own shape, untouched. The owner chose this on
  // 2026-09-30.
  function ladderShape(strategy, n) {
    return { weights: Array.from({ length: Math.max(1, n) }, (_, i) => i + 1), inner: strategy };
  }

  // The bins a SOL-only ladder occupies and how the deposit is cut up.
  //   activeId, binStep  the pool now
  //   solIsX             SOL is token X (rare: SOL/USDC-style pools)
  //   lamports           total SOL to deposit, BigInt
  //   slices, top, range as in a preset: top is where the ladder starts below
  //                      the price (0 = the active bin itself), range where it
  //                      ends, both in percent the OTHER token falls against SOL
  // SOL-only liquidity can only sit on SOL's side of the price: below the
  // active bin when SOL is Y, above it when SOL is X. Either way it buys the
  // other token as that token's price falls.
  // bins, when given, is the exact width in bins from the active bin (re-entry
  // keeps a ladder's width in bins; a percentage would round differently on a
  // pool whose bin step is not the one it was converted with).
  function planLadder({ activeId, binStep, solIsX, lamports, slices, top, range, strategy, bins }) {
    const topBins = bins ? 0 : Math.round(binsForDrop(top || 0, binStep));
    let endBins = bins ? Math.max(0, Math.floor(bins) - 1) : Math.max(topBins, Math.round(binsForDrop(range, binStep)));
    const wanted = Math.max(1, Math.floor(slices || 1));
    // A position here holds at most 70 bins without paying extension rent. A
    // band that overshoots the slices asked for by a bin or two is trimmed at
    // the far end (a 50% band on a 1% pool is 71 bins; one position still
    // covers 49.8%). A band well past that gets more slices rather than being
    // cut short.
    const fit = wanted * BINS_PER_POSITION;
    if (endBins - topBins + 1 > fit && endBins - topBins + 1 - fit <= Math.max(2, Math.ceil(fit * 0.03))) {
      endBins = topBins + fit - 1;
    }
    const total = endBins - topBins + 1;
    let n = Math.max(wanted, Math.ceil(total / BINS_PER_POSITION));
    n = Math.min(n, total);
    const shape = ladderShape(strategy, n);
    const wsum = shape.weights.reduce((a, b) => a + b, 0);
    const base = Math.floor(total / n), extra = total % n;
    const out = [];
    let offset = topBins, spent = 0n;
    for (let i = 0; i < n; i++) {
      const size = base + (i < extra ? 1 : 0);
      // Distance from the active bin, in bins, of this slice's two edges.
      const nearD = offset, farD = offset + size - 1;
      offset += size;
      const lo = solIsX ? activeId + nearD : activeId - farD;
      const hi = solIsX ? activeId + farD : activeId - nearD;
      let amount = i === n - 1 ? lamports - spent
        : (lamports * BigInt(Math.round(shape.weights[i] * 1e6))) / BigInt(Math.round(wsum * 1e6));
      if (amount < 0n) amount = 0n;
      spent += amount;
      out.push({ lo, hi, width: hi - lo + 1, lamports: amount, strategy: shape.inner, weight: shape.weights[i] / wsum });
    }
    return {
      slices: out.filter(s => s.lamports > 0n),
      raised: n > wanted ? n : 0,
      bins: total,
      topBins, endBins,
      realTop: dropForBins(topBins, binStep),
      realEnd: dropForBins(endBins, binStep),
      strategy,
      favorX: !!solIsX,
    };
  }

  // ------------------------------------------------------------ sending ----
  // Priority fee in micro-lamports per compute unit. Helius prices it from the
  // accounts the transaction touches; any other RPC gets the recent-fees
  // percentile. Solana fees are small either way; this only decides whether
  // the transaction lands during a busy minute.
  async function priorityFee(rpc, accountKeys, opts) {
    const o = opts || {};
    const floor = o.floor == null ? 20_000 : o.floor, cap = o.cap == null ? 2_000_000 : o.cap;
    const clamp = v => Math.max(floor, Math.min(cap, Math.round(v || 0)));
    if (o.helius) {
      try {
        const r = await rpc.call("getPriorityFeeEstimate", [{ accountKeys, options: { priorityLevel: o.level || "High" } }]);
        if (r && r.priorityFeeEstimate != null) return clamp(r.priorityFeeEstimate);
      } catch (e) {}
    }
    try {
      const r = await rpc.call("getRecentPrioritizationFees", [accountKeys.slice(0, 128)]);
      const fees = (r || []).map(x => x.prioritizationFee).filter(x => x > 0).sort((a, b) => a - b);
      if (fees.length) return clamp(fees[Math.floor(fees.length * 0.75)]);
    } catch (e) {}
    return floor;
  }

  // Simulate unsigned-for-real bytes. sigVerify off lets a transaction be
  // tried before a fresh position key has been asked to sign it for real, and
  // replaceRecentBlockhash lets the plan be checked before a blockhash is
  // chosen. Returns { ok, err, logs, units, accounts }.
  async function simulate(rpc, compiled, keypairs, opts) {
    const o = opts || {};
    const signed = signCompiled(compiled, keypairs);
    const cfg = { encoding: "base64", sigVerify: false, replaceRecentBlockhash: true, commitment: "confirmed" };
    if (o.accounts) cfg.accounts = { encoding: "base64", addresses: o.accounts };
    const r = await rpc.call("simulateTransaction", [signed.base64, cfg]);
    const v = r && r.value || {};
    return { ok: v.err == null, err: v.err, logs: v.logs || [], units: v.unitsConsumed || 0, accounts: v.accounts || null };
  }
  // The program's own words for a failure, when the logs carry them.
  function explainFailure(sim) {
    const logs = (sim && sim.logs) || [];
    for (let i = logs.length - 1; i >= 0; i--) {
      const m = logs[i].match(/Error Message: (.+?)\.?$/) || logs[i].match(/Program log: (?:Error: )?(.*(?:insufficient|exceed|invalid|slippage|not enough).*)$/i);
      if (m) return m[1];
    }
    const e = sim && sim.err;
    if (!e) return "simulation failed";
    if (e.InstructionError) return `instruction ${e.InstructionError[0]} failed: ${JSON.stringify(e.InstructionError[1])}`;
    return typeof e === "string" ? e : JSON.stringify(e);
  }

  // Build, price and sign one transaction. `build(computeUnits, microLamports)`
  // returns its instruction list; it is called once to measure (at the
  // maximum) and again with the measured units plus headroom, so a heavy
  // transaction is not refused for asking too little and a light one does not
  // reserve 1.4M units of priority fee it will never use.
  async function prepare(rpc, { payer, keypairs, build, microLamports, headroom, accounts }) {
    const bh = await rpc.call("getLatestBlockhash", [{ commitment: "confirmed" }]);
    const blockhash = bh.value.blockhash, lastValid = bh.value.lastValidBlockHeight;
    const probeIxs = build(1_400_000, microLamports);
    const probe = compile({ payer, blockhash, instructions: probeIxs });
    if (probe.size > TX_LIMIT) throw new Error(`transaction is ${probe.size} bytes, over Solana's ${TX_LIMIT}`);
    const sim = await simulate(rpc, probe, keypairs, { accounts });
    if (!sim.ok) {
      // The System program's error 1 on a transfer is the wallet running out.
      const ie = sim.err && sim.err.InstructionError;
      const short = ie && ie[1] && ie[1].Custom === 1 && probeIxs[ie[0]] && probeIxs[ie[0]].programId === PROGRAM.SYSTEM;
      const e = new Error("would fail: " + (short ? "not enough SOL in the wallet" : explainFailure(sim)));
      e.sim = sim;
      e.short = !!short;
      throw e;
    }
    const units = Math.min(1_400_000, Math.ceil(sim.units * (1 + (headroom == null ? 0.2 : headroom))) + 10_000);
    const compiled = compile({ payer, blockhash, instructions: build(units, microLamports) });
    const signed = signCompiled(compiled, keypairs);
    // The exact bytes that will be sent, simulated once more with signature
    // checks on and the real blockhash: a signing fault or a compute limit set
    // too low shows up here, before anything is broadcast. Skipped only for a
    // practice run as another wallet, whose signatures are placeholders.
    if (!keypairs.some(k => k.watchOnly)) {
      const r = await rpc.call("simulateTransaction", [signed.base64, { encoding: "base64", sigVerify: true, commitment: "confirmed" }]);
      const v = (r && r.value) || {};
      if (v.err != null) {
        const e = new Error("would fail as signed: " + explainFailure({ err: v.err, logs: v.logs }));
        e.sim = { err: v.err, logs: v.logs || [] };
        throw e;
      }
    }
    return { ...signed, lastValid, units, sim, blockhash };
  }

  // Send prepared transactions and wait for each to be confirmed. The signed
  // bytes are re-broadcast every few seconds until confirmed or until their
  // blockhash expires, after which they can never land and the outcome is
  // final. onStatus(i, text) reports progress per transaction.
  async function sendAll(rpc, prepared, onStatus) {
    const say = (i, s) => { try { onStatus && onStatus(i, s); } catch (e) {} };
    const state = prepared.map(() => ({ done: false, ok: false, err: null }));
    for (let i = 0; i < prepared.length; i++) {
      try {
        await rpc.call("sendTransaction", [prepared[i].base64, { encoding: "base64", skipPreflight: true, maxRetries: 0 }]);
        say(i, "sent " + prepared[i].signature.slice(0, 8) + "…");
      } catch (e) {
        // A send whose reply is lost may still land, so this is not final.
        say(i, "send answered: " + String(e.message || e).slice(0, 60));
      }
    }
    const started = Date.now();
    for (let round = 0; ; round++) {
      await sleep(round < 5 ? 1500 : 2500);
      const open = prepared.map((p, i) => i).filter(i => !state[i].done);
      if (!open.length) break;
      let st = null;
      try { st = await rpc.call("getSignatureStatuses", [open.map(i => prepared[i].signature)]); } catch (e) {}
      const vals = (st && st.value) || [];
      open.forEach((i, k) => {
        const v = vals[k];
        if (v && (v.confirmationStatus === "confirmed" || v.confirmationStatus === "finalized")) {
          state[i].done = true;
          state[i].ok = v.err == null;
          state[i].err = v.err;
          say(i, v.err == null ? "confirmed" : "failed on chain: " + JSON.stringify(v.err).slice(0, 60));
        }
      });
      const still = prepared.map((p, i) => i).filter(i => !state[i].done);
      if (!still.length) break;
      let height = null;
      try { height = await rpc.call("getBlockHeight", [{ commitment: "confirmed" }]); } catch (e) {}
      const past = still.filter(i => height != null && height > prepared[i].lastValid);
      if (past.length) {
        // Past its blockhash a transaction can no longer land, but it may have
        // landed already and aged out of the recent-status cache. Only a
        // successful read of the full history that knows nothing of it makes
        // it "expired"; an unreadable answer leaves it unknown.
        let hist = null;
        try { hist = await rpc.call("getSignatureStatuses", [past.map(i => prepared[i].signature), { searchTransactionHistory: true }]); } catch (e) {}
        past.forEach((i, k) => {
          const v = hist && hist.value ? hist.value[k] : undefined;
          state[i].done = true;
          if (v === undefined) { state[i].unknown = true; say(i, "could not confirm either way; check Solscan before retrying"); }
          else if (v === null) { state[i].expired = true; say(i, "expired without landing; nothing happened"); }
          else { state[i].ok = v.err == null; state[i].err = v.err; say(i, v.err == null ? "confirmed" : "failed on chain: " + JSON.stringify(v.err).slice(0, 60)); }
        });
      }
      if (round % 2 === 1) {
        for (const i of still) {
          if (state[i].done) continue;
          try { await rpc.call("sendTransaction", [prepared[i].base64, { encoding: "base64", skipPreflight: true, maxRetries: 0 }]); } catch (e) {}
        }
      }
      if (Date.now() - started > 150_000) {
        const left = still.filter(i => !state[i].done);
        let hist = null;
        try { hist = await rpc.call("getSignatureStatuses", [left.map(i => prepared[i].signature), { searchTransactionHistory: true }]); } catch (e) {}
        left.forEach((i, k) => {
          const v = hist && hist.value ? hist.value[k] : null;
          state[i].done = true;
          if (v && v.confirmationStatus && v.confirmationStatus !== "processed") { state[i].ok = v.err == null; state[i].err = v.err; say(i, v.err == null ? "confirmed" : "failed on chain"); }
          else { state[i].unknown = true; say(i, "no answer after 150s; check Solscan before retrying"); }
        });
        break;
      }
    }
    return state.map((s, i) => ({ ...s, signature: prepared[i].signature }));
  }

  // ---------------------------------------------------------- zap slices ---
  // Everything one SOL-only slice needs, in one transaction: a fresh position,
  // SOL wrapped just for this deposit and unwrapped again at the end (so no
  // WSOL is left behind and the temporary account's rent comes straight back).
  //   pool        { address, lb }       lb = parseLbPair() of fresh state
  //   other       { mint, program }     the non-SOL token
  //   initArrays  bin array indexes to create first (practice runs include
  //               them in every slice so each simulates on its own)
  function zapSliceBuilder({ pool, wallet, position, slice, activeId, maxSlip, other, initArrays, initBitmap, bitmapExtension }) {
    const lb = pool.lb;
    const solIsX = lb.tokenXMint === WSOL;
    const wsolAta = ata(wallet, WSOL, PROGRAM.TOKEN);
    return (units, microLamports) => [
      computeLimit(units), computePrice(microLamports),
      ...(initBitmap ? [ixInitializeBitmapExtension({ lbPair: pool.address, funder: wallet })] : []),
      ...(initArrays || []).map(index => ixInitializeBinArray({ lbPair: pool.address, index, funder: wallet })),
      createAtaIdempotent(wallet, wallet, other.mint, other.program),
      createAtaIdempotent(wallet, wallet, WSOL, PROGRAM.TOKEN),
      transfer(wallet, wsolAta, slice.lamports),
      syncNative(wsolAta),
      ixInitializePosition({ payer: wallet, position, lbPair: pool.address, owner: wallet, lowerBinId: slice.lo, width: slice.width }),
      ixAddLiquidityByStrategy2({
        pool, position, sender: wallet,
        amountX: solIsX ? slice.lamports : 0n, amountY: solIsX ? 0n : slice.lamports,
        activeId, maxActiveBinSlippage: maxSlip == null ? 3 : maxSlip,
        minBinId: slice.lo, maxBinId: slice.hi, strategy: slice.strategy, favorX: solIsX, bitmapExtension,
      }),
      closeTokenAccount(wsolAta, wallet, wallet),
    ];
  }
  // Shared accounts a ladder needs before its slices can go out together: the
  // bin arrays nobody has created yet, the bitmap extension for very far
  // ranges, and the wallet's account for the other token.
  function zapPrepBuilder({ pool, wallet, other, initArrays, initBitmap }) {
    return (units, microLamports) => [
      computeLimit(units), computePrice(microLamports),
      ...(initBitmap ? [ixInitializeBitmapExtension({ lbPair: pool.address, funder: wallet })] : []),
      ...(initArrays || []).map(index => ixInitializeBinArray({ lbPair: pool.address, index, funder: wallet })),
      createAtaIdempotent(wallet, wallet, other.mint, other.program),
    ];
  }

  const solText = lamports => "◎" + (Number(lamports) / 1e9).toFixed(3);

  // Opens a SOL-only ladder, from a fresh read of the pool. Used by MET Pool's
  // zap and MET Portfolio's re-enter, so both place a ladder the same way.
  //   actor       the paying wallet: a keypair, or { address, watchOnly }
  //               for a practice run as another address
  //   seedKp      the real keypair, used only to derive position keys
  //   other       { mint, program } of the non-SOL token
  //   plan args   lamports, slices, top, range, strategy
  //   rep         { start, detail, ok, fail, note } progress callbacks
  // Live: one transaction creates what the slices share (bin arrays, the
  // token account), then every slice goes out together so they land at nearly
  // the same price. Practice: every slice is simulated on its own, carrying
  // the shared setup it would need, and nothing is sent.
  // How many more times a transaction that did not go through is built and
  // sent again.
  const RESEND_ROUNDS = 2;
  // Sends prepared transactions (each { ...prepare() result, step }) and
  // sends again whatever is safe to: one that expired never ran, and one
  // `retryable(result)` names changed nothing either. Each of those is built
  // again by `rebuild(item, round, ctx)` (fresh blockhash, the chain as it is
  // then; `ctx` is whatever `refresh()` returned for that round) and sent,
  // RESEND_ROUNDS more times at most. A rebuild that throws is reported as
  // `blocked`. Returns { results, items } aligned with the input: the items
  // that were finally sent, the results they got.
  async function sendWithResend(rpc, prepared, { rebuild, retryable, refresh, detail }) {
    const items = prepared.slice();
    const say = (k, s) => { try { detail && detail(items[k], s); } catch (e) {} };
    const results = await sendAll(rpc, items, say);
    for (let round = 1; round <= RESEND_ROUNDS; round++) {
      const again = results.map((r, k) => (r.expired || (!r.ok && !r.unknown && retryable && retryable(r)) ? k : -1)).filter(k => k >= 0);
      if (!again.length) break;
      let ctx = null;
      if (refresh) { try { ctx = await refresh(); } catch (e) {} }
      const redo = [];
      for (const k of again) {
        say(k, `${results[k].expired ? "did not land" : "refused on chain"}; sending again (${round}/${RESEND_ROUNDS})`);
        try { redo.push({ k, item: await rebuild(items[k], round, ctx) }); }
        catch (e) { results[k] = { ...results[k], expired: false, blocked: String(e.message || e).slice(0, 90) }; }
      }
      if (!redo.length) break;
      const out = await sendAll(rpc, redo.map(x => x.item), (j, st) => { try { detail && detail(redo[j].item, st); } catch (e) {} });
      out.forEach((r, j) => { results[redo[j].k] = r; items[redo[j].k] = redo[j].item; });
    }
    return { results, items };
  }
  // How far the active bin may move before a slice's deposit is refused.
  // The program checks it for every slice, but only a slice next to the price
  // is changed by a move: one 40 bins below can take a 40-bin swing either
  // way and still sit wholly on SOL's side. So each slice allows at least
  // `base`, and as many bins as lie between it and the price. On a fast pool
  // a flat 3 refused every slice but the first (2026-10-01, SI, 1% bins).
  function slipFor(slice, activeId, solIsX, base) {
    const gap = solIsX ? slice.lo - activeId : activeId - slice.hi;
    return Math.max(base, gap);
  }
  // A slice whose transaction failed only because the price moved past that
  // allowance (DLMM 6004, ExceededBinSlippageTolerance): it changed nothing
  // (the whole transaction reverts, position account included), so it can be
  // rebuilt against the price now and sent again.
  function binSlipped(err) {
    const ie = err && err.InstructionError;
    return !!(ie && ie[1] && ie[1].Custom === 6004);
  }
  async function openLadder(rpc, o) {
    const rep = o.rep;
    const wallet = o.actor.address;
    const accs = await getAccounts(rpc, [o.poolAddress]);
    const acc = accs.get(o.poolAddress);
    if (!acc) throw new Error("pool account not found");
    const lb = parseLbPair(acc.data);
    const pool = { address: o.poolAddress, lb };
    const solIsX = lb.tokenXMint === WSOL;
    // only: slices planned earlier that did not land (MET Pool's Retry), sent
    // again as they were, same bins and amounts, instead of a new plan.
    const plan = o.only ? { slices: o.only, strategy: o.strategy }
      : planLadder({ activeId: lb.activeId, binStep: lb.binStep, solIsX, lamports: BigInt(o.lamports),
        slices: o.slices, top: o.top, range: o.range, strategy: o.strategy, bins: o.bins });
    if (!plan.slices.length) throw new Error("nothing to place");
    const label = i => o.only && o.only[i].index != null ? `Slice ${o.only[i].index + 1}` : `Slice ${i + 1}/${plan.slices.length}`;
    if (o.plannedActiveId != null && o.plannedActiveId !== lb.activeId) {
      const moved = lb.activeId - o.plannedActiveId;
      rep.note("Price moved", `${Math.abs(moved)} bin${Math.abs(moved) > 1 ? "s" : ""} ${moved > 0 ? "up" : "down"} since planning; the ladder follows it`);
    }
    const fi = rep.start("Priority fee");
    const micro = await priorityFee(rpc, [PROGRAM.DLMM, o.poolAddress], { helius: o.isHelius });
    rep.ok(fi, `${micro.toLocaleString("en-US")} µ-lamports per unit`);

    const arrays = new Map();
    for (const s of plan.slices) for (const a of binArraysCovering(o.poolAddress, s.lo, s.hi)) arrays.set(a.index, a.address);
    const needBitmap = [...arrays.keys()].some(bitmapOverflow);
    const bitmapAddr = bitmapExtensionAddress(o.poolAddress);
    const otherAta = ata(wallet, o.other.mint, o.other.program);
    const have = await getAccounts(rpc, [...arrays.values(), bitmapAddr, otherAta]);
    const newArrays = [...arrays.entries()].filter(([, a]) => !have.get(a)).map(([i]) => i);
    const initBitmap = needBitmap && !have.get(bitmapAddr);
    const bitmapExtension = needBitmap ? bitmapAddr : null;
    const needAta = !have.get(otherAta);
    const maxSlip = o.maxSlip == null ? 3 : o.maxSlip;
    const openedAt = Date.now();

    if (o.practice) {
      let okCount = 0;
      for (let i = 0; i < plan.slices.length; i++) {
        const s = plan.slices[i];
        const si = rep.start(`Simulate slice ${i + 1}/${plan.slices.length}`);
        const position = freshKeypair(o.seedKp, `${o.poolAddress}|${i}`);
        const mine = binArraysCovering(o.poolAddress, s.lo, s.hi).map(a => a.index).filter(ix => newArrays.includes(ix));
        try {
          const p = await prepare(rpc, { payer: wallet, keypairs: [o.actor, position], microLamports: micro,
            build: zapSliceBuilder({ pool, wallet, position: position.address, slice: s, activeId: lb.activeId,
              maxSlip: slipFor(s, lb.activeId, solIsX, maxSlip), other: o.other, initArrays: mine, initBitmap, bitmapExtension }) });
          okCount++;
          rep.ok(si, `${solText(s.lamports)} · bins ${s.lo}..${s.hi} · ${s.strategy} · ${p.units.toLocaleString("en-US")} units`);
        } catch (e) {
          rep.fail(si, String(e.message || e).slice(0, 120));
        }
      }
      return { dryRun: true, plan, n: plan.slices.length, okCount, ok: okCount === plan.slices.length, landed: [] };
    }

    if (newArrays.length || initBitmap || needAta) {
      const ai = rep.start("Create shared accounts");
      const prep = await prepare(rpc, { payer: wallet, keypairs: [o.actor], microLamports: micro,
        build: zapPrepBuilder({ pool, wallet, other: o.other, initArrays: newArrays, initBitmap }) });
      rep.detail(ai, "sending");
      const [res] = await sendAll(rpc, [prep], (k, s) => rep.detail(ai, s));
      if (!res.ok) {
        rep.fail(ai, res.expired ? "did not land; nothing was created" : res.unknown ? "no confirmation; check Solscan" : "failed on chain");
        throw new Error("the setup transaction did not go through, so no slices were sent");
      }
      rep.ok(ai, `${newArrays.length} bin array${newArrays.length === 1 ? "" : "s"}${needAta ? " + token account" : ""} · ${res.signature.slice(0, 8)}…`);
    }
    const prepared = [];
    for (let i = 0; i < plan.slices.length; i++) {
      const s = plan.slices[i];
      const si = rep.start(label(i));
      const position = freshKeypair(o.seedKp, `${o.poolAddress}|${i}`);
      try {
        const p = await prepare(rpc, { payer: wallet, keypairs: [o.actor, position], microLamports: micro,
          build: zapSliceBuilder({ pool, wallet, position: position.address, slice: s, activeId: lb.activeId,
            maxSlip: slipFor(s, lb.activeId, solIsX, maxSlip), other: o.other, initArrays: [], initBitmap: false, bitmapExtension }) });
        prepared.push({ ...p, slice: s, index: s.index != null ? s.index : i, position: position.address, keypair: position, step: si });
        rep.detail(si, `${solText(s.lamports)} · bins ${s.lo}..${s.hi} · simulated`);
      } catch (e) {
        rep.fail(si, String(e.message || e).slice(0, 120));
      }
    }
    if (!prepared.length) throw new Error("no slice passed its simulation, so nothing was sent");
    // A slice that expired never ran: its blockhash is past and the full
    // history knows nothing of it. One that failed on chain only because the
    // price moved too far (6004) changed nothing either. Both are built again
    // against the pool as it is then (same position key, same bins, same
    // SOL), simulated, and sent with a higher priority fee.
    const sent = await sendWithResend(rpc, prepared, {
      retryable: r => binSlipped(r.err),
      refresh: async () => parseLbPair((await getAccounts(rpc, [o.poolAddress])).get(o.poolAddress).data),
      rebuild: async (p, round, fresh) => {
        const now = fresh || lb;
        const q = await prepare(rpc, { payer: wallet, keypairs: [o.actor, p.keypair], microLamports: Math.max(micro, 50_000) * (round + 1),
          build: zapSliceBuilder({ pool: { address: o.poolAddress, lb: now }, wallet, position: p.position, slice: p.slice,
            activeId: now.activeId, maxSlip: slipFor(p.slice, now.activeId, solIsX, maxSlip), other: o.other, initArrays: [], initBitmap: false, bitmapExtension }) });
        return { ...q, slice: p.slice, index: p.index, position: p.position, keypair: p.keypair, step: p.step };
      },
      detail: (p, st) => rep.detail(p.step, st),
    });
    const results = sent.results;
    prepared.splice(0, prepared.length, ...sent.items);
    const landed = [], unknown = [], missing = [];
    results.forEach((r, k) => {
      const p = prepared[k];
      if (r.ok) { rep.ok(p.step, `${solText(p.slice.lamports)} \u00B7 ${r.signature.slice(0, 8)}\u2026`); landed.push({ position: p.position, slice: p.slice, signature: r.signature }); }
      else if (r.unknown) { rep.fail(p.step, "no confirmation either way; check Solscan before retrying"); unknown.push({ position: p.position, signature: r.signature }); }
      else {
        const slipped = binSlipped(r.err);
        rep.fail(p.step, r.blocked ? `did not open, and would now fail: ${r.blocked}`
          : r.expired ? `did not land after ${RESEND_ROUNDS + 1} tries; nothing happened`
          : slipped ? `price kept moving too fast (${RESEND_ROUNDS + 1} tries); nothing deposited, network fee only`
          : `failed on chain${r.err ? ": " + JSON.stringify(r.err).slice(0, 60) : ""}; nothing deposited, network fee only`);
        missing.push({ ...p.slice, index: p.index, reason: r.expired ? "expired" : "failed" });
      }
    });
    return { plan, n: plan.slices.length, landed, unknown, missing, ok: landed.length === plan.slices.length, openedAt, binStep: lb.binStep, solIsX };
  }

  // ------------------------------------------------------ close and claim --
  // One position's share of a close or a claim, as instructions. A position
  // wider than 70 bins (opened elsewhere, extended) is emptied in 70-bin
  // chunks, one transaction each; only the last one closes it.
  //   pool     { address, lb, bitmapExtension (address or null) }
  //   pos      parsePosition() of the position, fresh
  //   rewards  [{ index, mint, vault, program }] initialised on the pool
  //   mode     "close" | "claim"
  function positionChunks(pos) {
    const lo = pos.owedRange ? pos.owedRange[0] : null, hi = pos.owedRange ? pos.owedRange[1] : null;
    if (lo == null) return [{ lo: null, hi: null, last: true }];
    const out = [];
    for (let a = lo; a <= hi; a += BINS_PER_POSITION) out.push({ lo: a, hi: Math.min(hi, a + BINS_PER_POSITION - 1), last: false });
    out[out.length - 1].last = true;
    return out;
  }
  function positionIxs({ pool, wallet, position, pos, rewards, mode, chunk }) {
    const ixs = [];
    const c = chunk || positionChunks(pos)[0];
    if (c.lo != null) {
      const liquid = pos.bins.some(b => b.binId >= c.lo && b.binId <= c.hi && b.share > 0n);
      if (mode === "close" && liquid) {
        ixs.push(ixRemoveLiquidityByRange2({ pool, position, sender: wallet, fromBinId: c.lo, toBinId: c.hi, bps: 10000, bitmapExtension: pool.bitmapExtension }));
      }
      ixs.push(ixClaimFee2({ pool, position, sender: wallet, minBinId: c.lo, maxBinId: c.hi }));
      for (const r of rewards || []) {
        ixs.push(ixClaimReward2({ pool, position, sender: wallet, rewardIndex: r.index, rewardMint: r.mint,
          rewardVault: r.vault, rewardProgram: r.program, minBinId: c.lo, maxBinId: c.hi }));
      }
    }
    if (mode === "close" && c.last) ixs.push(ixClosePositionIfEmpty({ position, sender: wallet }));
    return ixs;
  }
  // The accounts every close or claim in a pool needs to receive into, created
  // if missing, and the WSOL account emptied back to SOL at the end. Only
  // when SOL is one of the pool's tokens (or a reward): in a pool of two
  // other tokens nothing creates that account, and closing an account that
  // does not exist fails the whole transaction (InvalidAccountData).
  function receiveIxs({ pool, wallet, rewards }) {
    const lb = pool.lb;
    const sol = lb.tokenXMint === WSOL || lb.tokenYMint === WSOL || (rewards || []).some(r => r.mint === WSOL);
    return {
      before: [
        createAtaIdempotent(wallet, wallet, lb.tokenXMint, tokenProgramOf(lb.tokenXProgramFlag)),
        createAtaIdempotent(wallet, wallet, lb.tokenYMint, tokenProgramOf(lb.tokenYProgramFlag)),
        ...(rewards || []).map(r => createAtaIdempotent(wallet, wallet, r.mint, r.program)),
      ],
      after: sol ? [closeTokenAccount(ata(wallet, WSOL, PROGRAM.TOKEN), wallet, wallet)] : [],
    };
  }
  // Packs position work into as few transactions as fit: each unit is one
  // position (or chunk), and a transaction takes units while it stays under
  // the size limit. Returns a list of instruction-list builders.
  function packUnits(wallet, units, maxPerTx, blockhashForSizing) {
    const bh = blockhashForSizing || "11111111111111111111111111111111";
    const groups = [];
    let cur = null;
    const sizeOf = g => compile({ payer: wallet, blockhash: bh, instructions: [
      computeLimit(1_400_000), computePrice(1), ...g.before, ...g.units.flatMap(u => u.ixs), ...g.after] }).size;
    for (const u of units) {
      if (cur && cur.poolKey === u.poolKey) {
        const trial = { ...cur, units: cur.units.concat([u]) };
        // Measured: closing three 70-bin positions in one transaction takes
        // about 1.1M of the 1.4M compute units, so closes go two at a time
        // and claims (about 100k each) three at a time.
        if (sizeOf(trial) <= TX_LIMIT && trial.units.length <= (maxPerTx || 2)) { cur.units.push(u); continue; }
      }
      cur = { poolKey: u.poolKey, before: u.before, after: u.after, units: [u] };
      if (sizeOf(cur) > TX_LIMIT) throw new Error("a single position does not fit a transaction");
      groups.push(cur);
    }
    return groups.map(g => ({
      units: g.units,
      build: (units, micro) => [computeLimit(units), computePrice(micro), ...g.before, ...g.units.flatMap(u => u.ixs), ...g.after],
    }));
  }

  // --------------------------------------------------------------- swaps ----
  // Selling back to SOL through Jupiter. The route and the transaction come
  // from Jupiter; this code decides whether to accept them, and refuses unless
  // every one of these holds:
  //   - the quote is for exactly the token and amount asked, into SOL;
  //   - there is an independent reference price (the pool the tokens came
  //     from, or a market price), and the quote's worst case is no further
  //     below it than maxLossPct, all in. No reference, no sale;
  //   - the transaction is paid by this wallet, needs no other signature, and
  //     calls only compute budget, the associated token program, Jupiter's
  //     aggregator, and the token program to close the wallet's own WSOL
  //     account: nothing that could approve, re-assign or transfer anything;
  //   - simulated, the wallet stays a plain system account, its SOL rises by
  //     at least the quoted minimum (plus any WSOL it unwraps) less the fee,
  //     the token falls by no more than the amount sold, and every other token
  //     account the wallet holds is left exactly as it was.
  const JUP_LITE = "https://lite-api.jup.ag/swap/v1";
  const JUP_KEYED = "https://api.jup.ag/swap/v1";
  const JUPITER_V6 = "JUP6LkbZbjS1jKKwapdHNy74zcZ3tLUZoi5QNyVTaV4";
  const SWAP_PRIORITY_MAX = 2_000_000;
  // Jupiter's free API answers 429 when asked too often; a few seconds later
  // it answers again. Two waits, then the 429 is the answer.
  async function jupiterJson(url, opts) {
    for (let attempt = 0; ; attempt++) {
      try { return await getJson(url, opts); }
      catch (e) {
        if (e.status !== 429 || attempt >= 2) throw e;
        await sleep(2500 * (attempt + 1));
      }
    }
  }
  async function jupiterQuote({ inputMint, amount, slippageBps, apiKey }) {
    const base = apiKey ? JUP_KEYED : JUP_LITE;
    const headers = apiKey ? { "x-api-key": apiKey } : {};
    return jupiterJson(`${base}/quote?inputMint=${inputMint}&outputMint=${WSOL}&amount=${amount}`
      + `&slippageBps=${slippageBps}&restrictIntermediateTokens=true`, { headers, timeout: 15 });
  }
  async function jupiterSwapTx({ quote, wallet, apiKey, maxPriorityLamports }) {
    const base = apiKey ? JUP_KEYED : JUP_LITE;
    const headers = apiKey ? { "x-api-key": apiKey } : {};
    return jupiterJson(`${base}/swap`, { headers, timeout: 20, body: {
      quoteResponse: quote, userPublicKey: wallet, wrapAndUnwrapSol: true, dynamicComputeUnitLimit: true,
      prioritizationFeeLamports: { priorityLevelWithMaxLamports: { maxLamports: maxPriorityLamports || SWAP_PRIORITY_MAX, priorityLevel: "high" } },
    } });
  }
  // The top-level instructions of a transaction message (legacy or v0). A
  // program id can only be a static key in either format; accounts may come
  // from lookup tables, in which case they read as null here.
  function messageInstructions(message) {
    let o = message[0] & 0x80 ? 1 : 0;
    o += 3;
    const nk = readShortvec(message, o); o += nk.size;
    const keys = [];
    for (let i = 0; i < nk.value; i++) { keys.push(b58(message.subarray(o, o + 32))); o += 32; }
    o += 32;
    const ni = readShortvec(message, o); o += ni.size;
    const out = [];
    for (let i = 0; i < ni.value; i++) {
      const pid = message[o]; o += 1;
      const na = readShortvec(message, o); o += na.size;
      const accts = Array.from(message.subarray(o, o + na.value), k => (k < keys.length ? keys[k] : null)); o += na.value;
      const dl = readShortvec(message, o); o += dl.size;
      const data = message.subarray(o, o + dl.value); o += dl.value;
      out.push({ program: pid < keys.length ? keys[pid] : null, accounts: accts, data });
    }
    return { keys, instructions: out };
  }
  function vetSwapMessage(base64, wallet) {
    const tx = unb64(base64);
    const n = readShortvec(tx, 0);
    const { keys, instructions } = messageInstructions(tx.subarray(n.size + 64 * n.value));
    if (keys[0] !== wallet) throw new Error("swap transaction is not paid by this wallet");
    const wsolAta = ata(wallet, WSOL, PROGRAM.TOKEN);
    instructions.forEach((ix, i) => {
      const bad = why => { throw new Error(`swap transaction refused: instruction ${i} ${why}`); };
      if (ix.program === PROGRAM.COMPUTE_BUDGET || ix.program === JUPITER_V6) return;
      if (ix.program === PROGRAM.ATA) {
        if (ix.data.length && ix.data[0] !== 0 && ix.data[0] !== 1) bad("is not an account creation");
        if (ix.accounts[0] !== wallet) bad("creates an account paid by someone else");
        return;
      }
      if (ix.program === PROGRAM.TOKEN || ix.program === PROGRAM.TOKEN_2022) {
        const tag = ix.data[0];
        if (tag === 17 && ix.accounts[0] === wsolAta) return;                                   // SyncNative
        if (tag === 9 && ix.accounts[0] === wsolAta && ix.accounts[1] === wallet && ix.accounts[2] === wallet) return;   // close own WSOL
        bad(`is a token instruction (${tag}) this swap has no reason to make`);
      }
      if (ix.program === PROGRAM.SYSTEM) {
        const tag = ix.data.length >= 4 ? rdU32(ix.data, 0) : -1;
        if (tag === 2 && ix.accounts[0] === wallet && ix.accounts[1] === wsolAta) return;       // wrap into own WSOL
        bad(`is a system instruction (${tag}) this swap has no reason to make`);
      }
      bad(`calls an unexpected program ${String(ix.program).slice(0, 8)}…`);
    });
    return { keys, instructions };
  }
  // Every account a transaction can write, lookup tables resolved: the static
  // keys the header marks writable, then the writable half of each lookup.
  // A transaction cannot change an account it does not list as writable, so
  // this is exactly the set a swap simulation has to account for.
  async function writableAccounts(rpc, base64) {
    const tx = unb64(base64);
    const n = readShortvec(tx, 0);
    const msg = tx.subarray(n.size + 64 * n.value);
    const versioned = !!(msg[0] & 0x80);
    let o = versioned ? 1 : 0;
    const [numSigned, roSigned, roUnsigned] = [msg[o], msg[o + 1], msg[o + 2]];
    o += 3;
    const nk = readShortvec(msg, o); o += nk.size;
    const keys = [];
    for (let i = 0; i < nk.value; i++) { keys.push(b58(msg.subarray(o, o + 32))); o += 32; }
    o += 32;
    const ni = readShortvec(msg, o); o += ni.size;
    for (let i = 0; i < ni.value; i++) {
      o += 1;
      const na = readShortvec(msg, o); o += na.size + na.value;
      const dl = readShortvec(msg, o); o += dl.size + dl.value;
    }
    const writable = keys.filter((k, i) => (i < numSigned && i < numSigned - roSigned) || (i >= numSigned && i < keys.length - roUnsigned));
    if (!versioned) return writable;
    const nl = readShortvec(msg, o); o += nl.size;
    const lookups = [];
    for (let i = 0; i < nl.value; i++) {
      const table = b58(msg.subarray(o, o + 32)); o += 32;
      const nw = readShortvec(msg, o); o += nw.size;
      const w = Array.from(msg.subarray(o, o + nw.value)); o += nw.value;
      const nr = readShortvec(msg, o); o += nr.size + nr.value;
      lookups.push({ table, w });
    }
    if (lookups.length) {
      const tables = await getAccounts(rpc, lookups.map(l => l.table));
      for (const l of lookups) {
        const acc = tables.get(l.table);
        if (!acc) throw new Error("swap refers to a lookup table that does not exist");
        for (const idx of l.w) {
          const at = 56 + idx * 32;
          if (at + 32 > acc.data.length) throw new Error("swap refers past the end of its lookup table");
          writable.push(b58(acc.data.subarray(at, at + 32)));
        }
      }
    }
    return writable;
  }
  // SPL token account (both programs share the first 165 bytes).
  function parseTokenAccount(data) {
    if (!data || data.length < 165) return null;
    // Token-2022 extensions follow the 165-byte base and one account-type
    // byte, as type (u16) / length (u16) / data. The one that matters here is
    // TransferFeeAmount (type 2): an account holding a withheld transfer fee
    // cannot be closed until that fee is harvested to the mint.
    let withheld = 0n;
    for (let o = 166; o + 4 <= data.length;) {
      const type = rdU16(data, o), len = rdU16(data, o + 2);
      if (!type && !len) break;
      if (type === 2 && len >= 8 && o + 12 <= data.length) withheld = rdU64(data, o + 4);
      o += 4 + len;
    }
    return {
      mint: b58(data.subarray(0, 32)), owner: b58(data.subarray(32, 64)), amount: rdU64(data, 64),
      delegateTag: rdU32(data, 72), state: data[108], closeTag: rdU32(data, 129), closeAuthority: b58(data.subarray(133, 165)),
      withheld,
    };
  }
  async function walletTokenAccounts(rpc, wallet) {
    const q = prog => rpc.call("getTokenAccountsByOwner", [wallet, { programId: prog }, { encoding: "base64", commitment: "confirmed" }]);
    // Both programs or nothing: a half-read wallet would hide an account the
    // swap might touch.
    const [a, b] = await Promise.all([q(PROGRAM.TOKEN), q(PROGRAM.TOKEN_2022)]);
    const out = [];
    for (const [program, r] of [[PROGRAM.TOKEN, a], [PROGRAM.TOKEN_2022, b]]) for (const v of (r && r.value) || []) {
      const t = parseTokenAccount(unb64(v.account.data[0]));
      if (t) out.push({ address: v.pubkey, program, lamports: v.account.lamports, ...t });
    }
    return out;
  }
  // One sale. refSolPerRaw: SOL per raw unit of the token at the reference
  // price; required. Returns { ok, dryRun, gotLamports (simulated), signature }
  // or throws with the reason it refused (e.refused for a price refusal).
  // A sale, tried up to three times. A route the price ran away from between
  // quote and simulation (Jupiter 6001, slippage exceeded) is quoted again
  // with more room, up to three times the asked slippage, and a swap that
  // expired unsent is quoted and sent again. The max-loss check against the
  // reference price applies to every try, so the extra room never takes a
  // sale past it.
  async function sellToSol(rpc, args) {
    const say = s => { try { args.onStatus && args.onStatus(s); } catch (e) {} };
    const base = args.slippageBps || 100;
    for (let attempt = 0; ; attempt++) {
      try {
        const res = await sellOnce(rpc, { ...args, slippageBps: base * (attempt + 1) });
        if (res.ok || !res.expired || attempt === 2) return res;
        say("did not land; quoting again");
      } catch (e) {
        if (e.refused || !e.slipped || attempt === 2) throw e;
        say(`price moved; quoting again with ${(base * (attempt + 2) / 100).toFixed(0)}% room`);
      }
    }
  }
  // Jupiter's own instruction failed with 6001, SlippageToleranceExceeded.
  function jupiterSlipped(err, swapBase64) {
    const ie = err && err.InstructionError;
    if (!ie || !ie[1] || ie[1].Custom !== 6001) return false;
    try {
      const tx = unb64(swapBase64), n = readShortvec(tx, 0);
      const { instructions } = messageInstructions(tx.subarray(n.size + 64 * n.value));
      return !!instructions[ie[0]] && instructions[ie[0]].program === JUPITER_V6;
    } catch (e) { return false; }
  }
  async function sellOnce(rpc, { mint, amountRaw, refSolPerRaw, maxLossPct, slippageBps, kp, apiKey, practice, onStatus }) {
    const say = s => { try { onStatus && onStatus(s); } catch (e) {} };
    const amount = BigInt(amountRaw);
    if (amount <= 0n) throw new Error("nothing to sell");
    if (!(refSolPerRaw > 0) || !Number.isFinite(refSolPerRaw)) {
      const e = new Error("no reference price to judge a route against, so it is not sold");
      e.refused = true;
      throw e;
    }
    say("quoting");
    const quote = await jupiterQuote({ inputMint: mint, amount: amount.toString(), slippageBps: slippageBps || 100, apiKey });
    if (quote.inputMint !== mint || quote.outputMint !== WSOL || String(quote.inAmount) !== amount.toString()) {
      throw new Error("the quote is not for the trade that was asked for");
    }
    const out = BigInt(quote.outAmount || "0"), minOut = BigInt(quote.otherAmountThreshold || "0");
    if (!(out > 0n) || !(minOut > 0n)) throw new Error("no route to SOL");
    const fair = Number(amount) * refSolPerRaw;
    const loss = (1 - Number(minOut) / 1e9 / fair) * 100;
    if (loss > maxLossPct) {
      const e = new Error(`best route pays ${(Number(out) / 1e9).toFixed(4)} SOL, ${loss.toFixed(1)}% under the reference price (limit ${maxLossPct}%)`);
      e.refused = true;
      throw e;
    }
    say("building");
    const swap = await jupiterSwapTx({ quote, wallet: kp.address, apiKey });
    vetSwapMessage(swap.swapTransaction, kp.address);
    const signed = signForeign(swap.swapTransaction, kp);
    say("simulating");
    // Watched: the wallet, and every account of the wallet's that the swap
    // could write. Anything else it cannot touch at all.
    const [tokens, writable] = await Promise.all([walletTokenAccounts(rpc, kp.address), writableAccounts(rpc, swap.swapTransaction)]);
    const wsolAta = ata(kp.address, WSOL, PROGRAM.TOKEN);
    const mine = new Set(tokens.map(t => t.address));
    const watch = [kp.address, ...[...new Set(writable)].filter(a => mine.has(a))];
    const pre = await getAccounts(rpc, watch);
    const sim = await rpc.call("simulateTransaction", [signed.base64, { encoding: "base64", sigVerify: !kp.watchOnly,
      replaceRecentBlockhash: !!kp.watchOnly, commitment: "confirmed", accounts: { encoding: "base64", addresses: watch } }]);
    const v = sim.value || {};
    if (v.err != null) {
      const e = new Error("swap would fail: " + explainFailure({ err: v.err, logs: v.logs }));
      e.slipped = jupiterSlipped(v.err, swap.swapTransaction);
      throw e;
    }
    const post = v.accounts || [];
    const walletPost = post[0];
    if (!walletPost || walletPost.owner !== PROGRAM.SYSTEM) throw new Error("swap simulation changes who owns the wallet; not sent");
    let wsolPre = 0n, spent = 0n;
    for (let i = 1; i < watch.length; i++) {
      const before = pre.get(watch[i]);
      const b = before ? parseTokenAccount(before.data) : null;
      const a = post[i] ? parseTokenAccount(unb64(post[i].data[0])) : null;
      if (!b) continue;
      if (watch[i] === wsolAta) { wsolPre = b.amount; continue; }   // closed on purpose: unwrapped to SOL
      if (!a) throw new Error("swap simulation closes a token account it should not touch; not sent");
      if (a.owner !== b.owner || a.delegateTag !== b.delegateTag || a.closeTag !== b.closeTag || a.closeAuthority !== b.closeAuthority) {
        throw new Error("swap simulation changes a token account's authority; not sent");
      }
      if (b.mint === mint) { if (a.amount < b.amount) spent += b.amount - a.amount; }
      else if (a.amount < b.amount) throw new Error("swap simulation takes a token that was not being sold; not sent");
    }
    if (spent > amount) throw new Error("swap simulation sells more than was asked; not sent");
    const gain = BigInt(walletPost.lamports) - BigInt(pre.get(kp.address) ? pre.get(kp.address).lamports : 0);
    const allowance = BigInt(SWAP_PRIORITY_MAX + 20_000);
    if (gain < minOut + wsolPre - allowance || gain <= 0n) {
      throw new Error(`swap simulation pays ${(Number(gain) / 1e9).toFixed(4)} SOL, under the quoted minimum; not sent`);
    }
    const got = Number(gain - wsolPre);
    if (practice) return { ok: true, dryRun: true, gotLamports: got, quoteOut: out };
    say("sending");
    const res = (await sendAll(rpc, [{ ...signed, lastValid: swap.lastValidBlockHeight || Number.MAX_SAFE_INTEGER }], (i, s) => say(s)))[0];
    return { ok: res.ok, gotLamports: got, quoteOut: out, signature: res.signature, expired: res.expired, unknown: res.unknown };
  }

  // Deterministic but unpredictable: the wallet's secret goes in, so nobody
  // without it can guess the address, and nothing needs a random number
  // generator the phone may not expose to scripts.
  function freshKeypair(wallet, label) {
    const salt = utf8(`${label}|${Date.now()}|${Math.random()}|${Math.random()}`);
    return keypairFromSeed(sha512(concat([wallet.seed, utf8("MET position key"), salt])).slice(0, 32));
  }

  return {
    // ladders and sending
    ladderShape, planLadder, slipFor, binSlipped, priorityFee, simulate, explainFailure, prepare, sendAll, sendWithResend, RESEND_ROUNDS, freshKeypair,
    zapSliceBuilder, zapPrepBuilder, openLadder, positionChunks, positionIxs, receiveIxs, packUnits,
    jupiterQuote, jupiterSwapTx, sellToSol, vetSwapMessage, messageInstructions, parseTokenAccount, walletTokenAccounts, writableAccounts,
    // bytes and encodings
    harvestWithheldToMint,
    concat, toHex, fromHex, utf8, eqBytes, u8, u16, u32, i32, u64, i64, rdU16, rdU32, rdI32, rdU64, rdI64, rdU128,
    shortvec, readShortvec, b58, unb58, b64, unb64, keyBytes, isAddress,
    // hashing and keys
    sha256, sha512, keypairFromSeed, sign, verify, parseSecretKey, onCurve, findPda, ata,
    // transactions
    TX_LIMIT, meta, compile, signCompiled, signForeign,
    // network
    SCRIPTABLE, sleep, http, parseReply, getJson, makeRpc, getAccounts,
    // programs
    PROGRAM, WSOL, ix, computeLimit, computePrice, transfer, syncNative, closeTokenAccount, createAtaIdempotent,
    // dlmm
    BINS_PER_ARRAY, BINS_PER_POSITION, POSITION_RENT, BIN_ARRAY_RENT, TOKEN_ACCOUNT_RENT, STRATEGY, rentRates,
    eventAuthority, binArrayIndex, binArrayAddress, bitmapExtensionAddress, bitmapOverflow, binArraysCovering,
    parseLbPair, baseFeeRate, parsePosition, parseBinArray, parseMint, tokenProgramOf, hasDisc,
    ixInitializePosition, ixInitializeBinArray, ixInitializeBitmapExtension, ixAddLiquidityByStrategy2,
    ixRemoveLiquidityByRange2, ixClaimFee2, ixClaimReward2, ixClosePositionIfEmpty,
    binPrice, binsForDrop, dropForBins,
  };
})();

// =============================================================================
//  MET UI: formatting, colours and the table pieces MET Pool and MET Portfolio
//  both draw. Spliced in at the //__MET_UI__ marker by tools/build.mjs.
// =============================================================================
const COL = {
  text: new Color("#F5F5FF"),
  muted: new Color("#9595B2"),
  faint: new Color("#6C6C89"),
  accent: new Color("#F54B00"),   // Meteora orange
  yellow: new Color("#F5BD00"),
  purple: new Color("#6E45FF"),
  green: new Color("#30D158"),
  red: new Color("#FF453A"),
  blue: new Color("#0A84FF"),
  bgTop: new Color("#1E1F35"),
  bgBot: new Color("#12121C"),
};

const SOL_SIGN = "◎";
function fmtSol(v, digits, signed) {
  if (v == null || !Number.isFinite(v)) return SOL_SIGN + " --";
  const a = Math.abs(v);
  const d = digits != null ? digits : a >= 100 ? 1 : a >= 1 ? 2 : a >= 0.01 ? 3 : 4;
  const sign = signed ? (v > 0 ? "+" : v < 0 ? "-" : "") : (v < 0 ? "-" : "");
  return sign + SOL_SIGN + a.toFixed(d);
}
function fmtUsd(v) {
  if (v == null || isNaN(v)) return "$ --";
  return "$" + (v >= 1000 ? v.toLocaleString("en-US", { maximumFractionDigits: 0 }) : v.toFixed(2));
}
function fmtCompact(v) {
  if (v == null || isNaN(v)) return "--";
  if (v <= 0) return "0";
  for (const [n, sfx] of [[1e9, "B"], [1e6, "M"], [1e3, "K"]]) {
    if (v >= n) { const x = v / n; return (x >= 100 ? x.toFixed(0) : x >= 10 ? x.toFixed(1) : x.toFixed(2)) + sfx; }
  }
  return v >= 1 ? v.toFixed(0) : v.toPrecision(2);
}
const fmtShort = v => fmtCompact(v).replace(/\.0+(?=[KMB]$)/, "");

// ------------------------------------------------------------ display ---
// Everything is counted in SOL. DISPLAY_CURRENCY in MET Secrets ("SOL",
// "USD" or "IDR") only changes how what you HOLD and EARN is shown: net
// worth, values, fees, PnL. Amounts that move (a deposit, rent, what a sale
// returns, every confirm dialog) stay in SOL, because that is what is sent.
// With no price to convert with, the SOL figure is shown.
const DISPLAY = { cur: "SOL", perSol: 1, perUsd: 1 };
function displayCurrency(v) {
  const c = String(v == null ? "" : v).trim().toUpperCase();
  return c === "USD" || c === "IDR" ? c : "SOL";
}
// The currency picked in the app (MET Portfolio's "Show money in" row), kept
// in the library directory so the widgets and MET Pool follow it. Until one
// is picked, MET Secrets' DISPLAY_CURRENCY is the default.
function displayChoicePath() { const fm = FileManager.local(); return fm.joinPath(fm.libraryDirectory(), "met_display.json"); }
function loadDisplayChoice(fallback) {
  try {
    const fm = FileManager.local(), p = displayChoicePath();
    if (fm.fileExists(p)) { const c = JSON.parse(fm.readString(p)); if (c && c.cur) return displayCurrency(c.cur); }
  } catch (e) {}
  return displayCurrency(fallback);
}
function saveDisplayChoice(cur) {
  try { FileManager.local().writeString(displayChoicePath(), JSON.stringify({ cur: displayCurrency(cur), t: Date.now() })); } catch (e) {}
}
// solUsd: dollars per SOL; usdIdr: rupiah per dollar (only IDR needs it).
function setDisplay(cur, solUsd, usdIdr) {
  const perUsd = cur === "IDR" ? (usdIdr > 0 ? usdIdr : null) : 1;
  DISPLAY.cur = cur;
  DISPLAY.perUsd = perUsd;
  DISPLAY.perSol = cur === "SOL" ? 1 : solUsd > 0 && perUsd ? solUsd * perUsd : null;
}
const displayIsFiat = () => DISPLAY.cur !== "SOL" && DISPLAY.perSol != null;
// 1234567.5 -> "1.234.568" (Indonesian: dots between thousands, comma for decimals).
function idNum(x, decimals) {
  const [i, f] = Math.abs(x).toFixed(decimals).split(".");
  return i.replace(/\B(?=(\d{3})+(?!\d))/g, ".") + (f ? "," + f : "");
}
// compact: for widgets and table cells, where "Rp 101.612.345" does not fit;
// rupiah then shortens to rb (thousand), jt (million) and M (billion).
function fmtFiat(v, cur, signed, compact) {
  if (v == null || !Number.isFinite(v)) return cur === "IDR" ? "Rp --" : "$ --";
  const a = Math.abs(v);
  const sign = signed ? (v > 0 ? "+" : v < 0 ? "-" : "") : (v < 0 ? "-" : "");
  if (cur === "IDR") {
    if (compact && a >= 1e9) return `${sign}Rp ${idNum(a / 1e9, a >= 1e11 ? 0 : 2)} M`;
    if (compact && a >= 1e6) return `${sign}Rp ${idNum(a / 1e6, a >= 1e8 ? 0 : 1)} jt`;
    if (compact && a >= 1e4) return `${sign}Rp ${idNum(a / 1e3, 0)} rb`;
    return `${sign}Rp ${idNum(a, 0)}`;
  }
  if (compact && a >= 1e6) return `${sign}$${(a / 1e6).toFixed(2)}M`;
  if (compact && a >= 1e4) return `${sign}$${(a / 1e3).toFixed(1)}K`;
  if (a >= 1000) return `${sign}$${a.toLocaleString("en-US", { maximumFractionDigits: 0 })}`;
  return `${sign}$${a.toFixed(a >= 0.01 || a === 0 ? 2 : 4)}`;
}
// A SOL amount you hold or earned, in the display currency.
function fmtVal(sol, digits, signed, compact) {
  if (!displayIsFiat() || sol == null || !Number.isFinite(sol)) return fmtSol(sol, digits, signed);
  return fmtFiat(sol * DISPLAY.perSol, DISPLAY.cur, signed, compact);
}
// A dollar amount: dollars, or rupiah when that is the display currency.
function fmtMoney(usd, compact) {
  return DISPLAY.cur === "IDR" && DISPLAY.perUsd ? fmtFiat(usd * DISPLAY.perUsd, "IDR", false, compact) : fmtFiat(usd, "USD", false, compact);
}

// Rupiah per dollar. Exchange rates move slowly: one read every six hours,
// kept in the library directory, and an older reading is still better than
// none when the sources cannot be reached. Two keyless FX sources, then
// CoinGecko's SOL price in both currencies.
async function usdIdrRate() {
  const fm = FileManager.local();
  const path = fm.joinPath(fm.libraryDirectory(), "met_fx_usd_idr.json");
  let cached = null;
  try { if (fm.fileExists(path)) cached = JSON.parse(fm.readString(path)); } catch (e) {}
  if (cached && cached.rate > 0 && Date.now() - cached.t < 6 * 3600 * 1000) return cached.rate;
  const sources = [
    ["https://open.er-api.com/v6/latest/USD", j => j && j.rates && j.rates.IDR],
    ["https://api.frankfurter.dev/v1/latest?base=USD&symbols=IDR", j => j && j.rates && j.rates.IDR],
    ["https://api.coingecko.com/api/v3/simple/price?ids=solana&vs_currencies=usd,idr",
      j => j && j.solana && j.solana.usd > 0 ? j.solana.idr / j.solana.usd : null],
  ];
  for (const [url, pick] of sources) {
    try {
      const rate = Number(pick(await MET.getJson(url)));
      if (rate > 1000 && rate < 1e6) {
        try { fm.writeString(path, JSON.stringify({ t: Date.now(), rate })); } catch (e) {}
        return rate;
      }
    } catch (e) {}
  }
  return cached && cached.rate > 0 ? cached.rate : null;
}
function fmtPct(v, digits) {
  if (v == null || isNaN(v)) return "--";
  if (digits == null) digits = Math.abs(v) >= 100 ? 0 : Math.abs(v) >= 10 ? 1 : 2;
  return v.toFixed(digits) + "%";
}
function fmtAge(days) {
  if (days == null || isNaN(days)) return "--";
  if (days < 1 / 24) return `${Math.max(1, Math.round(days * 1440))}m`;
  if (days < 1) return `${(days * 24).toFixed(1)}h`;
  if (days < 14) return `${days.toFixed(1)}d`;
  return `${Math.round(days / 7)}w`;
}
const fmtTime = ts => new Date(ts).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
const fmtTimeSec = ts => new Date(ts).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" });
const lamportsToSol = l => Number(l) / 1e9;
const shortAddr = a => a ? a.slice(0, 4) + "…" + a.slice(-4) : "";

// Fee-rate colours, in %/day. Memecoin DLMM pools run hot: the ranked table's
// top rows sit at 50-300%/day, and one under 10%/day is barely paying for the
// risk of holding the token it buys.
const FEE_RATE_STRONG = 50, FEE_RATE_OK = 10;
function feeRateColor(pct) {
  if (pct == null || isNaN(pct)) return COL.faint;
  return pct >= FEE_RATE_STRONG ? COL.green : pct >= FEE_RATE_OK ? COL.yellow : COL.red;
}
const signColor = v => v == null || isNaN(v) ? COL.muted : v >= 0 ? COL.green : COL.red;
const AGE_NEW_DAYS = 1, AGE_OLD_DAYS = 7;
function ageColor(days) {
  if (days == null || isNaN(days)) return COL.muted;
  return days < AGE_NEW_DAYS ? COL.red : days < AGE_OLD_DAYS ? COL.yellow : COL.green;
}

// ---- widget pieces ----------------------------------------------------------
function cell(parent, label, value, color) {
  const s = parent.addStack();
  s.layoutVertically();
  const l = s.addText(label.toUpperCase());
  l.font = Font.semiboldSystemFont(9);
  l.textColor = COL.faint;
  s.addSpacer(3);
  const v = s.addText(value);
  v.font = Font.semiboldSystemFont(15);
  v.textColor = color;
  v.lineLimit = 1;
  v.minimumScaleFactor = 0.7;
}
function smallCell(parent, label, value, color) {
  const s = parent.addStack();
  s.layoutVertically();
  const l = s.addText(label);
  l.font = Font.mediumSystemFont(8);
  l.textColor = COL.faint;
  s.addSpacer(2);
  const v = s.addText(value);
  v.font = Font.semiboldSystemFont(12);
  v.textColor = color;
  v.lineLimit = 1;
  v.minimumScaleFactor = 0.7;
}
function baseWidget() {
  const w = new ListWidget();
  w.spacing = 0;
  const g = new LinearGradient();
  g.colors = [COL.bgTop, COL.bgBot];
  g.locations = [0, 1];
  w.backgroundGradient = g;
  return w;
}
function col(stack, text, width, font, color, right) {
  const s = stack.addStack();
  s.size = new Size(width, 0);
  s.spacing = 0;
  const t = s.addText(text);
  t.font = font;
  t.textColor = color;
  t.lineLimit = 1;
  t.minimumScaleFactor = 0.75;
  if (right) t.rightAlignText(); else t.leftAlignText();
}
const WIDGET_BOX = {
  956: [382, 170], 932: [382, 170], 926: [364, 162], 896: [360, 159],
  874: [356, 158], 852: [345, 158], 844: [338, 155], 812: [329, 148],
  736: [338, 148], 667: [311, 148], 568: [282, 141],
};
function widgetSize() {
  const h = Math.round(Device.screenSize().height);
  const box = WIDGET_BOX[h] || [329, 148];
  return config.widgetFamily === "medium" ? { w: box[0], h: box[1] } : { w: box[0], h: box[0] };
}

// Where the price sits across a range of bins. Bins count up with the price of
// token X in token Y, so for a SOL/token pool (SOL is X) the token's own price
// runs the other way and the bar is flipped to keep "right = token dearer".
function rangeBar(inRange, lo, hi, current, w, h, flip) {
  w = w || 60; h = h || 12;
  const dc = new DrawContext();
  dc.size = new Size(w, h);
  dc.opaque = false;
  dc.respectScreenScale = true;
  const midY = h / 2, trackH = 3;
  dc.setFillColor(new Color("#FFFFFF", 0.18));
  dc.fillRect(new Rect(0, midY - trackH / 2, w, trackH));
  if (hi < lo) return dc.getImage();
  let f = hi === lo ? 0.5 : (current - lo) / (hi - lo);
  if (!isFinite(f)) f = 0.5;
  if (flip) f = 1 - f;
  const above = f >= 1;
  const hex = inRange ? "#30D158" : (above ? "#F5BD00" : "#FF453A");
  dc.setFillColor(new Color(hex, 0.35));
  dc.fillRect(new Rect(0, midY - trackH / 2, w, trackH));
  f = Math.max(0, Math.min(1, f));
  const mw = 3, x = Math.max(0, Math.min(w - mw, f * w - mw / 2));
  dc.setFillColor(new Color(hex));
  dc.fillRect(new Rect(x, 0, mw, h));
  return dc.getImage();
}

// ---- table pieces -----------------------------------------------------------
function statusRow(t, kind, text, onTap, right) {
  const r = new UITableRow();
  r.height = 22;
  const c = r.addText(`●  ${text}`);
  c.titleFont = Font.semiboldSystemFont(11);
  c.titleColor = kind === "refreshing" ? COL.yellow : kind === "fresh" ? COL.green : COL.red;
  if (right) {
    c.widthWeight = 72;
    const g = r.addText(right);
    g.widthWeight = 28;
    g.rightAligned();
    g.titleFont = Font.semiboldSystemFont(11);
    g.titleColor = COL.muted;
  }
  if (onTap) { r.onSelect = onTap; r.dismissOnSelect = false; }
  t.addRow(r);
}
function statusText(stale, dataTime, refreshing, error) {
  if (refreshing) return `Refreshing…   ·   showing data from ${fmtTimeSec(dataTime)}`;
  if (stale) return `Stale   ·   data from ${fmtTimeSec(dataTime)}   ·   fetch failed${error ? " (" + error + ")" : ""}   ·   tap to retry`;
  return `Updated ${fmtTimeSec(Date.now())}   ·   tap to refresh`;
}
// Table cells truncate instead of wrapping, so long text becomes a row a line.
function wrapLines(text, width) {
  const words = String(text == null ? "" : text).split(/\s+/).filter(Boolean);
  const out = [];
  let cur = "";
  for (const w of words) {
    if (!cur) cur = w;
    else if ((cur + " " + w).length <= width) cur += " " + w;
    else { out.push(cur); cur = w; }
    while (cur.length > width) { out.push(cur.slice(0, width)); cur = cur.slice(width); }
  }
  if (cur) out.push(cur);
  return out.length ? out : [""];
}
// The log of an action: stays on screen after it finishes, so the outcome can
// be read, and warns while running that the script must stay open.
function drawProgress(t, progress) {
  if (!progress) return;
  const head = new UITableRow();
  head.height = 34;
  const hc = head.addText(progress.running ? `Working: ${progress.title}`
    : (progress.outcome && progress.outcome.ok ? `Done: ${progress.title}` : `Problem: ${progress.title}`));
  hc.titleFont = Font.boldSystemFont(13);
  hc.titleColor = progress.running ? COL.yellow : (progress.outcome && progress.outcome.ok) ? COL.green : COL.red;
  t.addRow(head);
  if (progress.running) {
    const wr = new UITableRow();
    wr.height = 24;
    const wc = wr.addText("Keep this script open until it finishes.");
    wc.titleFont = Font.semiboldSystemFont(11);
    wc.titleColor = COL.yellow;
    t.addRow(wr);
  }
  for (const st of progress.steps) {
    // A step still marked running when the action is over is where it stopped.
    const state = st.state === "run" && !progress.running ? "fail" : st.state;
    const mark = state === "ok" ? "✓" : state === "fail" ? "✗" : state === "skip" ? "\u25CB" : state === "info" ? "·" : "…";
    const colour = state === "ok" ? COL.green : state === "fail" ? COL.red : state === "run" ? COL.yellow : COL.muted;
    const detail = wrapLines(st.detail, 46);
    const sr = new UITableRow();
    sr.height = st.detail ? 40 : 26;
    const c = sr.addText(`${mark}   ${st.label}`, detail[0] || "");
    c.titleFont = Font.systemFont(12);
    c.titleColor = colour;
    c.subtitleFont = Font.systemFont(10);
    c.subtitleColor = COL.faint;
    t.addRow(sr);
    for (const extra of detail.slice(1)) {
      const er = new UITableRow();
      er.height = 20;
      const ec = er.addText("      " + extra);
      ec.titleFont = Font.systemFont(10);
      ec.titleColor = COL.faint;
      t.addRow(er);
    }
  }
  if (progress.outcome) {
    const lines = wrapLines(progress.outcome.text, 44);
    const orow = new UITableRow();
    orow.height = 40;
    const oc = orow.addText(lines[0], lines[1] || progress.outcome.sub || "");
    oc.titleFont = Font.semiboldSystemFont(12);
    oc.titleColor = progress.outcome.ok ? COL.green : COL.red;
    oc.subtitleFont = Font.systemFont(11);
    oc.subtitleColor = COL.muted;
    if (progress.outcome.link) { orow.onSelect = () => Safari.open(progress.outcome.link); orow.dismissOnSelect = false; }
    t.addRow(orow);
    for (const extra of lines.slice(2)) {
      const er = new UITableRow();
      er.height = 20;
      const ec = er.addText(extra);
      ec.titleFont = Font.systemFont(11);
      ec.titleColor = progress.outcome.ok ? COL.green : COL.red;
      t.addRow(er);
    }
    if (progress.outcome.sub && lines.length > 1) {
      const gr = new UITableRow();
      gr.height = 22;
      const gc = gr.addText(progress.outcome.sub);
      gc.titleFont = Font.systemFont(11);
      gc.titleColor = COL.muted;
      t.addRow(gr);
    }
    if (progress.retry && !progress.running) {
      const rr = new UITableRow();
      rr.height = 38;
      const rb = rr.addButton("↻  Retry");
      rb.centerAligned();
      rb.titleFont = Font.semiboldSystemFont(14);
      rb.onTap = progress.retry;
      t.addRow(rr);
    }
  }
}
// The same callbacks every action reports through: steps appear in the table
// as they start, update and finish.
function makeReporter(progress, redraw) {
  return {
    start(l) { progress.steps.push({ label: l, state: "run", detail: "" }); redraw(); return progress.steps.length - 1; },
    detail(i, d) { if (progress.steps[i]) { progress.steps[i].detail = d; redraw(); } },
    ok(i, d) { if (progress.steps[i]) { progress.steps[i].state = "ok"; progress.steps[i].detail = d || ""; redraw(); } },
    fail(i, d) { if (progress.steps[i]) { progress.steps[i].state = "fail"; progress.steps[i].detail = d || ""; redraw(); } },
    skip(i, d) { if (progress.steps[i]) { progress.steps[i].state = "skip"; progress.steps[i].detail = d || ""; redraw(); } },
    note(l, d) { progress.steps.push({ label: l, state: "info", detail: d || "" }); redraw(); },
  };
}

// When an RPC reply cannot be read, the evidence goes to "MET debug.txt" in
// the Scriptable iCloud folder (the host and the reply only, never a key; the
// last five kept), where it can be looked at from a computer.
function saveRpcDebug(rec) {
  try {
    const fm = FileManager.iCloud();
    const p = fm.joinPath(fm.documentsDirectory(), "MET debug.txt");
    let list = [];
    try { if (fm.fileExists(p)) list = JSON.parse(fm.readString(p)); } catch (e) {}
    list = [rec].concat(Array.isArray(list) ? list : []).slice(0, 5);
    fm.writeString(p, JSON.stringify(list, null, 1));
  } catch (e) {}
}

// Keys and RPC, read the same way by both scripts from "MET Secrets".
function metRpcs(secret) {
  const helius = secret("HELIUS_API_KEY"), custom = secret("SOLANA_RPC_URL");
  const keyed = [
    /^https:\/\//.test(custom) ? custom : null,
    /^[A-Za-z0-9-]{20,}$/.test(helius) ? "https://mainnet.helius-rpc.com/?api-key=" + helius : null,
  ].filter(Boolean);
  return {
    keyed,
    isHelius: keyed.some(u => /helius/i.test(u)),
    read: MET.makeRpc(keyed.length ? keyed : ["https://api.mainnet-beta.solana.com"], { onBadReply: saveRpcDebug }),
    send: keyed.length ? MET.makeRpc(keyed, { onBadReply: saveRpcDebug }) : null,
  };
}
// The ladder store MET Pool writes after a zap: which positions were opened
// together, and with what shape. Shared through the library directory.
function ladderStorePath(wallet) {
  const f = FileManager.local();
  return f.joinPath(f.libraryDirectory(), `met_ladders_${wallet}.json`);
}
function loadLadders(wallet) {
  try {
    const f = FileManager.local(), p = ladderStorePath(wallet);
    if (f.fileExists(p)) return JSON.parse(f.readString(p)) || [];
  } catch (e) {}
  return [];
}
// Positions that join a ladder later (MET Pool's Retry of slices that did
// not land) go into that ladder's record. False when there is none yet.
function addToLadder(wallet, pool, openedAt, positions) {
  try {
    const f = FileManager.local(), p = ladderStorePath(wallet);
    const all = loadLadders(wallet);
    const rec = all.find(x => x.pool === pool && x.openedAt === openedAt);
    if (!rec) return false;
    rec.positions = [...new Set([...(rec.positions || []), ...positions])];
    f.writeString(p, JSON.stringify(all.slice(-400)));
    return true;
  } catch (e) { return false; }
}
function saveLadder(wallet, rec) {
  try {
    const f = FileManager.local(), p = ladderStorePath(wallet);
    const cutoff = Date.now() - 45 * 86400000;
    const all = loadLadders(wallet).filter(x => x.openedAt > cutoff);
    all.push(rec);
    f.writeString(p, JSON.stringify(all.slice(-400)));
  } catch (e) {}
}


// How money is shown beside SOL (setDisplay in the UI part): what was picked
// in MET Portfolio's "Show money in" row, else MET Secrets, else SOL.
// Deposits and rent here are always SOL, since that is what a zap spends.
const DISPLAY_CUR = loadDisplayChoice(SECRETS.DISPLAY_CURRENCY);

// ----------------------------- keys and RPC ----------------------------------
const RPCS = metRpcs(secret);
const IS_HELIUS = RPCS.isHelius;
// Reads fall back to the public endpoint; sending needs a keyed one, because
// the public node throttles hard and simulations are part of every send.
const READ_RPC = RPCS.read;
const SEND_RPC = RPCS.send;

let KEY_ERROR = null;
const KP = (() => {
  const raw = secret("SOLANA_PRIVATE_KEY");
  if (!raw) return null;
  try { return MET.parseSecretKey(raw); } catch (e) { KEY_ERROR = e.message; return null; }
})();
// Practice only: simulate every step as another (funded) address, with the
// signature checks off, to see what a zap would do before funding a wallet.
// Ignored the moment PRACTICE_MODE is false.
const PRACTICE_AS = DRY_RUN && MET.isAddress(secret("PRACTICE_WALLET")) ? secret("PRACTICE_WALLET") : null;
const ACTOR = KP ? (PRACTICE_AS ? { address: PRACTICE_AS, watchOnly: true } : KP) : null;
const WALLET = ACTOR ? ACTOR.address : null;
const ZAP_READY = !!(KP && SEND_RPC);
// What the table is missing for its open buttons, named by the row that
// stands where they would be.
const ZAP_MISSING = ZAP_READY ? null
  : !KP ? (KEY_ERROR ? "a valid SOLANA_PRIVATE_KEY (" + KEY_ERROR + ")" : "SOLANA_PRIVATE_KEY")
  : "HELIUS_API_KEY";

// ----------------------------- constants -------------------------------------
const WSOL = MET.WSOL;
const DATAPI = "https://dlmm.datapi.meteora.ag";
const DEX_PAIRS = "https://api.dexscreener.com/latest/dex/pairs/solana/";
const DEX_URL = "https://dexscreener.com/solana";
const METEORA_URL = "https://app.meteora.ag/dlmm";
const REFRESH_MIN = 15;

// Filters. A TVL floor because fee yield divides by TVL: a nearly empty pool
// with a couple of trades shows a rate no position could realise. The market
// cap floor is the widget Parameter ("300K", "2M").
const MCAP_DEFAULT = 300_000;
let MIN_MCAP = MCAP_DEFAULT;
const MIN_TVL = 10_000;
const MIN_VOL24 = 5_000;
const RANK_SIZE = 150;

// Never deposit the last of the SOL: closing a position later costs a fee too.
const RESERVE_SOL = 0.02;
const MAX_ACTIVE_SLIP = 3;

// ----------------------------- cache -----------------------------------------
// libraryDirectory, not cacheDirectory: the widget runs in its own process
// and only the library directory is shared between it and the app.
const fm = FileManager.local();
function cachePath(suffix) { return fm.joinPath(fm.libraryDirectory(), `met_pools_${suffix}.json`); }
function saveJson(suffix, data) { try { fm.writeString(cachePath(suffix), JSON.stringify(data)); } catch (e) {} }
function loadJson(suffix) {
  try { const p = cachePath(suffix); if (fm.fileExists(p)) return JSON.parse(fm.readString(p)); } catch (e) {}
  return null;
}

function parseUsd(v) {
  if (v == null) return null;
  const m = String(v).trim().replace(/[$,\s]/g, "").match(/^(\d+(?:\.\d+)?)([kmb])?$/i);
  if (!m) return null;
  const n = Number(m[1]) * ({ k: 1e3, m: 1e6, b: 1e9 }[(m[2] || "").toLowerCase()] || 1);
  return n > 0 ? Math.round(n) : null;
}
{
  const fromParam = parseUsd(args.widgetParameter);
  const fromQuery = parseUsd(args.queryParameters && args.queryParameters.mcap);
  MIN_MCAP = fromParam || fromQuery || MCAP_DEFAULT;
}
const DATA_SUFFIX = MIN_MCAP === MCAP_DEFAULT ? "data" : `data_${MIN_MCAP}`;
const ROW_SCHEMA = 1;
function loadDataCache() {
  const c = loadJson(DATA_SUFFIX);
  return c && c.v === ROW_SCHEMA && c.data && c.data.rows ? c : null;
}

// ----------------------------- sparkline -------------------------------------
// A 24h price shape drawn from what the row already holds: the current price
// and the 5m/1h/6h/24h changes give five past prices. Time runs
// logarithmically toward now, so a fresh dump is visible rather than a pixel.
const SPARK_LEGS = [[24, "chg24"], [6, "chg6h"], [1, "chg1h"], [5 / 60, "chg5m"], [0, null]];
function sparkPoints(r) {
  const now = Number(r && r.priceUsd);
  if (!(now > 0)) return null;
  const pts = [];
  for (const [hoursAgo, field] of SPARK_LEGS) {
    const chg = field == null ? 0 : r[field];
    if (chg == null || chg <= -99.9) continue;
    const past = now / (1 + chg / 100);
    if (!(past > 0) || !Number.isFinite(past)) continue;
    pts.push({ t: hoursAgo, p: past });
  }
  return pts.length >= 2 ? pts : null;
}
function sparkline(r, w, h) {
  try {
    const pts = sparkPoints(r);
    if (!pts) return null;
    const dc = new DrawContext();
    dc.size = new Size(w, h);
    dc.opaque = false;
    dc.respectScreenScale = true;
    const padY = 3, padX = 1, span = Math.log(1 + 24);
    const xs = pts.map(q => padX + (1 - Math.log(1 + q.t) / span) * (w - 2 * padX));
    const lo = Math.min(...pts.map(q => q.p)), hi = Math.max(...pts.map(q => q.p));
    const ys = pts.map(q => hi === lo ? h / 2 : (h - padY) - ((q.p - lo) / (hi - lo)) * (h - 2 * padY));
    const up = pts[pts.length - 1].p >= pts[0].p;
    const line = up ? COL.green : COL.red;
    const fill = new Path();
    fill.move(new Point(xs[0], h));
    for (let i = 0; i < xs.length; i++) fill.addLine(new Point(xs[i], ys[i]));
    fill.addLine(new Point(xs[xs.length - 1], h));
    fill.closeSubpath();
    dc.setFillColor(new Color(up ? "#30D158" : "#FF453A", 0.14));
    dc.addPath(fill);
    dc.fillPath();
    const path = new Path();
    path.move(new Point(xs[0], ys[0]));
    for (let i = 1; i < xs.length; i++) path.addLine(new Point(xs[i], ys[i]));
    dc.setStrokeColor(line);
    dc.setLineWidth(1.6);
    dc.addPath(path);
    dc.strokePath();
    const cx = xs[xs.length - 1], cy = ys[ys.length - 1];
    dc.setFillColor(line);
    dc.fillEllipse(new Rect(cx - 2.4, cy - 2.4, 4.8, 4.8));
    return dc.getImage();
  } catch (e) {
    return null;   // no chart is fine; a broken table is not
  }
}

// ----------------------------- data ------------------------------------------
// One row shape for every way a pool reaches the table: the ranked list, a
// search, a pool opened from a link.
function poolRow(p, now) {
  if (!p || !p.token_x || !p.token_y) return null;
  const solIsX = p.token_x.address === WSOL, solIsY = p.token_y.address === WSOL;
  if (!solIsX && !solIsY) return null;
  const tok = solIsX ? p.token_y : p.token_x;
  const sol = solIsX ? p.token_x : p.token_y;
  const num = v => (v == null || !Number.isFinite(Number(v)) ? null : Number(v));
  const w = (o, k) => (o && o[k] != null ? Number(o[k]) : null);
  const cfg = p.pool_config || {};
  const tvl = num(p.tvl) || 0;
  const created = num(p.created_at);
  return {
    id: p.address,
    symbol: tok.symbol || tok.address.slice(0, 6),
    tokenMint: tok.address,
    tokenDec: num(tok.decimals),
    solIsX,
    binStep: num(cfg.bin_step),
    baseFeePct: num(cfg.base_fee_pct),
    dynFeePct: num(p.dynamic_fee_pct),
    tvl,
    vol24: w(p.volume, "24h") || 0,
    vol1h: w(p.volume, "1h") || 0,
    fees24: w(p.fees, "24h") || 0,
    feePctDay: w(p.fee_tvl_ratio, "24h"),
    feePctDay1h: w(p.fee_tvl_ratio, "1h") != null ? w(p.fee_tvl_ratio, "1h") * 24 : null,
    mcap: num(tok.market_cap),
    priceUsd: num(tok.price),
    solUsd: num(sol.price),
    ageDays: created ? (now - created) / 86400000 : null,
    freezeOk: tok.freeze_authority_disabled !== false,
    verified: !!tok.is_verified,
    launchpad: p.launchpad || "",
    blacklisted: !!p.is_blacklisted,
    chg24: null, chg6h: null, chg1h: null, chg5m: null,
  };
}

async function fetchRanked() {
  const filter = `token_y=${WSOL} && tvl>=${MIN_TVL} && volume_24h>=${MIN_VOL24} && is_blacklisted=false`;
  const url = `${DATAPI}/pools?page_size=${RANK_SIZE}&sort_by=${encodeURIComponent("fee_tvl_ratio_24h:desc")}`
    + `&filter_by=${encodeURIComponent(filter)}`;
  const j = await MET.getJson(url, { timeout: 15 });
  const now = Date.now();
  return (j.data || []).map(p => poolRow(p, now)).filter(Boolean);
}

// DexScreener indexes DLMM pools by their address. 30 per request, in
// parallel; a failure costs the sparkline, never the row.
async function fetchMarket(ids) {
  const out = new Map();
  const batches = [];
  for (let i = 0; i < ids.length; i += 30) batches.push(ids.slice(i, i + 30));
  const bodies = await Promise.all(batches.map(b => MET.getJson(DEX_PAIRS + b.join(","), { timeout: 10 }).catch(() => null)));
  for (const j of bodies) {
    for (const p of (j && j.pairs) || []) {
      if (!p || !p.pairAddress) continue;
      const pc = p.priceChange || {};
      out.set(p.pairAddress, {
        chg24: pc.h24 != null ? Number(pc.h24) : null,
        chg6h: pc.h6 != null ? Number(pc.h6) : null,
        chg1h: pc.h1 != null ? Number(pc.h1) : null,
        chg5m: pc.m5 != null ? Number(pc.m5) : null,
        txns24: p.txns && p.txns.h24 ? (Number(p.txns.h24.buys) || 0) + (Number(p.txns.h24.sells) || 0) : null,
        mcap: Number(p.marketCap) || Number(p.fdv) || null,
      });
    }
  }
  return out;
}
function withMarket(rows, market) {
  return rows.map(r => {
    const m = market.get(r.id);
    if (!m) return r;
    return { ...r, chg24: m.chg24, chg6h: m.chg6h, chg1h: m.chg1h, chg5m: m.chg5m, txns24: m.txns24, mcap: r.mcap != null ? r.mcap : m.mcap };
  });
}

async function fetchBalance() {
  if (!WALLET) return null;
  const r = await READ_RPC.call("getBalance", [WALLET, { commitment: "confirmed" }]);
  return lamportsToSol(r.value);
}

// ----------------------------- widget ----------------------------------------
function buildWidget(d, stale, dataTime) {
  const small = config.widgetFamily === "small";
  const w = baseWidget();
  w.url = "scriptable:///run/" + encodeURIComponent(Script.name()) + (MIN_MCAP !== MCAP_DEFAULT ? "?mcap=" + MIN_MCAP : "");
  w.setPadding(12, 17, 8, 17);
  w.refreshAfterDate = new Date(Date.now() + REFRESH_MIN * 60 * 1000);

  const head = w.addStack();
  head.centerAlignContent();
  const icon = head.addText("☄️");
  icon.font = Font.systemFont(16);
  head.addSpacer(6);
  const t = head.addText("SOL Pools");
  t.font = Font.boldSystemFont(13);
  t.textColor = COL.text;
  head.addSpacer();
  const tag = head.addText(stale ? `STALE ${fmtTime(dataTime)}` : `Updated ${fmtTime(Date.now())}`);
  tag.font = Font.semiboldSystemFont(10);
  tag.textColor = stale ? COL.red : COL.green;

  w.addSpacer(8);
  const grid = w.addStack();
  grid.centerAlignContent();
  const total = d.total != null ? d.total : d.rows.length;
  cell(grid, "Pools", String(total), COL.text);
  grid.addSpacer();
  cell(grid, "Median %/d", fmtPct(d.medianPct, 1), feeRateColor(d.medianPct));
  if (!small) {
    grid.addSpacer();
    cell(grid, "TVL", "$" + fmtCompact(d.sumTvl), COL.text);
    grid.addSpacer();
    if (d.balance != null) {
      const maxPreset = Math.max(...ZAP_PRESETS.map(p => p.amount));
      cell(grid, "My SOL", fmtSol(d.balance, 2), d.balance < maxPreset + RESERVE_SOL ? COL.yellow : COL.green);
    } else {
      cell(grid, "Vol 24h", "$" + fmtCompact(d.sumVol24), COL.text);
    }
  }

  const list = d.rows || [];
  if (list.length && !small) {
    const usable = widgetSize().w - 34;
    const GAP = 3;
    const PT = usable >= 320 ? 9.5 : 9;
    const rem = usable - GAP * 5;
    const WT = { pool: 3.0, mcap: 1.7, tvl: 1.8, vol: 1.8, fee: 1.9, age: 1.3 };
    let tot = 0; for (const k in WT) tot += WT[k];
    const W = {}; for (const k in WT) W[k] = Math.floor(rem * WT[k] / tot);
    const LBL = Font.semiboldSystemFont(7.5), DAT = Font.semiboldSystemFont(PT);
    w.addSpacer(6);
    const hdr = w.addStack();
    hdr.centerAlignContent();
    col(hdr, "POOL", W.pool, LBL, COL.faint); hdr.addSpacer(GAP);
    col(hdr, "MCAP", W.mcap, LBL, COL.faint, true); hdr.addSpacer(GAP);
    col(hdr, "TVL", W.tvl, LBL, COL.faint, true); hdr.addSpacer(GAP);
    col(hdr, "VOL 24H", W.vol, LBL, COL.faint, true); hdr.addSpacer(GAP);
    col(hdr, "FEE%/D", W.fee, LBL, COL.faint, true); hdr.addSpacer(GAP);
    col(hdr, "AGE", W.age, LBL, COL.faint, true);
    w.addSpacer(2);
    for (let i = 0; i < list.length; i++) {
      const r = list[i];
      const row = w.addStack();
      row.centerAlignContent();
      col(row, `${r.symbol} ${r.binStep || ""}`, W.pool, DAT, COL.text); row.addSpacer(GAP);
      col(row, fmtCompact(r.mcap), W.mcap, DAT, COL.muted, true); row.addSpacer(GAP);
      col(row, fmtCompact(r.tvl), W.tvl, DAT, COL.text, true); row.addSpacer(GAP);
      col(row, fmtCompact(r.vol24), W.vol, DAT, COL.text, true); row.addSpacer(GAP);
      col(row, fmtPct(r.feePctDay), W.fee, DAT, feeRateColor(r.feePctDay), true); row.addSpacer(GAP);
      col(row, fmtAge(r.ageDays), W.age, DAT, ageColor(r.ageDays), true);
      if (i < list.length - 1) w.addSpacer(5);
    }
  }
  w.addSpacer();
  const foot = w.addText(`fee yield · mcap ≥ ${fmtShort(MIN_MCAP)} · TVL ≥ ${fmtShort(MIN_TVL)} · ${VERSION}`);
  foot.font = Font.systemFont(9);
  foot.textColor = COL.faint;
  return w;
}
function errorWidget(msg) {
  const w = baseWidget();
  w.setPadding(16, 17, 16, 17);
  const head = w.addStack();
  head.centerAlignContent();
  const icon = head.addText("☄️");
  icon.font = Font.systemFont(16);
  head.addSpacer(6);
  const t = head.addText("SOL Pools");
  t.font = Font.boldSystemFont(14);
  t.textColor = COL.text;
  w.addSpacer(6);
  const m = w.addText(msg);
  m.font = Font.systemFont(11);
  m.textColor = COL.muted;
  return w;
}

// ----------------------------- main ------------------------------------------
function summarise(rows) {
  const pcts = rows.map(r => r.feePctDay).filter(v => v != null).sort((a, b) => a - b);
  return {
    total: rows.length,
    medianPct: pcts.length ? pcts[Math.floor(pcts.length / 2)] : null,
    sumTvl: rows.reduce((s, r) => s + (r.tvl || 0), 0),
    sumVol24: rows.reduce((s, r) => s + (r.vol24 || 0), 0),
  };
}
async function main() {
  let data = null, stale = false, dataTime = Date.now(), error = null;
  try {
    const ranked = await fetchRanked();
    // The mcap floor applies to what is known. A token the API has no cap for
    // is left out of the browse list; a search still finds it.
    const kept = ranked.filter(r => r.mcap != null && r.mcap >= MIN_MCAP);
    const [market, balance, usdIdr] = await Promise.all([
      fetchMarket(kept.map(r => r.id)).catch(() => new Map()),
      fetchBalance().catch(() => null),
      DISPLAY_CUR === "IDR" ? usdIdrRate().catch(() => null) : null,
    ]);
    const rows = withMarket(kept, market);
    rows.sort((a, b) => (b.feePctDay || 0) - (a.feePctDay || 0) || b.vol24 - a.vol24);
    const CHROME = 110, PITCH = 15.5;
    const rowBudget = config.widgetFamily === "small" ? 0
      : Math.max(0, Math.min(20, Math.floor((widgetSize().h - CHROME) / PITCH)));
    data = {
      rows, ...summarise(rows), rowBudget, balance,
      solUsd: (rows.find(r => r.solUsd > 0) || {}).solUsd || null,
      usdIdr,
      ranked: ranked.length,
    };
    saveJson(DATA_SUFFIX, { t: Date.now(), v: ROW_SCHEMA, data });
  } catch (e) {
    const c = loadDataCache();
    if (c) { data = c.data; stale = true; dataTime = c.t; error = String((e && e.message) || e).slice(0, 80); }
    else {
      const msg = "Couldn't load pools: " + String((e && e.message) || e).slice(0, 120);
      return { widget: errorWidget(msg), data: null, message: msg };
    }
  }
  return { widget: buildWidget({ ...data, rows: data.rows.slice(0, data.rowBudget || 6) }, stale, dataTime), data, stale, dataTime, error };
}

// The widget only refreshes what it shows: one data-API call for the visible
// pools and the balance, both cheap. Ranking again belongs in the app.
async function refreshWidgetRows(cached) {
  const rows = cached.data.rows.slice(0, cached.data.rowBudget || 6);
  if (!rows.length) return null;
  // Multi-value filters take commas, whatever the API docs say about "|":
  // measured, "|" matches nothing.
  const filter = `pool_address=[${rows.map(r => r.id).join(",")}]`;
  const [j, balance] = await Promise.all([
    MET.getJson(`${DATAPI}/pools?page_size=${rows.length}&filter_by=${encodeURIComponent(filter)}`, { timeout: 8 }),
    fetchBalance().catch(() => null),
  ]);
  const now = Date.now();
  const fresh = new Map((j.data || []).map(p => [p.address, poolRow(p, now)]).filter(x => x[1]));
  if (!fresh.size) return null;
  const merged = rows.map(r => {
    const f = fresh.get(r.id);
    return f ? { ...f, chg24: r.chg24, chg6h: r.chg6h, chg1h: r.chg1h, chg5m: r.chg5m } : r;
  });
  merged.sort((a, b) => (b.feePctDay || 0) - (a.feePctDay || 0));
  return { ...cached.data, rows: merged, balance: balance != null ? balance : cached.data.balance };
}

// ----------------------------- interactive table -----------------------------
// Short enough for a phone: "⚡️◎0.5 BA 50%", "⚡️◎1 Spot 3×70%".
function presetLabel(p) {
  const amt = p.amount >= 10 ? p.amount.toFixed(0) : String(Number(p.amount.toFixed(2)));
  const band = p.top > 0 ? `${p.top}-${p.range}%` : `${p.range}%`;
  return `⚡️${SOL_SIGN}${amt} ${STRATEGY_SHORT[p.strategy]} ${p.slices > 1 ? p.slices + "×" : ""}${band}`;
}

function fillTable(t, d, stale, dataTime, refreshing, error, onRefresh, sortCol, sortMode, onSort, onZap, busy, progress) {
  setDisplay(DISPLAY_CUR, d.solUsd, d.usdIdr);
  t.removeAllRows();
  t.showSeparators = true;
  const top = d.rows[0];

  const header = new UITableRow();
  header.isHeader = true;
  header.height = 72;
  const hCell = header.addText(
    d.tokenView ? `${d.rows.length} ${d.tokenView} pools` : `${d.rows.length} pools`,
    d.tokenView ? `Meteora DLMM SOL pools for ${d.tokenView} · every pool, no floors`
      : `Meteora DLMM · SOL pairs · mcap ≥ ${fmtShort(MIN_MCAP)} · TVL ≥ ${fmtShort(MIN_TVL)}`);
  hCell.widthWeight = 55;
  hCell.titleFont = Font.boldSystemFont(20);
  hCell.subtitleFont = Font.systemFont(12);
  hCell.subtitleColor = COL.muted;
  const hTop = header.addText(top && top.feePctDay != null ? `${top.feePctDay.toFixed(1)}%/day` : "--",
    top ? `best · ${top.symbol} ${top.binStep}` : "");
  hTop.widthWeight = 45;
  hTop.rightAligned();
  hTop.titleFont = Font.boldSystemFont(20);
  hTop.titleColor = top ? feeRateColor(top.feePctDay) : COL.muted;
  hTop.subtitleFont = Font.systemFont(12);
  hTop.subtitleColor = COL.muted;
  header.onSelect = () => Safari.open("https://app.meteora.ag/dlmm");
  header.dismissOnSelect = false;
  t.addRow(header);

  // What is spendable, so the preset amounts can be read against it.
  if (WALLET && d.balance != null) {
    const bal = new UITableRow();
    bal.height = 40;
    const bl = bal.addText("Wallet", shortAddr(WALLET));
    bl.widthWeight = 40;
    bl.titleFont = Font.semiboldSystemFont(15);
    bl.subtitleFont = Font.systemFont(11);
    bl.subtitleColor = COL.faint;
    const br = bal.addText(`${fmtSol(d.balance, 3)} SOL`, d.solUsd ? `≈ ${fmtMoney(d.balance * d.solUsd)}` : "");
    br.widthWeight = 60;
    br.rightAligned();
    br.titleFont = Font.boldSystemFont(16);
    const maxPreset = Math.max(...ZAP_PRESETS.map(p => p.amount));
    br.titleColor = d.balance < maxPreset + RESERVE_SOL ? COL.yellow : COL.green;
    br.subtitleFont = Font.systemFont(11);
    br.subtitleColor = COL.faint;
    bal.onSelect = () => Safari.open(`https://solscan.io/account/${WALLET}`);
    bal.dismissOnSelect = false;
    t.addRow(bal);
  }

  statusRow(t, refreshing ? "refreshing" : stale ? "stale" : "fresh", statusText(stale, dataTime, refreshing, error), onRefresh, d.solUsd ? `SOL ${fmtMoney(d.solUsd)}` : null);

  if (!onZap && ZAP_MISSING) {
    const kr = new UITableRow();
    kr.height = 44;
    const kc = kr.addText("⚡️ Open buttons are off", `Add ${ZAP_MISSING} to MET Secrets to open positions.`);
    kc.titleFont = Font.semiboldSystemFont(14);
    kc.titleColor = COL.yellow;
    kc.subtitleFont = Font.systemFont(11);
    kc.subtitleColor = COL.muted;
    t.addRow(kr);
  }
  if (onZap && DRY_RUN) {
    const pr = new UITableRow();
    pr.height = 30;
    const pc = pr.addText("Practice mode: zaps are simulated, nothing is sent.", "Set PRACTICE_MODE: false in MET Secrets to trade.");
    pc.titleFont = Font.semiboldSystemFont(12);
    pc.titleColor = COL.yellow;
    pc.subtitleFont = Font.systemFont(10);
    pc.subtitleColor = COL.faint;
    t.addRow(pr);
  }

  // One way in for anything that names a pool: address, token mint, name, or
  // a DexScreener / Meteora link. While a result shows it is the way back.
  const sr = new UITableRow();
  sr.height = 40;
  const sb = sr.addButton(d.tokenView ? `✕  ${d.tokenView} · back to all pools` : "\u{1F50E}  Search a pool, token, or link");
  sb.centerAligned();
  sb.titleFont = Font.semiboldSystemFont(14);
  if (onZap) {
    sb.onTap = () => onZap(d.tokenView ? "__clearsearch__" : "__search__", null);
    t.addRow(sr);
  }

  // The same five columns as the rows below (name, 24h chart, TVL, fees,
  // mcap), so every label sits over its numbers.
  const COLW = [25, 18, 20, 19, 18];
  const labels = new UITableRow();
  labels.height = 34;
  const NAMES = ["POOL", "TVL", "FEES", "MCAP"];
  for (let ci = 0; ci < 4; ci++) {
    if (ci === 1) { const ch = labels.addText("24H"); ch.widthWeight = COLW[1]; ch.centerAligned(); ch.titleFont = Font.semiboldSystemFont(10); ch.titleColor = COL.faint; }
    const active = ci === sortCol;
    const arrow = active ? (!(sortMode & 1) ? " ▼" : " ▲") : "";
    const btn = labels.addButton(NAMES[ci] + arrow);
    btn.widthWeight = COLW[ci === 0 ? 0 : ci + 1];
    if (ci > 0) btn.rightAligned();
    btn.titleFont = Font.semiboldSystemFont(11);
    btn.titleColor = active ? COL.accent : COL.faint;
    btn.onTap = () => onSort(ci);
  }
  t.addRow(labels);
  const SUBS = ["bin · age", "", "tvl · vol", "$ · %/day", "1h pace"];
  const info = new UITableRow();
  info.height = 20;
  SUBS.forEach((txt, k) => {
    const ic = info.addText(txt);
    ic.widthWeight = COLW[k];
    if (k > 1) ic.rightAligned();
    ic.titleFont = Font.systemFont(9);
    const sc = k === 0 ? 0 : k === 1 ? -1 : k - 1;
    ic.titleColor = sc === sortCol ? COL.accent : COL.faint;
  });
  t.addRow(info);

  drawProgress(t, progress);

  for (const r of d.rows || []) {
    const row = new UITableRow();
    row.height = 58;
    const sub = [`bin ${r.binStep}`, fmtAge(r.ageDays)];
    if (r.chg24 != null) sub.push(`${r.chg24 >= 0 ? "+" : ""}${r.chg24.toFixed(0)}% 24h`);
    const name = row.addText((r.freezeOk ? "" : "❄️") + r.symbol, sub.join(" · "));
    name.widthWeight = 25;
    name.titleFont = Font.boldSystemFont(16);
    name.titleColor = COL.text;
    name.subtitleFont = Font.systemFont(11);
    name.subtitleColor = r.chg24 == null ? COL.muted : signColor(r.chg24);

    const spark = sparkline(r, 132, 34);
    if (spark) { const sp = row.addImage(spark); sp.widthWeight = 18; sp.centerAligned(); }
    else { const sp = row.addText(""); sp.widthWeight = 18; }

    const tv = row.addText(fmtCompact(r.tvl), `vol ${fmtCompact(r.vol24)}`);
    tv.widthWeight = 20;
    tv.rightAligned();
    tv.titleFont = Font.semiboldSystemFont(16);
    tv.subtitleFont = Font.systemFont(11);
    tv.subtitleColor = COL.muted;

    const fe = row.addText(fmtCompact(r.fees24), fmtPct(r.feePctDay) + "/day");
    fe.widthWeight = 19;
    fe.rightAligned();
    fe.titleFont = Font.semiboldSystemFont(16);
    fe.titleColor = r.fees24 > 0 ? COL.green : COL.muted;
    fe.subtitleFont = Font.systemFont(11);
    fe.subtitleColor = feeRateColor(r.feePctDay);

    const mk = row.addText(r.mcap == null ? "--" : fmtCompact(r.mcap),
      `1h ${fmtPct(r.feePctDay1h, r.feePctDay1h >= 100 ? 0 : 1)}/d`);
    mk.widthWeight = 18;
    mk.rightAligned();
    mk.titleFont = Font.semiboldSystemFont(16);
    mk.titleColor = COL.text;
    mk.subtitleFont = Font.systemFont(11);
    mk.subtitleColor = feeRateColor(r.feePctDay1h);

    row.onSelect = () => Safari.open(`${DEX_URL}/${r.id}`);
    row.dismissOnSelect = false;
    t.addRow(row);

    // Buttons on a row of their own with no onSelect: a near miss does
    // nothing, rather than opening a browser.
    if (onZap) {
      const zr = new UITableRow();
      zr.height = 36;
      // Presets get the room (their labels carry amount, shape and band);
      // Custom needs only its name.
      const w = Math.floor(78 / ZAP_PRESETS.length);
      for (const preset of ZAP_PRESETS) {
        const zb = zr.addButton(busy ? "…" : presetLabel(preset));
        zb.widthWeight = w;
        zb.centerAligned();
        zb.titleFont = Font.semiboldSystemFont(12);
        if (!busy) zb.onTap = () => onZap(r, preset);
      }
      const cz = zr.addButton(busy ? "…" : "✏️ Custom");
      cz.widthWeight = 22;
      cz.centerAligned();
      cz.titleFont = Font.semiboldSystemFont(12);
      if (!busy) cz.onTap = () => onZap(r, "__ask__");
      t.addRow(zr);
    }
  }

  const foot = new UITableRow();
  foot.height = 56;
  const fCell = foot.addText("Open Meteora · DLMM",
    `${d.ranked != null ? d.ranked + " ranked by fee yield · " : ""}${VERSION}`);
  fCell.titleFont = Font.semiboldSystemFont(15);
  fCell.titleColor = COL.accent;
  fCell.subtitleFont = Font.systemFont(11);
  fCell.subtitleColor = COL.faint;
  fCell.centerAligned();
  foot.onSelect = () => Safari.open("https://app.meteora.ag/dlmm");
  foot.dismissOnSelect = false;
  t.addRow(foot);
}
function fillEmptyTable(t, message, refreshing, onRefresh, progress) {
  t.removeAllRows();
  const header = new UITableRow();
  header.isHeader = true;
  header.height = 72;
  const c = header.addText("SOL Pools", "Meteora DLMM · Solana");
  c.titleFont = Font.boldSystemFont(20);
  c.subtitleFont = Font.systemFont(12);
  c.subtitleColor = COL.muted;
  t.addRow(header);
  statusRow(t, refreshing ? "refreshing" : "stale", refreshing ? (message || "Refreshing…") : `${message}   ·   tap to retry`, onRefresh);
  drawProgress(t, progress);
}

// ----------------------------- search ----------------------------------------
async function poolByAddress(addr) {
  const p = await MET.getJson(`${DATAPI}/pools/${addr}`, { timeout: 12 });
  const r = poolRow(p, Date.now());
  if (!r) throw new Error("that pool is not paired with SOL; MET Pool opens SOL-only positions");
  return r;
}
// Every SOL pool of one token, no floors: a search names what it wants.
async function poolsForToken(mint) {
  const q = async side => {
    const filter = side === "x" ? `token_x=${mint} && token_y=${WSOL}` : `token_y=${mint} && token_x=${WSOL}`;
    const j = await MET.getJson(`${DATAPI}/pools?page_size=50&sort_by=${encodeURIComponent("tvl:desc")}&filter_by=${encodeURIComponent(filter)}`, { timeout: 12 });
    return j.data || [];
  };
  const [a, b] = await Promise.all([q("x"), q("y").catch(() => [])]);
  const now = Date.now();
  const rows = a.concat(b).map(p => poolRow(p, now)).filter(Boolean);
  if (!rows.length) throw new Error("no Meteora DLMM pool pairs that token with SOL");
  rows.sort((x, y) => (y.feePctDay || 0) - (x.feePctDay || 0) || y.tvl - x.tvl);
  return rows;
}
// A name matches several contracts more often than not; a human picks.
async function findTokens(query) {
  const text = String(query || "").trim();
  if (text.length < 2) throw new Error("type at least two characters");
  const j = await MET.getJson(`${DATAPI}/pools?page_size=100&query=${encodeURIComponent(text)}&sort_by=${encodeURIComponent("tvl:desc")}`, { timeout: 12 });
  const byToken = new Map();
  for (const p of j.data || []) {
    const r = poolRow(p, Date.now());
    if (!r) continue;
    if (!byToken.has(r.tokenMint)) byToken.set(r.tokenMint, { mint: r.tokenMint, symbol: r.symbol, pools: 0, tvl: 0, mcap: r.mcap });
    const e = byToken.get(r.tokenMint);
    e.pools++;
    e.tvl += r.tvl || 0;
  }
  const out = [...byToken.values()].sort((a, b) => b.tvl - a.tvl);
  if (!out.length) throw new Error(`no SOL pools on Meteora DLMM match "${text}"`);
  return out;
}

// ----------------------------- zap -------------------------------------------
// Reads the pool, the other token and the wallet, and works out every cost
// before anything is asked for. Returns what the confirmation shows and what
// the send needs.
async function readPool(address) {
  const accs = await MET.getAccounts(SEND_RPC, [address]);
  const acc = accs.get(address);
  if (!acc) throw new Error("pool account not found on chain");
  if (acc.owner !== MET.PROGRAM.DLMM) throw new Error("not a Meteora DLMM pool");
  const lb = MET.parseLbPair(acc.data);
  return { address, lb };
}

async function planZap(row, preset, rep) {
  const pi = rep.start("Read pool");
  const pool = await readPool(row.id);
  const lb = pool.lb;
  const solIsX = lb.tokenXMint === WSOL;
  if (!solIsX && lb.tokenYMint !== WSOL) { rep.fail(pi, "not a SOL pool"); throw new Error("not a SOL pool"); }
  if (lb.status !== 0) { rep.fail(pi, "pool is disabled"); throw new Error("the pool is disabled"); }
  const otherMint = solIsX ? lb.tokenYMint : lb.tokenXMint;
  const mints = await MET.getAccounts(SEND_RPC, [otherMint]);
  const mAcc = mints.get(otherMint);
  if (!mAcc) { rep.fail(pi, "token mint missing"); throw new Error("token mint not found"); }
  const mint = MET.parseMint(mAcc.data, mAcc.owner);
  if (mint.transferHook) {
    rep.fail(pi, "token has a transfer hook");
    throw new Error("this token runs a transfer hook on every transfer, which these scripts do not support");
  }
  rep.ok(pi, `bin step ${lb.binStep} (${(lb.binStep / 100).toFixed(2)}%) · base fee ${(MET.baseFeeRate(lb) * 100).toFixed(2)}%`
    + (mint.is2022 ? " · Token-2022" : "") + (mint.transferFeeBps ? ` · ${(mint.transferFeeBps / 100).toFixed(2)}% transfer fee` : ""));

  const li = rep.start("Plan the ladder");
  const lamports = BigInt(Math.round(preset.amount * 1e9));
  const plans = {};
  for (const s of STRATEGIES) {
    plans[s] = MET.planLadder({ activeId: lb.activeId, binStep: lb.binStep, solIsX, lamports,
      slices: preset.slices, top: preset.top, range: preset.range, strategy: s });
  }
  const plan = plans[preset.strategy];
  if (!plan.slices.length) { rep.fail(li, "nothing to place"); throw new Error("empty plan"); }
  rep.ok(li, `${plan.slices.length} slice${plan.slices.length > 1 ? "s" : ""} · ${plan.bins} bins · `
    + (plan.topBins > 0 ? `-${plan.realTop.toFixed(1)}% to -${plan.realEnd.toFixed(1)}%` : `price to -${plan.realEnd.toFixed(1)}%`)
    + (plan.raised ? ` · raised to ${plan.raised} slices (70 bins max each)` : ""));

  const ci = rep.start("Check wallet and accounts");
  const arrays = new Map();
  for (const s of plan.slices) for (const a of MET.binArraysCovering(pool.address, s.lo, s.hi)) arrays.set(a.index, a.address);
  const needBitmap = [...arrays.keys()].some(MET.bitmapOverflow);
  const bitmapAddr = MET.bitmapExtensionAddress(pool.address);
  const otherAta = MET.ata(WALLET, otherMint, mint.program);
  const lookups = [...arrays.values(), otherAta, ...(needBitmap ? [bitmapAddr] : [])];
  const [have, bal] = await Promise.all([
    MET.getAccounts(SEND_RPC, lookups),
    SEND_RPC.call("getBalance", [WALLET, { commitment: "confirmed" }]),
  ]);
  const newArrays = [...arrays.entries()].filter(([, a]) => !have.get(a)).map(([i]) => i);
  const initBitmap = needBitmap && !have.get(bitmapAddr);
  const needAta = !have.get(otherAta);
  const rent = await MET.rentRates(SEND_RPC);
  const rentPositions = plan.slices.length * rent.position;
  const rentArrays = newArrays.length * rent.binArray + (initBitmap ? rent.bitmap : 0);
  const rentAta = needAta ? (mint.is2022 ? rent.tokenAccount2022 : rent.tokenAccount) : 0;
  // The wrapped-SOL account exists only inside each slice's transaction and
  // its rent comes straight back, but it has to be there while it runs.
  const fees = plan.slices.length * 60_000 + (newArrays.length || initBitmap || needAta ? 60_000 : 0);
  const need = Number(lamports) + rentPositions + rentArrays + rentAta + fees + rent.tokenAccount + RESERVE_SOL * 1e9;
  const held = bal.value;
  rep.ok(ci, `wallet ${fmtSol(held / 1e9, 3)} · ${newArrays.length} new bin array${newArrays.length === 1 ? "" : "s"}`
    + (needAta ? " · token account to create" : ""));
  if (held < need) {
    rep.note("Not enough SOL", `holds ${fmtSol(held / 1e9, 3)}, this needs ${fmtSol(need / 1e9, 3)} with rent and a ${RESERVE_SOL} reserve`);
    throw new Error(`wallet holds ${fmtSol(held / 1e9, 3)}, needs ${fmtSol(need / 1e9, 3)}`);
  }
  return {
    pool, lb, solIsX, mint, other: { mint: otherMint, program: mint.program }, plans, lamports,
    newArrays, initBitmap, needBitmap, bitmapAddr, needAta,
    rentPositions, rentArrays, rentAta, held, rent,
  };
}

function confirmText(row, preset, z) {
  const plan = z.plans[preset.strategy];
  const n = plan.slices.length;
  const lines = [];
  lines.push(`${fmtSol(preset.amount, 3)} SOL into ${n} ${n > 1 ? "slices" : "position"}`
    + (plan.raised ? ` (raised from ${preset.slices}: 70 bins max each)` : "") + ".");
  if (n > 1) {
    lines.push("");
    lines.push("Slices: " + plan.slices.map(x => (lamportsToSol(x.lamports)).toFixed(3)).join(" / "));
    lines.push("Slice 1 is next to the price; each one further down holds more. The shape you pick is drawn inside every slice.");
  }
  lines.push("");
  lines.push(plan.topBins > 0
    ? `Covers ${plan.realTop.toFixed(1)}% to ${plan.realEnd.toFixed(1)}% below the price (${plan.bins} bins).`
    : `Covers the price down to ${plan.realEnd.toFixed(1)}% below it (${plan.bins} bins).`);
  if (row.mcap != null) {
    const mNear = row.mcap * (1 - plan.realTop / 100), mFar = row.mcap * (1 - plan.realEnd / 100);
    lines.push(`mcap ${fmtCompact(mNear)} → ${fmtCompact(mFar)}`);
  }
  if (row.tvl > 0 && row.solUsd && preset.amount * row.solUsd > row.tvl * 0.25) {
    lines.push(`⚠️ The pool holds only ${fmtUsd(row.tvl)}; this is ${(preset.amount * row.solUsd / row.tvl * 100).toFixed(0)}% of it.`);
  }
  if (!row.freezeOk) lines.push("❄️ This token still has a freeze authority: its issuer can freeze holders.");
  lines.push("");
  lines.push(`Rent ${fmtSol(lamportsToSol(z.rentPositions), 3)} for ${n} position${n > 1 ? "s" : ""}, returned when you close.`);
  if (z.rentArrays) lines.push(`New bin arrays ${fmtSol(lamportsToSol(z.rentArrays), 3)}, not returned: they belong to the pool.`);
  if (z.rentAta) lines.push(`Token account ${fmtSol(lamportsToSol(z.rentAta), 4)}, kept for the tokens the ladder buys.`);
  lines.push("");
  lines.push(plan.topBins > 0
    ? `\u{1F7E1} It earns nothing until the price falls ${plan.realTop.toFixed(2)}% to reach the ladder.`
    : "\u{1F7E2} The top bin is the active bin, so it starts earning on the next sell.");
  lines.push("SOL only: nothing is bought at open. Pick the shape:");
  if (DRY_RUN) lines.push("\nPRACTICE MODE: simulated, nothing is sent.");
  return lines.join("\n");
}

// Opens the ladder through MET.openLadder, which Portfolio's re-enter uses
// too. Remembers what it opened so Portfolio can show and re-enter it whole.
async function runZap(row, preset, strategy, z, rep) {
  const res = await MET.openLadder(SEND_RPC, {
    poolAddress: row.id, actor: ACTOR, seedKp: KP, other: z.other, lamports: z.lamports,
    slices: preset.slices, top: preset.top, range: preset.range, strategy,
    practice: DRY_RUN, isHelius: IS_HELIUS, maxSlip: MAX_ACTIVE_SLIP, plannedActiveId: z.lb.activeId, rep,
  });
  const n = res.n;
  if (res.dryRun) {
    return { ok: res.ok, text: res.ok
      ? `Practice run finished. All ${n} slice${n > 1 ? "s" : ""} simulate cleanly; nothing was sent.`
      : `Practice run: ${res.okCount} of ${n} slices would succeed. Nothing was sent.` };
  }
  if (res.landed.length) {
    saveLadder(WALLET, {
      pool: row.id, token: z.other.mint, symbol: row.symbol, strategy, slices: preset.slices,
      top: preset.top, range: preset.range, amount: preset.amount, binStep: res.binStep, solIsX: res.solIsX,
      positions: res.landed.map(l => l.position), openedAt: res.openedAt,
    });
  }
  const k = res.landed.length, u = (res.unknown || []).length, m = (res.missing || []).length;
  return {
    ok: res.ok, partial: k > 0 && k < n, missing: u ? [] : res.missing || [], openedAt: res.openedAt,
    text: k === n ? `Opened ${n} position${n > 1 ? "s" : ""} in ${row.symbol} (${STRATEGY_NAME[strategy]}).`
      : u ? `Opened ${k} of ${n} slices; ${u} could not be confirmed either way. Check MET Portfolio or Solscan before zapping again.`
      : k ? `Opened ${k} of ${n} slices in ${row.symbol}. ${m} did not open; nothing was deposited`
        + `${(res.missing || []).some(x => x.reason === "failed") ? " (a failed try costs only the network fee)" : ""}. Retry sends ${m > 1 ? "them" : "it"} again, same bins and SOL.`
      : "No slice landed. Nothing was deposited.",
    sub: k ? `${fmtSol(lamportsToSol(k * z.rent.position), 3)} rent locked, returned on close` : "",
    link: k ? `https://solscan.io/tx/${res.landed[0].signature}` : u ? `https://solscan.io/tx/${res.unknown[0].signature}` : null,
  };
}

// ----------------------------- entry -----------------------------------------
if (config.runsInWidget) {
  let w;
  try {
    const cached = loadDataCache();
    if (!cached || !cached.data || !cached.data.rows || !cached.data.rows.length) {
      w = errorWidget("No data yet. Tap to open in the app and load pools.");
    } else {
      const stale = new Promise(resolve => Timer.schedule(12_000, false, () => resolve(null)));
      const fresh = await Promise.race([refreshWidgetRows(cached).catch(() => null), stale]);
      w = fresh ? buildWidget(fresh, false, Date.now())
        : buildWidget({ ...cached.data, rows: cached.data.rows.slice(0, cached.data.rowBudget || 6) }, true, cached.t);
    }
  } catch (e) {
    const c = loadDataCache();
    w = c && c.data && c.data.rows && c.data.rows.length
      ? buildWidget({ ...c.data, rows: c.data.rows.slice(0, c.data.rowBudget || 6) }, true, c.t)
      : errorWidget(String((e && e.message) || e).slice(0, 80));
  }
  Script.setWidget(w);
} else {
  const t = new UITable();
  let shown = null, current = null, busy = false, progress = null, tokenView = null;
  const SORT_KEYS = [["binStep", "ageDays"], ["tvl", "vol24"], ["fees24", "feePctDay"], ["mcap", "feePctDay1h"]];
  let sortCol = 2, sortMode = 2;
  const sortRows = rows => {
    const key = SORT_KEYS[sortCol][sortMode >> 1];
    const desc = !(sortMode & 1);
    return [...rows].sort((a, b) => {
      const va = a[key] == null ? (desc ? -Infinity : Infinity) : a[key];
      const vb = b[key] == null ? (desc ? -Infinity : Infinity) : b[key];
      return desc ? vb - va : va - vb;
    });
  };
  let lastCustom = { amount: ZAP_PRESETS[0].amount, top: ZAP_PRESETS[0].top, range: ZAP_PRESETS[0].range, slices: ZAP_PRESETS[0].slices, strategy: ZAP_PRESETS[0].strategy };

  const show = (r, refreshing) => {
    if (r && r.data && r.data.rows) {
      const base = tokenView ? { ...r.data, rows: tokenView.rows, tokenView: tokenView.symbol } : r.data;
      fillTable(t, { ...base, rows: sortRows(base.rows) }, r.stale, r.dataTime, refreshing, r.error, refresh,
        sortCol, sortMode, handleSort, ZAP_READY ? onZap : null, busy, progress);
    } else {
      fillEmptyTable(t, (r && r.message) || "Nothing loaded", refreshing, refresh, progress);
    }
    if (shown) t.reload(); else shown = t.present(false);
  };
  const handleSort = c => {
    if (c === sortCol) sortMode = (sortMode + 1) % 4;
    else { sortCol = c; sortMode = 0; }
    if (current && current.data) show(current, false);
  };

  async function onZap(row, preset) {
    if (busy) return;
    if (row === "__clearsearch__") { tokenView = null; return show(current, false); }
    if (row === "__search__") return onSearch();

    if (preset === "__ask__") {
      const ask = new Alert();
      ask.title = `Custom zap into ${row.symbol}`;
      ask.message = "SOL to deposit, the band below the price to cover, and how many "
        + "slices to split it into.\n\nTop is where the ladder starts: 0 puts it at the price. "
        + "Bottom is how far down it reaches. You pick Spot, Curve or Bid-Ask on the next screen.";
      ask.addTextField("SOL amount", String(lastCustom.amount));
      ask.addTextField("Top % below price (0 = at the price)", String(lastCustom.top || 0));
      ask.addTextField("Bottom % below price", String(lastCustom.range));
      ask.addTextField("Slices", String(lastCustom.slices));
      ask.addAction("Continue");
      ask.addCancelAction("Cancel");
      if ((await ask.presentAlert()) !== 0) return;
      // A decimal comma is a decimal point (0,5 is half a SOL, not five);
      // anything else that is not a plain number is refused below.
      const num = i => {
        const raw = String(ask.textFieldValue(i) == null ? "" : ask.textFieldValue(i)).trim().replace(/\s/g, "");
        const t = /^\d*,\d+$/.test(raw) ? raw.replace(",", ".") : raw;
        return /^\d*\.?\d+$|^\d+\.?$/.test(t) ? Number(t) : NaN;
      };
      const amount = num(0), top = String(ask.textFieldValue(1) || "").trim() === "" ? 0 : num(1), range = num(2), slices = Math.round(num(3));
      const bad = !(amount > 0) ? "Amount must be greater than zero."
        : !(top >= 0 && top < 99) ? "Top must be between 0 and 99%."
        : !(range > 0 && range < 100) ? "Bottom must be between 0 and 100%."
        : !(range > top) ? "Bottom must be further from the price than top."
        : !(slices >= 1 && slices <= MAX_SLICES) ? `Slices must be between 1 and ${MAX_SLICES}.`
        : amount / slices < MIN_SLICE_SOL ? `Each slice needs at least ${MIN_SLICE_SOL} SOL.`
        : null;
      if (bad) {
        progress = { title: "Custom zap", steps: [], running: false, outcome: { ok: false, text: bad } };
        return show(current, false);
      }
      lastCustom = { ...lastCustom, amount, top, range, slices };
      preset = { amount, top, range, slices, strategy: lastCustom.strategy };
    }

    busy = true;
    progress = { title: `${fmtSol(preset.amount, 3)} into ${row.symbol} bin ${row.binStep}`, steps: [], running: true, outcome: null };
    show(current, false);
    const rep = makeReporter(progress, () => show(current, false));
    let z = null;
    try {
      z = await planZap(row, preset, rep);
    } catch (e) {
      progress.outcome = { ok: false, text: `Stopped before sending: ${String(e.message || e).slice(0, 110)}` };
      progress.running = false;
      // Nothing was sent, so trying again is safe: same pool, same preset.
      progress.retry = () => onZap(row, preset);
      busy = false;
      return show(current, false);
    }

    const a = new Alert();
    a.title = `${DRY_RUN ? "Practice: zap" : "Zap"} ${row.symbol} · bin ${row.binStep}?`;
    a.message = confirmText(row, preset, z);
    const order = [preset.strategy, ...STRATEGIES.filter(s => s !== preset.strategy)];
    for (const s of order) {
      const label = (DRY_RUN ? "Practice · " : "") + STRATEGY_NAME[s] + (s === preset.strategy ? "  ★" : "");
      if (DRY_RUN) a.addAction(label); else a.addDestructiveAction(label);
    }
    a.addCancelAction("Cancel");
    const choice = await a.presentAlert();
    if (choice < 0 || choice >= order.length) {
      progress = null;
      busy = false;
      return show(current, false);
    }
    const strategy = order[choice];
    lastCustom.strategy = strategy;
    progress.title = `${fmtSol(preset.amount, 3)} into ${row.symbol} · ${STRATEGY_NAME[strategy]}`;
    try {
      const res = await runZap(row, preset, strategy, z, rep);
      progress.outcome = { ok: res.ok || !!res.partial, text: res.text, sub: res.sub, link: res.link };
      if (!DRY_RUN && res.missing && res.missing.length) progress.retry = () => onRetryMissing(row, strategy, z, res.missing, res.openedAt);
    } catch (e) {
      progress.outcome = { ok: false, text: `Stopped: ${String(e.message || e).slice(0, 120)}` };
    }
    progress.running = false;
    busy = false;
    show(current, false);
    if (!DRY_RUN && current && current.data) {
      try { current.data.balance = await fetchBalance(); } catch (e) {}
      show(current, false);
    }
  }

  // Retry: only the slices of a ladder that did not land, with the same bins
  // and SOL they were planned with, added to that ladder's record.
  async function onRetryMissing(row, strategy, z, missing, openedAt) {
    if (busy) return;
    busy = true;
    const n = missing.length;
    const sol = missing.reduce((t, x) => t + Number(x.lamports), 0) / 1e9;
    progress = { title: `Retry ${n} slice${n > 1 ? "s" : ""} · ${fmtSol(sol, 3)} into ${row.symbol}`, steps: [], running: true, outcome: null };
    show(current, false);
    const rep = makeReporter(progress, () => show(current, false));
    try {
      const res = await MET.openLadder(SEND_RPC, {
        poolAddress: row.id, actor: ACTOR, seedKp: KP, other: z.other, only: missing, strategy,
        practice: DRY_RUN, isHelius: IS_HELIUS, maxSlip: MAX_ACTIVE_SLIP, rep,
      });
      const landed = res.landed || [];
      if (landed.length && !addToLadder(WALLET, row.id, openedAt, landed.map(l => l.position))) {
        saveLadder(WALLET, { pool: row.id, token: z.other.mint, symbol: row.symbol, strategy, slices: n,
          binStep: res.binStep, solIsX: res.solIsX, positions: landed.map(l => l.position), openedAt });
      }
      const k = landed.length, u = (res.unknown || []).length;
      progress.outcome = {
        ok: k > 0,
        text: k === n ? `Opened the ${n} missing slice${n > 1 ? "s" : ""}; the ladder is complete.`
          : u ? `Opened ${k} of ${n}; ${u} could not be confirmed either way. Check MET Portfolio or Solscan first.`
          : k ? `Opened ${k} of ${n}. The rest did not open; nothing was deposited.`
          : "They did not land this time either. Nothing was deposited.",
        link: k ? `https://solscan.io/tx/${landed[0].signature}` : null,
      };
      if (!u && res.missing && res.missing.length) progress.retry = () => onRetryMissing(row, strategy, z, res.missing, openedAt);
    } catch (e) {
      progress.outcome = { ok: false, text: `Stopped: ${String(e.message || e).slice(0, 120)}` };
      progress.retry = () => onRetryMissing(row, strategy, z, missing, openedAt);
    }
    progress.running = false;
    busy = false;
    show(current, false);
  }

  async function onSearch() {
    const ask = new Alert();
    ask.title = "Search";
    ask.message = "A pool address, a token mint, a token name, or a DexScreener / Meteora link.\n\n"
      + "Every Meteora DLMM SOL pool for it is listed, including thin ones. Check the TVL before depositing.";
    ask.addTextField("Name, address, or link", "");
    ask.addAction("Search");
    ask.addCancelAction("Cancel");
    if ((await ask.presentAlert()) !== 0) return;
    const typed = String(ask.textFieldValue(0) || "").trim();
    if (!typed) return;
    const runLookup = async (fn, label) => {
      busy = true;
      progress = { title: "Search", steps: [{ label, state: "run", detail: "" }], running: true, outcome: null };
      show(current, false);
      try { tokenView = await fn(); progress = null; }
      catch (e) { progress = { title: "Search", steps: [], running: false, outcome: { ok: false, text: String(e.message || e).slice(0, 200) } }; }
      busy = false;
      show(current, false);
    };
    // The longest base58 run in the text: a pasted link carries it in its path.
    const found = (typed.match(/[1-9A-HJ-NP-Za-km-z]{32,44}/g) || []).filter(MET.isAddress);
    if (found.length) {
      const addr = found[0];
      return runLookup(async () => {
        try {
          const r = await poolByAddress(addr);
          return { symbol: `${r.symbol} bin ${r.binStep}`, rows: [r] };
        } catch (e) {
          if (e.status !== 404 && !/not found|404/i.test(e.message || "")) {
            if (/not paired with SOL/.test(e.message)) throw e;
          }
          const rows = await poolsForToken(addr);
          return { symbol: rows[0].symbol, rows };
        }
      }, "Reading address");
    }
    busy = true;
    progress = { title: "Search", steps: [{ label: "Searching", state: "run", detail: "" }], running: true, outcome: null };
    show(current, false);
    let cands;
    try { cands = await findTokens(typed); }
    catch (e) {
      busy = false;
      progress = { title: "Search", steps: [], running: false, outcome: { ok: false, text: String(e.message || e).slice(0, 200) } };
      return show(current, false);
    }
    busy = false;
    progress = null;
    let mint = cands[0].mint;
    if (cands.length > 1) {
      show(current, false);
      const pick = new Alert();
      pick.title = `${cands.length} tokens match "${typed}"`;
      pick.message = "Same name, different tokens. Pick by liquidity and market cap; the mint is shown so it can be checked.";
      const top = cands.slice(0, 6);
      for (const c of top) pick.addAction(`${c.symbol} · $${fmtShort(c.tvl)} TVL · mcap ${c.mcap != null ? fmtShort(c.mcap) : "?"} · ${c.mint.slice(0, 6)}…`);
      pick.addCancelAction("Cancel");
      const chosen = await pick.presentAlert();
      if (chosen < 0 || chosen >= top.length) return show(current, false);
      mint = top[chosen].mint;
    }
    return runLookup(async () => { const rows = await poolsForToken(mint); return { symbol: rows[0].symbol, rows }; }, "Finding pools");
  }

  const refresh = async () => {
    if (busy) return;
    busy = true;
    show(current || { data: null, message: "Loading…" }, true);
    try { current = await main(); }
    catch (e) { current = { data: null, message: "Couldn't load: " + String((e && e.message) || e).slice(0, 100) }; }
    busy = false;
    show(current, false);
  };

  const cached = loadDataCache();
  if (cached) current = { data: cached.data, stale: false, dataTime: cached.t };

  // Launched with ?pool=<address>: that pool alone, ready for the usual zap.
  const wanted = String((args.queryParameters && args.queryParameters.pool) || "").trim();
  if (MET.isAddress(wanted)) {
    busy = true;
    progress = { title: "Open pool", steps: [{ label: "Reading pool", state: "run", detail: "" }], running: true, outcome: null };
    show(current || { data: null, message: "Loading…" }, true);
    try {
      const r = await poolByAddress(wanted);
      tokenView = { symbol: `${r.symbol} bin ${r.binStep}`, rows: [r] };
      progress = null;
    } catch (e) {
      progress = { title: "Open pool", steps: [], running: false, outcome: { ok: false, text: "Could not read that pool: " + String(e.message || e).slice(0, 120) } };
    }
    busy = false;
  }

  await refresh();
  await shown;
}
Script.complete();
