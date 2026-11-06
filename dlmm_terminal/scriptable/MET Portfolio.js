// Variables used by Scriptable.
// These must be at the very top of the file. Do not edit.
// icon-color: orange; icon-glyph: meteor;
// ============================================================================
//  MET Portfolio : Meteora DLMM positions, in SOL : Solana
//
//  *** WITH A PRIVATE KEY IN "MET Secrets", THIS SCRIPT SPENDS MONEY. ***
//
//  Every number is in SOL. Net worth = SOL in the wallet + what the positions
//  hold + their unclaimed fees + the rent the positions lock (returned on
//  close) + leftover tokens at market. PnL is Meteora's own per-position
//  figure: value + fees (claimed and unclaimed) + withdrawals - deposits.
//
//  A row is one token. Its slices are the positions in it; Claim, Close and
//  Re-enter act on all of them at once. Close takes liquidity out, claims the
//  fees and closes the position accounts, so their rent comes back. "Close
//  and swap to SOL" then sells whatever token the close returned, refusing a
//  price more than MAX_LOSS_PERCENT under the pool's. Re-enter moves a ladder
//  the price has run away from (still all SOL) back up under the price, same
//  slices and width. Cleanup sells leftover tokens and closes empty token
//  accounts for their rent.
//
//  Data: Meteora's data API for positions, PnL and pools (keyless); Helius
//  for balances, signing and sending; Jupiter for swaps and token prices.
//
//  SETUP:
//   1. Fill in "MET Secrets" (Helius key, wallet key). PRACTICE_MODE starts
//      true: every button simulates and sends nothing.
//   2. Put this file in Scriptable as "MET Portfolio" and run it.
//   3. As a widget it follows the wallet in MET Secrets, or any address you
//      put in the widget's Parameter ("Name|address" shows a name).
// ============================================================================

if (config.runsInWidget) {
  try {
    const _f = new ListWidget();
    const _g = new LinearGradient();
    _g.colors = [new Color("#1E1F35"), new Color("#12121C")];
    _g.locations = [0, 1];
    _f.backgroundGradient = _g;
    _f.setPadding(16, 17, 16, 17);
    const _h = _f.addText("☄️ MET Portfolio");
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

const VERSION = "met-portfolio-v7+spec1";

const PRACTICE_MODE = typeof SECRETS.PRACTICE_MODE === "boolean" ? SECRETS.PRACTICE_MODE : true;
const DRY_RUN = PRACTICE_MODE;
// The most a sale back to SOL may give up against the pool price, all in.
const MAX_LOSS_PERCENT = (() => {
  const v = Number(SECRETS.MAX_LOSS_PERCENT);
  return v > 0 && v <= 50 ? v : 5;
})();
// What Jupiter's route may slip between quote and fill. Kept inside the loss
// budget rather than beside it, so the two cannot add up past it.
const ROUTER_SLIPPAGE_PERCENT = 1;

// ----------------------------- spec v1 exit rules ----------------------------
// From DLMM-Auto-Entry-Spec v1, replayed on 131 positions every 5 minutes:
//   take-profit +1/+2/+5/+10%   -7.16 / -7.57 / -4.37 / -1.06 SOL vs. the real exits
//   stop-loss -10/-15/-25% PnL  -7.60 / -4.24 / -1.35 SOL
//   close 10 min below range    +0.22 SOL   <- the only rule that added SOL
// So take-profit and stop-loss stay off unless set in MET Secrets, and the one
// automatic exit is "below the range for 10 minutes". Alerts: PnL at -10%, and
// price in the bottom third of the range.
//
// EXIT_MODE "alert" (default): a notification; tapping it opens the close.
// EXIT_MODE "auto": opening the app closes and swaps to SOL by itself.
// A widget run never sends a transaction, whatever the mode: iOS gives a
// widget seconds, not the minutes a close and a swap can take.
//
// The rules that read the range only apply to SOL-only positions (no token
// deposited): a two-sided position sits partly above the price, and in the
// wallet review one started below its range and still closed +19%.
const EXIT = (() => {
  const n = (k, d) => { const v = SECRETS[k]; if (v === null) return null; const x = Number(v); return v != null && v !== "" && Number.isFinite(x) ? x : d; };
  return {
    mode: String(SECRETS.EXIT_MODE || "alert").toLowerCase() === "auto" ? "auto" : "alert",
    alertAtPnlPct: n("EXIT_ALERT_PNL_PCT", -10),
    alertBottomThird: SECRETS.EXIT_ALERT_BOTTOM_THIRD !== false,
    closeBelowRangeAfterMin: n("EXIT_CLOSE_BELOW_RANGE_MIN", 10),
    takeProfitPct: n("EXIT_TAKE_PROFIT_PCT", null),
    stopLossPct: n("EXIT_STOP_LOSS_PCT", null),
  };
})();
function exitStatePath() { const f = FileManager.local(); return f.joinPath(f.libraryDirectory(), "met_exit_state.json"); }
function loadExitState() { try { const f = FileManager.local(); return f.fileExists(exitStatePath()) ? JSON.parse(f.readString(exitStatePath())) : {}; } catch (e) { return {}; } }
function saveExitState(st) { try { FileManager.local().writeString(exitStatePath(), JSON.stringify(st)); } catch (e) {} }
function exitLog(entry) {
  try {
    const f = FileManager.local();
    const path = f.joinPath(f.libraryDirectory(), "met_pnl_log.jsonl");
    let old = f.fileExists(path) ? f.readString(path) : "";
    if (old.length > 4_000_000) { f.writeString(path + ".1", old); old = ""; }
    f.writeString(path, old + JSON.stringify(entry) + "\n");
  } catch (e) {}
}

// One position against the rules. Bins, not prices: SOL-only sits below the
// price when SOL is Y (the token falling moves the active bin down) and above
// it when SOL is X (the active bin moves up). `st` is that position's saved
// state and is updated in place.
function exitCheck(p, st, nowMs) {
  const out = { alerts: [], exit: null, pnlPct: null, below: false, solOnly: !(p.depositTokenUi > 0) };
  if (p.partial || p.unindexed || p.lower == null || p.upper == null || p.active == null) return out;
  const pnlPct = p.depositSol > 0 ? p.pnlSol / p.depositSol * 100 : null;
  out.pnlPct = pnlPct;
  const span = p.upper - p.lower;
  const below = p.solIsX ? p.active > p.upper : p.active < p.lower;
  const bottomThird = p.solIsX ? p.active > p.upper - span / 3 : p.active < p.lower + span / 3;
  out.below = below;
  if (below && out.solOnly) { if (st.belowSince == null) st.belowSince = nowMs; } else st.belowSince = null;
  st.alerted = st.alerted || [];
  const once = (key, active, text) => {
    const had = st.alerted.includes(key);
    if (active && !had) { st.alerted.push(key); out.alerts.push({ key, text, fresh: true }); }
    else if (active) out.alerts.push({ key, text, fresh: false });
    else if (had) st.alerted = st.alerted.filter(k => k !== key);
  };
  if (EXIT.alertAtPnlPct != null && pnlPct != null) once("pnl", pnlPct <= EXIT.alertAtPnlPct, `PnL ${pnlPct.toFixed(1)}%`);
  if (out.solOnly) {
    once("out", below, "below the range: earning nothing");
    if (EXIT.alertBottomThird) once("third", bottomThird && !below, "price in the bottom third of the range");
  }
  // Exits: the first that holds. A stop counts before a take-profit.
  const belowMin = st.belowSince != null ? (nowMs - st.belowSince) / 60000 : 0;
  if (out.solOnly && EXIT.closeBelowRangeAfterMin != null && st.belowSince != null && belowMin >= EXIT.closeBelowRangeAfterMin) {
    out.exit = { rule: "belowRange", text: `below the range for ${Math.round(belowMin)} min` };
  } else if (EXIT.stopLossPct != null && pnlPct != null && pnlPct <= EXIT.stopLossPct) {
    out.exit = { rule: "stopLoss", text: `PnL ${pnlPct.toFixed(1)}% ≤ ${EXIT.stopLossPct}%` };
  } else if (EXIT.takeProfitPct != null && pnlPct != null && pnlPct >= EXIT.takeProfitPct) {
    out.exit = { rule: "takeProfit", text: `PnL ${pnlPct.toFixed(1)}% ≥ ${EXIT.takeProfitPct}%` };
  }
  return out;
}

// Every row's positions through exitCheck; new alerts and exits become
// notifications (once each). Fills r.exits / r.exitAlerts for the table.
async function runExitRules(rows, nowMs) {
  const st = loadExitState();
  const seen = new Set();
  const notes = [];
  for (const r of rows) {
    r.exits = [];
    r.exitAlerts = [];
    for (const p of r.positions) {
      seen.add(p.address);
      const ps = st[p.address] || (st[p.address] = {});
      const c = exitCheck(p, ps, nowMs);
      if (c.pnlPct != null) {
        exitLog({ ts: new Date(nowMs).toISOString(), position: p.address, pool: p.pool, symbol: r.symbol, pnlPct: +c.pnlPct.toFixed(3),
          pnlSol: p.pnlSol, inRange: p.inRange, below: c.below, solOnly: c.solOnly, exit: c.exit && c.exit.rule, alerts: c.alerts.map(a => a.key) });
      }
      for (const a of c.alerts) {
        r.exitAlerts.push(a.text);
        if (a.fresh) notes.push({ r, kind: "alert", text: `${a.text} (${shortAddr(p.address)})` });
      }
      if (c.exit) {
        r.exits.push({ address: p.address, ...c.exit });
        if (!ps.exitNotified) { ps.exitNotified = true; notes.push({ r, kind: "exit", text: `${c.exit.text} (${shortAddr(p.address)})` }); }
      } else ps.exitNotified = false;
    }
    r.exitAlerts = [...new Set(r.exitAlerts)];
  }
  for (const k of Object.keys(st)) if (!seen.has(k)) delete st[k];
  saveExitState(st);
  for (const n of notes) {
    try {
      const nt = new Notification();
      nt.title = n.kind === "exit" ? `\u{1F534} Exit ${n.r.symbol}` : `⚠️ ${n.r.symbol}`;
      nt.body = n.kind === "exit"
        ? `${n.text}. ${EXIT.mode === "auto" ? "Open MET Portfolio and it closes and swaps to SOL." : "Tap to close and swap to SOL."}`
        : `${n.text}. Your call.`;
      nt.threadIdentifier = "met-exit";
      nt.openURL = `scriptable:///run/${encodeURIComponent(Script.name())}?exit=${n.r.tokenMint}`;
      await nt.schedule();
    } catch (e) {}
  }
}

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


// How held and earned amounts are shown (setDisplay in the UI part): what was
// last picked in the "Show money in" row, else MET Secrets, else SOL.
let DISPLAY_CUR = loadDisplayChoice(SECRETS.DISPLAY_CURRENCY);

// ----------------------------- wallet ----------------------------------------
const RPCS = metRpcs(secret);
const READ_RPC = RPCS.read, SEND_RPC = RPCS.send, IS_HELIUS = RPCS.isHelius;

let KEY_ERROR = null;
const KP = (() => {
  const raw = secret("SOLANA_PRIVATE_KEY");
  if (!raw) return null;
  try { return MET.parseSecretKey(raw); } catch (e) { KEY_ERROR = e.message; return null; }
})();
// Practice only: act as another (funded) address with signature checks off.
const PRACTICE_AS = DRY_RUN && MET.isAddress(secret("PRACTICE_WALLET")) ? secret("PRACTICE_WALLET") : null;
const ACTOR = KP ? (PRACTICE_AS ? { address: PRACTICE_AS, watchOnly: true } : KP) : null;

// Which wallet to show: the widget Parameter ("address" or "Name|address"),
// the tap URL's ?wallet=, or the wallet MET Secrets holds.
const { WALLET, NAME } = (() => {
  const q = args.queryParameters || {};
  const raw = String(args.widgetParameter || q.wallet || "").trim();
  let name = String(q.name || "").trim(), addr = raw;
  if (raw.includes("|")) { name = raw.slice(0, raw.indexOf("|")).trim(); addr = raw.slice(raw.indexOf("|") + 1).trim(); }
  if (MET.isAddress(addr)) return { WALLET: addr, NAME: name };
  return { WALLET: ACTOR ? ACTOR.address : null, NAME: name };
})();
// Buttons only for the wallet whose key is here.
const CAN_ACT = !!(KP && SEND_RPC && ACTOR && WALLET === ACTOR.address);
const ACT_MISSING = CAN_ACT ? null
  : !KP ? (KEY_ERROR ? `a valid SOLANA_PRIVATE_KEY (${KEY_ERROR})` : "SOLANA_PRIVATE_KEY")
  : !SEND_RPC ? "HELIUS_API_KEY"
  : null;

// ----------------------------- constants -------------------------------------
const WSOL = MET.WSOL;
const DATAPI = "https://dlmm.datapi.meteora.ag";
// Jupiter prices: the keyed host when MET Secrets has a Jupiter key, the free
// one otherwise.
const JUP_KEY = secret("JUPITER_API_KEY");
const JUP_PRICE = JUP_KEY ? "https://api.jup.ag/price/v3" : "https://lite-api.jup.ag/price/v3";
const APP_URL = WALLET ? `https://app.lpagent.io/portfolio?address=${WALLET}` : "https://app.meteora.ag";
const REFRESH_MIN = 15;
const WIDGET_BUDGET_MS = 22_000;
// Never sold by Cleanup: they are money, not leftovers.
const KEEP_MINTS = new Set([
  WSOL,
  "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v",   // USDC
  "Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB",   // USDT
]);
const DUST_SOL = 0.002;
const REALISED_DAYS = 7;
const STRATEGIES = ["spot", "curve", "bidask"];
const STRATEGY_NAME = { spot: "Spot", curve: "Curve", bidask: "Bid-Ask" };

const num = v => { const n = Number(v); return v == null || v === "" || !Number.isFinite(n) ? null : n; };
const deep = (o, ...ks) => ks.reduce((a, k) => (a == null ? null : a[k]), o);

// ----------------------------- cache -----------------------------------------
const fm = FileManager.local();
const cachePath = suffix => fm.joinPath(fm.libraryDirectory(), `met_port_${WALLET}_${suffix}.json`);
function saveJson(suffix, data) { try { fm.writeString(cachePath(suffix), JSON.stringify(data)); } catch (e) {} }
function loadJson(suffix) {
  try { const p = cachePath(suffix); if (fm.fileExists(p)) return JSON.parse(fm.readString(p)); } catch (e) {}
  return null;
}
const DATA_SCHEMA = 1;
function loadCache() { const c = loadJson("data"); return c && c.v === DATA_SCHEMA && c.data ? c : null; }

// ----------------------------- data ------------------------------------------
async function fetchOpenPortfolio(wallet) {
  const pools = [];
  let total = null, solPrice = null;
  for (let page = 1; page <= 10; page++) {
    const j = await MET.getJson(`${DATAPI}/portfolio/open?user=${wallet}&page=${page}&page_size=50`, { timeout: 15 });
    if (page === 1) { total = j.total || null; solPrice = num(j.solPrice); }
    for (const p of j.pools || []) pools.push(p);
    if (!j.hasNext) break;
  }
  return { pools, total, solPrice };
}
async function fetchPositionsOf(wallet, pool, status) {
  const out = [];
  for (let page = 1; page <= 5; page++) {
    const j = await MET.getJson(`${DATAPI}/positions/${pool}/pnl?user=${wallet}&status=${status}&page=${page}&page_size=100`, { timeout: 15 });
    for (const q of j.positions || []) out.push(q);
    if (!j.hasNext) break;
  }
  return out;
}
// Pools by address, 50 to a call. The API's multi-value filter takes commas.
async function fetchPoolInfo(ids) {
  const map = new Map();
  for (let i = 0; i < ids.length; i += 50) {
    const chunk = ids.slice(i, i + 50);
    const j = await MET.getJson(`${DATAPI}/pools?page_size=${chunk.length}&filter_by=${encodeURIComponent(`pool_address=[${chunk.join(",")}]`)}`, { timeout: 15 });
    for (const p of j.data || []) map.set(p.address, p);
  }
  return map;
}
async function fetchWalletTokens(rpc, wallet) {
  const q = prog => rpc.call("getTokenAccountsByOwner", [wallet, { programId: prog }, { encoding: "jsonParsed", commitment: "confirmed" }]);
  const [a, b] = await Promise.all([q(MET.PROGRAM.TOKEN), q(MET.PROGRAM.TOKEN_2022).catch(() => ({ value: [] }))]);
  const out = [];
  for (const [prog, r] of [[MET.PROGRAM.TOKEN, a], [MET.PROGRAM.TOKEN_2022, b]]) {
    for (const v of (r && r.value) || []) {
      const info = deep(v, "account", "data", "parsed", "info");
      if (!info || !info.tokenAmount) continue;
      out.push({
        account: v.pubkey, mint: info.mint, program: prog,
        raw: BigInt(info.tokenAmount.amount), decimals: info.tokenAmount.decimals,
        ui: Number(info.tokenAmount.uiAmountString || 0), lamports: v.account.lamports, frozen: info.state === "frozen",
      });
    }
  }
  return out;
}
// USD prices by mint: Jupiter first (50 a call), DexScreener for whatever
// Jupiter did not answer (30 a call, deepest pool wins). Jupiter's free API
// throttles, and a throttled answer must not read as "worthless": the map
// carries how many lookups failed outright, so callers can say so.
async function fetchPrices(mints) {
  const out = new Map();
  let sourceFailed = false;
  const list = [...new Set(mints)].filter(Boolean);
  for (let i = 0; i < list.length; i += 50) {
    try {
      const j = await MET.getJson(`${JUP_PRICE}?ids=${list.slice(i, i + 50).join(",")}`, { timeout: 10, headers: JUP_KEY ? { "x-api-key": JUP_KEY } : {} });
      for (const [m, v] of Object.entries(j || {})) if (v && num(v.usdPrice) > 0) out.set(m, num(v.usdPrice));
    } catch (e) { sourceFailed = true; }
  }
  const missing = list.filter(m => !out.has(m));
  for (let i = 0; i < missing.length; i += 30) {
    const chunk = missing.slice(i, i + 30);
    try {
      const arr = await MET.getJson(`https://api.dexscreener.com/tokens/v1/solana/${chunk.join(",")}`, { timeout: 10 });
      const best = new Map();
      for (const p of Array.isArray(arr) ? arr : []) {
        const base = p && p.baseToken && p.baseToken.address;
        const usd = num(p && p.priceUsd), liq = num(p && p.liquidity && p.liquidity.usd) || 0;
        if (!base || !chunk.includes(base) || !(usd > 0)) continue;
        if (!best.has(base) || best.get(base).liq < liq) best.set(base, { usd, liq });
      }
      for (const [m, v] of best) out.set(m, v.usd);
    } catch (e) { sourceFailed = true; }
  }
  // Only a token still unpriced after a source failed is a read problem; one
  // both sources answered for, without a price, simply has no market.
  out.failed = sourceFailed ? list.filter(m => !out.has(m)).length : 0;
  return out;
}
// Every DLMM position this wallet owns, from the chain itself. The data API
// indexes a new position within moments, not instantly; the chain is the
// check that nothing is missing, and its lamports are the exact rent.
async function chainPositions(rpc, wallet) {
  const disc = MET.b58(Uint8Array.from([117, 176, 212, 199, 245, 180, 133, 182]));
  const r = await rpc.call("getProgramAccounts", [MET.PROGRAM.DLMM, {
    encoding: "base64", dataSlice: { offset: 8, length: 32 },
    filters: [{ memcmp: { offset: 0, bytes: disc } }, { memcmp: { offset: 40, bytes: wallet } }],
  }], { timeout: 30 });
  return (r || []).map(a => ({ address: a.pubkey, pool: MET.b58(MET.unb64(a.account.data[0])), lamports: a.account.lamports }));
}

// At most `n` requests in flight: the data API allows 30 a second, and a
// wallet spread over many pools would otherwise ask for all of them at once.
async function mapLimited(items, n, fn) {
  const out = new Array(items.length);
  let next = 0;
  const worker = async () => { while (next < items.length) { const i = next++; out[i] = await fn(items[i], i); } };
  await Promise.all(Array.from({ length: Math.min(n, items.length) }, worker));
  return out;
}
function startOfLocalDay(ms) { const d = new Date(ms); d.setHours(0, 0, 0, 0); return d.getTime(); }
function localDaysAgo(ms, now) { return Math.round((startOfLocalDay(now) - startOfLocalDay(ms)) / 86400000); }
function dayLabel(ms, now) {
  const n = localDaysAgo(ms, now);
  if (n === 0) return "Today";
  if (n === 1) return "Yesterday";
  return new Date(ms).toLocaleDateString([], { weekday: "short", month: "short", day: "numeric" });
}

// One position, from the data API's PnL entry.
function positionOf(q, pe) {
  const solIsX = pe.tokenXMint === WSOL;
  const u = q.unrealizedPnl || {};
  const amt = k => num(deep(u, k, "amount")) || 0;
  const sol = k => num(deep(u, k, "amountSol")) || 0;
  return {
    address: q.positionAddress, pool: pe.poolAddress, solIsX,
    lower: num(q.lowerBinId), upper: num(q.upperBinId), active: num(q.poolActiveBinId),
    inRange: q.isOutOfRange !== true,
    createdAt: num(q.createdAt) ? num(q.createdAt) * 1000 : null,
    pnlSol: num(q.pnlSol) || 0,
    depositSol: num(deep(q, "allTimeDeposits", "total", "sol")) || 0,
    withdrawnSol: num(deep(q, "allTimeWithdrawals", "total", "sol")) || 0,
    claimedSol: num(deep(q, "allTimeFees", "total", "sol")) || 0,
    valueSol: num(u.balancesSol) || 0,
    unclaimedSol: sol("unclaimedFeeTokenX") + sol("unclaimedFeeTokenY") + sol("unclaimedRewardTokenX") + sol("unclaimedRewardTokenY"),
    tokenUi: solIsX ? amt("balanceTokenY") : amt("balanceTokenX"),
    depositTokenUi: num(deep(q, "allTimeDeposits", solIsX ? "tokenY" : "tokenX", "amount")) || 0,
    solUi: solIsX ? amt("balanceTokenX") : amt("balanceTokenY"),
  };
}

async function main() {
  if (!WALLET) {
    const msg = "No wallet: add SOLANA_PRIVATE_KEY to MET Secrets, or put an address in the widget Parameter.";
    return { widget: errorWidget(msg), data: null, message: msg };
  }
  const startedAt = Date.now();
  let data = null, stale = false, dataTime = Date.now(), error = null;
  try {
    const warnings = [];
    const [port, balance, tokens, onChain, usdIdr] = await Promise.all([
      fetchOpenPortfolio(WALLET),
      READ_RPC.call("getBalance", [WALLET, { commitment: "confirmed" }]).then(r => r.value).catch(() => null),
      fetchWalletTokens(READ_RPC, WALLET).catch(() => null),
      RPCS.keyed.length ? chainPositions(READ_RPC, WALLET).catch(() => null) : Promise.resolve(null),
      DISPLAY_CUR === "IDR" ? usdIdrRate().catch(() => null) : null,
    ]);
    if (balance == null) warnings.push("SOL balance");
    if (tokens == null) warnings.push("token accounts");
    if (DISPLAY_CUR === "IDR" && !usdIdr) warnings.push("the rupiah rate (amounts shown in SOL)");
    if (onChain == null && RPCS.keyed.length) warnings.push("the chain check of positions");
    const poolIds = [...new Set(port.pools.map(p => p.poolAddress).concat((onChain || []).map(p => p.pool)))];
    const [pnlLists, poolInfo] = await Promise.all([
      mapLimited(port.pools, 8, pe => fetchPositionsOf(WALLET, pe.poolAddress, "open").catch(() => null)),
      fetchPoolInfo(poolIds).catch(() => new Map()),
    ]);
    let solUsd = port.solPrice || (() => {
      for (const p of poolInfo.values()) {
        if (deep(p, "token_x", "address") === WSOL && num(deep(p, "token_x", "price"))) return num(p.token_x.price);
        if (deep(p, "token_y", "address") === WSOL && num(deep(p, "token_y", "price"))) return num(p.token_y.price);
      }
      return null;
    })();
    // A wallet with no open position gets no SOL price from either of those;
    // ask the price sources directly (leftover tokens and money display need it).
    if (!solUsd) { try { solUsd = (await fetchPrices([WSOL])).get(WSOL) || null; } catch (e) {} }
    if (DISPLAY_CUR !== "SOL" && !solUsd) warnings.push("the SOL price (amounts shown in SOL)");

    // Positions by token. A symbol is not an identity; the mint is the key.
    const rowsByMint = new Map();
    const known = new Set();
    const rowFor = (mint, symbol, solIsX) => {
      if (!rowsByMint.has(mint)) rowsByMint.set(mint, { tokenMint: mint, symbol, solIsX, positions: [], pools: new Set() });
      return rowsByMint.get(mint);
    };
    port.pools.forEach((pe, i) => {
      const solIsX = pe.tokenXMint === WSOL;
      const mint = solIsX ? pe.tokenYMint : pe.tokenXMint;
      const symbol = solIsX ? pe.tokenY : pe.tokenX;
      const row = rowFor(mint, symbol, solIsX);
      row.pools.add(pe.poolAddress);
      const list = pnlLists[i];
      if (list) {
        for (const q of list) { const p = positionOf(q, pe); row.positions.push(p); known.add(p.address); }
      } else {
        // The per-pool call failed: keep the pool's totals, not its ranges.
        for (const a of pe.listPositions || []) {
          row.positions.push({ address: a, pool: pe.poolAddress, solIsX, partial: true, inRange: !(pe.positionsOutOfRange || []).includes(a) });
          known.add(a);
        }
        row.poolTotals = (row.poolTotals || []).concat([pe]);
      }
    });
    // On chain but not yet in the index: listed, closable, not valued.
    const unindexed = (onChain || []).filter(p => !known.has(p.address));
    for (const p of unindexed) {
      const info = poolInfo.get(p.pool);
      if (!info) continue;
      const solIsX = deep(info, "token_x", "address") === WSOL;
      const tok = solIsX ? info.token_y : info.token_x;
      const row = rowFor(tok.address, tok.symbol, solIsX);
      row.pools.add(p.pool);
      row.positions.push({ address: p.address, pool: p.pool, solIsX, unindexed: true, inRange: null });
    }
    const rentByPosition = new Map((onChain || []).map(p => [p.address, p.lamports]));
    // Without the chain read, the rent a standard position locks today.
    const positionRent = (await MET.rentRates(READ_RPC)).position;

    const ladders = loadLadders(WALLET);
    const nowMs = Date.now();
    const rows = [...rowsByMint.values()].map(r => {
      const ps = r.positions.filter(p => !p.partial && !p.unindexed);
      const sum = k => ps.reduce((s, p) => s + (p[k] || 0), 0);
      const totalsFromPools = (r.poolTotals || []).reduce((acc, pe) => ({
        value: acc.value + (num(pe.balancesSol) || 0), fees: acc.fees + (num(pe.unclaimedFeesSol) || 0),
        pnl: acc.pnl + (num(pe.pnlSol) || 0), dep: acc.dep + (num(pe.totalDepositSol) || 0),
      }), { value: 0, fees: 0, pnl: 0, dep: 0 });
      const valueSol = sum("valueSol") + totalsFromPools.value;
      const unclaimedSol = sum("unclaimedSol") + totalsFromPools.fees;
      const pnlSol = sum("pnlSol") + totalsFromPools.pnl;
      const depositSol = sum("depositSol") + totalsFromPools.dep;
      const claimedSol = sum("claimedSol");
      const created = ps.map(p => p.createdAt).filter(Boolean);
      const ageDays = created.length ? Math.max((nowMs - Math.min(...created)) / 86400000, 1 / 1440) : null;
      const ranged = ps.filter(p => p.lower != null && p.upper != null);
      // The pool holding most of the row's value sets the price marker.
      const byPool = new Map();
      for (const p of ps) byPool.set(p.pool, (byPool.get(p.pool) || 0) + (p.valueSol || 0));
      const mainPool = [...byPool.entries()].sort((a, b) => b[1] - a[1]).map(x => x[0])[0] || [...r.pools][0];
      const info = poolInfo.get(mainPool) || {};
      const tok = r.solIsX ? info.token_y : info.token_x;
      const mcap = num(deep(tok, "market_cap"));
      const mainActive = (ps.find(p => p.pool === mainPool && p.active != null) || {}).active;
      const inRangeCount = r.positions.filter(p => p.inRange === true).length;
      const rent = r.positions.reduce((s, p) => s + (rentByPosition.get(p.address) || positionRent), 0) / 1e9;
      const ladder = ladders.slice().reverse().find(l => l.positions && l.positions.some(a => r.positions.some(p => p.address === a)));
      return {
        symbol: r.symbol, tokenMint: r.tokenMint, solIsX: r.solIsX, pools: [...r.pools], mainPool,
        positions: r.positions, posCount: r.positions.length, inRangeCount,
        valueSol, unclaimedSol, pnlSol, depositSol, claimedSol, rentSol: rent,
        roi: depositSol > 0 ? pnlSol / depositSol * 100 : null,
        ageDays,
        feePctDay: ageDays && ageDays >= 30 / 1440 && valueSol > 0 ? (claimedSol + unclaimedSol) / valueSol * 100 / ageDays : null,
        mcap, binStep: num(deep(info, "pool_config", "bin_step")),
        poolFeePctDay1h: num(deep(info, "fee_tvl_ratio", "1h")) != null ? num(info.fee_tvl_ratio["1h"]) * 24 : null,
        lo: ranged.length ? Math.min(...ranged.map(p => p.lower)) : null,
        hi: ranged.length ? Math.max(...ranged.map(p => p.upper)) : null,
        active: mainActive,
        holdsToken: ps.some(p => p.tokenUi > 0),
        solInside: ps.reduce((s, p) => s + (p.solUi || 0), 0),
        unindexed: r.positions.filter(p => p.unindexed).length,
        partial: r.positions.some(p => p.partial),
        strategy: ladder ? ladder.strategy : null,
        ladder: ladder || null,
      };
    });
    rows.sort((a, b) => (b.pnlSol || 0) - (a.pnlSol || 0) || b.valueSol - a.valueSol);
    await runExitRules(rows, nowMs);

    // Wallet: SOL, wrapped SOL, leftover tokens at Jupiter's price.
    const walletSol = balance != null ? balance / 1e9 : null;
    const wsolSol = (tokens || []).filter(t => t.mint === WSOL).reduce((s, t) => s + Number(t.raw) / 1e9, 0);
    const others = (tokens || []).filter(t => t.mint !== WSOL && t.raw > 0n);
    const prices = await fetchPrices(others.map(t => t.mint)).catch(() => { const m = new Map(); m.failed = 1; return m; });
    if (prices.failed) warnings.push("some token prices");
    if (pnlLists.some(l => l == null)) warnings.push("some pools' position detail");
    const leftovers = others.map(t => {
      const usd = prices.get(t.mint);
      const valueSol = usd != null && solUsd ? t.ui * usd / solUsd : null;
      return { ...t, valueSol, keep: KEEP_MINTS.has(t.mint) };
    }).sort((a, b) => (b.valueSol || 0) - (a.valueSol || 0));
    const leftoverSol = leftovers.reduce((s, t) => s + (t.valueSol || 0), 0);
    const emptyAccounts = (tokens || []).filter(t => t.raw === 0n && !t.frozen);
    const emptyRentSol = emptyAccounts.reduce((s, t) => s + t.lamports, 0) / 1e9;

    const lpSol = rows.reduce((s, r) => s + r.valueSol, 0);
    const feesSol = rows.reduce((s, r) => s + r.unclaimedSol, 0);
    const rentSol = rows.reduce((s, r) => s + r.rentSol, 0);
    const pnlSol = rows.reduce((s, r) => s + r.pnlSol, 0);
    const basisSol = rows.reduce((s, r) => s + r.depositSol, 0);
    const netWorth = (walletSol || 0) + wsolSol + lpSol + feesSol + rentSol + leftoverSol;
    const posCount = rows.reduce((s, r) => s + r.posCount, 0);
    const CHROME = 194, PITCH = 24;
    const rowBudget = config.widgetFamily === "small" ? 0
      : Math.max(0, Math.min(10, Math.floor((widgetSize().h - CHROME + 11) / PITCH)));
    data = {
      rows, rowBudget, solUsd, usdIdr, walletSol, wsolSol, leftovers, leftoverSol, emptyAccounts: emptyAccounts.length, emptyRentSol,
      lpSol, feesSol, rentSol, pnlSol, basisSol, netWorth, posCount,
      tokenCount: rows.length, tokensInRange: rows.filter(r => r.inRangeCount > 0).length,
      unindexed: rows.reduce((s, r) => s + r.unindexed, 0),
      warnings,
      chainChecked: onChain != null,
      timings: { total: Date.now() - startedAt },
    };
    // Positions hold BigInt nowhere here, so the whole table caches.
    saveJson("data", { t: Date.now(), v: DATA_SCHEMA, data: { ...data, leftovers: data.leftovers.map(t => ({ ...t, raw: t.raw.toString() })) } });
  } catch (e) {
    const c = loadCache();
    if (c) { data = c.data; stale = true; dataTime = c.t; error = String((e && e.message) || e).slice(0, 80); }
    else {
      const msg = "Couldn't load positions: " + String((e && e.message) || e).slice(0, 110);
      return { widget: errorWidget(msg), data: null, message: msg };
    }
  }
  return { widget: buildWidget({ ...data, rows: data.rows.slice(0, data.rowBudget || 6) }, stale, dataTime), data, stale, dataTime, error };
}

// ----------------------------- realised --------------------------------------
// Closed positions from the data API, grouped into the ladders they were
// opened as: same pool, opened within three minutes of each other.
function loadRealised() { return loadJson("realised"); }
async function buildRealised(onNote) {
  const now = Date.now();
  const cutoff = startOfLocalDay(now) - (REALISED_DAYS - 1) * 86400000;
  const pools = [];
  for (let page = 1; page <= 10; page++) {
    const j = await MET.getJson(`${DATAPI}/portfolio?user=${WALLET}&days_back=${REALISED_DAYS}&page=${page}&page_size=50`, { timeout: 20 });
    for (const p of j.pools || []) pools.push(p);
    if (!j.hasNext) break;
  }
  const closed = [];
  let done = 0, unread = 0;
  for (let i = 0; i < pools.length; i += 4) {
    const batch = pools.slice(i, i + 4);
    const lists = await Promise.all(batch.map(p => fetchPositionsOf(WALLET, p.poolAddress, "closed").catch(() => null)));
    unread += lists.filter(l => l == null).length;
    lists.forEach((list, k) => {
      const pe = batch[k];
      const solIsX = pe.tokenXMint === WSOL;
      for (const q of list || []) {
        const closedAt = num(q.closedAt) ? num(q.closedAt) * 1000 : null;
        if (!closedAt || closedAt < cutoff) continue;
        closed.push({
          pool: pe.poolAddress, token: solIsX ? pe.tokenY : pe.tokenX,
          createdAt: num(q.createdAt) * 1000, closedAt,
          pnlSol: num(q.pnlSol) || 0,
          capitalSol: num(deep(q, "allTimeDeposits", "total", "sol")) || 0,
          feesSol: num(deep(q, "allTimeFees", "total", "sol")) || 0,
        });
      }
    });
    done += batch.length;
    onNote && onNote(`Reading closes: ${done}/${pools.length} pools`);
  }
  closed.sort((a, b) => a.createdAt - b.createdAt);
  const ladders = [];
  for (const c of closed) {
    const l = ladders.find(x => x.pool === c.pool && Math.abs(x.mintAt - c.createdAt) <= 180_000);
    if (l) {
      l.sliceCount++; l.pnlSol += c.pnlSol; l.capitalSol += c.capitalSol; l.feesSol += c.feesSol;
      l.burnAt = Math.max(l.burnAt, c.closedAt);
    } else {
      ladders.push({ key: c.pool + "|" + c.createdAt, pool: c.pool, token: c.token, sliceCount: 1, mintAt: c.createdAt,
        burnAt: c.closedAt, pnlSol: c.pnlSol, capitalSol: c.capitalSol, feesSol: c.feesSol });
    }
  }
  const byDay = new Map();
  for (const l of ladders) {
    const d = startOfLocalDay(l.burnAt);
    const e = byDay.get(d) || { at: d, pnlSol: 0, count: 0 };
    e.pnlSol += l.pnlSol; e.count++;
    byDay.set(d, e);
  }
  const snap = {
    builtAt: now, days: REALISED_DAYS, ladders: ladders.sort((a, b) => b.burnAt - a.burnAt),
    byDay: [...byDay.values()].sort((a, b) => b.at - a.at),
    totals: { pnlSol: ladders.reduce((s, l) => s + l.pnlSol, 0), count: ladders.length },
    unreadPools: unread,
  };
  saveJson("realised", snap);
  return snap;
}

// ----------------------------- widget ----------------------------------------
function rangePct(r) {
  if (r.lo == null || r.hi == null || r.active == null || r.hi < r.lo) return null;
  let f = r.hi === r.lo ? 0.5 : (r.active - r.lo) / (r.hi - r.lo);
  if (r.solIsX) f = 1 - f;
  return f * 100;
}
const fmtRangePct = p => (p == null ? "--" : Math.round(Math.max(-999, Math.min(999, p))) + "%");
function rangeColor(r, p) {
  if (r.inRangeCount > 0) return COL.green;
  return (p != null && p >= 100) ? COL.yellow : COL.red;
}
// Market cap the token would have at a bin of the row's main pool: bins step
// the price of X in Y, so for a token/SOL pool the cap scales up with the bin.
function mcapAtBin(r, bin) {
  if (r.mcap == null || r.active == null || bin == null || !r.binStep) return null;
  const d = bin - r.active;
  return r.mcap * Math.pow(1 + r.binStep / 10000, r.solIsX ? -d : d);
}

function buildWidget(d, stale, dataTime) {
  setDisplay(DISPLAY_CUR, d.solUsd, d.usdIdr);
  let realised = null;
  try { realised = loadRealised(); } catch (e) {}
  const small = config.widgetFamily === "small";
  const w = baseWidget();
  let runUrl = "scriptable:///run/" + encodeURIComponent(Script.name()) + "?wallet=" + WALLET;
  if (NAME) runUrl += "&name=" + encodeURIComponent(NAME);
  w.url = runUrl;
  w.setPadding(12, 17, 8, 17);
  w.refreshAfterDate = new Date(Date.now() + REFRESH_MIN * 60 * 1000);

  const head = w.addStack();
  head.centerAlignContent();
  const icon = head.addText("☄️");
  icon.font = Font.systemFont(16);
  head.addSpacer(6);
  const t = head.addText(NAME || shortAddr(WALLET));
  t.font = Font.boldSystemFont(13);
  t.textColor = COL.text;
  t.lineLimit = 1;
  head.addSpacer();
  const tag = head.addText(stale ? `STALE ${fmtTime(dataTime)}` : `Updated ${fmtTime(Date.now())}`);
  tag.font = Font.semiboldSystemFont(10);
  tag.textColor = stale ? COL.red : COL.green;

  w.addSpacer(6);
  const nwRow = w.addStack();
  const nwLabel = nwRow.addText("NET WORTH");
  nwLabel.font = Font.semiboldSystemFont(10);
  nwLabel.textColor = COL.accent;
  if (d.solUsd) {
    nwRow.addSpacer();
    // The other unit beside the big number: dollars under SOL, SOL under money.
    const px = nwRow.addText(`SOL ${fmtMoney(d.solUsd)} · ` + (displayIsFiat() ? fmtSol(d.netWorth, 2) : `≈ ${fmtUsd(d.netWorth * d.solUsd)}`));
    px.font = Font.mediumSystemFont(10);
    px.textColor = COL.muted;
    px.lineLimit = 1;
    px.minimumScaleFactor = 0.7;
  }
  w.addSpacer(3);
  const nw = w.addText(fmtVal(d.netWorth, 3));
  nw.font = Font.boldSystemFont(small ? 24 : 26);
  nw.textColor = COL.text;
  nw.minimumScaleFactor = 0.6;
  nw.lineLimit = 1;
  w.addSpacer(6);

  const pnlText = fmtVal(d.pnlSol, 3, true, true) + (d.basisSol > 0 ? `  ${d.pnlSol >= 0 ? "+" : ""}${(d.pnlSol / d.basisSol * 100).toFixed(1)}%` : "");
  const grid = w.addStack();
  grid.centerAlignContent();
  cell(grid, `LP · ${d.posCount}`, fmtVal(d.lpSol, 2, false, true), COL.text);
  grid.addSpacer();
  if (!small) {
    cell(grid, "Fees", fmtVal(d.feesSol, 3, false, true), d.feesSol > 0 ? COL.green : COL.muted);
    grid.addSpacer();
  }
  cell(grid, "PnL", pnlText, signColor(d.pnlSol));
  if (!small) {
    grid.addSpacer();
    cell(grid, "In Range", `${d.tokensInRange}/${d.tokenCount}`, d.tokensInRange === d.tokenCount ? COL.green : COL.yellow);
  }

  if (!small && realised && Array.isArray(realised.byDay)) {
    const today = startOfLocalDay(Date.now());
    const cols = [];
    for (let i = 0; i < 4; i++) {
      const at = today - i * 86400000;
      cols.push(realised.byDay.find(e => e.at === at) || { at, pnlSol: null });
    }
    w.addSpacer(5);
    const rg = w.addStack();
    cols.forEach((e, i) => {
      if (i) rg.addSpacer();
      smallCell(rg, i === 0 ? "TODAY RPNL" : `${i}d AGO RPNL`, e.pnlSol == null ? "--" : fmtVal(e.pnlSol, 3, true, true),
        e.pnlSol == null ? COL.muted : e.pnlSol >= 0 ? COL.green : COL.red);
    });
    rg.addSpacer();
  }

  const list = stale ? [] : (d.rows || []);
  if (!list.length && !small) {
    w.addSpacer(10);
    const note = w.addText(stale ? "Positions hidden until this refreshes" : "No open positions");
    note.font = Font.semiboldSystemFont(11);
    note.textColor = COL.muted;
    note.centerAlignText();
  }
  if (list.length && !small) {
    const usable = widgetSize().w - 34;
    const GAP = 3, PT = usable >= 320 ? 9.5 : 9;
    const barW = Math.round(Math.max(22, Math.min(32, usable * 0.085)));
    const LBLW = 28;
    const rem = usable - barW - LBLW - GAP * 6;
    const WT = { pair: 2.6, value: 2.2, pnl: 2.2, fees: 2.0, feePct: 1.9, age: 1.3 };
    let tot = 0; for (const k in WT) tot += WT[k];
    const W = {}; for (const k in WT) W[k] = Math.floor(rem * WT[k] / tot);
    const LBL = Font.semiboldSystemFont(7.5), DAT = Font.semiboldSystemFont(PT);
    w.addSpacer(6);
    const hdr = w.addStack();
    hdr.centerAlignContent();
    col(hdr, "", W.pair, LBL, COL.faint); hdr.addSpacer(GAP);
    col(hdr, "VALUE", W.value, LBL, COL.faint, true); hdr.addSpacer(GAP);
    col(hdr, "PNL", W.pnl, LBL, COL.faint, true); hdr.addSpacer(GAP);
    col(hdr, "FEES", W.fees, LBL, COL.faint, true); hdr.addSpacer(GAP);
    col(hdr, "FEE%/D", W.feePct, LBL, COL.faint, true); hdr.addSpacer(GAP);
    col(hdr, "AGE", W.age, LBL, COL.faint, true); hdr.addSpacer(GAP);
    col(hdr, "RANGE", barW, LBL, COL.faint);
    w.addSpacer(2);
    for (let i = 0; i < list.length; i++) {
      const r = list[i];
      const row = w.addStack();
      row.centerAlignContent();
      const rp = rangePct(r);
      col(row, r.symbol, W.pair, DAT, COL.text); row.addSpacer(GAP);
      col(row, fmtVal(r.valueSol, 2, false, true), W.value, DAT, COL.text, true); row.addSpacer(GAP);
      col(row, fmtVal(r.pnlSol, 3, true, true), W.pnl, DAT, signColor(r.pnlSol), true); row.addSpacer(GAP);
      col(row, fmtVal(r.unclaimedSol, 3, false, true), W.fees, DAT, r.unclaimedSol > 0 ? COL.green : COL.muted, true); row.addSpacer(GAP);
      col(row, r.feePctDay != null ? r.feePctDay.toFixed(1) + "%" : "--", W.feePct, DAT, feeRateColor(r.feePctDay), true); row.addSpacer(GAP);
      col(row, fmtAge(r.ageDays), W.age, DAT, COL.muted, true); row.addSpacer(GAP);
      const bar = row.addImage(rangeBar(r.inRangeCount > 0, r.lo, r.hi, r.active, barW, 12, r.solIsX));
      bar.imageSize = new Size(barW, 12);
      col(row, fmtRangePct(rp), LBLW, Font.semiboldSystemFont(7.5), rangeColor(r, rp), true);
      if (i < list.length - 1) w.addSpacer(5);
    }
  }
  w.addSpacer();
  const foot = w.addText(`${displayIsFiat() ? `In ${DISPLAY.cur}, counted in SOL` : "All values SOL"} · ${VERSION}`);
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
  const t = head.addText("MET Portfolio");
  t.font = Font.boldSystemFont(14);
  t.textColor = COL.text;
  w.addSpacer(6);
  const m = w.addText(msg);
  m.font = Font.systemFont(11);
  m.textColor = COL.muted;
  return w;
}

// ----------------------------- table -----------------------------------------
function fillTable(t, d, stale, dataTime, refreshing, error, h) {
  setDisplay(DISPLAY_CUR, d.solUsd, d.usdIdr);
  t.removeAllRows();
  t.showSeparators = false;
  const divider = gapAbove => {
    if (gapAbove) { const sp = new UITableRow(); sp.height = gapAbove; sp.addText(""); t.addRow(sp); }
    const dv = new UITableRow();
    dv.height = 1;
    dv.backgroundColor = new Color("#8E8E93", 0.35);
    dv.addText("");
    t.addRow(dv);
  };
  const busy = h.busy;

  const header = new UITableRow();
  header.isHeader = true;
  header.height = 72;
  const hCell = header.addText(fmtVal(d.netWorth, 3),
    `${NAME || shortAddr(WALLET)}   ·   LP ${fmtVal(d.lpSol, 2, false, true)}   ·   SOL ${fmtSol((d.walletSol || 0) + (d.wsolSol || 0), 2)}`);
  hCell.widthWeight = 55;
  hCell.titleFont = Font.boldSystemFont(20);
  hCell.subtitleFont = Font.systemFont(12);
  hCell.subtitleColor = COL.muted;
  const roi = d.basisSol > 0 ? d.pnlSol / d.basisSol * 100 : null;
  const hPnl = header.addText(fmtVal(d.pnlSol, 3, true),
    (roi != null ? `ROI ${roi >= 0 ? "+" : ""}${roi.toFixed(1)}%  •  ` : "") + `fees ${fmtVal(d.feesSol, 3, false, true)}`);
  hPnl.widthWeight = 45;
  hPnl.rightAligned();
  hPnl.titleFont = Font.boldSystemFont(20);
  hPnl.titleColor = signColor(d.pnlSol);
  hPnl.subtitleFont = Font.systemFont(12);
  hPnl.subtitleColor = roi != null ? signColor(roi) : COL.muted;
  header.onSelect = () => Safari.open(APP_URL);
  header.dismissOnSelect = false;
  t.addRow(header);

  const rangeRow = new UITableRow();
  rangeRow.height = 22;
  rangeRow.isHeader = true;
  const rl = rangeRow.addText(displayIsFiat() ? fmtSol(d.netWorth, 3) : d.solUsd ? `≈ ${fmtUsd(d.netWorth * d.solUsd)}` : "");
  rl.widthWeight = 55;
  rl.titleFont = Font.systemFont(12);
  rl.titleColor = COL.faint;
  const rr = rangeRow.addText(`${d.tokensInRange}/${d.tokenCount} in range`);
  rr.widthWeight = 45;
  rr.rightAligned();
  rr.titleFont = Font.systemFont(12);
  rr.titleColor = d.tokenCount === 0 ? COL.muted : d.tokensInRange === d.tokenCount ? COL.green : d.tokensInRange > 0 ? COL.yellow : COL.red;
  t.addRow(rangeRow);

  // SOL / IDR / USD. Only the drawing changes; the sums underneath are SOL.
  {
    const cr = new UITableRow();
    cr.height = 36;
    const noRate = DISPLAY_CUR !== "SOL" && !displayIsFiat();
    const cl = cr.addText("Show money in", noRate ? "no rate yet: SOL shown" : null);
    cl.widthWeight = 40;
    cl.titleFont = Font.systemFont(12);
    cl.titleColor = COL.muted;
    cl.subtitleFont = Font.systemFont(10);
    cl.subtitleColor = COL.yellow;
    for (const c of ["SOL", "IDR", "USD"]) {
      const on = c === DISPLAY_CUR;
      const b = cr.addButton(on ? `\u25CF ${c}` : c);
      b.widthWeight = 20;
      b.centerAligned();
      b.titleFont = on ? Font.boldSystemFont(13) : Font.systemFont(13);
      b.titleColor = on ? COL.accent : busy ? COL.faint : COL.muted;
      if (!busy && !on) b.onTap = () => h.onCurrency(c);
    }
    t.addRow(cr);
  }
  statusRow(t, refreshing ? "refreshing" : stale ? "stale" : "fresh", statusText(stale, dataTime, refreshing, error),
    () => h.refresh(), d.solUsd ? `SOL ${fmtMoney(d.solUsd)}` : null);
  divider(0);

  drawProgress(t, h.progress);
  if (h.progress) divider(0);

  if (!CAN_ACT && ACT_MISSING) {
    const kr = new UITableRow();
    kr.height = 44;
    const kc = kr.addText("Buttons are off", `Add ${ACT_MISSING} to MET Secrets to claim, close and re-enter.`);
    kc.titleFont = Font.semiboldSystemFont(14);
    kc.titleColor = COL.yellow;
    kc.subtitleFont = Font.systemFont(11);
    kc.subtitleColor = COL.muted;
    t.addRow(kr);
    divider(0);
  } else if (!CAN_ACT && KP) {
    const kr = new UITableRow();
    kr.height = 36;
    const kc = kr.addText("Watching another wallet: read only.");
    kc.titleFont = Font.semiboldSystemFont(13);
    kc.titleColor = COL.muted;
    t.addRow(kr);
    divider(0);
  }
  if (CAN_ACT && DRY_RUN) {
    const pr = new UITableRow();
    pr.height = 30;
    const pc = pr.addText("Practice mode: every button simulates, nothing is sent.", "Set PRACTICE_MODE: false in MET Secrets to trade.");
    pc.titleFont = Font.semiboldSystemFont(12);
    pc.titleColor = COL.yellow;
    pc.subtitleFont = Font.systemFont(10);
    pc.subtitleColor = COL.faint;
    t.addRow(pr);
  }

  if (CAN_ACT) {
    const rowsAll = d.rows || [];
    const nPos = rowsAll.reduce((s, r) => s + r.posCount, 0);
    const ar = new UITableRow();
    ar.height = 42;
    const mkAll = (title, enabled, color, tap) => {
      const b = ar.addButton(busy ? "…" : title);
      b.widthWeight = 33;
      b.centerAligned();
      b.titleFont = Font.boldSystemFont(14);
      b.titleColor = busy || !enabled ? COL.faint : color;
      if (!busy && enabled) b.onTap = tap;
    };
    mkAll(d.feesSol > 0 ? `CLAIM · ${fmtVal(d.feesSol, 3, false, true)}` : "NO FEES", d.feesSol > 0, COL.green, () => h.onClaim(rowsAll, "all fees"));
    mkAll(nPos ? `${DRY_RUN ? "PRACTICE" : "CLOSE"} · ${nPos}` : "NOTHING", nPos > 0, DRY_RUN ? COL.yellow : COL.accent, () => h.onClose(rowsAll, "all positions"));
    mkAll("CLEANUP", true, COL.text, () => h.onCleanup());
    t.addRow(ar);
    divider(0);
  }

  if ((d.warnings || []).length) {
    const wr = new UITableRow();
    wr.height = 44;
    const wc = wr.addText(`\u26A0\uFE0F Could not read ${d.warnings.join(", ")}`,
      "Totals above leave that out, so they are low. Tap to retry.");
    wc.titleFont = Font.semiboldSystemFont(13);
    wc.titleColor = COL.yellow;
    wc.subtitleFont = Font.systemFont(11);
    wc.subtitleColor = COL.muted;
    wr.onSelect = () => h.refresh();
    wr.dismissOnSelect = false;
    t.addRow(wr);
    divider(0);
  }
  if (d.unindexed > 0) {
    const warn = new UITableRow();
    warn.height = 44;
    const wc = warn.addText(`⚠️ ${d.unindexed} position${d.unindexed === 1 ? "" : "s"} not indexed yet`,
      "On chain, but Meteora's data has not caught up; value and PnL leave them out. Tap to retry.");
    wc.titleFont = Font.semiboldSystemFont(14);
    wc.titleColor = COL.yellow;
    wc.subtitleFont = Font.systemFont(11);
    wc.subtitleColor = COL.muted;
    warn.onSelect = () => h.refresh();
    warn.dismissOnSelect = false;
    t.addRow(warn);
    divider(0);
  }

  if (!(d.rows || []).length) {
    const none = new UITableRow();
    none.height = 44;
    const nc = none.addText("No open positions", "Wallet, Cleanup and realised PnL all still work here.");
    nc.centerAligned();
    nc.titleFont = Font.semiboldSystemFont(15);
    nc.titleColor = COL.muted;
    nc.subtitleFont = Font.systemFont(11);
    nc.subtitleColor = COL.faint;
    t.addRow(none);
    divider(0);
  } else {
    const labels = new UITableRow();
    labels.height = 34;
    const l1 = labels.addText("POSITION", "in range · age"); l1.widthWeight = 28;
    const l2 = labels.addText("PnL", "roi · value"); l2.widthWeight = 25; l2.rightAligned();
    const l3 = labels.addText("FEES", "%/d · claimed"); l3.widthWeight = 25; l3.rightAligned();
    const l4 = labels.addText("MCAP", "1h pace"); l4.widthWeight = 22; l4.rightAligned();
    for (const c of [l1, l2, l3, l4]) {
      c.titleFont = Font.semiboldSystemFont(10); c.titleColor = COL.faint;
      c.subtitleFont = Font.systemFont(9); c.subtitleColor = COL.faint;
    }
    t.addRow(labels);
    divider(0);
  }

  for (const r of d.rows || []) {
    const row = new UITableRow();
    row.height = 54;
    const shape = r.strategy ? ` · ${STRATEGY_NAME[r.strategy] || r.strategy}` : "";
    const name = row.addText(r.symbol,
      `${r.inRangeCount}/${r.posCount} in range${shape}   ${fmtAge(r.ageDays)}`);
    name.widthWeight = 28;
    name.titleFont = Font.boldSystemFont(16);
    name.subtitleFont = Font.systemFont(11);
    name.subtitleColor = r.inRangeCount > 0 ? COL.green : COL.red;

    const val = row.addText(fmtVal(r.pnlSol, 3, true, true),
      (r.roi != null ? `${r.roi >= 0 ? "+" : ""}${r.roi.toFixed(1)}% · ` : "") + fmtVal(r.valueSol, 2, false, true));
    val.widthWeight = 25;
    val.rightAligned();
    val.titleFont = Font.semiboldSystemFont(16);
    val.titleColor = signColor(r.pnlSol);
    val.subtitleFont = Font.systemFont(11);
    val.subtitleColor = r.roi != null ? signColor(r.roi) : COL.muted;

    const fee = row.addText(fmtVal(r.unclaimedSol, 3, false, true),
      (r.feePctDay != null ? `${r.feePctDay.toFixed(1)}%/d` : "--") + (r.claimedSol >= 0.0005 ? ` · ${fmtVal(r.claimedSol, 3, false, true)}` : ""));
    fee.widthWeight = 25;
    fee.rightAligned();
    fee.titleFont = Font.semiboldSystemFont(16);
    fee.titleColor = r.unclaimedSol > 0 ? COL.green : COL.muted;
    fee.subtitleFont = Font.systemFont(11);
    fee.subtitleColor = feeRateColor(r.feePctDay);

    const mkt = row.addText(fmtCompact(r.mcap), r.poolFeePctDay1h != null ? `1h ${r.poolFeePctDay1h.toFixed(r.poolFeePctDay1h >= 100 ? 0 : 1)}%/d` : "--");
    mkt.widthWeight = 22;
    mkt.rightAligned();
    mkt.titleFont = Font.semiboldSystemFont(15);
    mkt.titleColor = COL.text;
    mkt.subtitleFont = Font.systemFont(11);
    mkt.subtitleColor = r.poolFeePctDay1h != null ? feeRateColor(r.poolFeePctDay1h) : COL.muted;
    row.onSelect = () => Safari.open(`https://dexscreener.com/solana/${r.mainPool}`);
    row.dismissOnSelect = false;
    t.addRow(row);

    // Range strip: the market cap at each end of the ladder, the price marker
    // between them, and how far across it the price sits.
    const mA = mcapAtBin(r, r.lo), mB = mcapAtBin(r, r.hi);
    const rp = rangePct(r);
    const strip = new UITableRow();
    strip.height = 22;
    const sLo = strip.addText(mA != null && mB != null ? fmtCompact(Math.min(mA, mB)) : "--");
    sLo.widthWeight = 16;
    sLo.titleFont = Font.systemFont(11);
    sLo.titleColor = COL.muted;
    const sBar = strip.addImage(rangeBar(r.inRangeCount > 0, r.lo, r.hi, r.active, 200, 12, r.solIsX));
    sBar.widthWeight = 48;
    sBar.centerAligned();
    const sHi = strip.addText(mA != null && mB != null ? fmtCompact(Math.max(mA, mB)) : "--");
    sHi.widthWeight = 16;
    sHi.titleFont = Font.systemFont(11);
    sHi.titleColor = COL.muted;
    sHi.rightAligned();
    const sPct = strip.addText(fmtRangePct(rp));
    sPct.widthWeight = 20;
    sPct.titleFont = Font.semiboldSystemFont(12);
    sPct.titleColor = rangeColor(r, rp);
    sPct.rightAligned();
    strip.onSelect = row.onSelect;
    strip.dismissOnSelect = false;
    t.addRow(strip);

    if (CAN_ACT) {
      const acts = new UITableRow();
      acts.height = 38;
      const pad = acts.addText("");
      pad.widthWeight = 10;
      const mk = (title, tap) => {
        const b = acts.addButton(busy ? "…" : title);
        b.widthWeight = 30;
        b.centerAligned();
        b.titleFont = Font.semiboldSystemFont(14);
        if (!busy) b.onTap = tap;
      };
      mk("Re-enter", () => h.onReentry(r));
      mk("Claim", () => h.onClaim([r], r.symbol));
      mk("Close", () => h.onClose([r], r.symbol));
      t.addRow(acts);
    }
    // Spec v1 exit rules: what fired, and the button to act on it.
    if ((r.exits && r.exits.length) || (r.exitAlerts && r.exitAlerts.length)) {
      const er = new UITableRow();
      er.height = 30;
      const ex = r.exits && r.exits.length;
      const ec = er.addText(ex ? `\u{1F534} Exit rule: ${r.exits[0].text}` : `⚠️ ${r.exitAlerts.join(" · ")}`);
      ec.widthWeight = ex && CAN_ACT ? 62 : 100;
      ec.titleFont = Font.semiboldSystemFont(11);
      ec.titleColor = ex ? COL.red : COL.yellow;
      if (ex && CAN_ACT) {
        const eb = er.addButton(busy ? "…" : (DRY_RUN ? "Practice exit" : "Exit → SOL"));
        eb.widthWeight = 38;
        eb.rightAligned();
        eb.titleFont = Font.boldSystemFont(13);
        if (!busy) eb.onTap = () => h.onExit(r, true);
      }
      t.addRow(er);
    }
    divider(6);
  }

  // Wallet: where the rest of net worth sits.
  {
    const wr = new UITableRow();
    wr.height = 56;
    const parts = [];
    if (d.walletSol != null) parts.push(`${fmtSol(d.walletSol, 3)} SOL`);
    if (d.wsolSol > 0) parts.push(`${fmtSol(d.wsolSol, 3)} wrapped`);
    const priced = (d.leftovers || []).filter(x => x.valueSol != null && x.valueSol >= DUST_SOL);
    if (priced.length) parts.push(`${priced.length} token${priced.length > 1 ? "s" : ""} ${fmtVal(d.leftoverSol, 3, false, true)}`);
    if (d.rentSol > 0) parts.push(`rent ${fmtVal(d.rentSol, 3, false, true)}`);
    const wc = wr.addText("Wallet", parts.length ? parts.join("   ") : "empty");
    wc.widthWeight = 60;
    wc.titleFont = Font.semiboldSystemFont(15);
    wc.subtitleFont = Font.systemFont(11);
    wc.subtitleColor = COL.muted;
    const walletTotal = (d.walletSol || 0) + (d.wsolSol || 0) + (d.leftoverSol || 0);
    const wv = wr.addText(fmtVal(walletTotal, 3), d.emptyAccounts ? `${d.emptyAccounts} empty accounts · ${fmtSol(d.emptyRentSol, 3)} rent` : "");
    wv.widthWeight = 40;
    wv.rightAligned();
    wv.titleFont = Font.semiboldSystemFont(15);
    wv.subtitleFont = Font.systemFont(11);
    wv.subtitleColor = COL.muted;
    wr.onSelect = () => Safari.open(`https://solscan.io/account/${WALLET}`);
    wr.dismissOnSelect = false;
    t.addRow(wr);
    divider(0);
  }

  // Realised: computed only when asked, read back from the stored snapshot.
  divider(10);
  const realised = h.realisedNote ? { note: h.realisedNote } : h.realised;
  const building = realised && realised.note != null;
  const rs = !building && realised && realised.byDay ? realised : null;
  const rHead = new UITableRow();
  rHead.height = 44;
  const age = rs ? (Date.now() - rs.builtAt) / 86400000 : null;
  const rt = rHead.addText(`REALISED${rs ? `  ·  ${REALISED_DAYS} DAYS` : ""}`,
    building ? String(realised.note) : rs ? (rs.unreadPools ? `\u26A0\uFE0F ${rs.unreadPools} pool${rs.unreadPools > 1 ? "s" : ""} unread, total is partial` : `${rs.totals.count} closed ladders · ${fmtAge(age)} old`) : "closed positions");
  rt.widthWeight = 50;
  rt.titleFont = Font.semiboldSystemFont(13);
  rt.titleColor = COL.muted;
  rt.subtitleFont = Font.systemFont(11);
  rt.subtitleColor = building || !rs ? COL.faint : rs.unreadPools || age > 1 / 24 ? COL.yellow : COL.faint;
  const rv = rHead.addText(building ? "…" : rs ? fmtVal(rs.totals.pnlSol, 3, true) : "Calculate",
    building ? "working" : rs ? "tap to refresh" : "");
  rv.widthWeight = 50;
  rv.rightAligned();
  rv.titleFont = Font.boldSystemFont(18);
  rv.titleColor = building ? COL.muted : !rs ? COL.blue : rs.totals.pnlSol >= 0 ? COL.green : COL.red;
  rv.subtitleFont = Font.systemFont(11);
  rv.subtitleColor = COL.blue;
  if (!busy) { rHead.onSelect = () => h.onRealised(); rHead.dismissOnSelect = false; }
  t.addRow(rHead);
  if (rs) {
    const today = startOfLocalDay(Date.now());
    for (let i = 0; i < REALISED_DAYS; i++) {
      const at = today - i * 86400000;
      const e = rs.byDay.find(x => x.at === at);
      const dr = new UITableRow();
      dr.height = 28;
      const dl = dr.addText(`${dayLabel(at, Date.now())}   ${e ? e.count : "0"}`);
      dl.widthWeight = 55;
      dl.titleFont = Font.semiboldSystemFont(11);
      dl.titleColor = COL.muted;
      const dv = dr.addText(e ? fmtVal(e.pnlSol, 3, true) : fmtVal(0, 3));
      dv.widthWeight = 45;
      dv.rightAligned();
      dv.titleFont = Font.semiboldSystemFont(13);
      dv.titleColor = !e ? COL.muted : e.pnlSol >= 0 ? COL.green : COL.red;
      t.addRow(dr);
      if (h.realisedOpen && e) {
        for (const l of rs.ladders.filter(x => startOfLocalDay(x.burnAt) === at)) {
          const lr = new UITableRow();
          lr.height = 38;
          const held = l.burnAt > l.mintAt ? (l.burnAt - l.mintAt) / 86400000 : null;
          const nm = lr.addText(l.token, `${l.sliceCount} slice${l.sliceCount === 1 ? "" : "s"}${held != null ? "  ·  " + fmtAge(held) : ""}`);
          nm.widthWeight = 40;
          nm.titleFont = Font.semiboldSystemFont(14);
          nm.subtitleFont = Font.systemFont(11);
          nm.subtitleColor = COL.muted;
          const roiL = l.capitalSol > 0 ? l.pnlSol / l.capitalSol * 100 : null;
          const pv = lr.addText(fmtVal(l.pnlSol, 3, true, true), (roiL != null ? `${roiL >= 0 ? "+" : ""}${roiL.toFixed(1)}% · ` : "") + `fees ${fmtVal(l.feesSol, 3, false, true)}`);
          pv.widthWeight = 60;
          pv.rightAligned();
          pv.titleFont = Font.semiboldSystemFont(15);
          pv.titleColor = l.pnlSol >= 0 ? COL.green : COL.red;
          pv.subtitleFont = Font.systemFont(11);
          pv.subtitleColor = COL.faint;
          lr.onSelect = () => Safari.open(`https://dexscreener.com/solana/${l.pool}`);
          lr.dismissOnSelect = false;
          t.addRow(lr);
        }
      }
    }
    if (rs.ladders.length) {
      const tg = new UITableRow();
      tg.height = 36;
      const tb = tg.addButton(h.realisedOpen ? "Hide" : `Show ${rs.ladders.length}`);
      tb.centerAligned();
      tb.onTap = () => h.onRealisedToggle();
      t.addRow(tg);
    }
  }

  const foot = new UITableRow();
  foot.height = 56;
  const fCell = foot.addText("Open LP Agent portfolio",
    `${d.posCount} open slices · ${d.chainChecked ? "checked on chain" : "index only"} · ${((d.timings && d.timings.total) || 0) / 1000 | 0}s · ${VERSION}`);
  fCell.titleFont = Font.semiboldSystemFont(15);
  fCell.titleColor = COL.blue;
  fCell.subtitleFont = Font.systemFont(11);
  fCell.subtitleColor = COL.faint;
  fCell.centerAligned();
  foot.onSelect = () => Safari.open(APP_URL);
  foot.dismissOnSelect = false;
  t.addRow(foot);
}
function fillEmptyTable(t, message, refreshing, onRefresh, progress) {
  t.removeAllRows();
  const header = new UITableRow();
  header.isHeader = true;
  header.height = 72;
  const c = header.addText(WALLET ? (NAME || shortAddr(WALLET)) : "MET Portfolio", "Meteora DLMM · Solana · in SOL");
  c.titleFont = Font.boldSystemFont(20);
  c.subtitleFont = Font.systemFont(12);
  c.subtitleColor = COL.muted;
  t.addRow(header);
  statusRow(t, refreshing ? "refreshing" : "stale", refreshing ? (message || "Refreshing…") : `${message}   ·   tap to retry`, onRefresh);
  drawProgress(t, progress);
}

// ----------------------------- actions ---------------------------------------
// Fresh chain state for a set of positions: their accounts, their pools, the
// pools' bitmap extensions and reward mints. Nothing is built from the table,
// which can be minutes old.
async function readForAction(positions) {
  const addrs = [...new Set(positions.map(p => p.address))];
  const pools = [...new Set(positions.map(p => p.pool))];
  const accs = await MET.getAccounts(SEND_RPC, [...addrs, ...pools, ...pools.map(MET.bitmapExtensionAddress)]);
  const poolMap = new Map();
  const rewardMints = new Set();
  for (const a of pools) {
    const acc = accs.get(a);
    if (!acc) throw new Error("pool " + a.slice(0, 6) + "… not found");
    const lb = MET.parseLbPair(acc.data);
    const ext = MET.bitmapExtensionAddress(a);
    poolMap.set(a, { address: a, lb, bitmapExtension: accs.get(ext) ? ext : null });
    for (const r of lb.rewards) rewardMints.add(r.mint);
  }
  const mintAccs = rewardMints.size ? await MET.getAccounts(SEND_RPC, [...rewardMints]) : new Map();
  for (const pool of poolMap.values()) {
    pool.rewards = pool.lb.rewards.map(r => ({ index: r.index, mint: r.mint, vault: r.vault, program: (mintAccs.get(r.mint) || {}).owner || MET.PROGRAM.TOKEN }));
  }
  const live = [], gone = [];
  for (const a of addrs) {
    const acc = accs.get(a);
    if (!acc) { gone.push(a); continue; }
    const pos = MET.parsePosition(acc.data);
    if (pos.owner !== ACTOR.address) throw new Error(`position ${a.slice(0, 6)}… is not owned by this wallet`);
    const pool = poolMap.get(pos.lbPair);
    if (!pool) throw new Error(`position ${a.slice(0, 6)}… belongs to a pool the table did not list; refresh first`);
    live.push({ address: a, pos, pool });
  }
  return { live, gone, poolMap };
}
function unitsFor(read, mode) {
  const units = [];
  for (const p of read.live) {
    const recv = MET.receiveIxs({ pool: p.pool, wallet: ACTOR.address, rewards: p.pool.rewards });
    // A position wider than 70 bins (opened elsewhere) is worked in 70-bin
    // chunks for claims as well as closes; a chunk with nothing to do is left
    // out rather than sent as an empty transaction.
    for (const chunk of MET.positionChunks(p.pos)) {
      const ixs = MET.positionIxs({ pool: p.pool, wallet: ACTOR.address, position: p.address, pos: p.pos, rewards: p.pool.rewards, mode, chunk });
      if (ixs.length) units.push({ poolKey: p.pool.address, before: recv.before, after: recv.after, position: p.address, ixs });
    }
  }
  return units;
}
// Simulate each transaction, then (live) send them all and wait.
const RESEND_ROUNDS = MET.RESEND_ROUNDS;
async function runGroups(groups, label, rep) {
  const micro = await MET.priorityFee(SEND_RPC, [MET.PROGRAM.DLMM, ...new Set(groups.flatMap(g => g.units.map(u => u.poolKey)))].slice(0, 20), { helius: IS_HELIUS });
  const describe = g => `${g.units.length} position${g.units.length > 1 ? "s" : ""}`;
  // A position wider than 70 bins is worked in chunks across several
  // transactions, and only its last chunk closes it. Those go out one at a
  // time, each prepared after the one before has landed: sent together, the
  // close could land first, find the position not yet empty, do nothing,
  // and leave an empty account holding its rent.
  const seen = new Map();
  for (const g of groups) for (const u of g.units) seen.set(u.position, (seen.get(u.position) || 0) + 1);
  const chunked = [...seen.values()].some(n => n > 1);
  if (chunked && !DRY_RUN) {
    let ok = 0, sig = null;
    for (let i = 0; i < groups.length; i++) {
      const si = rep.start(`${label} ${i + 1}/${groups.length}`);
      try {
        // A dropped transaction changed nothing: prepared again (fresh
        // blockhash, higher fee) and sent, twice more at most.
        let r = null;
        for (let round = 0; round <= RESEND_ROUNDS; round++) {
          if (round) rep.detail(si, `did not land; sending again (${round}/${RESEND_ROUNDS})`);
          const p = await MET.prepare(SEND_RPC, { payer: ACTOR.address, keypairs: [ACTOR],
            microLamports: round ? Math.max(micro, 50_000) * (round + 1) : micro, build: groups[i].build, headroom: 0.3 });
          if (!round) rep.detail(si, `${describe(groups[i])} · simulated`);
          [r] = await MET.sendAll(SEND_RPC, [p], (k, st) => rep.detail(si, st));
          if (!r.expired) break;
        }
        if (!r.ok) {
          rep.fail(si, r.expired ? `did not land after ${RESEND_ROUNDS + 1} tries; nothing happened` : r.unknown ? "no confirmation; check Solscan" : "failed on chain");
          break;
        }
        ok++; sig = sig || r.signature;
        rep.ok(si, `${r.signature.slice(0, 8)}\u2026`);
      } catch (e) { rep.fail(si, String(e.message || e).slice(0, 120)); break; }
    }
    return { ok: ok === groups.length, done: ok, total: groups.length, signature: sig };
  }
  const prepared = [];
  for (let i = 0; i < groups.length; i++) {
    const g = groups[i];
    const si = rep.start(`${label} ${i + 1}/${groups.length}`);
    try {
      const p = await MET.prepare(SEND_RPC, { payer: ACTOR.address, keypairs: [ACTOR], microLamports: micro, build: g.build, headroom: 0.3 });
      prepared.push({ ...p, step: si, group: g });
      rep.detail(si, `${describe(g)} · ${p.units.toLocaleString("en-US")} units · simulated`);
    } catch (e) {
      rep.fail(si, String(e.message || e).slice(0, 120));
    }
  }
  if (DRY_RUN) {
    for (const p of prepared) rep.ok(p.step, `${describe(p.group)} · simulates cleanly`);
    return { dryRun: true, ok: prepared.length === groups.length, done: prepared.length, total: groups.length };
  }
  if (!prepared.length) return { ok: false, done: 0, total: groups.length };
  // A transaction that expired changed nothing, so it is prepared again
  // against the chain as it is now (fresh blockhash, higher fee) and sent,
  // twice more at most. Failures and unconfirmed ones are left alone.
  const sent = await MET.sendWithResend(SEND_RPC, prepared, {
    rebuild: async (p0, round) => ({ ...(await MET.prepare(SEND_RPC, { payer: ACTOR.address, keypairs: [ACTOR],
      microLamports: Math.max(micro, 50_000) * (round + 1), build: p0.group.build, headroom: 0.3 })), step: p0.step, group: p0.group }),
    detail: (p, st) => rep.detail(p.step, st),
  });
  const results = sent.results;
  prepared.splice(0, prepared.length, ...sent.items);
  let ok = 0;
  results.forEach((r, k) => {
    if (r.ok) { ok++; rep.ok(prepared[k].step, `${r.signature.slice(0, 8)}\u2026`); }
    else rep.fail(prepared[k].step, r.blocked ? `did not land, and would now fail: ${r.blocked}`
      : r.expired ? `did not land after ${RESEND_ROUNDS + 1} tries; nothing happened` : r.unknown ? "no confirmation; check Solscan" : "failed on chain");
  });
  return { ok: ok === groups.length, done: ok, total: groups.length, signature: (results.find(r => r.ok) || {}).signature };
}
// Balances of the given mints in the wallet, raw units by mint. Strict: both
// token programs are read or the whole snapshot fails, because a snapshot
// missing an account would make tokens already in the wallet look returned.
async function tokenBalances(mints) {
  const want = new Set(mints);
  const all = await MET.walletTokenAccounts(SEND_RPC, ACTOR.address);
  const out = new Map();
  for (const t of all) if (want.has(t.mint)) out.set(t.mint, (out.get(t.mint) || 0n) + t.amount);
  return { map: out };
}
// SOL per raw unit of a pool's token at the pool's own price. Only a pool with
// SOL on one side can say that; any other pool gives no reference.
function poolRefPrice(pool) {
  const lb = pool.lb;
  const px = Math.pow(1 + lb.binStep / 10000, lb.activeId);   // raw Y per raw X
  if (lb.tokenYMint === WSOL) return px / 1e9;
  if (lb.tokenXMint === WSOL) return 1 / px / 1e9;
  return 0;
}
// SOL per raw unit from Jupiter's market price, for tokens that did not come
// out of a SOL pool.
async function marketRefPrices(mints, solUsd) {
  const out = new Map();
  if (!mints.length) return out;
  const prices = await fetchPrices(mints.concat([WSOL]));
  const sol = prices.get(WSOL) || solUsd;
  if (!(sol > 0)) return out;
  const decs = await MET.getAccounts(SEND_RPC, mints).catch(() => new Map());
  for (const m of mints) {
    const usd = prices.get(m), acc = decs.get(m);
    if (usd && acc && acc.data && acc.data.length >= 45) out.set(m, usd / sol / Math.pow(10, acc.data[44]));
  }
  return out;
}
// Sell what an action returned: the difference between two balance reads, so
// tokens that were already in the wallet are never touched.
async function sellReturned(before, after, poolByMint, rep, limits) {
  let sold = 0, failed = 0, got = 0;
  const deltas = [];
  for (const [mint, amt] of after.map) {
    const delta = amt - (before.map.get(mint) || 0n);
    if (delta > 0n && !KEEP_MINTS.has(mint)) deltas.push([mint, delta]);
  }
  const market = await marketRefPrices(deltas.filter(([m]) => !(poolByMint.get(m) && poolRefPrice(poolByMint.get(m)) > 0)).map(([m]) => m), limits.solUsd).catch(() => new Map());
  for (const [mint, delta] of deltas) {
    const pool = poolByMint.get(mint);
    const si = rep.start(`Sell ${shortAddr(mint)}`);
    const ref = pool && poolRefPrice(pool) > 0 ? poolRefPrice(pool) : (market.get(mint) || 0);
    if (!(ref > 0)) { failed++; rep.fail(si, "no reference price to judge a route by; left in the wallet"); continue; }
    if (Number(delta) * ref < DUST_SOL) { rep.skip(si, "dust, left in the wallet"); continue; }
    try {
      const res = await MET.sellToSol(SEND_RPC, { mint, amountRaw: delta, refSolPerRaw: ref,
        maxLossPct: limits.maxLoss, slippageBps: Math.round(limits.slippage * 100), kp: ACTOR,
        apiKey: JUP_KEY, practice: DRY_RUN, onStatus: s => rep.detail(si, s) });
      if (res.ok) { sold++; got += res.gotLamports || 0; rep.ok(si, `about ${fmtSol((res.gotLamports || 0) / 1e9, 4)}${res.dryRun ? " (simulated)" : ""}`); }
      else { failed++; rep.fail(si, res.expired ? "did not land; still in the wallet" : "not confirmed; check Solscan"); }
    } catch (e) {
      failed++;
      rep.fail(si, String(e.message || e).slice(0, 110));
    }
  }
  return { sold, failed, got };
}

// ----------------------------- entry -----------------------------------------
if (config.runsInWidget) {
  let w;
  try {
    const fallback = new Promise(resolve => Timer.schedule(WIDGET_BUDGET_MS, false, () => {
      const c = loadCache();
      resolve({ widget: c ? buildWidget({ ...c.data, rows: (c.data.rows || []).slice(0, c.data.rowBudget || 6) }, true, c.t)
        : errorWidget("Refresh timed out. Tap to open in the app.") });
    }));
    w = (await Promise.race([main(), fallback])).widget;
  } catch (e) {
    const c = loadCache();
    w = c && c.data ? buildWidget({ ...c.data, rows: (c.data.rows || []).slice(0, 6) }, true, c.t)
      : errorWidget(String((e && e.message) || e).slice(0, 80));
  }
  Script.setWidget(w);
} else {
  const t = new UITable();
  let shown = null, current = null, busy = false, progress = null;
  let realised = null, realisedNote = null, realisedOpen = false;
  try { realised = loadRealised(); } catch (e) {}

  const h = {};
  const show = (r, refreshing) => {
    Object.assign(h, { busy, progress, realised, realisedNote, realisedOpen });
    if (r && r.data) fillTable(t, r.data, r.stale, r.dataTime, refreshing, r.error, h);
    else fillEmptyTable(t, (r && r.message) || "Nothing loaded", refreshing, () => h.refresh(), progress);
    if (shown) t.reload(); else shown = t.present(false);
  };
  const redraw = () => show(current, false);

  // Every action runs through here: a progress log that stays on screen, a
  // refresh afterwards that keeps the log, and no second action meanwhile.
  async function run(title, fn) {
    if (busy) return;
    busy = true;
    progress = { title, steps: [], running: true, outcome: null };
    redraw();
    const rep = makeReporter(progress, redraw);
    try {
      progress.outcome = await fn(rep);
      // An outcome can offer a Retry (what is still open, without asking again).
      if (progress.outcome && typeof progress.outcome.retry === "function") progress.retry = progress.outcome.retry;
    } catch (e) {
      progress.outcome = { ok: false, text: /would fail/.test(String(e.message)) ? `Stopped before sending: ${String(e.message).slice(0, 120)}` : `Failed: ${String((e && e.message) || e).slice(0, 120)}` };
    }
    progress.running = false;
    busy = false;
    redraw();
    if (!DRY_RUN) await h.refresh(true);
  }

  h.refresh = async keepProgress => {
    if (busy) return;
    busy = true;
    if (!keepProgress) progress = null;
    show(current || { data: null, message: "Loading…" }, true);
    try { current = await main(); }
    catch (e) { current = { data: null, message: "Couldn't load: " + String((e && e.message) || e).slice(0, 100) }; }
    busy = false;
    show(current, false);
  };
  h.onRealisedToggle = () => { realisedOpen = !realisedOpen; redraw(); };
  // The "Show money in" row: saved for the widgets and MET Pool, redrawn
  // without a reload. Rupiah needs the dollar rate, read once if missing.
  h.onCurrency = async cur => {
    if (busy || cur === DISPLAY_CUR) return;
    DISPLAY_CUR = cur;
    saveDisplayChoice(cur);
    if (cur === "IDR" && current && current.data && !current.data.usdIdr) {
      busy = true;
      redraw();
      current.data.usdIdr = await usdIdrRate().catch(() => null);
      busy = false;
    }
    redraw();
  };
  h.onRealised = async () => {
    if (busy) return;
    busy = true;
    realisedNote = "Reading closes…";
    redraw();
    try { realised = await buildRealised(n => { realisedNote = n; redraw(); }); realisedNote = null; }
    catch (e) { realisedNote = "Could not finish: " + String((e && e.message) || e).slice(0, 90); }
    busy = false;
    redraw();
  };

  // Claiming takes only the fees; liquidity stays exactly where it is.
  h.onClaim = async (rows, label) => {
    if (busy) return;
    const positions = rows.flatMap(r => r.positions.filter(p => p.unindexed || p.partial || (p.unclaimedSol || 0) > 0.000001));
    const fees = rows.reduce((s, r) => s + (r.unclaimedSol || 0), 0);
    if (!positions.length) {
      progress = { title: `Claim ${label}`, steps: [], running: false, outcome: { ok: false, text: "No fees have accrued on these positions yet." } };
      return redraw();
    }
    const a = new Alert();
    a.title = `${DRY_RUN ? "Practice: claim" : "Claim"} ${label}?`;
    a.message = `About ${fmtSol(fees, 4)} of fees from ${positions.length} position${positions.length > 1 ? "s" : ""}.\n\n`
      + "Liquidity stays where it is; only the earned fees are collected. SOL fees arrive as SOL. "
      + `Swapping sells the token fees to SOL, refusing any route more than ${MAX_LOSS_PERCENT}% under the pool price.`
      + (DRY_RUN ? "\n\nPRACTICE MODE: simulated, nothing is sent." : "");
    a.addAction("Claim only");
    a.addAction("Claim and swap to SOL");
    a.addCancelAction("Cancel");
    const choice = await a.presentAlert();
    if (choice < 0 || choice > 1) return;
    await run(`Claim ${label}`, async rep => {
      const ri = rep.start("Read positions");
      const read = await readForAction(positions);
      rep.ok(ri, `${read.live.length} open${read.gone.length ? ` · ${read.gone.length} already closed` : ""}`);
      if (!read.live.length) return { ok: false, text: "Nothing to claim: those positions are closed." };
      const mints = [...new Set(read.live.map(p => (p.pool.lb.tokenXMint === WSOL ? p.pool.lb.tokenYMint : p.pool.lb.tokenXMint)))];
      const before = choice === 1 && !DRY_RUN ? await tokenBalances(mints) : null;
      const res = await runGroups(MET.packUnits(ACTOR.address, unitsFor(read, "claim"), 3), "Claim", rep);
      let swapText = "";
      if (choice === 1) {
        if (DRY_RUN) swapText = " The swap runs only after a real claim, on what it returned.";
        else if (res.done) {
          let after = null;
          try { after = await tokenBalances(mints); } catch (e) {}
          if (!after) swapText = " Could not read the wallet afterwards, so nothing was sold; the tokens are in the wallet (CLEANUP sells them).";
          else {
            const byMint = new Map(read.live.map(p => [(p.pool.lb.tokenXMint === WSOL ? p.pool.lb.tokenYMint : p.pool.lb.tokenXMint), p.pool]));
            const s = await sellReturned(before, after, byMint, rep, { maxLoss: MAX_LOSS_PERCENT, slippage: ROUTER_SLIPPAGE_PERCENT, solUsd: current && current.data && current.data.solUsd });
            swapText = s.sold || s.failed ? ` ${s.sold} sold for about ${fmtSol(s.got / 1e9, 4)}${s.failed ? `, ${s.failed} not sold (still in the wallet)` : ""}.` : "";
          }
        }
      }
      return res.dryRun
        ? { ok: res.ok, text: `Practice run finished: ${res.done}/${res.total} claim transaction${res.total > 1 ? "s" : ""} simulate cleanly.${swapText}` }
        : { ok: res.ok, text: res.ok ? `Claimed fees on ${label}.${swapText}` : `Claimed ${res.done} of ${res.total} transactions.${swapText}`,
            link: res.signature ? `https://solscan.io/tx/${res.signature}` : null };
    });
  };

  // Closing empties every position of the rows, claims their fees and closes
  // the accounts so their rent returns. It never swaps unless asked.
  h.onClose = async (rows, label) => {
    if (busy) return;
    const positions = rows.flatMap(r => r.positions);
    if (!positions.length) return;
    const value = rows.reduce((s, r) => s + (r.valueSol || 0), 0);
    const fees = rows.reduce((s, r) => s + (r.unclaimedSol || 0), 0);
    const rent = rows.reduce((s, r) => s + (r.rentSol || 0), 0);
    const holds = rows.some(r => r.holdsToken);
    const a = new Alert();
    a.title = `${DRY_RUN ? "Practice: close" : "Close"} ${label}?`;
    a.message = `${positions.length} position${positions.length > 1 ? "s" : ""} across ${rows.length} token${rows.length > 1 ? "s" : ""}: `
      + `about ${fmtSol(value, 3)} plus ${fmtSol(fees, 4)} of fees, and ${fmtSol(rent, 3)} of rent comes back.\n\n`
      + (holds ? "Some of it is now the token, not SOL. " : "It is all SOL, so there is nothing to swap. ")
      + `"Close and swap to SOL" sells what the close returns, refusing any route more than ${MAX_LOSS_PERCENT}% under the pool price. `
      + "Tokens that were already in the wallet are never sold here."
      + (DRY_RUN ? "\n\nPRACTICE MODE: simulated, nothing is sent." : "");
    a.addAction("Close only");
    if (DRY_RUN) a.addAction("Close and swap to SOL"); else a.addDestructiveAction("Close and swap to SOL");
    a.addCancelAction("Cancel");
    const choice = await a.presentAlert();
    if (choice < 0 || choice > 1) return;
    await closeRows(rows, label, choice);
  };
  // The close itself: also what Retry runs, for whatever is still open.
  const closeRows = async (rows, label, choice) => {
    const positions = rows.flatMap(r => r.positions);
    await run(`Close ${label}`, async rep => {
      const ri = rep.start("Read positions");
      const read = await readForAction(positions);
      rep.ok(ri, `${read.live.length} open${read.gone.length ? ` · ${read.gone.length} already closed` : ""}`);
      if (!read.live.length) return { ok: false, text: "Nothing to close: those positions no longer exist. Tap the status line to refresh." };
      const mints = [...new Set(read.live.map(p => (p.pool.lb.tokenXMint === WSOL ? p.pool.lb.tokenYMint : p.pool.lb.tokenXMint)))];
      const before = choice === 1 && !DRY_RUN ? await tokenBalances(mints) : null;
      const res = await runGroups(MET.packUnits(ACTOR.address, unitsFor(read, "close"), 2), "Close", rep);
      let swapText = "";
      if (choice === 1) {
        if (DRY_RUN) swapText = " The swap runs only after a real close, on what it returned.";
        else if (res.done) {
          let after = null;
          try { after = await tokenBalances(mints); } catch (e) {}
          if (!after) swapText = " Could not read the wallet afterwards, so nothing was sold; the tokens are in the wallet (CLEANUP sells them).";
          else {
            const byMint = new Map(read.live.map(p => [(p.pool.lb.tokenXMint === WSOL ? p.pool.lb.tokenYMint : p.pool.lb.tokenXMint), p.pool]));
            const s = await sellReturned(before, after, byMint, rep, { maxLoss: MAX_LOSS_PERCENT, slippage: ROUTER_SLIPPAGE_PERCENT, solUsd: current && current.data && current.data.solUsd });
            swapText = s.sold || s.failed ? ` ${s.sold} sold for about ${fmtSol(s.got / 1e9, 4)}${s.failed ? `, ${s.failed} not sold: use CLEANUP` : ""}.` : " Nothing came back as the token.";
          }
        }
      }
      return res.dryRun
        ? { ok: res.ok, text: `Practice run finished: ${res.done}/${res.total} close transaction${res.total > 1 ? "s" : ""} simulate cleanly.${swapText}` }
        : { ok: res.ok, text: res.ok ? `Closed ${label}.${swapText}` : `Closed ${res.done} of ${res.total} transactions; the rest are still open. Retry closes them.${swapText}`,
            link: res.signature ? `https://solscan.io/tx/${res.signature}` : null,
            retry: res.ok ? null : () => closeRows(rows, label, choice) };
    });
  };

  // Spec v1 exit: close the positions whose rule fired, claim, and swap what
  // comes back to SOL (the same close as the Close button, choice 1). Asked
  // for in alert mode or from a tap; straight through in auto mode.
  h.onExit = async (r, ask) => {
    if (busy || !CAN_ACT || !r.exits || !r.exits.length) return;
    const addrs = new Set(r.exits.map(e => e.address));
    const sub = { ...r, positions: r.positions.filter(p => addrs.has(p.address)) };
    const label = `${r.symbol} (exit: ${r.exits[0].rule})`;
    if (ask) {
      const a = new Alert();
      a.title = `${DRY_RUN ? "Practice: exit" : "Exit"} ${r.symbol}?`;
      a.message = `${r.exits.map(e => e.text).join("\n")}\n\nCloses ${sub.positions.length} position${sub.positions.length > 1 ? "s" : ""}, claims the fees and sells what comes back as the token, `
        + `refusing any route more than ${MAX_LOSS_PERCENT}% under the pool price.`
        + (DRY_RUN ? "\n\nPRACTICE MODE: simulated, nothing is sent." : "");
      if (DRY_RUN) a.addAction("Close and swap to SOL"); else a.addDestructiveAction("Close and swap to SOL");
      a.addCancelAction("Not now");
      if ((await a.presentAlert()) !== 0) return;
    }
    exitLog({ ts: new Date().toISOString(), action: ask ? "exit-confirmed" : "exit-auto", symbol: r.symbol, positions: [...addrs], rules: r.exits.map(e => e.rule), practice: DRY_RUN });
    await closeRows([sub], label, 1);
  };

  // Re-enter: a ladder the price has run above (every slice still all SOL) is
  // closed and opened again under the current price, with the same number of
  // slices and the same width in bins. The table only offers it; whether the
  // ladder really is all SOL is decided from the chain just before closing,
  // and the new ladder is funded by the SOL the close actually returned.
  // Fees and the old rent stay in the wallet.
  h.onReentry = async r => {
    if (busy) return;
    const ps = r.positions.filter(p => !p.unindexed && !p.partial);
    const pools = [...new Set(r.positions.map(p => p.pool))];
    const why = pools.length !== 1 ? "its slices sit in more than one pool; close them instead"
      : ps.length !== r.positions.length ? "some positions are not indexed yet; try again in a minute"
      : !(r.holdsToken ? r.valueSol > 0.02 : r.solInside > 0.02) ? "it holds almost no SOL"
      : r.lo == null ? "its range is not known yet"
      : null;
    if (why) {
      progress = { title: `Re-enter ${r.symbol}`, steps: [], running: false, outcome: { ok: false, text: `Nothing to re-enter: ${why}.` } };
      return redraw();
    }
    const bins = r.hi - r.lo + 1;
    const slices = Math.min(20, ps.length);
    const def = r.strategy && STRATEGIES.includes(r.strategy) ? r.strategy : "spot";
    const a = new Alert();
    a.title = `${DRY_RUN ? "Practice: re-enter" : "Re-enter"} ${r.symbol}?`;
    a.message = `Closes ${ps.length} slice${ps.length > 1 ? "s" : ""} and opens ${slices} again from the price down, `
      + `${bins} bins wide, with the SOL the close returns (about ${fmtSol(r.holdsToken ? r.valueSol : r.solInside, 3)}).\n\n`
      + (r.holdsToken
        ? `The price is inside the ladder, so part of it is the token: what the close returns of it is sold to SOL first (refusing any route more than ${MAX_LOSS_PERCENT}% under the pool price). `
        : "It first checks on chain whether the ladder is still all SOL; if the price has moved into it, the token that comes back is sold to SOL first. ")
      + "Fees and the old positions' rent stay in the wallet; the new positions lock rent again. "
      + "It is two steps: if the re-open fails, the SOL simply stays in the wallet.\n\n"
      + `Pick the shape${r.strategy ? ` (it was opened as ${STRATEGY_NAME[r.strategy]})` : ""}:`
      + (DRY_RUN ? "\n\nPRACTICE MODE: simulated, nothing is sent." : "");
    const order = [def, ...STRATEGIES.filter(s => s !== def)];
    for (const s of order) a.addAction((DRY_RUN ? "Practice \u00B7 " : "") + STRATEGY_NAME[s] + (s === def ? "  \u2605" : ""));
    a.addCancelAction("Cancel");
    const choice = await a.presentAlert();
    if (choice < 0 || choice >= order.length) return;
    const strategy = order[choice];
    await run(`Re-enter ${r.symbol}`, async rep => {
      const ri = rep.start("Read positions");
      const read = await readForAction(ps);
      if (!read.live.length) { rep.fail(ri, "none open"); return { ok: false, text: "Nothing to re-enter: those positions are closed." }; }
      const pool = read.live[0].pool;
      const solIsX = pool.lb.tokenXMint === WSOL;
      // All SOL means every bin with liquidity sits strictly on SOL's side of
      // the active bin: below it when SOL is Y, above it when SOL is X.
      const active = pool.lb.activeId;
      const touched = read.live.filter(p => p.pos.liquidRange && (solIsX ? p.pos.liquidRange[0] <= active : p.pos.liquidRange[1] >= active));
      const holdsNow = touched.length > 0 || !!r.holdsToken;
      rep.ok(ri, `${read.live.length} open, ${holdsNow ? "price inside the ladder: the token will be sold" : "all SOL"}, ${bins} bins`);
      const otherMint = solIsX ? pool.lb.tokenYMint : pool.lb.tokenXMint;
      const mintAcc = (await MET.getAccounts(SEND_RPC, [otherMint])).get(otherMint);
      const mint = MET.parseMint(mintAcc.data, mintAcc.owner);
      const rentBack = [...(await MET.getAccounts(SEND_RPC, read.live.map(p => p.address))).values()].reduce((sum, acc) => sum + (acc ? acc.lamports : 0), 0);
      const balBefore = (await SEND_RPC.call("getBalance", [ACTOR.address, { commitment: "confirmed" }])).value;
      const byMint = new Map(read.live.map(p => [(p.pool.lb.tokenXMint === WSOL ? p.pool.lb.tokenYMint : p.pool.lb.tokenXMint), p.pool]));
      const tokBefore = holdsNow && !DRY_RUN ? await tokenBalances([...byMint.keys()]) : null;
      const closeRes = await runGroups(MET.packUnits(ACTOR.address, unitsFor(read, "close"), 2), "Close", rep);
      if (!closeRes.dryRun && !closeRes.ok) {
        return { ok: false, text: `Closed ${closeRes.done} of ${closeRes.total}; not re-opened. What closed is SOL in the wallet.` };
      }
      // Inside the ladder: sell the token the close returned (never what was already in the wallet).
      let sold = null;
      if (holdsNow && !DRY_RUN) {
        let tokAfter = null;
        try { tokAfter = await tokenBalances([...byMint.keys()]); } catch (e) {}
        if (!tokAfter) return { ok: false, text: "Closed, but could not read the wallet to sell the token; not re-opened. CLEANUP sells it." };
        sold = await sellReturned(tokBefore, tokAfter, byMint, rep, { maxLoss: MAX_LOSS_PERCENT, slippage: ROUTER_SLIPPAGE_PERCENT, solUsd: current && current.data && current.data.solUsd });
        if (sold.failed) return { ok: false, text: `Closed, but ${sold.failed} token sale${sold.failed > 1 ? "s" : ""} did not go through; not re-opened. Use CLEANUP to sell it, then open again.` };
      }
      // Live: what came back, less the rent refund, and never more than the
      // table said the ladder held (the rest is fees, which stay).
      // Practice: nothing came back, so the re-open is simulated with what the
      // wallet can pay for now; only the amount differs from a real run.
      // The table's SOL value, plus 5% when the token was sold (price moved since the table loaded).
      const tableLamports = BigInt(Math.floor((holdsNow ? (r.valueSol || r.solInside) * 1.05 : r.solInside) * 1e9));
      let lamports, note = "";
      const rent = await MET.rentRates(SEND_RPC);
      if (!DRY_RUN) {
        const balAfter = (await SEND_RPC.call("getBalance", [ACTOR.address, { commitment: "confirmed" }])).value;
        const returned = BigInt(Math.max(0, balAfter - balBefore - rentBack));
        lamports = returned < tableLamports ? returned : tableLamports;
        lamports = lamports * 999n / 1000n;
        if (lamports < 10_000_000n) return { ok: false, text: `Closed, but only ${fmtSol(lamportsToSol(returned), 4)} came back; not re-opened. It is in the wallet.` };
      } else {
        const spare = BigInt(Math.max(0, balBefore - slices * rent.position - 3 * rent.binArray - rent.tokenAccount * 2 - 30_000_000));
        lamports = spare < tableLamports ? spare : tableLamports;
        if (lamports < 10_000_000n) {
          return { ok: closeRes.ok, text: `Practice run: the close simulates${closeRes.ok ? "" : " only in part"}. `
            + "The re-open cannot be simulated until the close has really returned the SOL; the wallet holds too little right now." };
        }
        if (lamports < tableLamports) note = ` (simulated with ${fmtSol(lamportsToSol(lamports), 3)}, what the wallet holds now; a real run uses what the close returns)`;
      }
      const open = await MET.openLadder(SEND_RPC, {
        poolAddress: pool.address, actor: ACTOR, seedKp: KP, other: { mint: otherMint, program: mint.program },
        lamports, slices, bins, strategy, practice: DRY_RUN, isHelius: IS_HELIUS, maxSlip: 3, rep,
      });
      if (open.dryRun) {
        return { ok: closeRes.ok && open.ok, text: `Practice run: the close simulates${closeRes.ok ? "" : " only in part"}; `
          + `${open.okCount}/${open.n} new slices simulate${note}.` };
      }
      if (open.landed.length) {
        saveLadder(ACTOR.address, { pool: pool.address, token: otherMint, symbol: r.symbol, strategy, slices, top: 0, bins,
          amount: lamportsToSol(lamports), binStep: open.binStep, solIsX: open.solIsX, positions: open.landed.map(l => l.position), openedAt: open.openedAt });
      }
      const u = (open.unknown || []).length;
      return { ok: open.ok, text: open.ok ? `Re-entered ${r.symbol}: ${open.n} slices under the price (${STRATEGY_NAME[strategy]}).`
        : u ? `Closed; re-opened ${open.landed.length} of ${open.n} slices, ${u} unconfirmed. Check before trying again.`
        : `Closed, and re-opened ${open.landed.length} of ${open.n} slices. The rest is SOL in the wallet.`,
        link: open.landed.length ? `https://solscan.io/tx/${open.landed[0].signature}` : null };
    });
  };

  // Cleanup: sell leftover tokens to SOL, and close empty token accounts for
  // the rent they hold. Both limits are asked for, not assumed. A token with
  // no market price to judge a route by is not sold.
  h.onCleanup = async () => {
    if (busy) return;
    const d = current && current.data;
    const sellable = ((d && d.leftovers) || []).filter(x => !x.keep && x.valueSol != null && x.valueSol >= DUST_SOL && !x.frozen);
    const empty = (d && d.emptyAccounts) || 0;
    const a = new Alert();
    a.title = `${DRY_RUN ? "Practice: cleanup" : "Cleanup"}`;
    a.message = `${sellable.length} token${sellable.length === 1 ? "" : "s"} worth about ${fmtSol(sellable.reduce((s, x) => s + x.valueSol, 0), 3)} to sell, `
      + `and ${empty} empty token account${empty === 1 ? "" : "s"} holding ${fmtSol((d && d.emptyRentSol) || 0, 3)} of rent.\n\n`
      + "Max loss is measured against the token's market price; slippage is only what the route may give up between quote and fill. "
      + `SOL, USDC and USDT are never sold; tokens under ${DUST_SOL} SOL, or with no market price, are left alone.`
      + (DRY_RUN ? "\n\nPRACTICE MODE: simulated, nothing is sent." : "");
    a.addTextField("Max loss %", String(MAX_LOSS_PERCENT));
    a.addTextField("Slippage %", String(ROUTER_SLIPPAGE_PERCENT));
    a.addAction("Sell and close empty accounts");
    a.addAction("Only close empty accounts");
    a.addCancelAction("Cancel");
    const choice = await a.presentAlert();
    if (choice < 0 || choice > 1) return;
    // Typed input is checked, not trusted: a blank field is the default, a
    // decimal comma is a decimal point, anything else stops the sale.
    const readPct = (raw, dflt, hi) => {
      const t0 = String(raw == null ? "" : raw).trim().replace(/%$/, "").replace(/\s/g, "");
      if (!t0) return dflt;
      const t = /^\d*,\d+$/.test(t0) ? t0.replace(",", ".") : t0;
      if (!/^\d*\.?\d+$/.test(t)) return null;
      const v = Number(t);
      return v > 0 && v <= hi ? v : null;
    };
    const maxLoss = readPct(a.textFieldValue(0), MAX_LOSS_PERCENT, 50);
    const slippage = readPct(a.textFieldValue(1), ROUTER_SLIPPAGE_PERCENT, 10);
    if (maxLoss == null || slippage == null) {
      progress = { title: "Cleanup", steps: [], running: false, outcome: { ok: false, text: "Nothing was sold: max loss must be 0-50% and slippage 0-10%, as plain numbers." } };
      return redraw();
    }
    if (slippage >= maxLoss) {
      progress = { title: "Cleanup", steps: [], running: false, outcome: { ok: false, text: "Nothing was sold: slippage has to be smaller than max loss, which it counts against." } };
      return redraw();
    }
    await run("Cleanup", async rep => {
      let sold = 0, failed = 0, got = 0, skipped = 0;
      if (choice === 0 && sellable.length) {
        const refs = await marketRefPrices(sellable.map(x => x.mint), d && d.solUsd);
        const fresh = await MET.walletTokenAccounts(SEND_RPC, ACTOR.address);
        for (const x of sellable) {
          const tk = fresh.find(f => f.address === x.account);
          if (!tk || tk.amount <= 0n) continue;
          const si = rep.start(`Sell ${shortAddr(x.mint)}`);
          const ref = refs.get(x.mint) || 0;
          if (!(ref > 0)) { skipped++; rep.skip(si, "no market price to judge a route by; left in the wallet"); continue; }
          try {
            const res = await MET.sellToSol(SEND_RPC, { mint: x.mint, amountRaw: tk.amount, refSolPerRaw: ref, maxLossPct: maxLoss,
              slippageBps: Math.round(slippage * 100), kp: ACTOR, apiKey: JUP_KEY, practice: DRY_RUN, onStatus: s => rep.detail(si, s) });
            if (res.ok) { sold++; got += res.gotLamports || 0; rep.ok(si, `about ${fmtSol((res.gotLamports || 0) / 1e9, 4)}${res.dryRun ? " (simulated)" : ""}`); }
            else { failed++; rep.fail(si, res.expired ? "did not land; still in the wallet" : "not confirmed; check Solscan"); }
          } catch (e) { failed++; rep.fail(si, String(e.message || e).slice(0, 110)); }
        }
      }
      // Empty accounts, a dozen to a transaction, each with its own program.
      // A batch that will not go through is retried one account at a time, so
      // one account that cannot close (a Token-2022 account holding withheld
      // fees, say) does not keep the rest open.
      const fresh = await MET.walletTokenAccounts(SEND_RPC, ACTOR.address);
      const programOf = new Map(fresh.map(tk => [tk.address, tk.program]));
      // A frozen account cannot be closed; WSOL is left to the unwrap steps.
      const toClose = fresh.filter(tk => tk.amount === 0n && tk.mint !== WSOL && tk.state !== 2);
      let closed = 0, closeFailed = 0;
      const closeBatch = async (batch, label) => {
        const si = rep.start(label);
        try {
          const micro = await MET.priorityFee(SEND_RPC, batch.map(b => b.address), { helius: IS_HELIUS });
          // A Token-2022 account holding a withheld transfer fee is harvested
          // to its mint first (anyone may), or the close would be refused.
          const p = await MET.prepare(SEND_RPC, { payer: ACTOR.address, keypairs: [ACTOR], microLamports: micro,
            build: (u, m) => [MET.computeLimit(u), MET.computePrice(m), ...batch.flatMap(b => [
              ...(b.withheld > 0n && programOf.get(b.address) === MET.PROGRAM.TOKEN_2022 ? [MET.harvestWithheldToMint(b.mint, [b.address])] : []),
              MET.closeTokenAccount(b.address, ACTOR.address, ACTOR.address, programOf.get(b.address) || MET.PROGRAM.TOKEN)])] });
          if (DRY_RUN) { rep.ok(si, "simulates cleanly"); return true; }
          const [res] = await MET.sendAll(SEND_RPC, [p], (k, s) => rep.detail(si, s));
          if (res.ok) { rep.ok(si, `${res.signature.slice(0, 8)}\u2026`); return true; }
          rep.fail(si, res.unknown ? "no confirmation; check Solscan" : "did not land");
          return false;
        } catch (e) { rep.fail(si, String(e.message || e).slice(0, 100)); return false; }
      };
      // Accounts that need a harvest carry their mint too, so they go five to
      // a transaction instead of twelve.
      const batches = [];
      const plain = toClose.filter(tk => !(tk.withheld > 0n)), withFee = toClose.filter(tk => tk.withheld > 0n);
      for (let i = 0; i < plain.length; i += 12) batches.push(plain.slice(i, i + 12));
      for (let i = 0; i < withFee.length; i += 5) batches.push(withFee.slice(i, i + 5));
      for (const batch of batches) {
        if (await closeBatch(batch, `Close ${batch.length} empty account${batch.length > 1 ? "s" : ""}`)) { closed += batch.length; continue; }
        if (batch.length === 1) { closeFailed++; continue; }
        for (const one of batch) {
          if (await closeBatch([one], `Close ${shortAddr(one.address)} on its own`)) closed++;
          else closeFailed++;
        }
      }
      if (!sold && !failed && !closed && !closeFailed && !skipped) return { ok: true, text: "Nothing to do: no leftovers worth selling and no empty accounts." };
      const parts = [];
      if (sold || failed) parts.push(`${sold} sold${got ? ` for about ${fmtSol(got / 1e9, 4)}` : ""}${failed ? `, ${failed} not sold` : ""}`);
      if (skipped) parts.push(`${skipped} left (no market price)`);
      if (closed || closeFailed) parts.push(`${closed} empty account${closed === 1 ? "" : "s"} closed${closeFailed ? `, ${closeFailed} could not be` : ""}`);
      return { ok: failed === 0 && closeFailed === 0, text: `${DRY_RUN ? "Practice (simulated): " : ""}${parts.join("; ")}.` };
    });
  };

  const cached = loadCache();
  if (cached) current = { data: cached.data, stale: false, dataTime: cached.t };
  if (!WALLET) {
    fillEmptyTable(t, "No wallet: add SOLANA_PRIVATE_KEY to MET Secrets", false, null, null);
    await t.present(true);
  } else {
    await h.refresh();
    // Opened from an exit notification, or auto mode: act on what fired.
    const want = (typeof args !== "undefined" && args.queryParameters && args.queryParameters.exit) || null;
    const rowsNow = (current && current.data && !current.stale && current.data.rows) || [];
    const firing = rowsNow.filter(r => r.exits && r.exits.length && (!want || r.tokenMint === want));
    if (CAN_ACT && firing.length) {
      if (EXIT.mode === "auto") { for (const r of firing) await h.onExit(r, false); }
      else if (want) await h.onExit(firing[0], true);
    }
    await shown;
  }
}
Script.complete();
