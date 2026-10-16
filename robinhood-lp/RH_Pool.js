// Variables used by Scriptable.
// These must be at the very top of the file. Do not edit.
// icon-color: yellow; icon-glyph: bolt;
// ============================================================================
//  RH Pool : Uniswap V4 pool discovery and zap : Robinhood Chain (4663)
//
//  *** WITH A PRIVATE KEY SET, THIS SCRIPT SPENDS MONEY. DO NOT SHARE IT. ***
//
//  The same pool discovery table as "RH USDG Pools", plus a per-row zap that
//  opens a position with a preset you configure once. Leave PRIVATE_KEY blank
//  and it is exactly the read-only finder; fill it in and each row gains a
//  lightning button.
//
//  Ranks USDG-paired V4 pools by fee yield so the good LP targets stand out.
//  The 🔥 button switches to the fastest pools by the last hour's fee pace,
//  small ones included, with floors you set in the app; pin a token from its
//  search result to keep it in that view.
//
//  Fee yield = 24h volume x the pool's LP fee, divided by TVL, per day. The LP
//  fee is read from chain state (StateView.getSlot0), so dynamic-fee pools show
//  the fee they actually charge right now, not what their name says.
//
//  Data sources (keyless):
//   - GeckoTerminal: which USDG pools exist, ranked by 24h volume. Rate limited
//     hard (~1 call / 5s, max 10 pages), so this is cached on disk and refreshed
//     a few pages per run. The pool set changes slowly; the numbers do not come
//     from here.
//   - DexScreener: live TVL, volume, txns, price change for those pools.
//   - Robinhood Chain RPC via PublicNode: LP fee and tick (StateView), token
//     supply for market cap (Multicall3).
//
//  Market cap is total supply x an address-level reference price: the median of
//  that token's pools which clear a depth floor, never one pool's own price and
//  never DexScreener's marketCap as is, since that describes whichever token it
//  labels "base" and for several of these pools that is USDG itself.
//
//  SETUP:
//   1. In Scriptable: + > paste this file > name it "RH Pool".
//   2. Home Screen > add a Scriptable widget (medium or large). Parameter is
//      optional: a market-cap floor such as 500K or 2M (default 1M).
//   3. Tap the widget for the full interactive table (tap a row for DexScreener).
//   4. To enable zapping, fill in the ZAP SETUP block below.
//
//  Zapping mints single-sided liquidity: you deposit only USDG (or ETH) and the
//  range sits entirely on that side of the current price, so nothing is bought
//  at open and there is no slippage. The position starts earning if price comes
//  into your range.
// ============================================================================

// Ultra-early crash net: set a minimal widget BEFORE any code that could crash.
// The real Script.setWidget(w) later overwrites this. If anything between here
// and there throws, this fallback shows instead of a blank widget.
if (config.runsInWidget) {
  try {
    const _f = new ListWidget();
    const _g = new LinearGradient();
    _g.colors = [new Color("#1B1B2F"), new Color("#0D0D1A")];
    _g.locations = [0, 1];
    _f.backgroundGradient = _g;
    _f.setPadding(16, 17, 16, 17);
    const _h = _f.addText("\u{1F984} USDG Pools");
    _h.font = Font.boldSystemFont(14);
    _h.textColor = new Color("#F5F5FF");
    _f.addSpacer(6);
    const _m = _f.addText("Script crashed before widget built. Tap to open in app.");
    _m.font = Font.systemFont(11);
    _m.textColor = new Color("#FF453A");
    _f.url = "scriptable:///run/" + encodeURIComponent(Script.name());
    Script.setWidget(_f);
  } catch (e) {}
}

// ----------------------------- CONFIG ----------------------------------------
// Bump on every deploy. iCloud can take minutes to push a 175KB file to a
// phone, so without a visible marker there is no way to tell a change that did
// not work from one that has not arrived yet. It prints in the table footer.
// A separate "RH Secrets" script holds every credential and personal setting,
// so updating this file never overwrites them: paste a new build over the top
// and the keys and presets survive. A .js is used rather than a .env because
// Scriptable's file list hides dotfiles, leaving no way to create or edit one
// on the phone. Both spellings are accepted since earlier builds looked for
// "RHSecrets". Declared before the config block below so those constants can
// read from it without tripping over the temporal dead zone.
const SECRETS = (() => {
  if (typeof importModule !== "function") return {};
  for (const name of ["RH Secrets", "RHSecrets"]) {
    try {
      const m = importModule(name);
      if (m && typeof m === "object") return m;
    } catch (e) {}
  }
  return {};
})();

const VERSION = "zap-v36 (🔥 fee kenceng filter, pins)";

// =============================================================================
//  ZAP SETUP - the only part you need to edit.
//  Leave PRIVATE_KEY blank to keep this a read-only pool finder.
// =============================================================================

// -- 1. Your keys. -----------------------------------------------------------
// Leave both blank to read them from a ".env" file in your Scriptable folder
// instead, which keeps them out of this script entirely. See loadDotEnv below.
// With neither source set, this stays a plain read-only pool finder.
//
// Alchemy key: required for zapping. Get one free at alchemy.com (Robinhood chain).
const ALCHEMY_API_KEY = "";
// Private key of the wallet that will hold the positions, 64 hex characters.
// WARNING: anyone who reads this file can take everything in that wallet.
const PRIVATE_KEY = "";

// -- 2. The three zap buttons. -----------------------------------------------
// Every pool in this table is a USDG pair, so USDG is always what goes in.
// Each row in the table gets these three buttons, left to right. Change the
// numbers to whatever you actually use; the label is built from the amount.
//
//   amount  how much USDG to deposit, in whole tokens
//   slices  how many positions to split it into (1 = a plain single range)
//   range   how far the range reaches from the current price, in percent
//
// Slices are always weighted as a slope, thin nearest the price and thickest at
// the far edge, the way Zenith builds them. One slice has no slope to speak of.
//
// Each slice is its own position, so cost scales with the count. Measured on
// chain for a 700 USDG open at 0.46 gwei with ETH near $2,400. Closing later is
// extrapolated from real closes of 1 to 5 positions (~78k fixed + ~65k each):
//
//     slices    open     close    round trip
//       1      $0.30     $0.16      $0.46
//       3      $0.63     $0.30      $0.93
//       5      $0.96     $0.45      $1.40
//      10      $1.77     $0.81      $2.57
//      15      $2.58     $1.17      $3.74
//
// So roughly $0.15 per slice to open and $0.07 to close, on top of about $0.30
// and $0.16 of fixed cost. Gas here is cheap enough that the slice count is a
// shape decision, not a cost one, until you go well past 10.
//
// More slices does not put more money to work: with "slope", slice 1 of 5 holds
// 6.7% of the deposit and slice 1 of 10 holds 1.8%. The total is the same; you
// are only choosing how sharply the weight leans toward the far edge.
//
// A range is snapped to the pool's tick grid, so the real figure lands a little
// either side of what you ask for. On a 5% pool, 60 becomes about 59.3.
const DOTENV = loadDotEnv();
const secret = (inline, name) => {
  const v = String(inline || "").trim();
  if (v) return v;
  // The secrets script wins over .env so a friend who has both is not confused
  // by an invisible file quietly taking precedence over the one they can see.
  const s = SECRETS[name];
  if (s != null && String(s).trim()) return String(s).trim();
  return String(DOTENV[name] || "").trim();
};

const ZAP_PRESETS_DEFAULT = [
  { amount: 400, slices: 5, top: 0, range: 60 },
  { amount: 800, slices: 5, top: 0, range: 60 },
];

// Presets live in the secrets script so a rebuild never resets someone's sizing
// back to the defaults. Each entry is checked rather than trusted: a typo that
// reached planZap would place a ladder with the wrong width or no slices at all,
// and that costs gas to discover.
const ZAP_PRESETS = (() => {
  let raw = SECRETS.ZAP_PRESETS;
  // .env holds strings, so presets live there as a JSON array on one line.
  if (!raw && DOTENV.ZAP_PRESETS) {
    try { raw = JSON.parse(DOTENV.ZAP_PRESETS); } catch (e) { raw = null; }
  }
  if (!Array.isArray(raw) || !raw.length) return ZAP_PRESETS_DEFAULT;
  const ok = raw.filter(p => p && Number(p.amount) > 0
                          && Number(p.slices) >= 1 && Number(p.slices) <= 100
                          && Number(p.range) > 0 && Number(p.range) < 100
                          && (p.top == null
                              || (Number(p.top) >= 0 && Number(p.top) < Number(p.range))))
                .map(p => ({ amount: Number(p.amount),
                             slices: Math.floor(Number(p.slices)),
                             top: Number(p.top) || 0,
                             range: Number(p.range) }));
  return ok.length ? ok.slice(0, 3) : ZAP_PRESETS_DEFAULT;
})();

// -- 4. Practice mode. -------------------------------------------------------
// true  = work out the position and report it, send nothing.
// false = really open positions.
const PRACTICE_MODE = typeof SECRETS.PRACTICE_MODE === "boolean" ? SECRETS.PRACTICE_MODE
  : /^(true|1|yes)$/i.test(String(DOTENV.PRACTICE_MODE || "").trim()) ? true
  : false;

// -- Blockscout API key. Optional, but history needs it. -----------------------
// The free tier throttles hard: position history, the cleanup token list and
// position ids all read from Blockscout, and without a key those requests time
// out or return 403 under any real use. Measured: no key -> timeout; key -> 200
// in ~3s with a visible budget of ~180 requests. Free from blockscout.com.
// Leave blank to read BLOCKSCOUT_API_KEY from .env instead.
const BLOCKSCOUT_API_KEY = "";

// =============================================================================
//  Nothing below here needs editing.
// =============================================================================
const CHAIN_ID = 4663;
const POSITION_MANAGER = "0x58daec3116aae6d93017baaea7749052e8a04fa7";
const PERMIT2 = "0x000000000022D473030F116dDEE9F6B43aC78BA3";
const GAS_LIMIT_BUFFER = 1.30;
const MAX_FEE_MULT = 2.0;

// Retries for a throttled RPC. 5 attempts backing off 400ms, 800ms, 1.6s, 3.2s
// covers roughly 6 seconds of refusal, which is far longer than a burst limit
// needs to drain.
const RPC_RETRIES = 5;
const RPC_BACKOFF_MS = 400;
const DEADLINE_MIN = 20;
const DRY_RUN = PRACTICE_MODE;

// =============================================================================
//  VENDORED CRYPTO - generated by build-closer.mjs, do not hand-edit.
//    js-sha256          HMAC-SHA256 for RFC6979   sha256:2db6c8e554fbee14
//    noble-secp256k1 1.7.1  ECDSA signing         sha256:a6a95cbca6b6d980
//  keccak256 is NOT vendored: this script already carries one, verified against
//  NIST vectors and against js-sha3 over 200 random inputs.
//  Verified in tests/: NIST vectors, 200-key OpenSSL cross-check, RFC6979
//  determinism, low-s (EIP-2), and signSync == sign over 100 random cases.
// =============================================================================
const CRYPTO = (function () {
  function load(factory) {
    const module = { exports: {} };
    // noble calls require("crypto") at load time for its async path only; the
    // sync path below never touches it, so an empty stub is safe here.
    const require = function () { return {}; };
    factory(module, module.exports, require);
    return module.exports;
  }
  const _sha256 = load(function (module, exports, require) {
/**
 * [js-sha256]{@link https://github.com/emn178/js-sha256}
 *
 * @version 0.11.1
 * @author Chen, Yi-Cyuan [emn178@gmail.com]
 * @copyright Chen, Yi-Cyuan 2014-2025
 * @license MIT
 */
/*jslint bitwise: true */
(function () {
  'use strict';

  var ERROR = 'input is invalid type';
  var WINDOW = typeof window === 'object';
  var root = WINDOW ? window : {};
  if (root.JS_SHA256_NO_WINDOW) {
    WINDOW = false;
  }
  var WEB_WORKER = !WINDOW && typeof self === 'object';
  var NODE_JS = !root.JS_SHA256_NO_NODE_JS && typeof process === 'object' && process.versions && process.versions.node && process.type != 'renderer';
  if (NODE_JS) {
    root = global;
  } else if (WEB_WORKER) {
    root = self;
  }
  var COMMON_JS = !root.JS_SHA256_NO_COMMON_JS && typeof module === 'object' && module.exports;
  var AMD = typeof define === 'function' && define.amd;
  var ARRAY_BUFFER = !root.JS_SHA256_NO_ARRAY_BUFFER && typeof ArrayBuffer !== 'undefined';
  var HEX_CHARS = '0123456789abcdef'.split('');
  var EXTRA = [-2147483648, 8388608, 32768, 128];
  var SHIFT = [24, 16, 8, 0];
  var K = [
    0x428a2f98, 0x71374491, 0xb5c0fbcf, 0xe9b5dba5, 0x3956c25b, 0x59f111f1, 0x923f82a4, 0xab1c5ed5,
    0xd807aa98, 0x12835b01, 0x243185be, 0x550c7dc3, 0x72be5d74, 0x80deb1fe, 0x9bdc06a7, 0xc19bf174,
    0xe49b69c1, 0xefbe4786, 0x0fc19dc6, 0x240ca1cc, 0x2de92c6f, 0x4a7484aa, 0x5cb0a9dc, 0x76f988da,
    0x983e5152, 0xa831c66d, 0xb00327c8, 0xbf597fc7, 0xc6e00bf3, 0xd5a79147, 0x06ca6351, 0x14292967,
    0x27b70a85, 0x2e1b2138, 0x4d2c6dfc, 0x53380d13, 0x650a7354, 0x766a0abb, 0x81c2c92e, 0x92722c85,
    0xa2bfe8a1, 0xa81a664b, 0xc24b8b70, 0xc76c51a3, 0xd192e819, 0xd6990624, 0xf40e3585, 0x106aa070,
    0x19a4c116, 0x1e376c08, 0x2748774c, 0x34b0bcb5, 0x391c0cb3, 0x4ed8aa4a, 0x5b9cca4f, 0x682e6ff3,
    0x748f82ee, 0x78a5636f, 0x84c87814, 0x8cc70208, 0x90befffa, 0xa4506ceb, 0xbef9a3f7, 0xc67178f2
  ];
  var OUTPUT_TYPES = ['hex', 'array', 'digest', 'arrayBuffer'];

  var blocks = [];

  if (root.JS_SHA256_NO_NODE_JS || !Array.isArray) {
    Array.isArray = function (obj) {
      return Object.prototype.toString.call(obj) === '[object Array]';
    };
  }

  if (ARRAY_BUFFER && (root.JS_SHA256_NO_ARRAY_BUFFER_IS_VIEW || !ArrayBuffer.isView)) {
    ArrayBuffer.isView = function (obj) {
      return typeof obj === 'object' && obj.buffer && obj.buffer.constructor === ArrayBuffer;
    };
  }

  var createOutputMethod = function (outputType, is224) {
    return function (message) {
      return new Sha256(is224, true).update(message)[outputType]();
    };
  };

  var createMethod = function (is224) {
    var method = createOutputMethod('hex', is224);
    if (NODE_JS) {
      method = nodeWrap(method, is224);
    }
    method.create = function () {
      return new Sha256(is224);
    };
    method.update = function (message) {
      return method.create().update(message);
    };
    for (var i = 0; i < OUTPUT_TYPES.length; ++i) {
      var type = OUTPUT_TYPES[i];
      method[type] = createOutputMethod(type, is224);
    }
    return method;
  };

  var nodeWrap = function (method, is224) {
    var crypto = require('crypto')
    var Buffer = require('buffer').Buffer;
    var algorithm = is224 ? 'sha224' : 'sha256';
    var bufferFrom;
    if (Buffer.from && !root.JS_SHA256_NO_BUFFER_FROM) {
      bufferFrom = Buffer.from;
    } else {
      bufferFrom = function (message) {
        return new Buffer(message);
      };
    }
    var nodeMethod = function (message) {
      if (typeof message === 'string') {
        return crypto.createHash(algorithm).update(message, 'utf8').digest('hex');
      } else {
        if (message === null || message === undefined) {
          throw new Error(ERROR);
        } else if (message.constructor === ArrayBuffer) {
          message = new Uint8Array(message);
        }
      }
      if (Array.isArray(message) || ArrayBuffer.isView(message) ||
        message.constructor === Buffer) {
        return crypto.createHash(algorithm).update(bufferFrom(message)).digest('hex');
      } else {
        return method(message);
      }
    };
    return nodeMethod;
  };

  var createHmacOutputMethod = function (outputType, is224) {
    return function (key, message) {
      return new HmacSha256(key, is224, true).update(message)[outputType]();
    };
  };

  var createHmacMethod = function (is224) {
    var method = createHmacOutputMethod('hex', is224);
    method.create = function (key) {
      return new HmacSha256(key, is224);
    };
    method.update = function (key, message) {
      return method.create(key).update(message);
    };
    for (var i = 0; i < OUTPUT_TYPES.length; ++i) {
      var type = OUTPUT_TYPES[i];
      method[type] = createHmacOutputMethod(type, is224);
    }
    return method;
  };

  function Sha256(is224, sharedMemory) {
    if (sharedMemory) {
      blocks[0] = blocks[16] = blocks[1] = blocks[2] = blocks[3] =
        blocks[4] = blocks[5] = blocks[6] = blocks[7] =
        blocks[8] = blocks[9] = blocks[10] = blocks[11] =
        blocks[12] = blocks[13] = blocks[14] = blocks[15] = 0;
      this.blocks = blocks;
    } else {
      this.blocks = [0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0];
    }

    if (is224) {
      this.h0 = 0xc1059ed8;
      this.h1 = 0x367cd507;
      this.h2 = 0x3070dd17;
      this.h3 = 0xf70e5939;
      this.h4 = 0xffc00b31;
      this.h5 = 0x68581511;
      this.h6 = 0x64f98fa7;
      this.h7 = 0xbefa4fa4;
    } else { // 256
      this.h0 = 0x6a09e667;
      this.h1 = 0xbb67ae85;
      this.h2 = 0x3c6ef372;
      this.h3 = 0xa54ff53a;
      this.h4 = 0x510e527f;
      this.h5 = 0x9b05688c;
      this.h6 = 0x1f83d9ab;
      this.h7 = 0x5be0cd19;
    }

    this.block = this.start = this.bytes = this.hBytes = 0;
    this.finalized = this.hashed = false;
    this.first = true;
    this.is224 = is224;
  }

  Sha256.prototype.update = function (message) {
    if (this.finalized) {
      return;
    }
    var notString, type = typeof message;
    if (type !== 'string') {
      if (type === 'object') {
        if (message === null) {
          throw new Error(ERROR);
        } else if (ARRAY_BUFFER && message.constructor === ArrayBuffer) {
          message = new Uint8Array(message);
        } else if (!Array.isArray(message)) {
          if (!ARRAY_BUFFER || !ArrayBuffer.isView(message)) {
            throw new Error(ERROR);
          }
        }
      } else {
        throw new Error(ERROR);
      }
      notString = true;
    }
    var code, index = 0, i, length = message.length, blocks = this.blocks;
    while (index < length) {
      if (this.hashed) {
        this.hashed = false;
        blocks[0] = this.block;
        this.block = blocks[16] = blocks[1] = blocks[2] = blocks[3] =
          blocks[4] = blocks[5] = blocks[6] = blocks[7] =
          blocks[8] = blocks[9] = blocks[10] = blocks[11] =
          blocks[12] = blocks[13] = blocks[14] = blocks[15] = 0;
      }

      if (notString) {
        for (i = this.start; index < length && i < 64; ++index) {
          blocks[i >>> 2] |= message[index] << SHIFT[i++ & 3];
        }
      } else {
        for (i = this.start; index < length && i < 64; ++index) {
          code = message.charCodeAt(index);
          if (code < 0x80) {
            blocks[i >>> 2] |= code << SHIFT[i++ & 3];
          } else if (code < 0x800) {
            blocks[i >>> 2] |= (0xc0 | (code >>> 6)) << SHIFT[i++ & 3];
            blocks[i >>> 2] |= (0x80 | (code & 0x3f)) << SHIFT[i++ & 3];
          } else if (code < 0xd800 || code >= 0xe000) {
            blocks[i >>> 2] |= (0xe0 | (code >>> 12)) << SHIFT[i++ & 3];
            blocks[i >>> 2] |= (0x80 | ((code >>> 6) & 0x3f)) << SHIFT[i++ & 3];
            blocks[i >>> 2] |= (0x80 | (code & 0x3f)) << SHIFT[i++ & 3];
          } else {
            code = 0x10000 + (((code & 0x3ff) << 10) | (message.charCodeAt(++index) & 0x3ff));
            blocks[i >>> 2] |= (0xf0 | (code >>> 18)) << SHIFT[i++ & 3];
            blocks[i >>> 2] |= (0x80 | ((code >>> 12) & 0x3f)) << SHIFT[i++ & 3];
            blocks[i >>> 2] |= (0x80 | ((code >>> 6) & 0x3f)) << SHIFT[i++ & 3];
            blocks[i >>> 2] |= (0x80 | (code & 0x3f)) << SHIFT[i++ & 3];
          }
        }
      }

      this.lastByteIndex = i;
      this.bytes += i - this.start;
      if (i >= 64) {
        this.block = blocks[16];
        this.start = i - 64;
        this.hash();
        this.hashed = true;
      } else {
        this.start = i;
      }
    }
    if (this.bytes > 4294967295) {
      this.hBytes += this.bytes / 4294967296 << 0;
      this.bytes = this.bytes % 4294967296;
    }
    return this;
  };

  Sha256.prototype.finalize = function () {
    if (this.finalized) {
      return;
    }
    this.finalized = true;
    var blocks = this.blocks, i = this.lastByteIndex;
    blocks[16] = this.block;
    blocks[i >>> 2] |= EXTRA[i & 3];
    this.block = blocks[16];
    if (i >= 56) {
      if (!this.hashed) {
        this.hash();
      }
      blocks[0] = this.block;
      blocks[16] = blocks[1] = blocks[2] = blocks[3] =
        blocks[4] = blocks[5] = blocks[6] = blocks[7] =
        blocks[8] = blocks[9] = blocks[10] = blocks[11] =
        blocks[12] = blocks[13] = blocks[14] = blocks[15] = 0;
    }
    blocks[14] = this.hBytes << 3 | this.bytes >>> 29;
    blocks[15] = this.bytes << 3;
    this.hash();
  };

  Sha256.prototype.hash = function () {
    var a = this.h0, b = this.h1, c = this.h2, d = this.h3, e = this.h4, f = this.h5, g = this.h6,
      h = this.h7, blocks = this.blocks, j, s0, s1, maj, t1, t2, ch, ab, da, cd, bc;

    for (j = 16; j < 64; ++j) {
      // rightrotate
      t1 = blocks[j - 15];
      s0 = ((t1 >>> 7) | (t1 << 25)) ^ ((t1 >>> 18) | (t1 << 14)) ^ (t1 >>> 3);
      t1 = blocks[j - 2];
      s1 = ((t1 >>> 17) | (t1 << 15)) ^ ((t1 >>> 19) | (t1 << 13)) ^ (t1 >>> 10);
      blocks[j] = blocks[j - 16] + s0 + blocks[j - 7] + s1 << 0;
    }

    bc = b & c;
    for (j = 0; j < 64; j += 4) {
      if (this.first) {
        if (this.is224) {
          ab = 300032;
          t1 = blocks[0] - 1413257819;
          h = t1 - 150054599 << 0;
          d = t1 + 24177077 << 0;
        } else {
          ab = 704751109;
          t1 = blocks[0] - 210244248;
          h = t1 - 1521486534 << 0;
          d = t1 + 143694565 << 0;
        }
        this.first = false;
      } else {
        s0 = ((a >>> 2) | (a << 30)) ^ ((a >>> 13) | (a << 19)) ^ ((a >>> 22) | (a << 10));
        s1 = ((e >>> 6) | (e << 26)) ^ ((e >>> 11) | (e << 21)) ^ ((e >>> 25) | (e << 7));
        ab = a & b;
        maj = ab ^ (a & c) ^ bc;
        ch = (e & f) ^ (~e & g);
        t1 = h + s1 + ch + K[j] + blocks[j];
        t2 = s0 + maj;
        h = d + t1 << 0;
        d = t1 + t2 << 0;
      }
      s0 = ((d >>> 2) | (d << 30)) ^ ((d >>> 13) | (d << 19)) ^ ((d >>> 22) | (d << 10));
      s1 = ((h >>> 6) | (h << 26)) ^ ((h >>> 11) | (h << 21)) ^ ((h >>> 25) | (h << 7));
      da = d & a;
      maj = da ^ (d & b) ^ ab;
      ch = (h & e) ^ (~h & f);
      t1 = g + s1 + ch + K[j + 1] + blocks[j + 1];
      t2 = s0 + maj;
      g = c + t1 << 0;
      c = t1 + t2 << 0;
      s0 = ((c >>> 2) | (c << 30)) ^ ((c >>> 13) | (c << 19)) ^ ((c >>> 22) | (c << 10));
      s1 = ((g >>> 6) | (g << 26)) ^ ((g >>> 11) | (g << 21)) ^ ((g >>> 25) | (g << 7));
      cd = c & d;
      maj = cd ^ (c & a) ^ da;
      ch = (g & h) ^ (~g & e);
      t1 = f + s1 + ch + K[j + 2] + blocks[j + 2];
      t2 = s0 + maj;
      f = b + t1 << 0;
      b = t1 + t2 << 0;
      s0 = ((b >>> 2) | (b << 30)) ^ ((b >>> 13) | (b << 19)) ^ ((b >>> 22) | (b << 10));
      s1 = ((f >>> 6) | (f << 26)) ^ ((f >>> 11) | (f << 21)) ^ ((f >>> 25) | (f << 7));
      bc = b & c;
      maj = bc ^ (b & d) ^ cd;
      ch = (f & g) ^ (~f & h);
      t1 = e + s1 + ch + K[j + 3] + blocks[j + 3];
      t2 = s0 + maj;
      e = a + t1 << 0;
      a = t1 + t2 << 0;
      this.chromeBugWorkAround = true;
    }

    this.h0 = this.h0 + a << 0;
    this.h1 = this.h1 + b << 0;
    this.h2 = this.h2 + c << 0;
    this.h3 = this.h3 + d << 0;
    this.h4 = this.h4 + e << 0;
    this.h5 = this.h5 + f << 0;
    this.h6 = this.h6 + g << 0;
    this.h7 = this.h7 + h << 0;
  };

  Sha256.prototype.hex = function () {
    this.finalize();

    var h0 = this.h0, h1 = this.h1, h2 = this.h2, h3 = this.h3, h4 = this.h4, h5 = this.h5,
      h6 = this.h6, h7 = this.h7;

    var hex = HEX_CHARS[(h0 >>> 28) & 0x0F] + HEX_CHARS[(h0 >>> 24) & 0x0F] +
      HEX_CHARS[(h0 >>> 20) & 0x0F] + HEX_CHARS[(h0 >>> 16) & 0x0F] +
      HEX_CHARS[(h0 >>> 12) & 0x0F] + HEX_CHARS[(h0 >>> 8) & 0x0F] +
      HEX_CHARS[(h0 >>> 4) & 0x0F] + HEX_CHARS[h0 & 0x0F] +
      HEX_CHARS[(h1 >>> 28) & 0x0F] + HEX_CHARS[(h1 >>> 24) & 0x0F] +
      HEX_CHARS[(h1 >>> 20) & 0x0F] + HEX_CHARS[(h1 >>> 16) & 0x0F] +
      HEX_CHARS[(h1 >>> 12) & 0x0F] + HEX_CHARS[(h1 >>> 8) & 0x0F] +
      HEX_CHARS[(h1 >>> 4) & 0x0F] + HEX_CHARS[h1 & 0x0F] +
      HEX_CHARS[(h2 >>> 28) & 0x0F] + HEX_CHARS[(h2 >>> 24) & 0x0F] +
      HEX_CHARS[(h2 >>> 20) & 0x0F] + HEX_CHARS[(h2 >>> 16) & 0x0F] +
      HEX_CHARS[(h2 >>> 12) & 0x0F] + HEX_CHARS[(h2 >>> 8) & 0x0F] +
      HEX_CHARS[(h2 >>> 4) & 0x0F] + HEX_CHARS[h2 & 0x0F] +
      HEX_CHARS[(h3 >>> 28) & 0x0F] + HEX_CHARS[(h3 >>> 24) & 0x0F] +
      HEX_CHARS[(h3 >>> 20) & 0x0F] + HEX_CHARS[(h3 >>> 16) & 0x0F] +
      HEX_CHARS[(h3 >>> 12) & 0x0F] + HEX_CHARS[(h3 >>> 8) & 0x0F] +
      HEX_CHARS[(h3 >>> 4) & 0x0F] + HEX_CHARS[h3 & 0x0F] +
      HEX_CHARS[(h4 >>> 28) & 0x0F] + HEX_CHARS[(h4 >>> 24) & 0x0F] +
      HEX_CHARS[(h4 >>> 20) & 0x0F] + HEX_CHARS[(h4 >>> 16) & 0x0F] +
      HEX_CHARS[(h4 >>> 12) & 0x0F] + HEX_CHARS[(h4 >>> 8) & 0x0F] +
      HEX_CHARS[(h4 >>> 4) & 0x0F] + HEX_CHARS[h4 & 0x0F] +
      HEX_CHARS[(h5 >>> 28) & 0x0F] + HEX_CHARS[(h5 >>> 24) & 0x0F] +
      HEX_CHARS[(h5 >>> 20) & 0x0F] + HEX_CHARS[(h5 >>> 16) & 0x0F] +
      HEX_CHARS[(h5 >>> 12) & 0x0F] + HEX_CHARS[(h5 >>> 8) & 0x0F] +
      HEX_CHARS[(h5 >>> 4) & 0x0F] + HEX_CHARS[h5 & 0x0F] +
      HEX_CHARS[(h6 >>> 28) & 0x0F] + HEX_CHARS[(h6 >>> 24) & 0x0F] +
      HEX_CHARS[(h6 >>> 20) & 0x0F] + HEX_CHARS[(h6 >>> 16) & 0x0F] +
      HEX_CHARS[(h6 >>> 12) & 0x0F] + HEX_CHARS[(h6 >>> 8) & 0x0F] +
      HEX_CHARS[(h6 >>> 4) & 0x0F] + HEX_CHARS[h6 & 0x0F];
    if (!this.is224) {
      hex += HEX_CHARS[(h7 >>> 28) & 0x0F] + HEX_CHARS[(h7 >>> 24) & 0x0F] +
        HEX_CHARS[(h7 >>> 20) & 0x0F] + HEX_CHARS[(h7 >>> 16) & 0x0F] +
        HEX_CHARS[(h7 >>> 12) & 0x0F] + HEX_CHARS[(h7 >>> 8) & 0x0F] +
        HEX_CHARS[(h7 >>> 4) & 0x0F] + HEX_CHARS[h7 & 0x0F];
    }
    return hex;
  };

  Sha256.prototype.toString = Sha256.prototype.hex;

  Sha256.prototype.digest = function () {
    this.finalize();

    var h0 = this.h0, h1 = this.h1, h2 = this.h2, h3 = this.h3, h4 = this.h4, h5 = this.h5,
      h6 = this.h6, h7 = this.h7;

    var arr = [
      (h0 >>> 24) & 0xFF, (h0 >>> 16) & 0xFF, (h0 >>> 8) & 0xFF, h0 & 0xFF,
      (h1 >>> 24) & 0xFF, (h1 >>> 16) & 0xFF, (h1 >>> 8) & 0xFF, h1 & 0xFF,
      (h2 >>> 24) & 0xFF, (h2 >>> 16) & 0xFF, (h2 >>> 8) & 0xFF, h2 & 0xFF,
      (h3 >>> 24) & 0xFF, (h3 >>> 16) & 0xFF, (h3 >>> 8) & 0xFF, h3 & 0xFF,
      (h4 >>> 24) & 0xFF, (h4 >>> 16) & 0xFF, (h4 >>> 8) & 0xFF, h4 & 0xFF,
      (h5 >>> 24) & 0xFF, (h5 >>> 16) & 0xFF, (h5 >>> 8) & 0xFF, h5 & 0xFF,
      (h6 >>> 24) & 0xFF, (h6 >>> 16) & 0xFF, (h6 >>> 8) & 0xFF, h6 & 0xFF
    ];
    if (!this.is224) {
      arr.push((h7 >>> 24) & 0xFF, (h7 >>> 16) & 0xFF, (h7 >>> 8) & 0xFF, h7 & 0xFF);
    }
    return arr;
  };

  Sha256.prototype.array = Sha256.prototype.digest;

  Sha256.prototype.arrayBuffer = function () {
    this.finalize();

    var buffer = new ArrayBuffer(this.is224 ? 28 : 32);
    var dataView = new DataView(buffer);
    dataView.setUint32(0, this.h0);
    dataView.setUint32(4, this.h1);
    dataView.setUint32(8, this.h2);
    dataView.setUint32(12, this.h3);
    dataView.setUint32(16, this.h4);
    dataView.setUint32(20, this.h5);
    dataView.setUint32(24, this.h6);
    if (!this.is224) {
      dataView.setUint32(28, this.h7);
    }
    return buffer;
  };

  function HmacSha256(key, is224, sharedMemory) {
    var i, type = typeof key;
    if (type === 'string') {
      var bytes = [], length = key.length, index = 0, code;
      for (i = 0; i < length; ++i) {
        code = key.charCodeAt(i);
        if (code < 0x80) {
          bytes[index++] = code;
        } else if (code < 0x800) {
          bytes[index++] = (0xc0 | (code >>> 6));
          bytes[index++] = (0x80 | (code & 0x3f));
        } else if (code < 0xd800 || code >= 0xe000) {
          bytes[index++] = (0xe0 | (code >>> 12));
          bytes[index++] = (0x80 | ((code >>> 6) & 0x3f));
          bytes[index++] = (0x80 | (code & 0x3f));
        } else {
          code = 0x10000 + (((code & 0x3ff) << 10) | (key.charCodeAt(++i) & 0x3ff));
          bytes[index++] = (0xf0 | (code >>> 18));
          bytes[index++] = (0x80 | ((code >>> 12) & 0x3f));
          bytes[index++] = (0x80 | ((code >>> 6) & 0x3f));
          bytes[index++] = (0x80 | (code & 0x3f));
        }
      }
      key = bytes;
    } else {
      if (type === 'object') {
        if (key === null) {
          throw new Error(ERROR);
        } else if (ARRAY_BUFFER && key.constructor === ArrayBuffer) {
          key = new Uint8Array(key);
        } else if (!Array.isArray(key)) {
          if (!ARRAY_BUFFER || !ArrayBuffer.isView(key)) {
            throw new Error(ERROR);
          }
        }
      } else {
        throw new Error(ERROR);
      }
    }

    if (key.length > 64) {
      key = (new Sha256(is224, true)).update(key).array();
    }

    var oKeyPad = [], iKeyPad = [];
    for (i = 0; i < 64; ++i) {
      var b = key[i] || 0;
      oKeyPad[i] = 0x5c ^ b;
      iKeyPad[i] = 0x36 ^ b;
    }

    Sha256.call(this, is224, sharedMemory);

    this.update(iKeyPad);
    this.oKeyPad = oKeyPad;
    this.inner = true;
    this.sharedMemory = sharedMemory;
  }
  HmacSha256.prototype = new Sha256();

  HmacSha256.prototype.finalize = function () {
    Sha256.prototype.finalize.call(this);
    if (this.inner) {
      this.inner = false;
      var innerHash = this.array();
      Sha256.call(this, this.is224, this.sharedMemory);
      this.update(this.oKeyPad);
      this.update(innerHash);
      Sha256.prototype.finalize.call(this);
    }
  };

  var exports = createMethod();
  exports.sha256 = exports;
  exports.sha224 = createMethod(true);
  exports.sha256.hmac = createHmacMethod();
  exports.sha224.hmac = createHmacMethod(true);

  if (COMMON_JS) {
    module.exports = exports;
  } else {
    root.sha256 = exports.sha256;
    root.sha224 = exports.sha224;
    if (AMD) {
      define(function () {
        return exports;
      });
    }
  }
})();

  });
  const _secp = load(function (module, exports, require) {
"use strict";
/*! noble-secp256k1 - MIT License (c) 2019 Paul Miller (paulmillr.com) */
Object.defineProperty(exports, "__esModule", { value: true });
exports.utils = exports.schnorr = exports.verify = exports.signSync = exports.sign = exports.getSharedSecret = exports.recoverPublicKey = exports.getPublicKey = exports.Signature = exports.Point = exports.CURVE = void 0;
const nodeCrypto = require("crypto");
const _0n = BigInt(0);
const _1n = BigInt(1);
const _2n = BigInt(2);
const _3n = BigInt(3);
const _8n = BigInt(8);
const CURVE = Object.freeze({
    a: _0n,
    b: BigInt(7),
    P: BigInt('0xfffffffffffffffffffffffffffffffffffffffffffffffffffffffefffffc2f'),
    n: BigInt('0xfffffffffffffffffffffffffffffffebaaedce6af48a03bbfd25e8cd0364141'),
    h: _1n,
    Gx: BigInt('55066263022277343669578718895168534326250603453777594175500187360389116729240'),
    Gy: BigInt('32670510020758816978083085130507043184471273380659243275938904335757337482424'),
    beta: BigInt('0x7ae96a2b657c07106e64479eac3434e99cf0497512f58995c1396c28719501ee'),
});
exports.CURVE = CURVE;
const divNearest = (a, b) => (a + b / _2n) / b;
const endo = {
    beta: BigInt('0x7ae96a2b657c07106e64479eac3434e99cf0497512f58995c1396c28719501ee'),
    splitScalar(k) {
        const { n } = CURVE;
        const a1 = BigInt('0x3086d221a7d46bcde86c90e49284eb15');
        const b1 = -_1n * BigInt('0xe4437ed6010e88286f547fa90abfe4c3');
        const a2 = BigInt('0x114ca50f7a8e2f3f657c1108d9d44cfd8');
        const b2 = a1;
        const POW_2_128 = BigInt('0x100000000000000000000000000000000');
        const c1 = divNearest(b2 * k, n);
        const c2 = divNearest(-b1 * k, n);
        let k1 = mod(k - c1 * a1 - c2 * a2, n);
        let k2 = mod(-c1 * b1 - c2 * b2, n);
        const k1neg = k1 > POW_2_128;
        const k2neg = k2 > POW_2_128;
        if (k1neg)
            k1 = n - k1;
        if (k2neg)
            k2 = n - k2;
        if (k1 > POW_2_128 || k2 > POW_2_128) {
            throw new Error('splitScalarEndo: Endomorphism failed, k=' + k);
        }
        return { k1neg, k1, k2neg, k2 };
    },
};
const fieldLen = 32;
const groupLen = 32;
const hashLen = 32;
const compressedLen = fieldLen + 1;
const uncompressedLen = 2 * fieldLen + 1;
function weierstrass(x) {
    const { a, b } = CURVE;
    const x2 = mod(x * x);
    const x3 = mod(x2 * x);
    return mod(x3 + a * x + b);
}
const USE_ENDOMORPHISM = CURVE.a === _0n;
class ShaError extends Error {
    constructor(message) {
        super(message);
    }
}
function assertJacPoint(other) {
    if (!(other instanceof JacobianPoint))
        throw new TypeError('JacobianPoint expected');
}
class JacobianPoint {
    constructor(x, y, z) {
        this.x = x;
        this.y = y;
        this.z = z;
    }
    static fromAffine(p) {
        if (!(p instanceof Point)) {
            throw new TypeError('JacobianPoint#fromAffine: expected Point');
        }
        if (p.equals(Point.ZERO))
            return JacobianPoint.ZERO;
        return new JacobianPoint(p.x, p.y, _1n);
    }
    static toAffineBatch(points) {
        const toInv = invertBatch(points.map((p) => p.z));
        return points.map((p, i) => p.toAffine(toInv[i]));
    }
    static normalizeZ(points) {
        return JacobianPoint.toAffineBatch(points).map(JacobianPoint.fromAffine);
    }
    equals(other) {
        assertJacPoint(other);
        const { x: X1, y: Y1, z: Z1 } = this;
        const { x: X2, y: Y2, z: Z2 } = other;
        const Z1Z1 = mod(Z1 * Z1);
        const Z2Z2 = mod(Z2 * Z2);
        const U1 = mod(X1 * Z2Z2);
        const U2 = mod(X2 * Z1Z1);
        const S1 = mod(mod(Y1 * Z2) * Z2Z2);
        const S2 = mod(mod(Y2 * Z1) * Z1Z1);
        return U1 === U2 && S1 === S2;
    }
    negate() {
        return new JacobianPoint(this.x, mod(-this.y), this.z);
    }
    double() {
        const { x: X1, y: Y1, z: Z1 } = this;
        const A = mod(X1 * X1);
        const B = mod(Y1 * Y1);
        const C = mod(B * B);
        const x1b = X1 + B;
        const D = mod(_2n * (mod(x1b * x1b) - A - C));
        const E = mod(_3n * A);
        const F = mod(E * E);
        const X3 = mod(F - _2n * D);
        const Y3 = mod(E * (D - X3) - _8n * C);
        const Z3 = mod(_2n * Y1 * Z1);
        return new JacobianPoint(X3, Y3, Z3);
    }
    add(other) {
        assertJacPoint(other);
        const { x: X1, y: Y1, z: Z1 } = this;
        const { x: X2, y: Y2, z: Z2 } = other;
        if (X2 === _0n || Y2 === _0n)
            return this;
        if (X1 === _0n || Y1 === _0n)
            return other;
        const Z1Z1 = mod(Z1 * Z1);
        const Z2Z2 = mod(Z2 * Z2);
        const U1 = mod(X1 * Z2Z2);
        const U2 = mod(X2 * Z1Z1);
        const S1 = mod(mod(Y1 * Z2) * Z2Z2);
        const S2 = mod(mod(Y2 * Z1) * Z1Z1);
        const H = mod(U2 - U1);
        const r = mod(S2 - S1);
        if (H === _0n) {
            if (r === _0n) {
                return this.double();
            }
            else {
                return JacobianPoint.ZERO;
            }
        }
        const HH = mod(H * H);
        const HHH = mod(H * HH);
        const V = mod(U1 * HH);
        const X3 = mod(r * r - HHH - _2n * V);
        const Y3 = mod(r * (V - X3) - S1 * HHH);
        const Z3 = mod(Z1 * Z2 * H);
        return new JacobianPoint(X3, Y3, Z3);
    }
    subtract(other) {
        return this.add(other.negate());
    }
    multiplyUnsafe(scalar) {
        const P0 = JacobianPoint.ZERO;
        if (typeof scalar === 'bigint' && scalar === _0n)
            return P0;
        let n = normalizeScalar(scalar);
        if (n === _1n)
            return this;
        if (!USE_ENDOMORPHISM) {
            let p = P0;
            let d = this;
            while (n > _0n) {
                if (n & _1n)
                    p = p.add(d);
                d = d.double();
                n >>= _1n;
            }
            return p;
        }
        let { k1neg, k1, k2neg, k2 } = endo.splitScalar(n);
        let k1p = P0;
        let k2p = P0;
        let d = this;
        while (k1 > _0n || k2 > _0n) {
            if (k1 & _1n)
                k1p = k1p.add(d);
            if (k2 & _1n)
                k2p = k2p.add(d);
            d = d.double();
            k1 >>= _1n;
            k2 >>= _1n;
        }
        if (k1neg)
            k1p = k1p.negate();
        if (k2neg)
            k2p = k2p.negate();
        k2p = new JacobianPoint(mod(k2p.x * endo.beta), k2p.y, k2p.z);
        return k1p.add(k2p);
    }
    precomputeWindow(W) {
        const windows = USE_ENDOMORPHISM ? 128 / W + 1 : 256 / W + 1;
        const points = [];
        let p = this;
        let base = p;
        for (let window = 0; window < windows; window++) {
            base = p;
            points.push(base);
            for (let i = 1; i < 2 ** (W - 1); i++) {
                base = base.add(p);
                points.push(base);
            }
            p = base.double();
        }
        return points;
    }
    wNAF(n, affinePoint) {
        if (!affinePoint && this.equals(JacobianPoint.BASE))
            affinePoint = Point.BASE;
        const W = (affinePoint && affinePoint._WINDOW_SIZE) || 1;
        if (256 % W) {
            throw new Error('Point#wNAF: Invalid precomputation window, must be power of 2');
        }
        let precomputes = affinePoint && pointPrecomputes.get(affinePoint);
        if (!precomputes) {
            precomputes = this.precomputeWindow(W);
            if (affinePoint && W !== 1) {
                precomputes = JacobianPoint.normalizeZ(precomputes);
                pointPrecomputes.set(affinePoint, precomputes);
            }
        }
        let p = JacobianPoint.ZERO;
        let f = JacobianPoint.BASE;
        const windows = 1 + (USE_ENDOMORPHISM ? 128 / W : 256 / W);
        const windowSize = 2 ** (W - 1);
        const mask = BigInt(2 ** W - 1);
        const maxNumber = 2 ** W;
        const shiftBy = BigInt(W);
        for (let window = 0; window < windows; window++) {
            const offset = window * windowSize;
            let wbits = Number(n & mask);
            n >>= shiftBy;
            if (wbits > windowSize) {
                wbits -= maxNumber;
                n += _1n;
            }
            const offset1 = offset;
            const offset2 = offset + Math.abs(wbits) - 1;
            const cond1 = window % 2 !== 0;
            const cond2 = wbits < 0;
            if (wbits === 0) {
                f = f.add(constTimeNegate(cond1, precomputes[offset1]));
            }
            else {
                p = p.add(constTimeNegate(cond2, precomputes[offset2]));
            }
        }
        return { p, f };
    }
    multiply(scalar, affinePoint) {
        let n = normalizeScalar(scalar);
        let point;
        let fake;
        if (USE_ENDOMORPHISM) {
            const { k1neg, k1, k2neg, k2 } = endo.splitScalar(n);
            let { p: k1p, f: f1p } = this.wNAF(k1, affinePoint);
            let { p: k2p, f: f2p } = this.wNAF(k2, affinePoint);
            k1p = constTimeNegate(k1neg, k1p);
            k2p = constTimeNegate(k2neg, k2p);
            k2p = new JacobianPoint(mod(k2p.x * endo.beta), k2p.y, k2p.z);
            point = k1p.add(k2p);
            fake = f1p.add(f2p);
        }
        else {
            const { p, f } = this.wNAF(n, affinePoint);
            point = p;
            fake = f;
        }
        return JacobianPoint.normalizeZ([point, fake])[0];
    }
    toAffine(invZ) {
        const { x, y, z } = this;
        const is0 = this.equals(JacobianPoint.ZERO);
        if (invZ == null)
            invZ = is0 ? _8n : invert(z);
        const iz1 = invZ;
        const iz2 = mod(iz1 * iz1);
        const iz3 = mod(iz2 * iz1);
        const ax = mod(x * iz2);
        const ay = mod(y * iz3);
        const zz = mod(z * iz1);
        if (is0)
            return Point.ZERO;
        if (zz !== _1n)
            throw new Error('invZ was invalid');
        return new Point(ax, ay);
    }
}
JacobianPoint.BASE = new JacobianPoint(CURVE.Gx, CURVE.Gy, _1n);
JacobianPoint.ZERO = new JacobianPoint(_0n, _1n, _0n);
function constTimeNegate(condition, item) {
    const neg = item.negate();
    return condition ? neg : item;
}
const pointPrecomputes = new WeakMap();
class Point {
    constructor(x, y) {
        this.x = x;
        this.y = y;
    }
    _setWindowSize(windowSize) {
        this._WINDOW_SIZE = windowSize;
        pointPrecomputes.delete(this);
    }
    hasEvenY() {
        return this.y % _2n === _0n;
    }
    static fromCompressedHex(bytes) {
        const isShort = bytes.length === 32;
        const x = bytesToNumber(isShort ? bytes : bytes.subarray(1));
        if (!isValidFieldElement(x))
            throw new Error('Point is not on curve');
        const y2 = weierstrass(x);
        let y = sqrtMod(y2);
        const isYOdd = (y & _1n) === _1n;
        if (isShort) {
            if (isYOdd)
                y = mod(-y);
        }
        else {
            const isFirstByteOdd = (bytes[0] & 1) === 1;
            if (isFirstByteOdd !== isYOdd)
                y = mod(-y);
        }
        const point = new Point(x, y);
        point.assertValidity();
        return point;
    }
    static fromUncompressedHex(bytes) {
        const x = bytesToNumber(bytes.subarray(1, fieldLen + 1));
        const y = bytesToNumber(bytes.subarray(fieldLen + 1, fieldLen * 2 + 1));
        const point = new Point(x, y);
        point.assertValidity();
        return point;
    }
    static fromHex(hex) {
        const bytes = ensureBytes(hex);
        const len = bytes.length;
        const header = bytes[0];
        if (len === fieldLen)
            return this.fromCompressedHex(bytes);
        if (len === compressedLen && (header === 0x02 || header === 0x03)) {
            return this.fromCompressedHex(bytes);
        }
        if (len === uncompressedLen && header === 0x04)
            return this.fromUncompressedHex(bytes);
        throw new Error(`Point.fromHex: received invalid point. Expected 32-${compressedLen} compressed bytes or ${uncompressedLen} uncompressed bytes, not ${len}`);
    }
    static fromPrivateKey(privateKey) {
        return Point.BASE.multiply(normalizePrivateKey(privateKey));
    }
    static fromSignature(msgHash, signature, recovery) {
        const { r, s } = normalizeSignature(signature);
        if (![0, 1, 2, 3].includes(recovery))
            throw new Error('Cannot recover: invalid recovery bit');
        const h = truncateHash(ensureBytes(msgHash));
        const { n } = CURVE;
        const radj = recovery === 2 || recovery === 3 ? r + n : r;
        const rinv = invert(radj, n);
        const u1 = mod(-h * rinv, n);
        const u2 = mod(s * rinv, n);
        const prefix = recovery & 1 ? '03' : '02';
        const R = Point.fromHex(prefix + numTo32bStr(radj));
        const Q = Point.BASE.multiplyAndAddUnsafe(R, u1, u2);
        if (!Q)
            throw new Error('Cannot recover signature: point at infinify');
        Q.assertValidity();
        return Q;
    }
    toRawBytes(isCompressed = false) {
        return hexToBytes(this.toHex(isCompressed));
    }
    toHex(isCompressed = false) {
        const x = numTo32bStr(this.x);
        if (isCompressed) {
            const prefix = this.hasEvenY() ? '02' : '03';
            return `${prefix}${x}`;
        }
        else {
            return `04${x}${numTo32bStr(this.y)}`;
        }
    }
    toHexX() {
        return this.toHex(true).slice(2);
    }
    toRawX() {
        return this.toRawBytes(true).slice(1);
    }
    assertValidity() {
        const msg = 'Point is not on elliptic curve';
        const { x, y } = this;
        if (!isValidFieldElement(x) || !isValidFieldElement(y))
            throw new Error(msg);
        const left = mod(y * y);
        const right = weierstrass(x);
        if (mod(left - right) !== _0n)
            throw new Error(msg);
    }
    equals(other) {
        return this.x === other.x && this.y === other.y;
    }
    negate() {
        return new Point(this.x, mod(-this.y));
    }
    double() {
        return JacobianPoint.fromAffine(this).double().toAffine();
    }
    add(other) {
        return JacobianPoint.fromAffine(this).add(JacobianPoint.fromAffine(other)).toAffine();
    }
    subtract(other) {
        return this.add(other.negate());
    }
    multiply(scalar) {
        return JacobianPoint.fromAffine(this).multiply(scalar, this).toAffine();
    }
    multiplyAndAddUnsafe(Q, a, b) {
        const P = JacobianPoint.fromAffine(this);
        const aP = a === _0n || a === _1n || this !== Point.BASE ? P.multiplyUnsafe(a) : P.multiply(a);
        const bQ = JacobianPoint.fromAffine(Q).multiplyUnsafe(b);
        const sum = aP.add(bQ);
        return sum.equals(JacobianPoint.ZERO) ? undefined : sum.toAffine();
    }
}
exports.Point = Point;
Point.BASE = new Point(CURVE.Gx, CURVE.Gy);
Point.ZERO = new Point(_0n, _0n);
function sliceDER(s) {
    return Number.parseInt(s[0], 16) >= 8 ? '00' + s : s;
}
function parseDERInt(data) {
    if (data.length < 2 || data[0] !== 0x02) {
        throw new Error(`Invalid signature integer tag: ${bytesToHex(data)}`);
    }
    const len = data[1];
    const res = data.subarray(2, len + 2);
    if (!len || res.length !== len) {
        throw new Error(`Invalid signature integer: wrong length`);
    }
    if (res[0] === 0x00 && res[1] <= 0x7f) {
        throw new Error('Invalid signature integer: trailing length');
    }
    return { data: bytesToNumber(res), left: data.subarray(len + 2) };
}
function parseDERSignature(data) {
    if (data.length < 2 || data[0] != 0x30) {
        throw new Error(`Invalid signature tag: ${bytesToHex(data)}`);
    }
    if (data[1] !== data.length - 2) {
        throw new Error('Invalid signature: incorrect length');
    }
    const { data: r, left: sBytes } = parseDERInt(data.subarray(2));
    const { data: s, left: rBytesLeft } = parseDERInt(sBytes);
    if (rBytesLeft.length) {
        throw new Error(`Invalid signature: left bytes after parsing: ${bytesToHex(rBytesLeft)}`);
    }
    return { r, s };
}
class Signature {
    constructor(r, s) {
        this.r = r;
        this.s = s;
        this.assertValidity();
    }
    static fromCompact(hex) {
        const arr = hex instanceof Uint8Array;
        const name = 'Signature.fromCompact';
        if (typeof hex !== 'string' && !arr)
            throw new TypeError(`${name}: Expected string or Uint8Array`);
        const str = arr ? bytesToHex(hex) : hex;
        if (str.length !== 128)
            throw new Error(`${name}: Expected 64-byte hex`);
        return new Signature(hexToNumber(str.slice(0, 64)), hexToNumber(str.slice(64, 128)));
    }
    static fromDER(hex) {
        const arr = hex instanceof Uint8Array;
        if (typeof hex !== 'string' && !arr)
            throw new TypeError(`Signature.fromDER: Expected string or Uint8Array`);
        const { r, s } = parseDERSignature(arr ? hex : hexToBytes(hex));
        return new Signature(r, s);
    }
    static fromHex(hex) {
        return this.fromDER(hex);
    }
    assertValidity() {
        const { r, s } = this;
        if (!isWithinCurveOrder(r))
            throw new Error('Invalid Signature: r must be 0 < r < n');
        if (!isWithinCurveOrder(s))
            throw new Error('Invalid Signature: s must be 0 < s < n');
    }
    hasHighS() {
        const HALF = CURVE.n >> _1n;
        return this.s > HALF;
    }
    normalizeS() {
        return this.hasHighS() ? new Signature(this.r, mod(-this.s, CURVE.n)) : this;
    }
    toDERRawBytes() {
        return hexToBytes(this.toDERHex());
    }
    toDERHex() {
        const sHex = sliceDER(numberToHexUnpadded(this.s));
        const rHex = sliceDER(numberToHexUnpadded(this.r));
        const sHexL = sHex.length / 2;
        const rHexL = rHex.length / 2;
        const sLen = numberToHexUnpadded(sHexL);
        const rLen = numberToHexUnpadded(rHexL);
        const length = numberToHexUnpadded(rHexL + sHexL + 4);
        return `30${length}02${rLen}${rHex}02${sLen}${sHex}`;
    }
    toRawBytes() {
        return this.toDERRawBytes();
    }
    toHex() {
        return this.toDERHex();
    }
    toCompactRawBytes() {
        return hexToBytes(this.toCompactHex());
    }
    toCompactHex() {
        return numTo32bStr(this.r) + numTo32bStr(this.s);
    }
}
exports.Signature = Signature;
function concatBytes(...arrays) {
    if (!arrays.every((b) => b instanceof Uint8Array))
        throw new Error('Uint8Array list expected');
    if (arrays.length === 1)
        return arrays[0];
    const length = arrays.reduce((a, arr) => a + arr.length, 0);
    const result = new Uint8Array(length);
    for (let i = 0, pad = 0; i < arrays.length; i++) {
        const arr = arrays[i];
        result.set(arr, pad);
        pad += arr.length;
    }
    return result;
}
const hexes = Array.from({ length: 256 }, (v, i) => i.toString(16).padStart(2, '0'));
function bytesToHex(uint8a) {
    if (!(uint8a instanceof Uint8Array))
        throw new Error('Expected Uint8Array');
    let hex = '';
    for (let i = 0; i < uint8a.length; i++) {
        hex += hexes[uint8a[i]];
    }
    return hex;
}
const POW_2_256 = BigInt('0x10000000000000000000000000000000000000000000000000000000000000000');
function numTo32bStr(num) {
    if (typeof num !== 'bigint')
        throw new Error('Expected bigint');
    if (!(_0n <= num && num < POW_2_256))
        throw new Error('Expected number 0 <= n < 2^256');
    return num.toString(16).padStart(64, '0');
}
function numTo32b(num) {
    const b = hexToBytes(numTo32bStr(num));
    if (b.length !== 32)
        throw new Error('Error: expected 32 bytes');
    return b;
}
function numberToHexUnpadded(num) {
    const hex = num.toString(16);
    return hex.length & 1 ? `0${hex}` : hex;
}
function hexToNumber(hex) {
    if (typeof hex !== 'string') {
        throw new TypeError('hexToNumber: expected string, got ' + typeof hex);
    }
    return BigInt(`0x${hex}`);
}
function hexToBytes(hex) {
    if (typeof hex !== 'string') {
        throw new TypeError('hexToBytes: expected string, got ' + typeof hex);
    }
    if (hex.length % 2)
        throw new Error('hexToBytes: received invalid unpadded hex' + hex.length);
    const array = new Uint8Array(hex.length / 2);
    for (let i = 0; i < array.length; i++) {
        const j = i * 2;
        const hexByte = hex.slice(j, j + 2);
        const byte = Number.parseInt(hexByte, 16);
        if (Number.isNaN(byte) || byte < 0)
            throw new Error('Invalid byte sequence');
        array[i] = byte;
    }
    return array;
}
function bytesToNumber(bytes) {
    return hexToNumber(bytesToHex(bytes));
}
function ensureBytes(hex) {
    return hex instanceof Uint8Array ? Uint8Array.from(hex) : hexToBytes(hex);
}
function normalizeScalar(num) {
    if (typeof num === 'number' && Number.isSafeInteger(num) && num > 0)
        return BigInt(num);
    if (typeof num === 'bigint' && isWithinCurveOrder(num))
        return num;
    throw new TypeError('Expected valid private scalar: 0 < scalar < curve.n');
}
function mod(a, b = CURVE.P) {
    const result = a % b;
    return result >= _0n ? result : b + result;
}
function pow2(x, power) {
    const { P } = CURVE;
    let res = x;
    while (power-- > _0n) {
        res *= res;
        res %= P;
    }
    return res;
}
function sqrtMod(x) {
    const { P } = CURVE;
    const _6n = BigInt(6);
    const _11n = BigInt(11);
    const _22n = BigInt(22);
    const _23n = BigInt(23);
    const _44n = BigInt(44);
    const _88n = BigInt(88);
    const b2 = (x * x * x) % P;
    const b3 = (b2 * b2 * x) % P;
    const b6 = (pow2(b3, _3n) * b3) % P;
    const b9 = (pow2(b6, _3n) * b3) % P;
    const b11 = (pow2(b9, _2n) * b2) % P;
    const b22 = (pow2(b11, _11n) * b11) % P;
    const b44 = (pow2(b22, _22n) * b22) % P;
    const b88 = (pow2(b44, _44n) * b44) % P;
    const b176 = (pow2(b88, _88n) * b88) % P;
    const b220 = (pow2(b176, _44n) * b44) % P;
    const b223 = (pow2(b220, _3n) * b3) % P;
    const t1 = (pow2(b223, _23n) * b22) % P;
    const t2 = (pow2(t1, _6n) * b2) % P;
    const rt = pow2(t2, _2n);
    const xc = (rt * rt) % P;
    if (xc !== x)
        throw new Error('Cannot find square root');
    return rt;
}
function invert(number, modulo = CURVE.P) {
    if (number === _0n || modulo <= _0n) {
        throw new Error(`invert: expected positive integers, got n=${number} mod=${modulo}`);
    }
    let a = mod(number, modulo);
    let b = modulo;
    let x = _0n, y = _1n, u = _1n, v = _0n;
    while (a !== _0n) {
        const q = b / a;
        const r = b % a;
        const m = x - u * q;
        const n = y - v * q;
        b = a, a = r, x = u, y = v, u = m, v = n;
    }
    const gcd = b;
    if (gcd !== _1n)
        throw new Error('invert: does not exist');
    return mod(x, modulo);
}
function invertBatch(nums, p = CURVE.P) {
    const scratch = new Array(nums.length);
    const lastMultiplied = nums.reduce((acc, num, i) => {
        if (num === _0n)
            return acc;
        scratch[i] = acc;
        return mod(acc * num, p);
    }, _1n);
    const inverted = invert(lastMultiplied, p);
    nums.reduceRight((acc, num, i) => {
        if (num === _0n)
            return acc;
        scratch[i] = mod(acc * scratch[i], p);
        return mod(acc * num, p);
    }, inverted);
    return scratch;
}
function bits2int_2(bytes) {
    const delta = bytes.length * 8 - groupLen * 8;
    const num = bytesToNumber(bytes);
    return delta > 0 ? num >> BigInt(delta) : num;
}
function truncateHash(hash, truncateOnly = false) {
    const h = bits2int_2(hash);
    if (truncateOnly)
        return h;
    const { n } = CURVE;
    return h >= n ? h - n : h;
}
let _sha256Sync;
let _hmacSha256Sync;
class HmacDrbg {
    constructor(hashLen, qByteLen) {
        this.hashLen = hashLen;
        this.qByteLen = qByteLen;
        if (typeof hashLen !== 'number' || hashLen < 2)
            throw new Error('hashLen must be a number');
        if (typeof qByteLen !== 'number' || qByteLen < 2)
            throw new Error('qByteLen must be a number');
        this.v = new Uint8Array(hashLen).fill(1);
        this.k = new Uint8Array(hashLen).fill(0);
        this.counter = 0;
    }
    hmac(...values) {
        return exports.utils.hmacSha256(this.k, ...values);
    }
    hmacSync(...values) {
        return _hmacSha256Sync(this.k, ...values);
    }
    checkSync() {
        if (typeof _hmacSha256Sync !== 'function')
            throw new ShaError('hmacSha256Sync needs to be set');
    }
    incr() {
        if (this.counter >= 1000)
            throw new Error('Tried 1,000 k values for sign(), all were invalid');
        this.counter += 1;
    }
    async reseed(seed = new Uint8Array()) {
        this.k = await this.hmac(this.v, Uint8Array.from([0x00]), seed);
        this.v = await this.hmac(this.v);
        if (seed.length === 0)
            return;
        this.k = await this.hmac(this.v, Uint8Array.from([0x01]), seed);
        this.v = await this.hmac(this.v);
    }
    reseedSync(seed = new Uint8Array()) {
        this.checkSync();
        this.k = this.hmacSync(this.v, Uint8Array.from([0x00]), seed);
        this.v = this.hmacSync(this.v);
        if (seed.length === 0)
            return;
        this.k = this.hmacSync(this.v, Uint8Array.from([0x01]), seed);
        this.v = this.hmacSync(this.v);
    }
    async generate() {
        this.incr();
        let len = 0;
        const out = [];
        while (len < this.qByteLen) {
            this.v = await this.hmac(this.v);
            const sl = this.v.slice();
            out.push(sl);
            len += this.v.length;
        }
        return concatBytes(...out);
    }
    generateSync() {
        this.checkSync();
        this.incr();
        let len = 0;
        const out = [];
        while (len < this.qByteLen) {
            this.v = this.hmacSync(this.v);
            const sl = this.v.slice();
            out.push(sl);
            len += this.v.length;
        }
        return concatBytes(...out);
    }
}
function isWithinCurveOrder(num) {
    return _0n < num && num < CURVE.n;
}
function isValidFieldElement(num) {
    return _0n < num && num < CURVE.P;
}
function kmdToSig(kBytes, m, d, lowS = true) {
    const { n } = CURVE;
    const k = truncateHash(kBytes, true);
    if (!isWithinCurveOrder(k))
        return;
    const kinv = invert(k, n);
    const q = Point.BASE.multiply(k);
    const r = mod(q.x, n);
    if (r === _0n)
        return;
    const s = mod(kinv * mod(m + d * r, n), n);
    if (s === _0n)
        return;
    let sig = new Signature(r, s);
    let recovery = (q.x === sig.r ? 0 : 2) | Number(q.y & _1n);
    if (lowS && sig.hasHighS()) {
        sig = sig.normalizeS();
        recovery ^= 1;
    }
    return { sig, recovery };
}
function normalizePrivateKey(key) {
    let num;
    if (typeof key === 'bigint') {
        num = key;
    }
    else if (typeof key === 'number' && Number.isSafeInteger(key) && key > 0) {
        num = BigInt(key);
    }
    else if (typeof key === 'string') {
        if (key.length !== 2 * groupLen)
            throw new Error('Expected 32 bytes of private key');
        num = hexToNumber(key);
    }
    else if (key instanceof Uint8Array) {
        if (key.length !== groupLen)
            throw new Error('Expected 32 bytes of private key');
        num = bytesToNumber(key);
    }
    else {
        throw new TypeError('Expected valid private key');
    }
    if (!isWithinCurveOrder(num))
        throw new Error('Expected private key: 0 < key < n');
    return num;
}
function normalizePublicKey(publicKey) {
    if (publicKey instanceof Point) {
        publicKey.assertValidity();
        return publicKey;
    }
    else {
        return Point.fromHex(publicKey);
    }
}
function normalizeSignature(signature) {
    if (signature instanceof Signature) {
        signature.assertValidity();
        return signature;
    }
    try {
        return Signature.fromDER(signature);
    }
    catch (error) {
        return Signature.fromCompact(signature);
    }
}
function getPublicKey(privateKey, isCompressed = false) {
    return Point.fromPrivateKey(privateKey).toRawBytes(isCompressed);
}
exports.getPublicKey = getPublicKey;
function recoverPublicKey(msgHash, signature, recovery, isCompressed = false) {
    return Point.fromSignature(msgHash, signature, recovery).toRawBytes(isCompressed);
}
exports.recoverPublicKey = recoverPublicKey;
function isProbPub(item) {
    const arr = item instanceof Uint8Array;
    const str = typeof item === 'string';
    const len = (arr || str) && item.length;
    if (arr)
        return len === compressedLen || len === uncompressedLen;
    if (str)
        return len === compressedLen * 2 || len === uncompressedLen * 2;
    if (item instanceof Point)
        return true;
    return false;
}
function getSharedSecret(privateA, publicB, isCompressed = false) {
    if (isProbPub(privateA))
        throw new TypeError('getSharedSecret: first arg must be private key');
    if (!isProbPub(publicB))
        throw new TypeError('getSharedSecret: second arg must be public key');
    const b = normalizePublicKey(publicB);
    b.assertValidity();
    return b.multiply(normalizePrivateKey(privateA)).toRawBytes(isCompressed);
}
exports.getSharedSecret = getSharedSecret;
function bits2int(bytes) {
    const slice = bytes.length > fieldLen ? bytes.slice(0, fieldLen) : bytes;
    return bytesToNumber(slice);
}
function bits2octets(bytes) {
    const z1 = bits2int(bytes);
    const z2 = mod(z1, CURVE.n);
    return int2octets(z2 < _0n ? z1 : z2);
}
function int2octets(num) {
    return numTo32b(num);
}
function initSigArgs(msgHash, privateKey, extraEntropy) {
    if (msgHash == null)
        throw new Error(`sign: expected valid message hash, not "${msgHash}"`);
    const h1 = ensureBytes(msgHash);
    const d = normalizePrivateKey(privateKey);
    const seedArgs = [int2octets(d), bits2octets(h1)];
    if (extraEntropy != null) {
        if (extraEntropy === true)
            extraEntropy = exports.utils.randomBytes(fieldLen);
        const e = ensureBytes(extraEntropy);
        if (e.length !== fieldLen)
            throw new Error(`sign: Expected ${fieldLen} bytes of extra data`);
        seedArgs.push(e);
    }
    const seed = concatBytes(...seedArgs);
    const m = bits2int(h1);
    return { seed, m, d };
}
function finalizeSig(recSig, opts) {
    const { sig, recovery } = recSig;
    const { der, recovered } = Object.assign({ canonical: true, der: true }, opts);
    const hashed = der ? sig.toDERRawBytes() : sig.toCompactRawBytes();
    return recovered ? [hashed, recovery] : hashed;
}
async function sign(msgHash, privKey, opts = {}) {
    const { seed, m, d } = initSigArgs(msgHash, privKey, opts.extraEntropy);
    const drbg = new HmacDrbg(hashLen, groupLen);
    await drbg.reseed(seed);
    let sig;
    while (!(sig = kmdToSig(await drbg.generate(), m, d, opts.canonical)))
        await drbg.reseed();
    return finalizeSig(sig, opts);
}
exports.sign = sign;
function signSync(msgHash, privKey, opts = {}) {
    const { seed, m, d } = initSigArgs(msgHash, privKey, opts.extraEntropy);
    const drbg = new HmacDrbg(hashLen, groupLen);
    drbg.reseedSync(seed);
    let sig;
    while (!(sig = kmdToSig(drbg.generateSync(), m, d, opts.canonical)))
        drbg.reseedSync();
    return finalizeSig(sig, opts);
}
exports.signSync = signSync;
const vopts = { strict: true };
function verify(signature, msgHash, publicKey, opts = vopts) {
    let sig;
    try {
        sig = normalizeSignature(signature);
        msgHash = ensureBytes(msgHash);
    }
    catch (error) {
        return false;
    }
    const { r, s } = sig;
    if (opts.strict && sig.hasHighS())
        return false;
    const h = truncateHash(msgHash);
    let P;
    try {
        P = normalizePublicKey(publicKey);
    }
    catch (error) {
        return false;
    }
    const { n } = CURVE;
    const sinv = invert(s, n);
    const u1 = mod(h * sinv, n);
    const u2 = mod(r * sinv, n);
    const R = Point.BASE.multiplyAndAddUnsafe(P, u1, u2);
    if (!R)
        return false;
    const v = mod(R.x, n);
    return v === r;
}
exports.verify = verify;
function schnorrChallengeFinalize(ch) {
    return mod(bytesToNumber(ch), CURVE.n);
}
class SchnorrSignature {
    constructor(r, s) {
        this.r = r;
        this.s = s;
        this.assertValidity();
    }
    static fromHex(hex) {
        const bytes = ensureBytes(hex);
        if (bytes.length !== 64)
            throw new TypeError(`SchnorrSignature.fromHex: expected 64 bytes, not ${bytes.length}`);
        const r = bytesToNumber(bytes.subarray(0, 32));
        const s = bytesToNumber(bytes.subarray(32, 64));
        return new SchnorrSignature(r, s);
    }
    assertValidity() {
        const { r, s } = this;
        if (!isValidFieldElement(r) || !isWithinCurveOrder(s))
            throw new Error('Invalid signature');
    }
    toHex() {
        return numTo32bStr(this.r) + numTo32bStr(this.s);
    }
    toRawBytes() {
        return hexToBytes(this.toHex());
    }
}
function schnorrGetPublicKey(privateKey) {
    return Point.fromPrivateKey(privateKey).toRawX();
}
class InternalSchnorrSignature {
    constructor(message, privateKey, auxRand = exports.utils.randomBytes()) {
        if (message == null)
            throw new TypeError(`sign: Expected valid message, not "${message}"`);
        this.m = ensureBytes(message);
        const { x, scalar } = this.getScalar(normalizePrivateKey(privateKey));
        this.px = x;
        this.d = scalar;
        this.rand = ensureBytes(auxRand);
        if (this.rand.length !== 32)
            throw new TypeError('sign: Expected 32 bytes of aux randomness');
    }
    getScalar(priv) {
        const point = Point.fromPrivateKey(priv);
        const scalar = point.hasEvenY() ? priv : CURVE.n - priv;
        return { point, scalar, x: point.toRawX() };
    }
    initNonce(d, t0h) {
        return numTo32b(d ^ bytesToNumber(t0h));
    }
    finalizeNonce(k0h) {
        const k0 = mod(bytesToNumber(k0h), CURVE.n);
        if (k0 === _0n)
            throw new Error('sign: Creation of signature failed. k is zero');
        const { point: R, x: rx, scalar: k } = this.getScalar(k0);
        return { R, rx, k };
    }
    finalizeSig(R, k, e, d) {
        return new SchnorrSignature(R.x, mod(k + e * d, CURVE.n)).toRawBytes();
    }
    error() {
        throw new Error('sign: Invalid signature produced');
    }
    async calc() {
        const { m, d, px, rand } = this;
        const tag = exports.utils.taggedHash;
        const t = this.initNonce(d, await tag(TAGS.aux, rand));
        const { R, rx, k } = this.finalizeNonce(await tag(TAGS.nonce, t, px, m));
        const e = schnorrChallengeFinalize(await tag(TAGS.challenge, rx, px, m));
        const sig = this.finalizeSig(R, k, e, d);
        if (!(await schnorrVerify(sig, m, px)))
            this.error();
        return sig;
    }
    calcSync() {
        const { m, d, px, rand } = this;
        const tag = exports.utils.taggedHashSync;
        const t = this.initNonce(d, tag(TAGS.aux, rand));
        const { R, rx, k } = this.finalizeNonce(tag(TAGS.nonce, t, px, m));
        const e = schnorrChallengeFinalize(tag(TAGS.challenge, rx, px, m));
        const sig = this.finalizeSig(R, k, e, d);
        if (!schnorrVerifySync(sig, m, px))
            this.error();
        return sig;
    }
}
async function schnorrSign(msg, privKey, auxRand) {
    return new InternalSchnorrSignature(msg, privKey, auxRand).calc();
}
function schnorrSignSync(msg, privKey, auxRand) {
    return new InternalSchnorrSignature(msg, privKey, auxRand).calcSync();
}
function initSchnorrVerify(signature, message, publicKey) {
    const raw = signature instanceof SchnorrSignature;
    const sig = raw ? signature : SchnorrSignature.fromHex(signature);
    if (raw)
        sig.assertValidity();
    return {
        ...sig,
        m: ensureBytes(message),
        P: normalizePublicKey(publicKey),
    };
}
function finalizeSchnorrVerify(r, P, s, e) {
    const R = Point.BASE.multiplyAndAddUnsafe(P, normalizePrivateKey(s), mod(-e, CURVE.n));
    if (!R || !R.hasEvenY() || R.x !== r)
        return false;
    return true;
}
async function schnorrVerify(signature, message, publicKey) {
    try {
        const { r, s, m, P } = initSchnorrVerify(signature, message, publicKey);
        const e = schnorrChallengeFinalize(await exports.utils.taggedHash(TAGS.challenge, numTo32b(r), P.toRawX(), m));
        return finalizeSchnorrVerify(r, P, s, e);
    }
    catch (error) {
        return false;
    }
}
function schnorrVerifySync(signature, message, publicKey) {
    try {
        const { r, s, m, P } = initSchnorrVerify(signature, message, publicKey);
        const e = schnorrChallengeFinalize(exports.utils.taggedHashSync(TAGS.challenge, numTo32b(r), P.toRawX(), m));
        return finalizeSchnorrVerify(r, P, s, e);
    }
    catch (error) {
        if (error instanceof ShaError)
            throw error;
        return false;
    }
}
exports.schnorr = {
    Signature: SchnorrSignature,
    getPublicKey: schnorrGetPublicKey,
    sign: schnorrSign,
    verify: schnorrVerify,
    signSync: schnorrSignSync,
    verifySync: schnorrVerifySync,
};
Point.BASE._setWindowSize(8);
const crypto = {
    node: nodeCrypto,
    web: typeof self === 'object' && 'crypto' in self ? self.crypto : undefined,
};
const TAGS = {
    challenge: 'BIP0340/challenge',
    aux: 'BIP0340/aux',
    nonce: 'BIP0340/nonce',
};
const TAGGED_HASH_PREFIXES = {};
exports.utils = {
    bytesToHex,
    hexToBytes,
    concatBytes,
    mod,
    invert,
    isValidPrivateKey(privateKey) {
        try {
            normalizePrivateKey(privateKey);
            return true;
        }
        catch (error) {
            return false;
        }
    },
    _bigintTo32Bytes: numTo32b,
    _normalizePrivateKey: normalizePrivateKey,
    hashToPrivateKey: (hash) => {
        hash = ensureBytes(hash);
        const minLen = groupLen + 8;
        if (hash.length < minLen || hash.length > 1024) {
            throw new Error(`Expected valid bytes of private key as per FIPS 186`);
        }
        const num = mod(bytesToNumber(hash), CURVE.n - _1n) + _1n;
        return numTo32b(num);
    },
    randomBytes: (bytesLength = 32) => {
        if (crypto.web) {
            return crypto.web.getRandomValues(new Uint8Array(bytesLength));
        }
        else if (crypto.node) {
            const { randomBytes } = crypto.node;
            return Uint8Array.from(randomBytes(bytesLength));
        }
        else {
            throw new Error("The environment doesn't have randomBytes function");
        }
    },
    randomPrivateKey: () => exports.utils.hashToPrivateKey(exports.utils.randomBytes(groupLen + 8)),
    precompute(windowSize = 8, point = Point.BASE) {
        const cached = point === Point.BASE ? point : new Point(point.x, point.y);
        cached._setWindowSize(windowSize);
        cached.multiply(_3n);
        return cached;
    },
    sha256: async (...messages) => {
        if (crypto.web) {
            const buffer = await crypto.web.subtle.digest('SHA-256', concatBytes(...messages));
            return new Uint8Array(buffer);
        }
        else if (crypto.node) {
            const { createHash } = crypto.node;
            const hash = createHash('sha256');
            messages.forEach((m) => hash.update(m));
            return Uint8Array.from(hash.digest());
        }
        else {
            throw new Error("The environment doesn't have sha256 function");
        }
    },
    hmacSha256: async (key, ...messages) => {
        if (crypto.web) {
            const ckey = await crypto.web.subtle.importKey('raw', key, { name: 'HMAC', hash: { name: 'SHA-256' } }, false, ['sign']);
            const message = concatBytes(...messages);
            const buffer = await crypto.web.subtle.sign('HMAC', ckey, message);
            return new Uint8Array(buffer);
        }
        else if (crypto.node) {
            const { createHmac } = crypto.node;
            const hash = createHmac('sha256', key);
            messages.forEach((m) => hash.update(m));
            return Uint8Array.from(hash.digest());
        }
        else {
            throw new Error("The environment doesn't have hmac-sha256 function");
        }
    },
    sha256Sync: undefined,
    hmacSha256Sync: undefined,
    taggedHash: async (tag, ...messages) => {
        let tagP = TAGGED_HASH_PREFIXES[tag];
        if (tagP === undefined) {
            const tagH = await exports.utils.sha256(Uint8Array.from(tag, (c) => c.charCodeAt(0)));
            tagP = concatBytes(tagH, tagH);
            TAGGED_HASH_PREFIXES[tag] = tagP;
        }
        return exports.utils.sha256(tagP, ...messages);
    },
    taggedHashSync: (tag, ...messages) => {
        if (typeof _sha256Sync !== 'function')
            throw new ShaError('sha256Sync is undefined, you need to set it');
        let tagP = TAGGED_HASH_PREFIXES[tag];
        if (tagP === undefined) {
            const tagH = _sha256Sync(Uint8Array.from(tag, (c) => c.charCodeAt(0)));
            tagP = concatBytes(tagH, tagH);
            TAGGED_HASH_PREFIXES[tag] = tagP;
        }
        return _sha256Sync(tagP, ...messages);
    },
    _JacobianPoint: JacobianPoint,
};
Object.defineProperties(exports.utils, {
    sha256Sync: {
        configurable: false,
        get() {
            return _sha256Sync;
        },
        set(val) {
            if (!_sha256Sync)
                _sha256Sync = val;
        },
    },
    hmacSha256Sync: {
        configurable: false,
        get() {
            return _hmacSha256Sync;
        },
        set(val) {
            if (!_hmacSha256Sync)
                _hmacSha256Sync = val;
        },
    },
});

  });
  const sha256fn = _sha256.sha256 || _sha256;
  // RFC6979 needs synchronous HMAC-SHA256; JavaScriptCore has no WebCrypto.
  _secp.utils.hmacSha256Sync = function (key) {
    const h = sha256fn.hmac.create(key);
    for (let i = 1; i < arguments.length; i++) h.update(arguments[i]);
    return new Uint8Array(h.arrayBuffer());
  };
  _secp.utils.sha256Sync = function () {
    const h = sha256fn.create();
    for (let i = 0; i < arguments.length; i++) h.update(arguments[i]);
    return new Uint8Array(h.arrayBuffer());
  };
  // keccak256 is bound later, once the script's own implementation is defined.
  return { sha256: sha256fn, secp: _secp, keccak256: null };
})();


// ----------------------------- secrets ---------------------------------------
// Keys may live either in the constants above or in a ".env" file beside this
// script in the Scriptable folder. The file keeps credentials out of the script
// itself, so the script can be edited, shared or re-deployed without carrying
// them along. Constants win when both are set.
//
// Format is one KEY=value per line; blank lines and # comments are ignored:
//
//     ALCHEMY_API_KEY=your_key_here
//     PRIVATE_KEY=0xabc123...
//
// With neither source set, this stays a plain read-only widget.
function loadDotEnv() {
  const out = {};
  try {
    // iCloud first, since that is where Scriptable keeps its files; a local
    // folder is the fallback when iCloud Drive is not enabled.
    const dirs = [];
    try { const f = FileManager.iCloud(); dirs.push([f, f.documentsDirectory()]); } catch (e) {}
    try { const f = FileManager.local();  dirs.push([f, f.documentsDirectory()]); } catch (e) {}
    for (const [f, dir] of dirs) {
      const path = f.joinPath(dir, ".env");
      if (!f.fileExists(path)) continue;
      // Read first: on the normal path the file is already on the device and
      // this just works. downloadFileFromiCloud is async and this function has
      // to stay synchronous (top-level await is not available here, and the
      // constants below are evaluated at load), so an evicted file cannot be
      // waited for. Kick the download anyway so the NEXT run finds it local,
      // which is what matters for a widget that wakes on a timer.
      let text = "";
      try { text = f.readString(path) || ""; } catch (e) {}
      if (!text) {
        try {
          if (f.isFileStoredIniCloud && f.isFileStoredIniCloud(path) && !f.isFileDownloaded(path)) {
            f.downloadFileFromiCloud(path);   // deliberately not awaited
          }
        } catch (e) {}
        try { text = f.readString(path) || ""; } catch (e) {}
      }
      if (!text) continue;
      for (const line of text.split("\n")) {
        const s = line.trim();
        if (!s || s[0] === "#") continue;
        const eq = s.indexOf("=");
        if (eq < 1) continue;
        const key = s.slice(0, eq).trim();
        let val = s.slice(eq + 1).trim();
        // Tolerate quoted values.
        if ((val[0] === '"' && val.slice(-1) === '"') || (val[0] === "'" && val.slice(-1) === "'")) {
          val = val.slice(1, -1);
        }
        if (key && !(key in out)) out[key] = val;
      }
      if (Object.keys(out).length) break;
    }
  } catch (e) {}
  return out;
}



// --- Keccak-256 (Ethereum variant, pre-FIPS padding 0x01) ---
const KECCAK_RC = [
  0x0000000000000001n, 0x0000000000008082n, 0x800000000000808An,
  0x8000000080008000n, 0x000000000000808Bn, 0x0000000080000001n,
  0x8000000080008081n, 0x8000000000008009n, 0x000000000000008An,
  0x0000000000000088n, 0x0000000080008009n, 0x000000008000000An,
  0x000000008000808Bn, 0x800000000000008Bn, 0x8000000000008089n,
  0x8000000000008003n, 0x8000000000008002n, 0x8000000000000080n,
  0x000000000000800An, 0x800000008000000An, 0x8000000080008081n,
  0x8000000000008080n, 0x0000000080000001n, 0x8000000080008008n,
];
const KECCAK_RHO = [1,3,6,10,15,21,28,36,45,55,2,14,27,41,56,8,25,43,62,18,39,61,20,44];
const KECCAK_PI  = [10,7,11,17,18,3,5,16,8,21,24,4,15,23,19,13,12,2,20,14,22,9,6,1];
function keccak256(hexInput) {
  const input = hexInput.replace("0x", "");
  const bytes = [];
  for (let i = 0; i < input.length; i += 2) bytes.push(parseInt(input.substr(i, 2), 16));
  const rate = 136;
  const padLen = rate - (bytes.length % rate);
  if (padLen === 1) { bytes.push(0x81); }
  else { bytes.push(0x01); for (let i = 1; i < padLen - 1; i++) bytes.push(0); bytes.push(0x80); }

  const state = new Array(25).fill(0n);
  const mask64 = (1n << 64n) - 1n;
  const rotl64 = (x, n) => ((x << n) | (x >> (64n - n))) & mask64;

  for (let off = 0; off < bytes.length; off += rate) {
    for (let i = 0; i < 17; i++) {
      let lane = 0n;
      for (let b = 0; b < 8; b++) lane |= BigInt(bytes[off + i * 8 + b]) << BigInt(b * 8);
      state[i] ^= lane;
    }
    for (let round = 0; round < 24; round++) {
      // theta
      const C = new Array(5);
      for (let x = 0; x < 5; x++) C[x] = state[x] ^ state[x+5] ^ state[x+10] ^ state[x+15] ^ state[x+20];
      for (let x = 0; x < 5; x++) {
        const D = C[(x+4)%5] ^ rotl64(C[(x+1)%5], 1n);
        for (let y = 0; y < 25; y += 5) state[x+y] = (state[x+y] ^ D) & mask64;
      }
      // rho + pi
      let current = state[1];
      for (let t = 0; t < 24; t++) {
        const j = KECCAK_PI[t];
        const tmp = state[j];
        state[j] = rotl64(current, BigInt(KECCAK_RHO[t]));
        current = tmp;
      }
      // chi
      for (let y = 0; y < 25; y += 5) {
        const t0=state[y], t1=state[y+1], t2=state[y+2], t3=state[y+3], t4=state[y+4];
        state[y]   = (t0 ^ ((~t1 & mask64) & t2)) & mask64;
        state[y+1] = (t1 ^ ((~t2 & mask64) & t3)) & mask64;
        state[y+2] = (t2 ^ ((~t3 & mask64) & t4)) & mask64;
        state[y+3] = (t3 ^ ((~t4 & mask64) & t0)) & mask64;
        state[y+4] = (t4 ^ ((~t0 & mask64) & t1)) & mask64;
      }
      // iota
      state[0] = (state[0] ^ KECCAK_RC[round]) & mask64;
    }
  }
  let out = "";
  for (let i = 0; i < 4; i++) {
    let lane = state[i];
    for (let b = 0; b < 8; b++) { out += (Number(lane & 0xFFn)).toString(16).padStart(2, "0"); lane >>= 8n; }
  }
  return out;
}
// keccak256 here takes hex and returns hex; bind it for the signing code.
CRYPTO.keccak256 = function (input) {
  const hex = typeof input === "string" ? input
    : Array.from(input, b => b.toString(16).padStart(2, "0")).join("");
  return keccak256(hex).replace(/^0x/, "");
};

const _hexToBytes = h => {
  const s = String(h || "").replace(/^0x/, "");
  const p = s.length % 2 ? "0" + s : s;
  const o = new Uint8Array(p.length / 2);
  for (let i = 0; i < o.length; i++) o[i] = parseInt(p.slice(i * 2, i * 2 + 2), 16);
  return o;
};
const _bytesToHex = b => Array.from(b, x => x.toString(16).padStart(2, "0")).join("");
const _concat = arrs => {
  let n = 0; for (const a of arrs) n += a.length;
  const o = new Uint8Array(n); let k = 0;
  for (const a of arrs) { o.set(a, k); k += a.length; }
  return o;
};
// RLP encodes quantities minimally: no leading zero bytes, and zero is empty.
const _numBytes = v => {
  const n = BigInt(v);
  if (n === 0n) return new Uint8Array(0);
  let s = n.toString(16); if (s.length % 2) s = "0" + s;
  return _hexToBytes(s);
};
function _rlpLen(len, offset) {
  if (len < 56) return Uint8Array.of(offset + len);
  const lb = _numBytes(len);
  return _concat([Uint8Array.of(offset + 55 + lb.length), lb]);
}
function _rlp(item) {
  if (Array.isArray(item)) {
    const body = _concat(item.map(_rlp));
    return _concat([_rlpLen(body.length, 0xc0), body]);
  }
  const b = item instanceof Uint8Array ? item : _hexToBytes(item);
  if (b.length === 1 && b[0] < 0x80) return b;
  return _concat([_rlpLen(b.length, 0x80), b]);
}

const _u = v => BigInt(v).toString(16).padStart(64, "0");
const _a = x => String(x || "").replace(/^0x/, "").toLowerCase().padStart(64, "0");
const _i = v => { const n = BigInt(v); return (n < 0n ? n + (1n << 256n) : n).toString(16).padStart(64, "0"); };
const _encBytes = h => {
  const x = String(h || "").replace(/^0x/, "");
  return _u(x.length / 2) + x.padEnd(Math.ceil(x.length / 64) * 64, "0");
};
function _unlockData(actionsHex, paramsHex) {
  const ab = _encBytes(actionsHex);
  let offs = "", blobs = "", cursor = 32 * paramsHex.length;
  for (const p of paramsHex) { offs += _u(cursor); const b = _encBytes(p); blobs += b; cursor += b.length / 2; }
  return _u(64) + _u(64 + ab.length / 2) + ab + _u(paramsHex.length) + offs + blobs;
}
function encodeModifyLiquidities(actionsHex, paramsHex, deadline) {
  const u = _unlockData(actionsHex, paramsHex);
  return "0xdd46508f" + _u(64) + _u(deadline) + _u(u.length / 2) + u;
}
const ACT_MINT_POSITION = "02";
const ACT_SETTLE_PAIR   = "0d";
// MINT_POSITION(PoolKey, tickLower, tickUpper, liquidity, amount0Max, amount1Max, owner, hookData)
const mintParam = (pk, tl, tu, L, max0, max1, owner) =>
  _a(pk.currency0) + _a(pk.currency1) + _u(pk.fee) + _i(pk.tickSpacing) + _a(pk.hooks)
  + _i(tl) + _i(tu) + _u(L) + _u(max0) + _u(max1) + _a(owner) + _u(384) + _u(0);
const settlePairParam = (c0, c1) => _a(c0) + _a(c1);

const SEL_BALANCE_OF = "70a08231";
const SEL_ALLOWANCE  = "dd62ed3e";
const SEL_APPROVE    = "095ea7b3";
const SEL_P2_ALLOWANCE = "927da105";
const SEL_P2_APPROVE   = "87517c45";
const MAX_U256 = (1n << 256n) - 1n;
const MAX_U160 = (1n << 160n) - 1n;
const MAX_U48  = (1n << 48n) - 1n;

function normPriv(k) {
  const x = String(k || "").replace(/^0x/, "").trim();
  if (!/^[0-9a-fA-F]{64}$/.test(x)) return null;
  const n = BigInt("0x" + x);
  const ORDER = 0xfffffffffffffffffffffffffffffffebaaedce6af48a03bbfd25e8cd0364141n;
  return (n === 0n || n >= ORDER) ? null : x;
}
const _PRIV_RAW = String(secret(PRIVATE_KEY, "PRIVATE_KEY") || "").trim();
const PRIV = normPriv(_PRIV_RAW);
const WALLET = PRIV
  ? ("0x" + CRYPTO.keccak256(CRYPTO.secp.getPublicKey(PRIV, false).slice(1)).slice(-40)).toLowerCase()
  : null;
const _ALCHEMY = secret(ALCHEMY_API_KEY, "ALCHEMY_API_KEY");
const ZAP_RPC = _ALCHEMY && /^[A-Za-z0-9_-]+$/.test(_ALCHEMY)
  ? "https://robinhood-mainnet.g.alchemy.com/v2/" + _ALCHEMY : "";
// Backup Alchemy keys (ALCHEMY_API_KEY_2, _3). zapRpc moves to the next one
// when a read stays rate limited on the current key, and later calls use it.
const ZAP_RPCS = [ZAP_RPC, ...[2, 3].map(i => secret("", `ALCHEMY_API_KEY_${i}`))
  .filter(k => k && /^[A-Za-z0-9_-]+$/.test(k))
  .map(k => "https://robinhood-mainnet.g.alchemy.com/v2/" + k)]
  .filter((u, i, a) => u && a.indexOf(u) === i);
let _zapIdx = 0;
const ZAP_READY = !!(PRIV && ZAP_RPC);
// What the table lacks for its open buttons, named by the row standing in
// for them.
const ZAP_MISSING = ZAP_READY ? null
  : !PRIV ? (_PRIV_RAW ? "a valid PRIVATE_KEY" : "PRIVATE_KEY")
  : "ALCHEMY_API_KEY";

// Used by the receipt poll in sendTx.
function pause(ms) { return new Promise(r => Timer.schedule(ms, false, r)); }

async function zapRpc(method, params) {
  if (!ZAP_RPC) throw new Error("ALCHEMY_API_KEY is required to zap");
  try { return await zapRpcAt(ZAP_RPCS[_zapIdx] || ZAP_RPC, method, params); }
  catch (e) {
    // Never for a broadcast: a send goes once, to one endpoint. A read that
    // stayed throttled or unreachable on this key tries the next key, which
    // every later call then uses.
    const m = String((e && e.message) || e);
    if (method === "eth_sendRawTransaction" || /revert|execution|insufficient|nonce/i.test(m)
        || _zapIdx + 1 >= ZAP_RPCS.length) throw e;
    _zapIdx += 1;
    return zapRpc(method, params);
  }
}
async function zapRpcAt(url, method, params) {
  const req = new Request(url);
  req.method = "POST";
  req.headers = { "Content-Type": "application/json" };
  req.body = JSON.stringify({ jsonrpc: "2.0", method, params, id: 1 });
  req.timeoutInterval = 30;

  // Alchemy enforces a per-second compute limit (300 CU/s on the free tier) and
  // rejects anything over it at the edge: HTTP 403 with a completely empty body,
  // never reaching the account logs. A burst across 69 pools passes that limit
  // easily, so this is a normal condition to ride out, not a failure. Retry with
  // growing backoff; only give up if it stays refused.
  let lastErr = null;
  for (let attempt = 0; attempt < RPC_RETRIES; attempt++) {
    if (attempt) await pause(RPC_BACKOFF_MS * Math.pow(2, attempt - 1));
    let body;
    try {
      body = await req.loadString();
    } catch (e) {
      lastErr = e;
      continue;
    }
    if (!body || body[0] !== "{") {
      // Empty body is the throttle signature. Keep waiting for it to clear.
      lastErr = new Error("rate limited by the RPC provider");
      continue;
    }
    const j = JSON.parse(body);
    if (j && j.error) throw new Error(j.error.message || "RPC error");
    return j.result;
  }
  throw new Error(String((lastErr && lastErr.message) || lastErr || "RPC unavailable"));
}

function _unsignedPayload(tx) {
  return _concat([Uint8Array.of(0x02), _rlp([
    _numBytes(tx.chainId), _numBytes(tx.nonce),
    _numBytes(tx.maxPriorityFeePerGas), _numBytes(tx.maxFeePerGas),
    _numBytes(tx.gasLimit), _hexToBytes(tx.to),
    _numBytes(tx.value || 0), _hexToBytes(tx.data || "0x"), [],
  ])]);
}
function signTx(tx, priv) {
  const sighash = CRYPTO.keccak256(_unsignedPayload(tx));
  const [sig, recovery] = CRYPTO.secp.signSync(sighash, priv, { recovered: true, der: false });
  const r = BigInt("0x" + _bytesToHex(sig.slice(0, 32)));
  const s = BigInt("0x" + _bytesToHex(sig.slice(32, 64)));
  const raw = _concat([Uint8Array.of(0x02), _rlp([
    _numBytes(tx.chainId), _numBytes(tx.nonce),
    _numBytes(tx.maxPriorityFeePerGas), _numBytes(tx.maxFeePerGas),
    _numBytes(tx.gasLimit), _hexToBytes(tx.to),
    _numBytes(tx.value || 0), _hexToBytes(tx.data || "0x"), [],
    _numBytes(recovery), _numBytes(r), _numBytes(s),
  ])]);
  return "0x" + _bytesToHex(raw);
}

// Writes always go to the configured private endpoint. The public nodes time
// out on eth_estimateGas, and racing endpoints for a broadcast risks submitting
// the same nonce twice.
async function rpcRaw(method, params, _try) {
  if (!RPC_KEYED) throw new Error("ALCHEMY_API_KEY is required to close positions");
  // Broadcasting is never retried. A send whose reply is lost may already have
  // been mined, so resending it either duplicates the transaction or comes back
  // as a confusing nonce error for work that actually succeeded.
  const isSend = method === "eth_sendRawTransaction";
  try {
    const req = new Request(RPC_KEYED);
    req.method = "POST";
    req.headers = { "Content-Type": "application/json" };
    req.body = JSON.stringify({ jsonrpc: "2.0", method, params, id: 1 });
    req.timeoutInterval = 30;
    // Throttled requests return an empty body. Safe to retry a read; never a
    // broadcast, whose lost reply may belong to a transaction already mined.
    let body = null, lastErr = null;
    const tries = isSend ? 1 : RPC_RETRIES;
    for (let attempt = 0; attempt < tries; attempt++) {
      if (attempt) await pause(RPC_BACKOFF_MS * Math.pow(2, attempt - 1));
      try {
        body = await req.loadString();
      } catch (e) { lastErr = e; body = null; if (isSend) throw e; continue; }
      if (body && body[0] === "{") break;
      lastErr = new Error("rate limited by the RPC provider");
      body = null;
    }
    if (!body) throw new Error(String((lastErr && lastErr.message) || "RPC unavailable"));
    const j = JSON.parse(body);
    if (j && j.error) {
      const m = String(j.error.message || "RPC error");
      // A revert is a real answer from the chain, not a transient fault, so it
      // is never retried. Rate limits and capacity errors are.
      if (!isSend && /rate limit|too many|capacity|timeout|429|503/i.test(m) && (_try || 0) < 3) {
        await pause(1500 * ((_try || 0) + 1));
        return rpcRaw(method, params, (_try || 0) + 1);
      }
      throw new Error(m);
    }
    return j.result;
  } catch (e) {
    const m = String((e && e.message) || e);
    // Network-level failures are worth a few attempts; reverts are not.
    if (!isSend && !/revert|execution|insufficient|ALCHEMY_API_KEY/i.test(m) && (_try || 0) < 3) {
      await pause(1500 * ((_try || 0) + 1));
      return rpcRaw(method, params, (_try || 0) + 1);
    }
    throw e;
  }
}

// ----------------------------- Uniswap API -----------------------------------
// Routing is delegated to Uniswap rather than guessed locally. This chain uses
// non-standard fee tiers (HOTDOG/WETH is fee 9000 spacing 90), so hand-built
// pool keys landed on initialised-but-empty pools and every swap reverted. The
// API knows the real pools and returns ready calldata.
const UNI_API = "https://trade-api.gateway.uniswap.org/v1";

async function uniPost(path, body) {
  const req = new Request(UNI_API + path);
  req.method = "POST";
  req.headers = {
    "x-api-key": UNISWAP_API_KEY,
    "Content-Type": "application/json",
    "Accept": "application/json",
  };
  req.body = JSON.stringify(body);
  req.timeoutInterval = 30;
  const txt = await req.loadString();
  let j = null;
  try { j = JSON.parse(txt); } catch (e) {}
  if (!j) throw new Error("Uniswap API: unreadable response");
  if (j.errorCode || j.detail) throw new Error(`Uniswap API: ${j.detail || j.errorCode}`);
  return j;
}

// EIP-712 digest for a Permit2 PermitSingle, as returned by /quote.
const _bare = h => String(h).replace(/^0x/, "");
const _kStr = str => _bare(keccak256("0x" + Array.from(String(str), c => c.charCodeAt(0).toString(16).padStart(2, "0")).join("")));
const _kHex = hex => _bare(keccak256("0x" + _bare(hex)));
function permitDigest(pd) {
  const d = pd.domain, v = pd.values;
  // This domain has no version field, matching what Permit2 declares.
  const DOMAIN = "EIP712Domain(string name,uint256 chainId,address verifyingContract)";
  const domainSep = _kHex(_kStr(DOMAIN) + _kStr(d.name) + _u(d.chainId) + _a(d.verifyingContract));
  const DETAILS = "PermitDetails(address token,uint160 amount,uint48 expiration,uint48 nonce)";
  const SINGLE  = "PermitSingle(PermitDetails details,address spender,uint256 sigDeadline)" + DETAILS;
  const det = v.details;
  const detHash = _kHex(_kStr(DETAILS) + _a(det.token) + _u(det.amount) + _u(det.expiration) + _u(det.nonce));
  const structHash = _kHex(_kStr(SINGLE) + detHash + _a(v.spender) + _u(v.sigDeadline));
  return _kHex("1901" + domainSep + structHash);
}
function signPermit(pd) {
  const digest = permitDigest(pd);
  const [sig, rec] = CRYPTO.secp.signSync(digest, PRIV, { recovered: true, der: false });
  return "0x" + _bytesToHex(sig) + (27 + rec).toString(16).padStart(2, "0");
}



const RPC_URL = "https://robinhood-rpc.publicnode.com";
const REFRESH_MIN = 15;
const WIDGET_BUDGET_MS = 18_000;

// Filters. A TVL floor is needed because fee yield divides by TVL: a near-empty
// pool with a few swaps shows an absurd rate that no position could realise.
// The market-cap floor is the widget Parameter ("500K", "2M"); see below.
const MCAP_DEFAULT = 1_000_000;   // USD, applies to the non-USDG token
let   MIN_MCAP = MCAP_DEFAULT;
// $5K since zap-v35 (friends asked for more choice; a 12% pool with $5.6K
// of liquidity was the example). Below it the fee yield still runs wild.
const MIN_TVL  = 5_000;           // USD

// Discovery: pages of 20 pools each, ranked by 24h volume. 10 is GeckoTerminal's
// hard cap (page 11 is refused), which today puts the cutoff near $500K/day for
// a V4 USDG pool. Besides it, only the 🔥 view's two sources add candidates
// (last hour's trending pools, pinned tokens; see HOT_DEFAULTS): no log scan,
// sibling expansion, or launch-age extrapolation runs behind the ranking.
//
// Widget mode: NEVER fetch GeckoTerminal (its rate limits + sleep gaps blow the
// ~30s budget). The widget uses whatever is cached from in-app opens. If nothing
// is cached, it shows a "tap to discover" message.
//
// In-app mode: refresh a few ranked pages per run so the interactive table
// becomes current quickly. The remaining pages stay available from cache.
const DISCOVERY_PAGES = 10;
const PAGES_PER_RUN   = config.runsInWidget ? 0 : 3;
// The ranked set only says which pools exist; every number shown is live from
// DexScreener and chain state, so it can age a while before it costs anything.
// A shorter TTL just makes more in-app opens pay the 15s of rate-limited paging.
const DISCOVERY_TTL_MS = 2 * 60 * 60 * 1000;
const GECKO_GAP_MS = 1500;
// How long a pool stays a candidate after it was last seen on any page. The
// ranking's page-10 cutoff sits near $500K/day and pools hover across it, so a
// pool is kept for days once seen; the live filters decide whether it shows.
const SEEN_TTL_MS = 3 * 24 * 60 * 60 * 1000;

// LP fee ceiling, in hundredths of a bip. Pools above it (there are hundreds a
// day at 94-98%) are honeypots, not fee tiers.
// Fee tiers are in hundredths of a bip, so 1_000_000 is 100%. A high fee is not
// bad for a liquidity provider: the fee is what you earn.
//
// Measured across every V4 USDG pool of 51 tokens on this chain (38 with a live
// fee read from StateView), pools sort into two groups with a clean gap:
//
//   fee tier    pools   traded 24h   TVL>=10K   median TVL
//   < 1%           12           12         12     $281,566
//   1-3%            4            4          4     $163,450
//   3-5%           13           13         11      $99,349
//   5-8%            5            5          5      $86,564
//   20-25%          1            1          1      $45,512   <- SHROOM, 381 txns
//   60%+            3            3          0            $32
//
// SHROOM at 20% is a real pool: $45K of liquidity and 381 trades in a day. The
// old 10% cap hid it. Above that, nothing exists until 60%+, where the pools
// hold about $32 and only the TVL floor is keeping them out anyway (they do
// trade, so "nobody routes through it" was never the right test).
//
// 15% since zap-v35, at friends' request: the busiest USDG pool for one token
// sat at 12% and was hidden by the old 10% cap. Still a deliberate ceiling:
// 20% SHROOM-shaped pools stay out. Raise to 500_000 (50%, mid-gap) to include
// them.
const MAX_LP_FEE = 150_000;

const USDG_ADDR  = "0x5fc5360d0400a0fd4f2af552add042d716f1d168";
const WETH_ADDR  = "0x0bd7d308f8e1639fab988df18a8011f41eacad73";
const ZERO_ADDR  = "0x0000000000000000000000000000000000000000";   // native ETH
// The V4 dex's own ranking rather than USDG's: all 200 slots are V4 pools, so
// the cutoff for a V4 USDG pool sits about 20% lower than in the USDG-token
// ranking, where half the slots go to pools on other DEXes.
const GECKO_POOLS = "https://api.geckoterminal.com/api/v2/networks/robinhood/dexes/uniswap-v4-robinhood/pools";
const DEX_PAIRS   = "https://api.dexscreener.com/latest/dex/pairs/robinhood/";
const DEX_CHAIN_URL = "https://dexscreener.com/robinhood";

// "🔥 Fee kenceng": the table's second view, for the small busy pools whose fee
// is running hardest right now. The ranked view hides them twice over: they
// rarely reach the top 200 by 24h volume, and when they do the $1M mcap and $5K
// TVL floors drop them. This view relaxes both floors (editable in the app),
// ranks by the last hour's fee pace, and adds two more ways in:
//   - GeckoTerminal's trending pools for the last hour, which is where a pool
//     with a burst of swaps shows up long before its 24h volume ranks it.
//   - Pinned tokens: pin one from its search result and its pools are fetched
//     on every refresh and always listed here, with no floors at all, the same
//     rule search follows. Pins live in a file next to the caches, so pasting a
//     new build over this script keeps them.
// The ranked view and the widget are unchanged: they still read only the rows
// that pass the normal floors.
// minPace is the 1h fee pace in %/day; 25 is where feeRateColor turns yellow.
const HOT_DEFAULTS = { minPace: 25, minMcap: 100_000, minTvl: 1_000 };
const GECKO_TRENDING = "https://api.geckoterminal.com/api/v2/networks/robinhood/trending_pools";
const TRENDING_PAGES = 2;
// Trending is about the last hour, so it goes stale fast; one refresh per ten
// minutes keeps GeckoTerminal's rate limit for the ranked pages.
const TRENDING_TTL_MS = 10 * 60 * 1000;
const TRENDING_SEEN_TTL_MS = 6 * 60 * 60 * 1000;
// Pins fetched per refresh, at one DexScreener call each (limit is 300/min).
const MAX_PINS = 25;

// Uniswap V4 on Robinhood Chain (4663)
const STATE_VIEW = "0xf3334192d15450cdd385c8b70e03f9a6bd9e673b";
const MULTICALL3 = "0xcA11bde05977b3631167028862bE2a173976CA11";
const GECKO_V4_DEX = "uniswap-v4-robinhood";

// Majors have no meaningful "market cap" from supply x price (WETH's supply is
// just what has been wrapped here), so they skip the mcap filter.
const MAJORS = { [ZERO_ADDR]: "ETH", [WETH_ADDR]: "WETH" };

const SEL = {
  getSlot0:     "c815641c",
  totalSupply:  "18160ddd",
  decimals:     "313ce567",
};

const COL = {
  text:   new Color("#F5F5FF"),
  muted:  new Color("#9595B2"),
  faint:  new Color("#6C6C89"),
  accent: new Color("#FF007A"),   // Uniswap pink
  yellow: new Color("#F5BD00"),
  green:  new Color("#30D158"),
  red:    new Color("#FF453A"),
  bgTop:  new Color("#1B1B2F"),
  bgBot:  new Color("#0D0D1A"),
};

// A 24h price shape drawn from what the row already holds. The current price
// and the 5m/1h/6h/24h changes give five past prices, so this costs no request
// and nothing to tap: the point is to glance and move on.
//
// Time runs logarithmically toward now. On a linear axis the last hour would
// occupy 4% of the width and a fresh dump, the thing most worth seeing, would
// be a single invisible pixel at the right edge.
const SPARK_LEGS = [[24, "chg24"], [6, "chg6h"], [1, "chg1h"], [5 / 60, "chg5m"], [0, null]];
function sparkPoints(r) {
  const now = Number(r && r.price);
  if (!(now > 0)) return null;
  if (r.baseAddr && String(r.baseAddr).toLowerCase() === USDG_ADDR) return null;
  const pts = [];
  for (const [hoursAgo, field] of SPARK_LEGS) {
    const chg = field == null ? 0 : r[field];
    if (chg == null) continue;
    // A -100% move would divide by zero, and anything at or below it is bad data.
    if (chg <= -99.9) continue;
    const past = now / (1 + chg / 100);
    if (!(past > 0) || !Number.isFinite(past)) continue;
    pts.push({ t: hoursAgo, p: past });
  }
  return pts.length >= 2 ? pts : null;
}

function sparkline(r, w, h) {
  try {
    return drawSpark(r, w, h);
  } catch (e) {
    return null;                 // no chart is fine; a broken table is not
  }
}

function drawSpark(r, w, h) {
  const pts = sparkPoints(r);
  if (!pts) return null;
  const dc = new DrawContext();
  dc.size = new Size(w, h);
  dc.opaque = false;
  dc.respectScreenScale = true;

  const padY = 3, padX = 1;
  const span = Math.log(1 + 24);
  const xs = pts.map(q => padX + (1 - Math.log(1 + q.t) / span) * (w - 2 * padX));
  const lo = Math.min(...pts.map(q => q.p)), hi = Math.max(...pts.map(q => q.p));
  // A flat line still has to sit somewhere: put it mid-box rather than divide by zero.
  const ys = pts.map(q => hi === lo ? h / 2
    : (h - padY) - ((q.p - lo) / (hi - lo)) * (h - 2 * padY));

  const first = pts[0].p, last = pts[pts.length - 1].p;
  const up = last >= first;
  const line = up ? COL.green : COL.red;

  // Fill under the line, so direction reads before the eye finds the stroke.
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

  // Where the price sits now, which is the "top or bottom" question.
  const cx = xs[xs.length - 1], cy = ys[ys.length - 1];
  dc.setFillColor(line);
  dc.fillEllipse(new Rect(cx - 2.4, cy - 2.4, 4.8, 4.8));
  return dc.getImage();
}

// USDG and ETH balance for the header. Two reads: an ERC-20 balanceOf for USDG
// and the native balance for gas. ETH is priced off the USDG/ETH pool the pool
// table already ranks, so no extra price source is needed.
async function fetchBalances() {
  const [rawUsdg, rawEth] = await Promise.all([
    zapRpc("eth_call", [{ to: USDG_ADDR, data: "0x70a08231" + hexAddr(WALLET) }, "latest"]),
    zapRpc("eth_getBalance", [WALLET, "latest"]),
  ]);
  const usdg = Number(BigInt(rawUsdg || "0x0")) / 1e6;
  const eth  = Number(BigInt(rawEth  || "0x0")) / 1e18;
  let ethUsd = null;
  try {
    const r = new Request("https://api.dexscreener.com/latest/dex/search?q=WETH%20USDG");
    r.timeoutInterval = 6;
    const j = await r.loadJSON();
    const p = (j.pairs || []).find(x => x.chainId === "robinhood" && Number(x.priceUsd) > 100);
    if (p) ethUsd = eth * Number(p.priceUsd);
  } catch (e) {}
  return { usdg, eth, ethUsd };
}

// Current gas price, and how it compares with the last ~100 blocks. Gas used by
// a zap is stable; the price is not, and it is what decides the cost: one
// 5-slice open cost $11.63 at 5.7 gwei against a table measured at 0.46. Showing
// the number alone would not say whether it is high, so the median comes too.
async function fetchGasNow() {
  try {
    const [blk, tipHex] = await Promise.all([
      zapRpc("eth_getBlockByNumber", ["latest", false]),
      zapRpc("eth_maxPriorityFeePerGas", []).catch(() => "0x0"),
    ]);
    const base = BigInt(blk.baseFeePerGas || "0x0");
    const gwei = Number(base + BigInt(tipHex || "0x0")) / 1e9;
    let spike = null;
    try {
      const h = await zapRpc("eth_feeHistory", ["0x64", "latest", []]);
      const b = (h.baseFeePerGas || []).map(x => Number(x) / 1e9).filter(x => x > 0);
      if (b.length > 4) {
        const med = [...b].sort((x, y) => x - y)[Math.floor(b.length / 2)];
        if (med > 0) spike = (Number(base) / 1e9) / med;
      }
    } catch (e) {}
    return { gwei, spike };
  } catch (e) { return null; }
}

// ----------------------------- ABI helpers ------------------------------------
function hexU256(n) {
  if (typeof n === "bigint") return n.toString(16).padStart(64, "0");
  return Math.floor(n).toString(16).padStart(64, "0");
}
function hexAddr(a) { return a.replace("0x", "").toLowerCase().padStart(64, "0"); }
function decU256(hex) { return BigInt("0x" + (hex || "0")); }
function decInt24(raw) { return raw >= 0x800000 ? raw - 0x1000000 : raw; }
function padCalldata(hex) {
  const bytes = hex.length / 2;
  const padded = Math.ceil(bytes / 32) * 32;
  return hex + "0".repeat((padded - bytes) * 2);
}

// ----------------------------- Multicall3 -------------------------------------
// tryAggregate(bool requireSuccess, (address target, bytes callData)[] calls)
function encodeTryAggregate(calls) {
  let head = "bce38bd7" + hexU256(0n) + hexU256(64n);
  const arr = hexU256(BigInt(calls.length));
  const tuples = calls.map(c => hexAddr(c.target) + hexU256(64n) + hexU256(BigInt(c.data.length / 2)) + padCalldata(c.data));
  let offsets = "", cur = calls.length * 32;
  for (const t of tuples) { offsets += hexU256(BigInt(cur)); cur += t.length / 2; }
  return "0x" + head + arr + offsets + tuples.join("");
}
function decodeTryAggregateResult(hex) {
  const r = hex.startsWith("0x") ? hex.slice(2) : hex;
  const arrOffset = Number(decU256(r.slice(0, 64))) * 2;
  const arrLen = Number(decU256(r.slice(arrOffset, arrOffset + 64)));
  const elemsStart = arrOffset + 64;
  const results = [];
  for (let i = 0; i < arrLen; i++) {
    const elemOffset = Number(decU256(r.slice(elemsStart + i * 64, elemsStart + (i + 1) * 64))) * 2;
    const abs = elemsStart + elemOffset;
    const success = Number(decU256(r.slice(abs, abs + 64))) !== 0;
    const dataOffset = Number(decU256(r.slice(abs + 64, abs + 128))) * 2;
    const dataAbs = abs + dataOffset;
    const dataLen = Number(decU256(r.slice(dataAbs, dataAbs + 64)));
    results.push({ success, data: r.slice(dataAbs + 64, dataAbs + 64 + dataLen * 2) });
  }
  return results;
}
async function rpc(method, params) {
  return rpcOnce(RPC_URL, method, params);
}
async function rpcOnce(url, method, params) {
  const req = new Request(url);
  req.method = "POST";
  req.headers = { "Content-Type": "application/json" };
  req.body = JSON.stringify({ jsonrpc: "2.0", method, params, id: 1 });
  req.timeoutInterval = config.runsInWidget ? 4 : 6;
  // Read-only. A widget refresh has very little time, so one quick retry only:
  // enough to ride out a brief throttle without risking the widget being killed.
  let body = await req.loadString().catch(() => null);
  if (!body || body[0] !== "{") {
    await pause(RPC_BACKOFF_MS);
    body = await req.loadString().catch(() => null);
  }
  if (!body || body[0] !== "{") throw new Error("rate limited by the RPC provider");
  const j = JSON.parse(body);
  if (j && j.result != null) return j.result;
  throw new Error((j && j.error && j.error.message) || (j && j.message) || "RPC error");
}
const ethCall = (to, data) => rpc("eth_call", [{ to, data }, "latest"]);
async function multicall(calls) {
  if (!calls.length) return [];
  return decodeTryAggregateResult(await ethCall(MULTICALL3, encodeTryAggregate(calls)));
}

// ----------------------------- zapping ---------------------------------------
// Send one prepared transaction: simulate, estimate, sign, broadcast, confirm.
// The simulation runs first and throws before anything is signed, so a call that
// cannot succeed never reaches the chain and costs nothing.
// Last known ETH price, kept so a transaction's fee can be shown in dollars.
let _ethUsd = null;
// Gas spent by every transaction in the current action, for a running total.
let _gasLog = [];
function feeText(gasUsed, gasPrice) {
  const wei = BigInt(gasUsed || 0) * BigInt(gasPrice || 0);
  if (wei === 0n) return `${BigInt(gasUsed || 0)} gas`;
  const eth = Number(wei) / 1e18;
  const usd = _ethUsd ? eth * _ethUsd : null;
  return `${BigInt(gasUsed || 0)} gas · ${eth.toFixed(6)} ETH`
       + (usd != null ? ` · ${fmtUsd(usd)}` : "");
}

let _nonceOverride = null;
async function sendTx(to, calldata, rep, label, opts) {
  const i = rep ? rep.start(label) : -1;
  try {
    return await _sendTxInner(to, calldata, rep, label, i, opts);
  } catch (e) {
    const m = String((e && e.message) || e);
    rep && rep.fail(i, /revert|execution/i.test(m) ? "would revert, not sent" : m.slice(0, 70));
    throw e;
  }
}
async function _sendTxInner(to, calldata, rep, label, i, opts) {
  rep && rep.detail(i, "simulating");
  try {
    await zapRpc("eth_call", [{ from: WALLET, to, data: calldata }, "latest"]);
  } catch (e) {
    // Simulation is trustworthy here: replaying a known-good swap at the block
    // before it was mined simulates OK, and only fails later because that
    // sender had already spent the tokens. So a revert in simulation is a real
    // revert, and sending anyway would just burn gas.
    throw e;
  }

  rep && rep.detail(i, "estimating gas");
  const [nonceHex, block, tipHex, gasHex] = await Promise.all([
    _nonceOverride != null ? Promise.resolve(null) : zapRpc("eth_getTransactionCount", [WALLET, "pending"]),
    zapRpc("eth_getBlockByNumber", ["latest", false]),
    zapRpc("eth_maxPriorityFeePerGas", []).catch(() => "0x0"),
    zapRpc("eth_estimateGas", [{ from: WALLET, to, data: calldata, value: "0x0" }]),
  ]);
  const nonce = _nonceOverride != null ? _nonceOverride : BigInt(nonceHex);
  const baseFee = BigInt(block.baseFeePerGas || "0x0");
  const tip = BigInt(tipHex || "0x0");
  const maxFee = (baseFee * BigInt(Math.round(MAX_FEE_MULT * 100)) / 100n) + tip;
  const gasLimit = BigInt(gasHex) * BigInt(Math.round(GAS_LIMIT_BUFFER * 100)) / 100n;

  if (DRY_RUN) {
    rep && rep.ok(i, `practice: would cost about ${feeText(gasLimit, maxFee)}`);
    return { dryRun: true, gasLimit, calldataBytes: calldata.length / 2 };
  }

  rep && rep.detail(i, "signing");
  const tx = {
    chainId: CHAIN_ID, nonce,
    maxPriorityFeePerGas: tip, maxFeePerGas: maxFee,
    gasLimit, to, value: 0n, data: calldata,
  };
  const raw = signTx(tx, PRIV);

  rep && rep.detail(i, "broadcasting");
  let hash;
  try {
    hash = await zapRpc("eth_sendRawTransaction", [raw]);
  } catch (e) {
    const m = String((e && e.message) || e);
    // "nonce too low" / "already known" mean this exact transaction is already
    // in flight or mined. Report that plainly instead of as a failure.
    if (/nonce too low|already known|already imported|replacement/i.test(m)) {
      rep && rep.ok(i, "already submitted; check the explorer for the result");
      return { hash: null, ok: true, alreadySent: true };
    }
    throw e;
  }
  rep && rep.detail(i, `sent ${hash.slice(0, 10)}… waiting`);
  // Chain the nonce locally so a follow-up in the same flow does not collide
  // with a transaction the node has not surfaced as pending yet.
  _nonceOverride = nonce + 1n;

  for (let n = 0; n < 90; n++) {
    await pause(2000);
    const rec = await zapRpc("eth_getTransactionReceipt", [hash]).catch(() => null);
    if (rec) {
      const ok = BigInt(rec.status || "0x0") === 1n;
      const price = rec.effectiveGasPrice || tx.maxFeePerGas || "0x0";
      const wei = BigInt(rec.gasUsed || 0) * BigInt(price);
      _gasLog.push({ label, wei });
      rep && (ok ? rep.ok(i, `${hash.slice(0, 10)}…  ${feeText(rec.gasUsed, price)}`)
                 : rep.fail(i, `reverted on chain ${hash.slice(0, 10)}…  ${feeText(rec.gasUsed, price)}`));
      return { hash, ok, gasUsed: rec.gasUsed, receipt: rec, feeWei: wei };
    }
    if (n % 5 === 4) rep && rep.detail(i, `waiting ${(n + 1) * 2}s  ${hash.slice(0, 10)}…`);
  }
  rep && rep.fail(i, `still pending after 3min ${hash.slice(0, 10)}…`);
  return { hash, pending: true };
}



// The discovery table knows a pool's id, its two tokens and its fee, but minting
// needs the whole PoolKey, and a pool id is a hash so it cannot be reversed.
//
// Two ways to get there. A quick local pass hashes common tick spacings and
// keeps whichever reproduces the id, which covers most pools without a network
// call. Anything else is read from the pool's own Initialize event, which
// carries every field. That fallback matters more than it sounds: this chain
// mints pools with arbitrary parameters, such as fee 40355 with tickSpacing
// 404, that no candidate list would ever contain, and it also recovers pools
// that do have a hook.
const TICK_SPACINGS = [1, 2, 5, 10, 20, 25, 30, 50, 60, 90, 100, 120, 150, 200,
                       250, 300, 350, 400, 500, 600, 750, 1000, 1500, 2000, 5000];

// Read the PoolKey from the pool's Initialize event, then confirm it by hashing
// it back to the pool id. Authoritative, and handles hooks and odd spacings.
// Only reached when the local solve fails, which is rare now that candidates are
// derived from the fee: the explorer rate-limits hard.
const INIT_TOPIC = "0xdd466e674ea557f56295e2d0218a125ea4b4f0f6f3307b95f85e6110838d6438";
const POOL_MANAGER_ADDR = "0x8366a39CC670B4001A1121B8F6A443A643e40951";
const EXPLORER_UA = "Mozilla/5.0 (iPhone; CPU iPhone OS 18_5 like Mac OS X) "
                  + "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.0 Mobile/15E148 Safari/604.1";

// Two Blockscout endpoints exist and they are not interchangeable:
//   hosted  api.blockscout.com/4663   metered, needs BLOCKSCOUT_API_KEY
//   self    robinhoodchain.blockscout.com   community instance, no auth
// The hosted one only accepts the key as an "apikey" query parameter; sent as
// an x-api-key header it answers 402, which is why an earlier header-based
// attempt registered no usage at all. Measured 2026-09-05: the community
// instance times out on this query (45s, no response), the hosted one answers
// in ~14s. Without a key we still use the community instance, so a friend
// running an unkeyed copy behaves exactly as before.
const EXPLORER_SELF = "https://robinhoodchain.blockscout.com";
const EXPLORER_HOSTED = "https://api.blockscout.com/4663";


// The keyed API can stop answering while the key itself is fine (402 "Out of
// credits" on 2026-09-26). Checked once per run and remembered for 30 minutes:
// while it refuses (401/402/403), explorer calls use the public instance.
let _hostedExplorerDown = false;
// The key in use: the first of BLOCKSCOUT_API_KEY, _2, _3 (all read from
// RH Secrets) that the keyed API still accepts. Backups are optional lines in
// RH Secrets; with none, this is simply BLOCKSCOUT_API_KEY.
let _explorerKey = null;
function blockscoutKeys() {
  const keys = [secret(BLOCKSCOUT_API_KEY, "BLOCKSCOUT_API_KEY")];
  for (let i = 2; i <= 9; i++) keys.push(secret("", `BLOCKSCOUT_API_KEY_${i}`));
  return keys.filter(Boolean);
}
function activeBlockscoutKey() {
  if (_hostedExplorerDown) return "";
  return _explorerKey != null ? _explorerKey : (blockscoutKeys()[0] || "");
}
async function checkHostedExplorer() {
  const keys = blockscoutKeys();
  if (!keys.length) return;
  const c = loadJson("explorer_health");
  if (c && Date.now() - (c.at || 0) < 30 * 60 * 1000 && c.n === keys.length) {
    _hostedExplorerDown = !!c.down;
    _explorerKey = c.down ? null : (keys[c.idx || 0] || keys[0]);
    return;
  }
  for (let i = 0; i < keys.length; i++) {
    try {
      const req = new Request(EXPLORER_HOSTED + "/api/v2/stats?apikey=" + encodeURIComponent(keys[i]));
      req.headers = { "User-Agent": EXPLORER_UA, "Accept": "application/json" };
      req.timeoutInterval = 8;
      await req.loadString();
      const code = req.response && req.response.statusCode;
      if (code === 401 || code === 402 || code === 403) continue;
      _explorerKey = keys[i]; _hostedExplorerDown = false;
      saveJson("explorer_health", { at: Date.now(), down: false, idx: i, n: keys.length, code });
      return;
    } catch (e) { return; /* offline: decide on the next run */ }
  }
  _explorerKey = null; _hostedExplorerDown = true;
  saveJson("explorer_health", { at: Date.now(), down: true, n: keys.length });
}

function explorerUrl(path) {
  const k = activeBlockscoutKey();
  if (!k) return EXPLORER_SELF + path;
  return EXPLORER_HOSTED + path + (path.includes("?") ? "&" : "?") + "apikey=" + encodeURIComponent(k);
}

function explorerHeaders() {
  return { "User-Agent": EXPLORER_UA, "Accept": "application/json" };
}

async function poolKeyFromInitialize(poolId) {
  const url = explorerUrl("/api?module=logs&action=getLogs"
            + "&fromBlock=1&toBlock=latest&address=" + POOL_MANAGER_ADDR
            + "&topic0=" + INIT_TOPIC + "&topic1=" + poolId + "&topic0_1_opr=and");
  let j = null;
  for (let attempt = 0; attempt < 3; attempt++) {
    const req = new Request(url);
    req.headers = explorerHeaders();
    req.timeoutInterval = 25;
    try {
      const body = await req.loadString();
      const status = req.response ? req.response.statusCode : 200;
      // Bursts get an empty 429 from the explorer; a short wait clears it.
      if (status === 429 || !body) { await new Promise(r => Timer.schedule(2500, false, r)); continue; }
      j = JSON.parse(body);
      if (j && j.result && j.result.length) break;
    } catch (e) {}
    await new Promise(r => Timer.schedule(1500, false, r));
  }
  const lg = (j && j.result || [])[0];
  if (!lg) return null;
  const d = String(lg.data || "").replace(/^0x/, "");
  const word = k => d.slice(k * 64, (k + 1) * 64);
  const toInt24 = n => (n & 0x800000) ? n - 0x1000000 : n;
  const pk = {
    currency0: "0x" + String(lg.topics[2]).slice(26),
    currency1: "0x" + String(lg.topics[3]).slice(26),
    fee: Number(BigInt("0x" + word(0))),
    tickSpacing: toInt24(Number(BigInt("0x" + word(1)) & 0xFFFFFFn)),
    hooks: "0x" + word(2).slice(24),
  };
  // Never trust the explorer blindly: the key must hash back to this pool id.
  const want = String(poolId).replace(/^0x/, "").toLowerCase();
  const got = keccak256(_a(pk.currency0) + _a(pk.currency1) + _u(pk.fee)
                      + _i(pk.tickSpacing) + _a(pk.hooks)).replace(/^0x/, "").toLowerCase();
  return got === want ? pk : null;
}

async function resolvePoolKey(poolId, c0, c1, fee) {
  const local = solvePoolKey(poolId, c0, c1, fee);
  if (local) return local;
  try { return await poolKeyFromInitialize(poolId); } catch (e) { return null; }
}

function solvePoolKey(poolId, c0, c1, fee) {
  const want = String(poolId).replace(/^0x/, "").toLowerCase();
  const hooks = "0x0000000000000000000000000000000000000000";
  // Most pools here set tickSpacing to a fixed fraction of the fee: 50000/500,
  // 9000/90 and 40355/404 are all fee/100. Deriving candidates from the fee
  // catches the odd values a fixed list never would, and keeps the resolve
  // local instead of leaning on an explorer that throttles.
  const derived = [100, 50, 200, 500, 20, 10].map(d => Math.round(fee / d));
  const candidates = [];
  for (const ts of derived.concat(TICK_SPACINGS)) {
    if (ts >= 1 && ts <= 32767 && candidates.indexOf(ts) === -1) candidates.push(ts);
  }
  for (const ts of candidates) {
    const enc = _a(c0) + _a(c1) + _u(fee) + _i(ts) + _a(hooks);
    if (keccak256(enc).replace(/^0x/, "").toLowerCase() === want) {
      return { currency0: c0, currency1: c1, fee, tickSpacing: ts, hooks };
    }
  }
  return null;
}

// Split the deposit into slices and place them entirely on one side of the price,
// so only the token being deposited is ever needed and nothing is bought at open.
const _sqrtAt = t => Math.pow(1.0001, t / 2);
// Ticks between the price and a drawdown of pct percent below it. 1.0001^n is
// the price ratio per tick, so a percentage is a distance.
function ticksBelow(pct) {
  const f = Math.min(99, Math.max(0, pct)) / 100;
  return f <= 0 ? 0 : Math.log(1 / (1 - f)) / Math.log(1.0001);
}

function planZap(poolKey, currentTick, depositRaw, depositIsC0, preset) {
  const sp = poolKey.tickSpacing;
// 100, raised from 20 on 2026-09-17.
//
// Measured with real calldata through eth_estimateGas rather than extrapolated:
// cost is linear at about 132,700 gas a slice on top of ~145k fixed, so the
// slice count is a shape decision and not a cost one. A 100-slice open is
// 13,477,245 gas -- $0.54 on Arc at 20 gwei, $3.26 on Robinhood Chain at 0.05
// gwei.
//
// What actually bounds it is the block, not the fee. On Arc that open is 45% of
// a 30M block; Robinhood Chain's limit is effectively unbounded. Re-entry is the
// tightest case, because it burns and re-mints every slice in ONE transaction:
// roughly 22M gas for 100 slices, about three quarters of an Arc block. 100 is
// therefore close to the practical ceiling on Arc, not a round number with room
// behind it.
  const slices = Math.max(1, Math.min(100, Math.floor(preset.slices)));
  // "top" holds the ladder back from the price: 0 starts it adjacent to the
  // market, 10 starts it a tenth below. The band is what lies between the two,
  // so a 10/60 ladder covers -10% to -60% rather than the whole way down.
  const topPct = Math.min(98, Math.max(0, Number(preset.top) || 0));
  const botPct = Math.min(99, Math.max(1, preset.range));
  const topTicks = Math.round(ticksBelow(topPct) / sp) * sp;
  const span = Math.max(sp, Math.round((ticksBelow(botPct) - ticksBelow(topPct)) / sp) * sp);

  // Depositing currency0 means the range must sit strictly above the price;
  // depositing currency1 means at or below it. A top offset pushes the near
  // edge further from the price, in whichever direction that is.
  const start = depositIsC0 ? Math.ceil((currentTick + 1) / sp) * sp + topTicks
                            : Math.floor(currentTick / sp) * sp - topTicks;

  // Slice boundaries, evenly spaced on the grid.
  const step = Math.max(sp, Math.round(span / slices / sp) * sp);
  const edges = [];
  for (let i = 0; i <= slices; i++) edges.push(depositIsC0 ? start + i * step : start - i * step);

  // "slope" puts the most at the far edge, matching the Zenith ladder.
  const weights = [];
  // Weight rises linearly with distance: slice i gets i+1 shares, so five slices
  // split 1:2:3:4:5. A single slice takes the lot.
  for (let i = 0; i < slices; i++) weights.push(i + 1);
  const wTotal = weights.reduce((a, b) => a + b, 0);

  const out = [];
  for (let i = 0; i < slices; i++) {
    const lo = depositIsC0 ? edges[i] : edges[i + 1];
    const hi = depositIsC0 ? edges[i + 1] : edges[i];
    const amount = Math.floor(Number(depositRaw) * weights[i] / wTotal);
    if (!(amount > 0)) continue;
    const sa = _sqrtAt(lo), sb = _sqrtAt(hi);
    if (!(sb > sa)) continue;
    const L = depositIsC0 ? amount / (1 / sa - 1 / sb) : amount / (sb - sa);
    if (!Number.isFinite(L) || !(L > 0)) continue;
    // Trim a billionth so the mint can never ask for more than was budgeted.
    out.push({ tickLower: lo, tickUpper: hi, amount: BigInt(amount),
               liquidity: BigInt(Math.floor(L * (1 - 1e-9))) });
  }
  return { slices: out, span, step, topTicks };
}

// ----------------------------- service fee -----------------------------------
// The public build charges 0.3 USDG per open, disclosed on the page it is
// downloaded from. SERVICE_FEE_WALLET is empty in the source and set only by
// tools/build-scripts.mjs for that build, so the owner's own copy charges
// nothing.
//
// It is not a separate transfer. A TAKE to the fee wallet inside the mint's own
// unlock deepens the USDG debt by exactly the fee, and the SETTLE_PAIR that
// funds the mint settles the full debt, so the fee is paid if and only if the
// positions are opened.
const SERVICE_FEE_WALLET = "";
const SERVICE_OPEN_FEE_RAW = 300_000n;    // 0.3 USDG, 6 decimals
const ACT_TAKE = "0e";
// TAKE(Currency currency, address recipient, uint256 amount)
const takeParam = (c, to, amount) => _a(c) + _a(to) + _u(amount);
function openFeeRaw() {
  const w = String(SERVICE_FEE_WALLET).replace(/^0x/, "").toLowerCase();
  if (!/^[0-9a-f]{40}$/.test(w)) return 0n;
  return ("0x" + w) === String(WALLET || "").toLowerCase() ? 0n : SERVICE_OPEN_FEE_RAW;
}

function buildZapCalldata(poolKey, plan, depositIsC0, recipient, deadline) {
  let actions = "";
  const params = [];
  for (const s of plan.slices) {
    actions += ACT_MINT_POSITION;
    params.push(mintParam(poolKey, s.tickLower, s.tickUpper, s.liquidity,
      depositIsC0 ? s.amount : 0n, depositIsC0 ? 0n : s.amount, recipient));
  }
  const fee = openFeeRaw();
  if (fee > 0n) {
    // Every pool this script opens is a USDG pair, but check rather than trust:
    // a TAKE of a currency the pair does not settle would strand the debt.
    const bare = a => String(a || "").replace(/^0x/, "").toLowerCase();
    const usdg = bare(USDG_ADDR);
    if (bare(poolKey.currency0) !== usdg && bare(poolKey.currency1) !== usdg) {
      throw new Error("not a USDG pool, so the open fee cannot be settled");
    }
    actions += ACT_TAKE;
    params.push(takeParam(USDG_ADDR, SERVICE_FEE_WALLET, fee));
  }
  actions += ACT_SETTLE_PAIR;
  params.push(settlePairParam(poolKey.currency0, poolKey.currency1));
  return encodeModifyLiquidities(actions, params, deadline);
}

// Minting pulls the deposit from the wallet, which goes through Permit2 with the
// PositionManager as spender. Both approvals are one-time per token.
async function ensureZapApprovals(tokenAddr, amount, rep) {
  const bare = String(tokenAddr).replace(/^0x/, "").toLowerCase();
  if (bare === String(ZERO_ADDR).replace(/^0x/, "")) return;   // native ETH
  const erc20 = BigInt(await zapRpc("eth_call", [{ to: "0x" + bare, data: "0x" + SEL_ALLOWANCE + _a(WALLET) + _a(PERMIT2) }, "latest"]).catch(() => "0x0"));
  if (erc20 < amount) {
    await sendTx("0x" + bare, "0x" + SEL_APPROVE + _a(PERMIT2) + _u(MAX_U256), rep, "Approve token");
  }
  const p2 = String(await zapRpc("eth_call", [{ to: PERMIT2, data: "0x" + SEL_P2_ALLOWANCE + _a(WALLET) + _a(bare) + _a(POSITION_MANAGER) }, "latest"]).catch(() => "0x")).replace(/^0x/, "");
  const amt = p2.length >= 64 ? BigInt("0x" + p2.slice(0, 64)) : 0n;
  const exp = p2.length >= 128 ? BigInt("0x" + p2.slice(64, 128)) : 0n;
  const now = BigInt(Math.floor(Date.now() / 1000));
  if (amt < amount || (exp !== 0n && exp < now)) {
    await sendTx(PERMIT2, "0x" + SEL_P2_APPROVE + _a(bare) + _a(POSITION_MANAGER) + _u(MAX_U160) + _u(MAX_U48), rep, "Approve Permit2");
  }
}

// ----------------------------- cache -----------------------------------------
const fm = FileManager.local();
// libraryDirectory, not cacheDirectory: the widget runs in its own extension
// process with its own cache directory, and only the library directory is
// shared with the app. With separate caches the widget never sees what an
// in-app run fetched, and the app never sees the widget's.
// ---------------------- market cap: one answer per address --------------------
// A market cap belongs to a token contract, never to a symbol or to one pool.
// Every row for one address must show the same figure, including when the chain
// reads fail.
//
// Pools of a single token disagree badly. Measured 2026-09-06: one PEZ contract's
// pools spanned 137%, one UNIPCS contract's 215%, STRATTON's 7,613%. Taking the
// single deepest pool by reported liquidity inherits all of that, and reported
// liquidity is not executable depth near a concentrated tick.
//
// So admit pools with real depth, take the unweighted median of the survivors,
// and report the spread rather than hiding it. Liquidity admits a pool; it never
// weights one, because weighting would give an imperfect measurement more
// authority than it has.
//
// Unlike the swap guard this never refuses: a table has to show something. It
// shows the midpoint and marks it uncertain.
const MCAP_MIN_POOL_LIQUIDITY_USD = 1000;
const MCAP_MIN_LIQUIDITY_SHARE = 0.05;
const MCAP_MAX_DISAGREEMENT_PERCENT = 5;

// candidates: [{ pairId, tokenAddr, px, liq }] -> { px, lowPx, highPx, pools, spreadPct, disagrees }
// Pure, and independent of the order candidates arrive in.
function mcapReference(candidates) {
  const ok = [];
  for (const c of candidates || []) {
    const px = Number(c && c.px);
    const liq = Number(c && c.liq);
    if (!Number.isFinite(px) || !(px > 0)) continue;
    if (!Number.isFinite(liq) || !(liq > 0)) continue;
    ok.push({ px, liq });
  }
  if (!ok.length) return null;
  const deepest = ok.reduce((a, c) => (c.liq > a ? c.liq : a), 0);
  const floor = Math.max(MCAP_MIN_POOL_LIQUIDITY_USD, deepest * MCAP_MIN_LIQUIDITY_SHARE);
  const kept = ok.filter(c => c.liq >= floor);
  // Nothing clears the floor only when every pool is dust; then the dust is all
  // there is, and saying so with a wide spread beats saying nothing.
  const use = kept.length ? kept : ok;
  const s = use.map(c => c.px).sort((a, b) => a - b);
  const m = Math.floor(s.length / 2);
  const px = s.length % 2 ? s[m] : (s[m - 1] + s[m]) / 2;
  const lowPx = s[0];
  const highPx = s[s.length - 1];
  const spreadPct = lowPx > 0 ? (highPx / lowPx - 1) * 100 : 0;
  return {
    px, lowPx, highPx, pools: s.length, spreadPct,
    disagrees: s.length > 1 && spreadPct > MCAP_MAX_DISAGREEMENT_PERCENT,
  };
}

// The last six hex characters of a contract address. A symbol is not an
// identity: UNIPCS is at least four separate contracts on this chain, and the
// public search caps at 30 pairs so colliding contracts can be invisible. Shown
// on every non-major token rather than only when a collision is detected,
// because a collision that is hidden cannot be detected.
function addrTag(addr) {
  const a = String(addr == null ? "" : addr).replace(/^0x/, "");
  return a.length >= 6 ? "…" + a.slice(-6) : "";
}

function cachePath(suffix) { return fm.joinPath(fm.libraryDirectory(), `rh_usdg_pools_${suffix}.json`); }
function saveJson(suffix, data) { try { fm.writeString(cachePath(suffix), JSON.stringify(data)); } catch (e) {} }
function loadJson(suffix) {
  try { const p = cachePath(suffix); if (fm.fileExists(p)) return JSON.parse(fm.readString(p)); } catch (e) {}
  return null;
}

// Market-cap floor: the widget Parameter ("500K", "2M", "1000000"), carried in
// the tap URL so the table applies the same floor, and remembered for opens
// straight from the app. Results are cached per floor so two widgets with
// different floors do not overwrite each other; discovery caches are shared.
function parseUsd(v) {
  if (v == null) return null;
  const m = String(v).trim().replace(/[$,\s]/g, "").match(/^(\d+(?:\.\d+)?)([kmb])?$/i);
  if (!m) return null;
  const n = Number(m[1]) * ({ k: 1e3, m: 1e6, b: 1e9 }[(m[2] || "").toLowerCase()] || 1);
  return n > 0 ? Math.round(n) : null;
}
function parsePoolFee(name) {
  const m = String(name || "").match(/(\d+(?:\.\d+)?)%\s*$/);
  if (!m) return null;
  const fee = Math.round(Number(m[1]) * 1e4);
  return fee > 0 && fee <= MAX_LP_FEE ? fee : null;
}
{
  const fromParam = parseUsd(args.widgetParameter);
  const fromQuery = parseUsd(args.queryParameters && args.queryParameters.mcap);
  MIN_MCAP = fromParam || fromQuery || MCAP_DEFAULT;
}
const DATA_SUFFIX = MIN_MCAP === MCAP_DEFAULT ? "data" : `data_${MIN_MCAP}`;
// Rows written before market cap became an address-level result can hold two
// different caps for one token. Stamped on save and required on load, so those
// cannot keep rendering; the cost is one refresh.
const MCAP_ROW_SCHEMA = 1;

// A higher-floor widget can be derived from the default cache immediately; it
// does not need a separate successful network run before it has something to
// render. The default cache already contains only rows at or above 1M.
function applyMcapFloor(data) {
  const majors = new Set(Object.values(MAJORS));
  // Same rule as the live path: the floor applies to the whole qualified range,
  // so a cap that could be above it is kept and marked rather than dropped on a
  // midpoint that the pools do not agree on.
  const rows = (data.rows || []).filter(r => majors.has(r.symbol)
    || (r.mcap != null && (r.mcapHigh == null ? r.mcap : r.mcapHigh) >= MIN_MCAP));
  const pcts = rows.map(r => r.feePctDay).sort((a, b) => a - b);
  return {
    ...data,
    rows,
    total: rows.length,
    medianPct: pcts.length ? pcts[Math.floor(pcts.length / 2)] : null,
    sumTvl: rows.reduce((s, r) => s + r.tvl, 0),
    sumVol24: rows.reduce((s, r) => s + r.vol24, 0),
  };
}
function loadDataCache() {
  const usable = c => c && c.v === MCAP_ROW_SCHEMA && c.data && c.data.rows;
  const direct = loadJson(DATA_SUFFIX);
  if (usable(direct) && direct.data.rows.length) return direct;
  if (MIN_MCAP > MCAP_DEFAULT) {
    const base = loadJson("data");
    if (usable(base)) {
      const filtered = applyMcapFloor(base.data);
      if (filtered.rows.length || !direct) return { t: base.t, data: filtered };
    }
  }
  return usable(direct) ? direct : null;
}

// One-time carry-over of the caches earlier versions wrote to cacheDirectory,
// which the widget could not see. Merged rather than copied, since the shared
// directory may already hold a partial refill. In-app only: a widget's own old
// cache is the stale one this fixes.
function migrateOldCache() {
  if (config.runsInWidget) return;
  const flag = fm.joinPath(fm.libraryDirectory(), "rh_usdg_pools_migrated");
  if (fm.fileExists(flag)) return;
  const old = s => { try { const p = fm.joinPath(fm.cacheDirectory(), `rh_usdg_pools_${s}.json`); return fm.fileExists(p) ? JSON.parse(fm.readString(p)) : null; } catch (e) { return null; } };
  const disc = old("discovery");
  if (disc) {
    const m = loadJson("discovery") || { pages: {} };
    for (const pg in disc.pages || {}) if (!m.pages[pg] || m.pages[pg].t < disc.pages[pg].t) m.pages[pg] = disc.pages[pg];
    m.seen = m.seen || {};
    for (const id in disc.seen || {}) if (!m.seen[id] || m.seen[id].seenAt < disc.seen[id].seenAt) m.seen[id] = disc.seen[id];
    saveJson("discovery", m);
  }
  const d = old("data"), cur = loadJson(DATA_SUFFIX);
  if (d && d.v === MCAP_ROW_SCHEMA && (!cur || cur.t < d.t)) saveJson(DATA_SUFFIX, d);
  try { fm.writeString(flag, "1"); } catch (e) {}
}

// ----------------------------- discovery --------------------------------------
async function fetchGeckoPage(page) {
  return fetchGeckoPools(`${GECKO_POOLS}?page=${page}&sort=h24_volume_usd_desc&include=base_token,quote_token`);
}
// Trending covers every dex on the chain; the dex filter below keeps V4 only.
async function fetchTrendingPage(page) {
  return fetchGeckoPools(`${GECKO_TRENDING}?page=${page}&duration=1h&include=base_token,quote_token`);
}
async function fetchGeckoPools(url) {
  const req = new Request(url);
  req.headers = { "Accept": "application/json;version=20230302" };
  req.timeoutInterval = 10;
  const body = await req.loadString();
  const status = req.response ? req.response.statusCode : 200;
  if (status === 429) return { retry: true };
  if (status !== 200) throw new Error(`GeckoTerminal HTTP ${status}`);
  const j = JSON.parse(body);
  const tokens = {};
  for (const inc of j.included || []) if (inc.type === "token") tokens[inc.id] = inc.attributes;
  const pools = [];
  for (const p of j.data || []) {
    const a = p.attributes, rel = p.relationships || {};
    if (!rel.dex || !rel.dex.data || rel.dex.data.id !== GECKO_V4_DEX) continue;
    const tok = ref => { const t = tokens[ref && ref.data && ref.data.id]; return t ? { addr: (t.address || "").toLowerCase(), sym: t.symbol || "?", dec: t.decimals != null ? Number(t.decimals) : 18 } : null; };
    const base = tok(rel.base_token), quote = tok(rel.quote_token);
    if (!base || !quote) continue;
    pools.push({
      id: (a.address || "").toLowerCase(), name: a.name || "", base, quote,
      created: a.pool_created_at ? Date.parse(a.pool_created_at) : null,
      mcap: Number(a.market_cap_usd) || Number(a.fdv_usd) || null,
      // Fallback numbers only, for a pool DexScreener has not indexed.
      reserve: Number(a.reserve_in_usd) || 0,
      vol24: Number(a.volume_usd && a.volume_usd.h24) || 0,
      vol1h: Number(a.volume_usd && a.volume_usd.h1) || 0,
    });
  }
  return { pools };
}

// Returns every cached V4 USDG pool after refreshing the stalest pages this run.
// A page that cannot be fetched keeps its cached copy, so a rate-limited run
// degrades to slightly older discovery rather than an empty widget.
async function discoverPools() {
  const cache = loadJson("discovery") || { pages: {} };
  const now = Date.now();
  const due = [];
  for (let p = 1; p <= DISCOVERY_PAGES; p++) {
    const c = cache.pages[p];
    if (!c || now - c.t > DISCOVERY_TTL_MS) due.push(p);
  }
  // Missing pages first, then the oldest.
  due.sort((a, b) => (cache.pages[a] ? cache.pages[a].t : 0) - (cache.pages[b] ? cache.pages[b].t : 0));
  let fetched = 0;
  const refreshPages = due.slice(0, PAGES_PER_RUN);
  for (let i = 0; i < refreshPages.length; i++) {
    const p = refreshPages[i];
    try {
      let r = await fetchGeckoPage(p);
      if (r.retry) { await sleep(5000); r = await fetchGeckoPage(p); }
      if (r.pools) { cache.pages[p] = { t: Date.now(), pools: r.pools }; fetched++; }
    } catch (e) {}
    if (i < refreshPages.length - 1) await sleep(GECKO_GAP_MS);
  }
  // Last hour's trending pools, for the 🔥 view. Same rate-limited host, so it
  // waits the same gap after the ranked pages and keeps its old copy on failure.
  if (PAGES_PER_RUN > 0 && (!cache.trending || now - cache.trending.t > TRENDING_TTL_MS)) {
    const got = [];
    let ok = false;
    for (let p = 1; p <= TRENDING_PAGES; p++) {
      if (refreshPages.length || p > 1) await sleep(GECKO_GAP_MS);
      try {
        let r = await fetchTrendingPage(p);
        if (r.retry) { await sleep(5000); r = await fetchTrendingPage(p); }
        if (!r.pools) break;
        ok = true;
        // USDG pairs only: anything else would only sit in the candidate set.
        got.push(...r.pools.filter(x => x.base.addr === USDG_ADDR || x.quote.addr === USDG_ADDR));
      } catch (e) { break; }
    }
    if (ok) { cache.trending = { t: Date.now(), pools: got }; fetched++; }
  }
  // Pools stay candidates for SEEN_TTL_MS after they were last seen on any
  // page, not only while they sit on a cached page. Pages are fetched on
  // different runs, so a pool near a page boundary can be on page 5 when page 4
  // is fetched and on page 4 when page 5 is, and appear on neither; a page the
  // rate limit refused leaves a hole the same way. The live filters drop
  // anything that has since died, so carrying a pool costs one lookup.
  const seen = cache.seen || {};
  for (const p in cache.pages) {
    const t = cache.pages[p].t;
    for (const pool of cache.pages[p].pools) {
      if (!seen[pool.id] || seen[pool.id].seenAt <= t) seen[pool.id] = { ...pool, seenAt: t };
      // On a ranked page now, so no longer on trending's shorter clock.
      else if (seen[pool.id].trending) seen[pool.id] = { ...pool, seenAt: seen[pool.id].seenAt };
    }
  }
  // A pool known only from trending is kept for hours rather than days: a
  // burst that has passed should not keep adding chain reads to every refresh.
  // Once a ranked page lists it, it is an ordinary candidate again.
  if (cache.trending) {
    const t = cache.trending.t;
    for (const pool of cache.trending.pools || []) {
      if (!seen[pool.id]) seen[pool.id] = { ...pool, seenAt: t, trending: true };
      else if (seen[pool.id].seenAt <= t) seen[pool.id] = { ...seen[pool.id], seenAt: t };
    }
  }
  for (const id in seen) {
    const ttl = seen[id].trending ? TRENDING_SEEN_TTL_MS : SEEN_TTL_MS;
    if (now - seen[id].seenAt > ttl) delete seen[id];
  }
  cache.seen = seen;
  if (fetched > 0) saveJson("discovery", cache);
  const oldest = Object.values(cache.pages).reduce((m, c) => Math.min(m, c.t), now);
  return { pools: Object.values(seen), pagesCached: Object.keys(cache.pages).length, oldest };
}

function sleep(ms) { return new Promise(r => Timer.schedule(ms, false, r)); }

// ----------------------------- pins (🔥 view) ---------------------------------
// { tokenAddr: { symbol, t } }, set from a search result. Kept beside the caches
// rather than in this script, so a new build pasted over the top keeps them.
function loadPins() {
  const p = loadJson("pins");
  return p && typeof p === "object" && !Array.isArray(p) ? p : {};
}
function savePins(p) { saveJson("pins", p); }

// A DexScreener pair as a discovery candidate, or null when it is not a USDG
// Uniswap V4 pool on this chain: the same test search applies. Fee tier and
// market cap are left to the chain reads every other candidate goes through.
function dexPairToCandidate(p) {
  if (!p || p.chainId !== "robinhood" || !p.pairAddress) return null;
  const v4 = String(p.dexId || "").includes("uniswap")
          && (p.labels || []).some(l => String(l).toLowerCase() === "v4");
  if (!v4) return null;
  const tok = t => ({ addr: String((t && t.address) || "").toLowerCase(), sym: (t && t.symbol) || "?", dec: 18 });
  const base = tok(p.baseToken), quote = tok(p.quoteToken);
  if (base.addr !== USDG_ADDR && quote.addr !== USDG_ADDR) return null;
  return {
    id: String(p.pairAddress).toLowerCase(), name: "", base, quote,
    created: p.pairCreatedAt || null,
    mcap: Number(p.marketCap) || Number(p.fdv) || null,
    reserve: Number(p.liquidity && p.liquidity.usd) || 0,
    vol24: Number(p.volume && p.volume.h24) || 0,
    vol1h: Number(p.volume && p.volume.h1) || 0,
  };
}

// Every USDG V4 pool of every pinned token. One call per token, all at once,
// because the token endpoint answers for at most 30 pairs however many tokens
// it is asked about. A token that fails just sits this refresh out.
async function pinnedPools(pins) {
  const addrs = Object.keys(pins || {}).slice(0, MAX_PINS);
  const lists = await Promise.all(addrs.map(async addr => {
    try {
      const req = new Request("https://api.dexscreener.com/latest/dex/tokens/" + addr);
      req.headers = { Accept: "application/json" };
      req.timeoutInterval = 10;
      const j = await req.loadJSON();
      return ((j && j.pairs) || []).map(dexPairToCandidate).filter(Boolean);
    } catch (e) { return []; }
  }));
  return [].concat(...lists);
}


// ----------------------------- live numbers -----------------------------------
// DexScreener uses the V4 poolId as its pair address; 30 ids per request, and
// the requests are independent so they all go out at once (limit is 300/min).
async function fetchMarket(ids) {
  const out = new Map();
  const batches = [];
  for (let i = 0; i < ids.length; i += 30) batches.push(ids.slice(i, i + 30));
  const bodies = await Promise.all(batches.map(async b => {
    const req = new Request(DEX_PAIRS + b.join(","));
    req.headers = { "Accept": "application/json" };
    req.timeoutInterval = 8;
    try { return await req.loadJSON(); } catch (e) { return null; }
  }));
  for (const j of bodies) {
    for (const p of (j && j.pairs) || []) {
      if (!p || !p.pairAddress) continue;
      out.set(p.pairAddress.toLowerCase(), {
        liq: (p.liquidity && Number(p.liquidity.usd)) || 0,
        vol24: (p.volume && Number(p.volume.h24)) || 0,
        vol6h: (p.volume && Number(p.volume.h6)) || 0,
        vol1h: (p.volume && Number(p.volume.h1)) || 0,
        txns24: p.txns && p.txns.h24 ? (Number(p.txns.h24.buys) || 0) + (Number(p.txns.h24.sells) || 0) : null,
        chg24: p.priceChange && p.priceChange.h24 != null ? Number(p.priceChange.h24) : null,
        chg6h: p.priceChange && p.priceChange.h6 != null ? Number(p.priceChange.h6) : null,
        chg1h: p.priceChange && p.priceChange.h1 != null ? Number(p.priceChange.h1) : null,
        chg5m: p.priceChange && p.priceChange.m5 != null ? Number(p.priceChange.m5) : null,
        created: p.pairCreatedAt || null,
        baseAddr: ((p.baseToken && p.baseToken.address) || "").toLowerCase(),
        quoteAddr: ((p.quoteToken && p.quoteToken.address) || "").toLowerCase(),
        mcap: Number(p.marketCap) || Number(p.fdv) || null,
        priceUsd: Number(p.priceUsd) || null,
        priceNative: Number(p.priceNative) || null,
      });
    }
  }
  return out;
}

// One multicall: slot0 per pool (tick + LP fee), supply + decimals per token.
async function fetchChain(pools, tokens) {
  const calls = [];
  for (const p of pools) calls.push({ target: STATE_VIEW, data: SEL.getSlot0 + p.id.slice(2) });
  for (const t of tokens) {
    calls.push({ target: t, data: SEL.totalSupply });
    calls.push({ target: t, data: SEL.decimals });
  }
  const res = await multicall(calls);
  const slot = new Map(), supply = new Map();
  pools.forEach((p, i) => {
    const r = res[i];
    if (!r || !r.success || r.data.length < 256) return;
    slot.set(p.id, {
      tick: decInt24(Number(decU256(r.data.slice(64, 128)) & 0xFFFFFFn)),
      lpFee: Number(decU256(r.data.slice(192, 256))),
    });
  });
  tokens.forEach((t, i) => {
    const s = res[pools.length + i * 2], d = res[pools.length + i * 2 + 1];
    if (!s || !s.success || s.data.length < 64) return;
    supply.set(t, { raw: decU256(s.data.slice(0, 64)), dec: d && d.success && d.data.length >= 64 ? Number(decU256(d.data.slice(0, 64))) : null });
  });
  return { slot, supply };
}

// ----------------------------- formatting ------------------------------------
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
  return v.toFixed(0);
}
// For filter labels: "1M", "10K" rather than "1.00M".
function fmtShort(v) { return fmtCompact(v).replace(/\.0+(?=[KMB]$)/, ""); }
// Default precision shrinks as the number grows: a launch-day pool can post
// four digits before the point, and "4679.95%" is no more informative than "4680%".
function fmtPct(v, digits) {
  if (v == null || isNaN(v)) return "--";
  if (digits == null) digits = v >= 100 ? 0 : v >= 10 ? 1 : 2;
  return v.toFixed(digits) + "%";
}
// LP fee in hundredths of a bip: 3000 -> "0.3%", 7800 -> "0.78%", 47990 -> "4.799%".
function fmtFeeTier(lpFee) {
  if (lpFee == null) return "?";
  return (lpFee / 1e4).toFixed(3).replace(/0+$/, "").replace(/\.$/, "") + "%";
}
// Colour thresholds for a fee rate, in %/day. Set against the measured spread
// of live pools on this chain (median %/day by fee tier: 3.3, 10.4, 62.5, 109
// for <1%, 1-3%, 3-5%, 5-10% tiers; the ranked table's top rows sit at 45-290):
//   green  >= 75   the top tier, worth entering now
//   yellow 25-75   decent, most working pools
//   red    <  25   not worth the gas to enter
const FEE_RATE_STRONG = 75, FEE_RATE_OK = 25;

// Entry distance thresholds. An Alert renders plain text only, so the state has
// to be carried by a coloured circle rather than by colouring the number.
// Absolute rather than relative to the grid: a 3% wait is a 3% wait whatever
// the pool's spacing, and waiting is what the reader is deciding about.
const ENTRY_NEAR_PCT = 1;   // green below this
const ENTRY_FAR_PCT  = 3;   // red above this
function entryDot(pct) {
  if (!(pct > 0)) return "\u{1F7E2}";
  if (pct < ENTRY_NEAR_PCT) return "\u{1F7E2}";
  return pct <= ENTRY_FAR_PCT ? "\u{1F7E1}" : "\u{1F534}";
}

function feeRateColor(pct) {
  if (pct == null || isNaN(pct)) return COL.faint;
  if (pct >= FEE_RATE_STRONG) return COL.green;
  if (pct >= FEE_RATE_OK) return COL.yellow;
  return COL.red;
}
function signColor(v) { return v == null || isNaN(v) ? COL.muted : v >= 0 ? COL.green : COL.red; }
// Age as a risk colour: under a day is a launch (its rate is scaled up from
// hours of history), a week old has settled, in between is still young.
const AGE_NEW_DAYS = 1, AGE_OLD_DAYS = 7;
function ageColor(days) {
  if (days == null || isNaN(days)) return COL.muted;
  return days < AGE_NEW_DAYS ? COL.red : days < AGE_OLD_DAYS ? COL.yellow : COL.green;
}
function fmtAge(days) {
  if (days == null || isNaN(days)) return "--";
  if (days < 1 / 24) return `${Math.max(1, Math.round(days * 1440))}m`;
  if (days < 1) return `${(days * 24).toFixed(1)}h`;
  if (days < 14) return `${days.toFixed(1)}d`;
  return `${Math.round(days / 7)}w`;
}
function fmtTime(ts) { return new Date(ts).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" }); }
function fmtTimeSec(ts) { return new Date(ts).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" }); }

// ----------------------------- widget UI -------------------------------------
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

function buildWidget(d, stale, dataTime) {
  const small = config.widgetFamily === "small";
  const w = baseWidget();
  w.url = "scriptable:///run/" + encodeURIComponent(Script.name())
    + (MIN_MCAP !== MCAP_DEFAULT ? "?mcap=" + MIN_MCAP : "");
  w.setPadding(12, 17, 8, 17);
  w.refreshAfterDate = new Date(Date.now() + REFRESH_MIN * 60 * 1000);

  const head = w.addStack();
  head.spacing = 0;
  head.centerAlignContent();
  const icon = head.addText("\u{1F984}");
  icon.font = Font.systemFont(16);
  head.addSpacer(6);
  const t = head.addText("USDG Pools");
  t.font = Font.boldSystemFont(13);
  t.textColor = COL.text;
  head.addSpacer();
  // Status lives in the header: red for a cached result, green for a live one.
  const tag = head.addText(stale ? `STALE ${fmtTime(dataTime)}` : `Updated ${fmtTime(Date.now())}`);
  tag.font = Font.semiboldSystemFont(10);
  tag.textColor = stale ? COL.red : COL.green;
  const total = d.total != null ? d.total : d.rows.length;

  w.addSpacer(8);

  const grid = w.addStack();
  grid.spacing = 0;
  grid.centerAlignContent();
  if (small) {
    cell(grid, "Pools", String(total), COL.text);
    grid.addSpacer();
    cell(grid, "Median %/d", fmtPct(d.medianPct, 1), feeRateColor(d.medianPct));
  } else {
    cell(grid, "Pools", String(total), COL.text);
    grid.addSpacer();
    cell(grid, "Median %/d", fmtPct(d.medianPct, 1), feeRateColor(d.medianPct));
    grid.addSpacer();
    cell(grid, "TVL", fmtCompact(d.sumTvl), COL.text);
    grid.addSpacer();
    // Spendable balance rather than aggregate volume: it answers whether another
    // position can be opened, which is the only question this widget leads to.
    // Falls back to volume when no key is set and there is no balance to show.
    if (d.balances && d.balances.usdg != null) {
      const maxPreset = Math.max(...ZAP_PRESETS.map(p => p.amount));
      cell(grid, "My USDG", fmtCompact(d.balances.usdg),
           d.balances.usdg < maxPreset ? COL.yellow : COL.green);
    } else {
      cell(grid, "Vol 24h", fmtCompact(d.sumVol24), COL.text);
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
    hdr.spacing = 0;
    hdr.centerAlignContent();
    col(hdr, "POOL", W.pool, LBL, COL.faint);
    hdr.addSpacer(GAP);
    col(hdr, "MCAP", W.mcap, LBL, COL.faint, true);
    hdr.addSpacer(GAP);
    col(hdr, "TVL", W.tvl, LBL, COL.faint, true);
    hdr.addSpacer(GAP);
    col(hdr, "VOL 24H", W.vol, LBL, COL.faint, true);
    hdr.addSpacer(GAP);
    col(hdr, "FEE%/D", W.fee, LBL, COL.faint, true);
    hdr.addSpacer(GAP);
    col(hdr, "AGE", W.age, LBL, COL.faint, true);
    w.addSpacer(2);

    for (let i = 0; i < list.length; i++) {
      const r = list[i];
      const row = w.addStack();
      row.spacing = 0;
      row.centerAlignContent();
      col(row, r.label, W.pool, DAT, COL.text);
      row.addSpacer(GAP);
      col(row, r.mcap == null ? "--"
        : (r.mcapUncertain ? "~" : "") + fmtCompact(r.mcap), W.mcap, DAT, COL.muted, true);
      row.addSpacer(GAP);
      col(row, fmtCompact(r.tvl), W.tvl, DAT, COL.text, true);
      row.addSpacer(GAP);
      col(row, fmtCompact(r.vol24), W.vol, DAT, COL.text, true);
      row.addSpacer(GAP);
      col(row, fmtPct(r.feePctDay), W.fee, DAT, feeRateColor(r.feePctDay), true);
      row.addSpacer(GAP);
      col(row, fmtAge(r.ageDays), W.age, DAT, ageColor(r.ageDays), true);
      if (i < list.length - 1) w.addSpacer(5);
    }
  }

  w.addSpacer();
  const foot = w.addText(`24h volume · mcap ≥ ${fmtShort(MIN_MCAP)} · TVL ≥ ${fmtShort(MIN_TVL)} · ${VERSION}`);
  foot.font = Font.systemFont(9);
  foot.textColor = COL.faint;
  return w;
}

function errorWidget(msg) {
  const w = baseWidget();
  w.setPadding(16, 17, 16, 17);
  const head = w.addStack();
  head.centerAlignContent();
  const icon = head.addText("\u{1F984}");
  icon.font = Font.systemFont(16);
  head.addSpacer(6);
  const t = head.addText("USDG Pools");
  t.font = Font.boldSystemFont(14);
  t.textColor = COL.text;
  w.addSpacer(6);
  const m = w.addText(msg);
  m.font = Font.systemFont(11);
  m.textColor = COL.muted;
  return w;
}

// -------------------------------- main ---------------------------------------
async function main() {
  migrateOldCache();
  await checkHostedExplorer();
  let data = null, stale = false, dataTime = Date.now(), error = null;
  try {
    const disc = await discoverPools();
    // Pinned tokens join the candidates on every in-app refresh, whatever the
    // rankings say. Discovery's copy of a pool goes first so its name, which
    // carries the fee tier as a fallback, wins the dedupe.
    const pins = loadPins();
    const pinned = config.runsInWidget ? [] : await pinnedPools(pins).catch(() => []);
    const seen = new Set(), pools = [];
    for (const p of disc.pools.concat(pinned)) {
      if (seen.has(p.id) || (p.base.addr !== USDG_ADDR && p.quote.addr !== USDG_ADDR)) continue;
      seen.add(p.id);
      pools.push(p);
    }
    if (!pools.length) {
      if (config.runsInWidget)
        throw new Error("No cached pools. Tap widget to discover.");
      throw new Error("No pools from discovery (rate limited?)");
    }

    const tokenOf = p => p.base.addr === USDG_ADDR ? p.quote : p.base;
    const tokens = [...new Set(pools.map(p => tokenOf(p).addr).filter(a => a !== ZERO_ADDR))];
    const [market, chain] = await Promise.all([
      fetchMarket(pools.map(p => p.id)).catch(() => new Map()),
      fetchChain(pools, tokens).catch(() => ({ slot: new Map(), supply: new Map() })),
    ]);

    const now = Date.now();

    // One market cap per token address, decided before any row exists.
    //
    // A row must never reach for its own pool's reported figure. That is what
    // let two pools of one token show 3.39M and 4.48M: when the chain reads
    // failed there was no entry for the address, and each row fell through to
    // whatever its own pool happened to report.
    //
    // Asking DexScreener per token cannot work here either: its /tokens/
    // endpoint returns at most 30 pairs however many are requested, so a batch
    // of 25 silently answers for only a few. The tick is already fetched for
    // every pool and reproduces DexScreener's price to four significant figures.
    const decByAddr = new Map();
    for (const p of pools) {
      const tok = tokenOf(p);
      if (tok.addr === ZERO_ADDR) continue;
      const sp = chain.supply.get(tok.addr);
      decByAddr.set(tok.addr, sp && sp.dec != null ? sp.dec : tok.dec);
    }

    const mcapByAddr = (() => {
      const cands = new Map();     // address -> price candidates
      const reported = new Map();  // address -> caps from pools that name it base
      const push = (m, k, v) => { if (!m.has(k)) m.set(k, []); m.get(k).push(v); };
      for (const p of pools) {
        const tok = tokenOf(p);
        if (tok.addr === ZERO_ADDR) continue;
        const mm = market.get(p.id);
        const liq = mm ? mm.liq : p.reserve;
        if (!(liq > 0)) continue;
        const dc = decByAddr.get(tok.addr);
        const sl = chain.slot.get(p.id);
        // On-chain tick first. A correctly oriented market price is only a
        // fallback candidate, so losing the chain degrades the source of the
        // answer without letting rows of one address disagree.
        let px = null;
        if (sl) {
          const isC1 = tok.addr > USDG_ADDR;
          const t = Math.pow(1.0001, isC1 ? -sl.tick : sl.tick) * Math.pow(10, dc - 6);
          if (Number.isFinite(t) && t > 0) px = t;
        }
        if (px == null && mm) {
          if (mm.baseAddr === tok.addr && mm.priceUsd > 0) px = mm.priceUsd;
          else if (mm.quoteAddr === tok.addr && mm.priceNative > 0) px = mm.priceUsd / mm.priceNative;
        }
        if (px != null && Number.isFinite(px) && px > 0) push(cands, tok.addr, { pairId: p.id, tokenAddr: tok.addr, px, liq });
        // A reported cap counts only from a pool that names this token as its
        // base. On a USDG pair the base is often the stablecoin, and taking its
        // cap would print USDG's market cap under the token's symbol.
        if (mm && mm.mcap != null && mm.baseAddr === tok.addr && liq > 0) {
          push(reported, tok.addr, { cap: Number(mm.mcap), liq });
        }
      }

      const out = new Map();
      for (const addr of decByAddr.keys()) {
        const sup = chain.supply.get(addr);
        const dec = decByAddr.get(addr);
        const ref = mcapReference(cands.get(addr) || []);
        if (sup && ref) {
          const units = Number(sup.raw) / Math.pow(10, dec);
          out.set(addr, {
            mcap: units * ref.px, low: units * ref.lowPx, high: units * ref.highPx,
            pools: ref.pools, uncertain: ref.disagrees, source: "supply",
          });
          continue;
        }
        // No supply: an address-level median of the caps qualified pools report
        // for this token, never a single row's own figure.
        const rc = reported.get(addr) || [];
        const q = mcapReference(rc.map(r => ({ px: r.cap, liq: r.liq })));
        if (q) {
          out.set(addr, { mcap: q.px, low: q.lowPx, high: q.highPx,
                          pools: q.pools, uncertain: q.disagrees, source: "reported" });
          continue;
        }
        out.set(addr, { mcap: null, low: null, high: null, pools: 0, uncertain: true, source: "none" });
      }
      return out;
    })();

    // rows: the ranked view and the widget, which keep the normal floors exactly
    // as before. hotRows: every pool with money in it, for the 🔥 view, which
    // applies its own lower floors when it is shown so they can be changed in
    // the app without another network run.
    const rows = [], hotRows = [];
    for (const p of pools) {
      const tok = tokenOf(p);
      const s = chain.slot.get(p.id);
      const lpFee = s ? s.lpFee : parsePoolFee(p.name);
      if (!lpFee) continue;
      const m = market.get(p.id);
      const tvl = m ? m.liq : p.reserve;
      const vol24 = m ? m.vol24 : p.vol24;
      const vol1h = m ? m.vol1h : p.vol1h;
      // Fee yield divides by TVL, so an empty pool has no row in either view.
      if (!(tvl > 0)) continue;
      let ranked = tvl >= MIN_TVL;

      // Token price from the tick. Pool price is currency1 per currency0 in raw
      // units; the token is currency1 when its address sorts after USDG's.
      const sup = chain.supply.get(tok.addr);
      const dec = sup && sup.dec != null ? sup.dec : tok.dec;
      const tokIsC1 = tok.addr !== ZERO_ADDR && tok.addr > USDG_ADDR;
      let price = s ? Math.pow(1.0001, tokIsC1 ? -s.tick : s.tick) * Math.pow(10, dec - 6) : null;
      if (price == null && m) {
        if (m.baseAddr === tok.addr) price = m.priceUsd;
        else if (m.quoteAddr === tok.addr && m.priceNative) price = m.priceUsd / m.priceNative;
      }
      const major = MAJORS[tok.addr];
      // The one answer for this address, decided above. Rows never compute their
      // own: two pools of one token showing different caps is the bug this
      // replaces, and it appeared only when the chain reads failed.
      const mr = mcapByAddr.get(tok.addr) || { mcap: null, low: null, high: null, uncertain: true };
      const mcap = major ? null : mr.mcap;
      const mcapLow = major ? null : mr.low;
      const mcapHigh = major ? null : mr.high;
      const mcapUncertain = major ? false : !!mr.uncertain;
      // The floor is applied to the whole qualified range, not to a point. A
      // range wholly below it cannot qualify; one wholly above it clearly does;
      // one straddling it is shown and marked rather than decided arbitrarily.
      // Unknown is excluded, because the script cannot show that it passes.
      if (!major) {
        if (mcap == null) ranked = false;
        else if ((mcapHigh == null ? mcap : mcapHigh) < MIN_MCAP) ranked = false;
      }

      // A zero LP fee means either a genuinely free pool or a hook that prices
      // each swap itself; neither has an on-chain fee to rank by, so skip. Above
      // MAX_LP_FEE no swap would route through it, so a position would sit idle.
      if (lpFee > MAX_LP_FEE) continue;
      const feeFrac = lpFee / 1e6;
      const fees24 = vol24 * feeFrac;
      const created = (m && m.created) || p.created;
      const ageDays = created ? (now - created) / 86400000 : null;
      const _c0 = p.base.addr < p.quote.addr ? p.base.addr : p.quote.addr;
      const _c1 = p.base.addr < p.quote.addr ? p.quote.addr : p.base.addr;
      const row = {
        id: p.id,
        currency0: _c0,
        currency1: _c1,
        tokenAddr: tok.addr,
        symbol: major || tok.sym,
        tier: fmtFeeTier(lpFee),
        label: `${major || tok.sym} ${fmtFeeTier(lpFee)}`,
        lpFee,
        mcap, mcapLow, mcapHigh, mcapUncertain, tvl, vol24, vol1h,
        fees24,
        feePctDay: (fees24 / tvl) * 100,
        feePctDay1h: (vol1h * 24 * feeFrac / tvl) * 100,
        txns24: m ? m.txns24 : null,
        chg24: m ? m.chg24 : null,
        baseAddr: m ? m.baseAddr : null,
        chg6h: m ? m.chg6h : null,
        chg1h: m ? m.chg1h : null,
        chg5m: m ? m.chg5m : null,
        price: m ? m.priceUsd : null,
        price,
        ageDays,
        live: !!m,
        pinned: !!pins[tok.addr],
      };
      hotRows.push(row);
      if (ranked) rows.push(row);
    }
    rows.sort((a, b) => b.feePctDay - a.feePctDay || b.vol24 - a.vol24);
    hotRows.sort((a, b) => b.feePctDay1h - a.feePctDay1h || b.feePctDay - a.feePctDay);

    const pcts = rows.map(r => r.feePctDay).sort((a, b) => a - b);
    const medianPct = pcts.length ? pcts[Math.floor(pcts.length / 2)] : null;
    // Rows are text only (no range bar), about 15.5pt each; the chrome above and
    // below them measures about 110pt on a large widget.
    const CHROME = 110, PITCH = 15.5;
    const rowBudget = config.widgetFamily === "small" ? 0
      : Math.max(0, Math.min(20, Math.floor((widgetSize().h - CHROME) / PITCH)));

    data = {
      rows, hotRows, medianPct, total: rows.length,
      sumTvl: rows.reduce((s, r) => s + r.tvl, 0),
      sumVol24: rows.reduce((s, r) => s + r.vol24, 0),
      candidates: pools.length, pagesCached: disc.pagesCached, discoveryOldest: disc.oldest,
      rowBudget,
      // Spendable balance for the header. Only fetched when a key is present,
      // and a failure here must not lose the pool table, so it is caught.
      balances: WALLET ? await fetchBalances().catch(() => null) : null,
      gas: ZAP_RPC ? await fetchGasNow() : null,
    };
    saveJson(DATA_SUFFIX, { t: Date.now(), v: MCAP_ROW_SCHEMA, data });
  } catch (e) {
    const c = loadDataCache();
    if (c) { data = c.data; stale = true; dataTime = c.t; error = String((e && e.message) || e).slice(0, 80); }
    else { const msg = "Couldn't load pools: " + String((e && e.message) || e).slice(0, 120); return { widget: errorWidget(msg), data: null, message: msg }; }
  }
  return { widget: buildWidget({ ...data, rows: data.rows.slice(0, data.rowBudget || 6) }, stale, dataTime), data, stale, dataTime, error };
}

// ----------------------------- interactive table ------------------------------
// One line under the header saying how current the rows are: yellow while a
// cached result is shown and the fetch is in flight, red when the fetch failed
// and the cached result is all there is, green when the rows are live. A tap
// on it fetches again.
function statusRow(t, kind, text, onTap, gas) {
  const r = new UITableRow();
  r.height = 22;
  const c = r.addText(`●  ${text}`);
  c.titleFont = Font.semiboldSystemFont(11);
  c.titleColor = kind === "refreshing" ? COL.yellow : kind === "fresh" ? COL.green : COL.red;
  if (gas && gas.gwei != null) {
    c.widthWeight = 68;
    // Own cell, right-aligned: the status text varies in length and would
    // otherwise truncate this off the end of the row.
    const g = r.addText(`⛽️ ${gas.gwei.toFixed(2)} gwei`);
    g.widthWeight = 32;
    g.rightAligned();
    g.titleFont = Font.semiboldSystemFont(11);
    // Red when a transaction now costs several times what it usually does.
    g.titleColor = gas.spike == null ? COL.muted
                 : gas.spike >= 3   ? COL.red
                 : gas.spike >= 1.5 ? COL.yellow
                                    : COL.green;
  }
  if (onTap) { r.onSelect = onTap; r.dismissOnSelect = false; }
  t.addRow(r);
}
function statusText(stale, dataTime, refreshing, error) {
  if (refreshing) return `Refreshing…   ·   showing data from ${fmtTimeSec(dataTime)}`;
  if (stale) return `Stale   ·   data from ${fmtTimeSec(dataTime)}   ·   fetch failed${error ? " (" + error + ")" : ""}   ·   tap to retry`;
  return `Updated ${fmtTimeSec(Date.now())}   ·   tap to refresh`;
}

// Fills (or refills) a table in place, so the same one can open on cached rows
// and be reloaded with fresh ones. "refreshing" is that first, cached state.
function fillTable(t, d, stale, dataTime, refreshing, error, onRefresh, _sortCol, _sortMode, _onSort, onZap, busy, progress, onHot) {
  t.removeAllRows();
  t.showSeparators = true;
  // The 🔥 view is about the current pace, so its headline is the fastest 1h
  // pace rather than whichever row the chosen sort happens to put first.
  const paceKey = d.hot ? "feePctDay1h" : "feePctDay";
  const top = d.hot
    ? d.rows.reduce((a, r) => (!a || r.feePctDay1h > a.feePctDay1h ? r : a), null)
    : d.rows[0];

  const header = new UITableRow();
  header.isHeader = true;
  header.height = 72;
  const hCell = header.addText(
    d.tokenView ? `${d.rows.length} ${d.tokenView} pools`
      : d.hot ? `🔥 ${d.rows.length} pools` : `${d.rows.length} pools`,
    d.tokenView
      // A token lookup is deliberate, so it shows every V4 USDG pool for that
      // token rather than applying the ranked table's mcap and TVL floors.
      // Thin pools included: the zap confirmation is where a small pool is
      // called out, with the deposit as a percentage of what it holds.
      ? `Uniswap V4 USDG pools for ${d.tokenView} · every pool, no floors`
      : d.hot
      ? `1h ≥ ${fmtPct(d.hot.minPace, 0)}/d · mcap ≥ ${fmtShort(d.hot.minMcap)} · TVL ≥ ${fmtShort(d.hot.minTvl)}`
      : `Uniswap V4 · USDG pairs · mcap ≥ ${fmtShort(MIN_MCAP)} · TVL ≥ ${fmtShort(MIN_TVL)}`
  );
  hCell.widthWeight = 55;
  hCell.titleFont = Font.boldSystemFont(20);
  hCell.subtitleFont = Font.systemFont(12);
  hCell.subtitleColor = COL.muted;
  const hTop = header.addText(top ? `${top[paceKey].toFixed(1)}%/day` : "--",
    top ? `${d.hot ? "best 1h pace" : "best"} · ${top.label}` : "");
  hTop.widthWeight = 45;
  hTop.rightAligned();
  hTop.titleFont = Font.boldSystemFont(20);
  hTop.titleColor = top ? feeRateColor(top[paceKey]) : COL.muted;
  hTop.subtitleFont = Font.systemFont(12);
  hTop.subtitleColor = COL.muted;
  header.onSelect = () => Safari.open(DEX_CHAIN_URL);
  header.dismissOnSelect = false;
  t.addRow(header);

  // What is actually spendable, so the preset amounts can be read against it
  // rather than guessed at. Populated by refreshBalances(); absent on the very
  // first paint from cache, in which case the row is skipped entirely.
  if (WALLET && d.balances) {
    const bal = new UITableRow();
    bal.height = 40;
    const usdg = d.balances.usdg, eth = d.balances.eth;
    const bl = bal.addText("Wallet", WALLET.slice(0, 6) + "…" + WALLET.slice(-4));
    bl.widthWeight = 40;
    bl.titleFont = Font.semiboldSystemFont(15);
    bl.subtitleFont = Font.systemFont(11);
    bl.subtitleColor = COL.faint;
    const br = bal.addText(
      `${usdg == null ? "--" : fmtUsd(usdg)} USDG`,
      `${eth == null ? "--" : eth.toFixed(4)} ETH${d.balances.ethUsd != null ? ` · ${fmtUsd(d.balances.ethUsd)}` : ""}`
    );
    br.widthWeight = 60;
    br.rightAligned();
    br.titleFont = Font.boldSystemFont(16);
    // Red when the largest preset no longer fits, so a doomed tap is obvious
    // before the confirmation dialog rather than after a revert.
    const maxPreset = Math.max(...ZAP_PRESETS.map(p => p.amount));
    br.titleColor = usdg == null ? COL.muted : usdg < maxPreset ? COL.yellow : COL.green;
    br.subtitleFont = Font.systemFont(11);
    br.subtitleColor = COL.faint;
    bal.dismissOnSelect = false;
    t.addRow(bal);
  }

  statusRow(t, refreshing ? "refreshing" : stale ? "stale" : "fresh", statusText(stale, dataTime, refreshing, error), onRefresh, d.gas);

  // Read-only without a key. This row stands where the open buttons would be,
  // so their absence reads as a setting rather than a broken table.
  if (!onZap && ZAP_MISSING) {
    const kr = new UITableRow();
    kr.height = 44;
    const kc = kr.addText("⚡️ Open buttons are off", `Add ${ZAP_MISSING} to RH Secrets to open positions.`);
    kc.titleFont = Font.semiboldSystemFont(14);
    kc.titleColor = COL.yellow;
    kc.subtitleFont = Font.systemFont(11);
    kc.subtitleColor = COL.muted;
    t.addRow(kr);
  }

  // One way in for anything that names a pool: poolId, token address, token
  // name, or a DexScreener link. While a result is showing it becomes the way
  // back out, so the same control never means two things at once.
  // Beside it: the 🔥 view's switch, or on a search result its pin, which
  // makes that token's pools join the 🔥 view on every refresh.
  const hotBtn = !onHot ? null
    : d.tokenView ? (d.tokenAddr ? (d.pinned ? "\u2605 Pinned" : "\u2606 Pin to \u{1F525}") : null)
    : (d.hot ? "\u{1F525} ON \u00B7 edit" : "\u{1F525} Fee kenceng");
  if (onZap || hotBtn) {
    const sr = new UITableRow();
    sr.height = 40;
    if (onZap) {
      const sb = sr.addButton(d.tokenView
        ? `\u2715  ${d.tokenView}\u2003·\u2003back${hotBtn ? "" : " to all pools"}`
        : hotBtn ? "\u{1F50E}  Search" : "\u{1F50E}  Search a pool, token, or link");
      sb.widthWeight = hotBtn ? 58 : 100;
      sb.centerAligned();
      sb.titleFont = Font.semiboldSystemFont(14);
      sb.onTap = () => onZap(d.tokenView ? "__clearsearch__" : "__search__", null);
    }
    if (hotBtn) {
      const hb = sr.addButton(hotBtn);
      hb.widthWeight = onZap ? 42 : 100;
      hb.centerAligned();
      hb.titleFont = Font.semiboldSystemFont(14);
      hb.titleColor = d.hot || d.pinned ? COL.yellow : COL.accent;
      hb.onTap = () => onHot(d.tokenView ? "__pin__" : "__hot__");
    }
    t.addRow(sr);
  }

  // Two-line labels, one line per line of the cells under them: a single line
  // such as "FEES 24H / %DAY" does not fit a 23% column on a phone.
  const labels = new UITableRow();
  labels.height = 34;
  if (_onSort) {
    const NAMES = ["POOL", "TVL", "FEES 24H", "MCAP"];
    for (let ci = 0; ci < 4; ci++) {
      const active = ci === _sortCol;
      const desc = !(_sortMode & 1);
      const arrow = active ? (desc ? " ▼" : " ▲") : "";
      const btn = labels.addButton(NAMES[ci] + arrow);
      btn.widthWeight = ci === 0 ? 31 : 23;
      if (ci > 0) btn.rightAligned();
      btn.titleFont = Font.semiboldSystemFont(10);
      btn.titleColor = active ? COL.accent : COL.faint;
      btn.onTap = () => _onSort(ci);
    }
  } else {
    const l1 = labels.addText("POOL", "fee · age · 24h");  l1.widthWeight = 31;
    const l2 = labels.addText("TVL", "vol 24h");           l2.widthWeight = 23; l2.rightAligned();
    const l3 = labels.addText("FEES 24H", "% / day");      l3.widthWeight = 23; l3.rightAligned();
    const l4 = labels.addText("MCAP", "1h pace");          l4.widthWeight = 23; l4.rightAligned();
    for (const c of [l1, l2, l3, l4]) {
      c.titleFont = Font.semiboldSystemFont(10); c.titleColor = COL.faint;
      c.subtitleFont = Font.systemFont(9);       c.subtitleColor = COL.faint;
    }
  }
  t.addRow(labels);

  if (_onSort) {
    const SUBS = [
      ["fee%", "age", "24h"],
      ["tvl", "vol 24h"],
      ["fees $", "%/day"],
      ["mcap", "1h pace"],
    ];
    const metricIdx = _sortMode >> 1;
    const info = new UITableRow();
    info.height = 20;
    const cells = [];
    for (let ci = 0; ci < 4; ci++) {
      const parts = SUBS[ci];
      const label = ci === 0 ? parts.join(" · ") : parts.join(" · ");
      const ic = info.addText(label);
      ic.widthWeight = ci === 0 ? 31 : 23;
      if (ci > 0) ic.rightAligned();
      ic.titleFont = Font.systemFont(9);
      ic.titleColor = ci === _sortCol ? COL.accent : COL.faint;
      cells.push(ic);
    }
    t.addRow(info);
  }

  // Table cells truncate instead of wrapping, so long text is broken into lines
  // here and each one gets its own row. Errors are the reason: they are the
  // longest strings shown and the part you most need to read in full.
  const wrapLines = (text, width) => {
    const words = String(text == null ? "" : text).split(/\s+/).filter(Boolean);
    const out = [];
    let cur = "";
    for (const w of words) {
      if (!cur) { cur = w; }
      else if ((cur + " " + w).length <= width) { cur += " " + w; }
      else { out.push(cur); cur = w; }
      // A single word longer than the line gets hard-split rather than clipped.
      while (cur.length > width) { out.push(cur.slice(0, width)); cur = cur.slice(width); }
    }
    if (cur) out.push(cur);
    return out.length ? out : [""];
  };

  if (progress) {
    const head = new UITableRow();
    head.height = 34;
    const hc = head.addText(progress.running ? `Working: ${progress.title}`
      : (progress.outcome && progress.outcome.ok ? `Done: ${progress.title}` : `Problem: ${progress.title}`));
    hc.titleFont = Font.boldSystemFont(13);
    hc.titleColor = progress.running ? COL.yellow
      : (progress.outcome && progress.outcome.ok) ? COL.green : COL.red;
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
      const mark = st.state === "ok" ? "✓" : st.state === "fail" ? "✗"
                 : st.state === "skip" ? "–" : st.state === "info" ? "·" : "…";
      const colour = st.state === "ok" ? COL.green : st.state === "fail" ? COL.red
                   : st.state === "run" ? COL.yellow : COL.muted;
      const detail = wrapLines(st.detail, 46);
      const sr = new UITableRow();
      sr.height = st.detail ? 40 : 26;
      const c = sr.addText(`${mark}   ${st.label}`, detail[0] || "");
      c.titleFont = Font.systemFont(12);
      c.titleColor = colour;
      c.subtitleFont = Font.systemFont(10);
      c.subtitleColor = COL.faint;
      t.addRow(sr);
      // Anything that did not fit continues on its own rows.
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
      const oc = orow.addText(lines[0], lines[1] || progress.outcome.gas || "");
      oc.titleFont = Font.semiboldSystemFont(12);
      oc.titleColor = progress.outcome.ok ? COL.green : COL.red;
      oc.subtitleFont = Font.systemFont(11);
      oc.subtitleColor = COL.muted;
      t.addRow(orow);
      for (const extra of lines.slice(2)) {
        const er = new UITableRow();
        er.height = 20;
        const ec = er.addText(extra);
        ec.titleFont = Font.systemFont(11);
        ec.titleColor = progress.outcome.ok ? COL.green : COL.red;
        t.addRow(er);
      }
      if (progress.outcome.gas && lines.length > 1) {
        const gr = new UITableRow();
        gr.height = 22;
        const gc = gr.addText(progress.outcome.gas);
        gc.titleFont = Font.systemFont(11);
        gc.titleColor = COL.muted;
        t.addRow(gr);
      }
    }
  }

  // An empty 🔥 view says which knob to turn rather than looking broken.
  if (d.hot && !d.rows.length) {
    const er = new UITableRow();
    er.height = 48;
    const ec = er.addText(d.hotCold ? "Nothing here yet: refresh once to fill this view"
                                    : "No pool is that fast right now",
      `Tap \u{1F525} to lower the pace, mcap or TVL floor, or pin a token from its search.`);
    ec.titleFont = Font.semiboldSystemFont(13);
    ec.titleColor = COL.yellow;
    ec.subtitleFont = Font.systemFont(11);
    ec.subtitleColor = COL.muted;
    t.addRow(er);
  }

  for (const r of d.rows || []) {
    const row = new UITableRow();
    row.height = 58;
    // Fee tier lives in the subtitle: "DEMOTHREE 6.9%" does not fit a title cell.
    //
    // No contract suffix here. The review asked for one on every row, but this
    // subtitle already wraps to three lines with it, and a lone row's suffix
    // disambiguates nothing the reader can see. The portfolio rows keep theirs,
    // which is where two positions can be compared against each other.
    const sub = [r.tier, fmtAge(r.ageDays)];
    if (r.chg24 != null) sub.push(`${r.chg24 >= 0 ? "+" : ""}${r.chg24.toFixed(0)}% 24h`);
    const name = row.addText(r.pinned && !d.tokenView ? "⭐" + r.symbol : r.symbol, sub.join(" · "));
    name.widthWeight = 25;
    name.titleFont = Font.boldSystemFont(16);
    name.titleColor = COL.text;
    name.subtitleFont = Font.systemFont(11);
    name.subtitleColor = r.chg24 == null ? COL.muted : signColor(r.chg24);

    // 24h shape, right where the eye lands after the name.
    const spark = sparkline(r, 132, 34);
    if (spark) {
      const sp = row.addImage(spark);
      sp.widthWeight = 18;
      sp.centerAligned();
    } else {
      const sp = row.addText("");
      sp.widthWeight = 18;
    }

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

    // Same per-day scale as the main column, but from the last hour's volume, so
    // it reads as "at the current pace" rather than a different unit.
    const mk = row.addText(r.mcap == null ? "--" : fmtCompact(r.mcap), `1h ${fmtPct(r.feePctDay1h, r.feePctDay1h >= 100 ? 0 : 1)}/d`);
    mk.widthWeight = 18;
    mk.rightAligned();
    mk.titleFont = Font.semiboldSystemFont(16);
    mk.titleColor = COL.text;
    mk.subtitleFont = Font.systemFont(11);
    mk.subtitleColor = feeRateColor(r.feePctDay1h);

    row.onSelect = () => Safari.open(`${DEX_CHAIN_URL}/${r.id}`);
    row.dismissOnSelect = false;
    t.addRow(row);

    // The zap button gets a row to itself and no onSelect, so missing it does
    // nothing rather than opening a browser.
    if (onZap) {
      const zr = new UITableRow();
      zr.height = 36;
      // Equal weights across the full width, so the buttons sit evenly rather
      // than bunching against one edge.
      // The two presets plus one slot for entering values by hand.
      const w = Math.floor(100 / (ZAP_PRESETS.length + 1));
      for (const preset of ZAP_PRESETS) {
        // The label carries the whole preset, so two buttons differing only in
        // slice count are still distinguishable. The bolt leads because it is the
        // action, then the amount, which varies most; the shape trails as
        // "slices × range". One slice drops the multiplier, since a single
        // position has no ladder to describe.
        const amt = preset.amount >= 1000
          ? (preset.amount / 1000).toFixed(preset.amount % 1000 ? 1 : 0) + "k"
          : String(preset.amount);
        const band = preset.top > 0 ? `${preset.top}-${preset.range}%` : `${preset.range}%`;
        const shape = preset.slices > 1 ? `${preset.slices}×${band}` : band;
        const zb = zr.addButton(busy ? "…" : `⚡️$${amt} · ${shape}`);
        zb.widthWeight = w;
        zb.centerAligned();
        zb.titleFont = Font.semiboldSystemFont(13);
        if (!busy) zb.onTap = () => onZap(r, preset);
      }
      // Same flow, values typed in rather than fixed.
      const cz = zr.addButton(busy ? "…" : "\u270F\uFE0F Custom");
      cz.widthWeight = w;
      cz.centerAligned();
      cz.titleFont = Font.semiboldSystemFont(13);
      if (!busy) cz.onTap = () => onZap(r, "__ask__");
      t.addRow(zr);
    }
  }

  const foot = new UITableRow();
  foot.height = 56;
  const fCell = foot.addText(
    "Open DexScreener · Robinhood Chain",
    `${d.candidates} ranked candidates · ${d.pagesCached}/${DISCOVERY_PAGES} volume pages · ${VERSION}`
  );
  fCell.titleFont = Font.semiboldSystemFont(15);
  fCell.titleColor = COL.accent;
  fCell.subtitleFont = Font.systemFont(11);
  fCell.subtitleColor = COL.faint;
  fCell.centerAligned();
  foot.onSelect = () => Safari.open(DEX_CHAIN_URL);
  foot.dismissOnSelect = false;
  t.addRow(foot);
}

// Table for when there is nothing to show: no cache and the fetch failed, or
// no positions. The status line carries the reason and a tap on it tries again.
function fillEmptyTable(t, message, refreshing, onRefresh) {
  t.removeAllRows();
  const header = new UITableRow();
  header.isHeader = true;
  header.height = 72;
  const c = header.addText("USDG Pools", "Uniswap V4 · Robinhood Chain");
  c.titleFont = Font.boldSystemFont(20);
  c.subtitleFont = Font.systemFont(12);
  c.subtitleColor = COL.muted;
  t.addRow(header);
  statusRow(t, refreshing ? "refreshing" : "stale",
            refreshing ? (message || "Refreshing…") : `${message}   ·   tap to retry`, onRefresh);
}

// Refresh only the rows the widget actually shows. Running full discovery here
// means ranking 100+ pools and reading every one of them on chain, which the
// widget extension has neither the memory nor the ~30s budget for: it is killed
// and iOS keeps redrawing its last good snapshot, so the widget looks frozen.
// One market call for the visible ids updates TVL, volume and the fee rate, and
// the fee tier and mcap carry over from the cache the app wrote.
// One pool, by its 32-byte poolId, as a full table row. Market numbers come
// from DexScreener and the live fee tier from the chain, so a pool the ranked
// pages have not reached yet still shows the same columns as everything else.
async function rowFromPoolId(poolId) {
  const id = String(poolId || "").trim().toLowerCase();
  const req = new Request("https://api.dexscreener.com/latest/dex/pairs/robinhood/" + id);
  req.headers = { Accept: "application/json" };
  req.timeoutInterval = 12;
  const j = await req.loadJSON();
  const p = j && ((j.pairs && j.pairs[0]) || j.pair);
  if (!p || !p.pairAddress) throw new Error("no pool with that id on this chain");

  const b = (p.baseToken && p.baseToken.address || "").toLowerCase();
  const q = (p.quoteToken && p.quoteToken.address || "").toLowerCase();
  if (b !== USDG_ADDR && q !== USDG_ADDR) throw new Error("this script only opens positions in USDG pairs");

  let lpFee = null;
  try {
    const slot = await zapRpc("eth_call", [{ to: STATE_VIEW, data: "0x" + SEL.getSlot0 + id.replace(/^0x/, "") }, "latest"]);
    const hex = String(slot).replace(/^0x/, "");
    if (hex.length >= 256) lpFee = Number(BigInt("0x" + hex.slice(192, 256)));
  } catch (e) {}
  if (!(lpFee > 0)) throw new Error("pool is not initialised, or prices each swap through a hook");
  if (lpFee > MAX_LP_FEE) throw new Error(`fee tier is ${fmtFeeTier(lpFee)}; above ${fmtFeeTier(MAX_LP_FEE)} nothing would trade through it`);

  const tok = b === USDG_ADDR ? q : b;
  const sym = (b === USDG_ADDR ? (p.quoteToken && p.quoteToken.symbol) : (p.baseToken && p.baseToken.symbol)) || tok.slice(0, 8);
  const tvl = Number((p.liquidity && p.liquidity.usd) || 0);
  const vol24 = Number((p.volume && p.volume.h24) || 0);
  const vol1h = Number((p.volume && p.volume.h1) || 0);
  const feeFrac = lpFee / 1e6, fees24 = vol24 * feeFrac;
  const _c0 = b < q ? b : q, _c1 = b < q ? q : b;
  return {
    id, currency0: _c0, currency1: _c1, tokenAddr: tok,
    symbol: sym, tier: fmtFeeTier(lpFee), label: `${sym} ${fmtFeeTier(lpFee)}`,
    lpFee, tvl, vol24, vol1h, fees24,
    mcap: p.marketCap != null ? Number(p.marketCap) : (p.fdv != null ? Number(p.fdv) : null),
    feePctDay: tvl > 0 ? (fees24 / tvl) * 100 : 0,
    feePctDay1h: tvl > 0 ? (vol1h * 24 * feeFrac / tvl) * 100 : 0,
    txns24: p.txns && p.txns.h24 ? (p.txns.h24.buys || 0) + (p.txns.h24.sells || 0) : null,
    baseAddr: b,
    chg24: p.priceChange && p.priceChange.h24 != null ? Number(p.priceChange.h24) : null,
    chg6h: p.priceChange && p.priceChange.h6 != null ? Number(p.priceChange.h6) : null,
    chg1h: p.priceChange && p.priceChange.h1 != null ? Number(p.priceChange.h1) : null,
    chg5m: p.priceChange && p.priceChange.m5 != null ? Number(p.priceChange.m5) : null,
    price: p.priceUsd != null ? Number(p.priceUsd) : null,
    ageDays: p.pairCreatedAt ? (Date.now() - p.pairCreatedAt) / 86400000 : null,
    live: true,
  };
}

// Tokens matching a name or symbol, for the times an address is not to hand.
// Grouped by contract address, because a symbol is not an identity: searching
// "PIXELCAT" returns five different contracts, one with $180K of liquidity and
// the rest with a few thousand. The caller must let a human pick, never guess,
// so this returns candidates with the numbers needed to tell them apart.
async function findTokens(query) {
  const q = String(query || "").trim();
  if (q.length < 2) throw new Error("type at least two characters");

  const req = new Request("https://api.dexscreener.com/latest/dex/search?q=" + encodeURIComponent(q));
  req.headers = { Accept: "application/json" };
  req.timeoutInterval = 12;
  const j = await req.loadJSON();

  const byToken = new Map();
  for (const p of (j && j.pairs) || []) {
    if (p.chainId !== "robinhood") continue;
    const b = (p.baseToken && p.baseToken.address || "").toLowerCase();
    const qa = (p.quoteToken && p.quoteToken.address || "").toLowerCase();
    if (b !== USDG_ADDR && qa !== USDG_ADDR) continue;
    const v4 = String(p.dexId || "").includes("uniswap")
            && (p.labels || []).some(l => String(l).toLowerCase() === "v4");
    if (!v4) continue;
    const addr = b === USDG_ADDR ? qa : b;
    const sym = (b === USDG_ADDR ? (p.quoteToken && p.quoteToken.symbol) : (p.baseToken && p.baseToken.symbol)) || "?";
    if (!byToken.has(addr)) byToken.set(addr, { addr, symbol: sym, pools: 0, tvl: 0, mcap: null });
    const e = byToken.get(addr);
    e.pools++;
    e.tvl += Number((p.liquidity && p.liquidity.usd) || 0);
    const mc = p.marketCap != null ? Number(p.marketCap) : (p.fdv != null ? Number(p.fdv) : null);
    if (mc != null && (e.mcap == null || mc > e.mcap)) e.mcap = mc;
  }
  const out = [...byToken.values()].sort((a, b) => b.tvl - a.tvl);
  if (!out.length) throw new Error(`no Uniswap V4 USDG pools match "${q}"`);
  return out;
}

// Every USDG Uniswap V4 pool holding a given token, built into the same row
// shape the discovery table uses so the normal renderer and zap buttons work
// unchanged. DexScreener lists a token's pools directly, which sidesteps the
// ranked-page rotation entirely: a pool minutes old is returned here even
// though discovery has not reached its page yet. Fee tier is not in that
// response, so each pool's live lpFee is read from the chain.
async function rowsForToken(tokenAddr) {
  const addr = String(tokenAddr || "").trim().toLowerCase();
  if (!/^0x[0-9a-fA-F]{40}$/.test(addr)) throw new Error("that is not a 20-byte token address");

  const req = new Request("https://api.dexscreener.com/latest/dex/tokens/" + addr);
  req.headers = { Accept: "application/json" };
  req.timeoutInterval = 12;
  const j = await req.loadJSON();
  const all = (j && j.pairs) || [];
  if (!all.length) throw new Error("DexScreener knows no pools for that token");

  // USDG pairs on this chain, Uniswap V4 only: those are the pools this script
  // can actually open a position in.
  const cands = all.filter(p => {
    if (p.chainId !== "robinhood") return false;
    const b = (p.baseToken && p.baseToken.address || "").toLowerCase();
    const q = (p.quoteToken && p.quoteToken.address || "").toLowerCase();
    if (b !== USDG_ADDR && q !== USDG_ADDR) return false;
    const v4 = String(p.dexId || "").includes("uniswap")
            && (p.labels || []).some(l => String(l).toLowerCase() === "v4");
    return v4 && p.pairAddress;
  });
  if (!cands.length) throw new Error("no Uniswap V4 USDG pools for that token");

  const now = Date.now();
  const sym = (cands[0].baseToken && cands[0].baseToken.address || "").toLowerCase() === USDG_ADDR
    ? (cands[0].quoteToken && cands[0].quoteToken.symbol) || addr.slice(0, 8)
    : (cands[0].baseToken && cands[0].baseToken.symbol) || addr.slice(0, 8);

  const rows = [];
  for (const p of cands) {
    const id = String(p.pairAddress).toLowerCase();
    // Live fee tier, and proof the pool is initialised.
    let lpFee = null;
    try {
      const slot = await zapRpc("eth_call", [{ to: STATE_VIEW, data: "0x" + SEL.getSlot0 + id.replace(/^0x/, "") }, "latest"]);
      const hex = String(slot).replace(/^0x/, "");
      if (hex.length >= 256) lpFee = Number(BigInt("0x" + hex.slice(192, 256)));
    } catch (e) {}
    if (lpFee == null || lpFee <= 0 || lpFee > MAX_LP_FEE) continue;

    const tvl = Number((p.liquidity && p.liquidity.usd) || 0);
    // No TVL floor here. The ranked table keeps one because it is a browse
    // list and a fee yield that divides by a near-empty pool sorts to the top
    // on arithmetic alone. A search is not browsing: the user has named a pool
    // and wants to see it, and hiding it behind a floor answered "1 pool found,
    // all below the 10K TVL floor" -- which is the widget knowing the answer
    // and refusing to show it. Depositing a large share of a small pool is
    // still a different trade from joining a deep one, so the zap confirmation
    // says so with the actual numbers before anything is signed.
    const vol24 = Number((p.volume && p.volume.h24) || 0);
    const vol1h = Number((p.volume && p.volume.h1) || 0);
    const feeFrac = lpFee / 1e6;
    const fees24 = vol24 * feeFrac;
    const b = (p.baseToken && p.baseToken.address || "").toLowerCase();
    const q = (p.quoteToken && p.quoteToken.address || "").toLowerCase();
    const tok = b === USDG_ADDR ? q : b;
    const _c0 = b < q ? b : q, _c1 = b < q ? q : b;
    rows.push({
      id, currency0: _c0, currency1: _c1, tokenAddr: tok,
      symbol: sym, tier: fmtFeeTier(lpFee), label: `${sym} ${fmtFeeTier(lpFee)}`,
      lpFee, tvl, vol24, vol1h, fees24,
      mcap: p.marketCap != null ? Number(p.marketCap) : (p.fdv != null ? Number(p.fdv) : null),
      feePctDay: tvl > 0 ? (fees24 / tvl) * 100 : 0,
      feePctDay1h: tvl > 0 ? (vol1h * 24 * feeFrac / tvl) * 100 : 0,
      txns24: p.txns && p.txns.h24 ? (p.txns.h24.buys || 0) + (p.txns.h24.sells || 0) : null,
      chg24: p.priceChange && p.priceChange.h24 != null ? Number(p.priceChange.h24) : null,
      price: p.priceUsd != null ? Number(p.priceUsd) : null,
      ageDays: p.pairCreatedAt ? (now - p.pairCreatedAt) / 86400000 : null,
      live: true,
    });
  }
  if (!rows.length) {
    throw new Error("found pools, but none were initialised with a usable fee");
  }
  rows.sort((a, b) => b.feePctDay - a.feePctDay || b.tvl - a.tvl);
  return { rows, symbol: sym };
}

async function refreshWidgetRows(cached) {
  const rows = cached.data.rows.slice(0, cached.data.rowBudget || 6);
  // Balance is refreshed alongside the market data, not read from cache: it
  // changes every time a position is opened, so a cached figure would be wrong
  // in exactly the situation the number is consulted for. Two cheap reads, and
  // the cached value stands if they fail.
  const [market, balances] = await Promise.all([
    fetchMarket(rows.map(r => r.id)),
    WALLET ? fetchBalances().catch(() => null) : Promise.resolve(null),
  ]);
  if (!market.size) return null;
  const freshBalances = balances || cached.data.balances || null;
  let touched = 0;
  const fresh = rows.map(r => {
    const m = market.get(r.id);
    if (!m || !(m.liq > 0)) return r;
    touched++;
    const feeFrac = r.lpFee / 1e6;
    const fees24 = m.vol24 * feeFrac;
    return {
      ...r,
      tvl: m.liq, vol24: m.vol24, vol1h: m.vol1h, fees24,
      feePctDay: (fees24 / m.liq) * 100,
      feePctDay1h: (m.vol1h * 24 * feeFrac / m.liq) * 100,
      chg24: m.chg24 != null ? m.chg24 : r.chg24,
      txns24: m.txns24 != null ? m.txns24 : r.txns24,
    };
  });
  if (!touched) return null;
  fresh.sort((a, b) => b.feePctDay - a.feePctDay || b.vol24 - a.vol24);
  return { ...cached.data, rows: fresh, balances: freshBalances };
}

if (config.runsInWidget) {
  let w;
  try {
    const cached = loadDataCache();
    if (!cached || !cached.data || !cached.data.rows || !cached.data.rows.length) {
      w = errorWidget("No data yet. Tap to open in app and discover pools.");
    } else {
      // Cached rows render if the market call is slow or fails, so a bad network
      // shows yesterday's numbers rather than an empty square.
      const stale = new Promise(resolve => Timer.schedule(12_000, false, () => resolve(null)));
      const fresh = await Promise.race([refreshWidgetRows(cached).catch(() => null), stale]);
      w = fresh
        ? buildWidget(fresh, false, Date.now())
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
  let shown = null, current = null, busy = false;

  // Sort: 4 columns x 4 modes (metric1 desc, metric1 asc, metric2 desc, metric2 asc)
  const SORT_KEYS = [
    ["lpFee",      "ageDays"    ],   // col 0: POOL (fee tier, age)
    ["tvl",        "vol24"      ],   // col 1: TVL (tvl, volume 24h)
    ["fees24",     "feePctDay"  ],   // col 2: FEES ($ fees, %/day)
    ["mcap",       "feePctDay1h"],   // col 3: MCAP (mcap, 1h pace)
  ];
  let sortCol = 2, sortMode = 2;    // default: feePctDay descending
  const sortRows = (rows) => {
    const key = SORT_KEYS[sortCol][sortMode >> 1];
    const desc = !(sortMode & 1);
    return [...rows].sort((a, b) => {
      const va = a[key] ?? (desc ? -Infinity : Infinity);
      const vb = b[key] ?? (desc ? -Infinity : Infinity);
      return desc ? vb - va : va - vb;
    });
  };
  // Re-render through show() rather than calling fillTable directly. A second
  // call site had drifted out of sync and was passing 10 of the 13 arguments,
  // dropping onZap, so every zap button disappeared the moment you sorted.
  const handleSort = (col) => {
    if (col === sortCol) sortMode = (sortMode + 1) % 4;
    else { sortCol = col; sortMode = 0; }
    if (current && current.data && current.data.rows) show(current, false);
  };

  let progress = null;
  // When set, the table shows one token's pools instead of the ranked list.
  let tokenView = null;
  // Last hand-entered zap, so the dialog reopens with what was used before.
  let lastCustom = { amount: ZAP_PRESETS[0].amount, top: ZAP_PRESETS[0].top || 0,
                     range: ZAP_PRESETS[0].range, slices: ZAP_PRESETS[0].slices };

  // 🔥 view: whether it is on and its floors, remembered between opens. Pins
  // are read once here and changed in place, so a pin shows without a refresh.
  let hot = (() => {
    const h = loadJson("hot") || {};
    const num = (v, dflt) => (v != null && Number.isFinite(Number(v)) && Number(v) >= 0 ? Number(v) : dflt);
    return { on: !!h.on, minPace: num(h.minPace, HOT_DEFAULTS.minPace),
             minMcap: num(h.minMcap, HOT_DEFAULTS.minMcap), minTvl: num(h.minTvl, HOT_DEFAULTS.minTvl) };
  })();
  if (hot.on) { sortCol = 3; sortMode = 2; }   // 1h pace, descending
  let pins = loadPins();
  const majorSyms = new Set(Object.values(MAJORS));
  // A pinned token was named by the user, so it skips every floor, the same
  // rule search follows. Everything else needs the pace and both floors; an
  // unknown market cap only passes when the mcap floor is zero.
  const passesHot = r => {
    if (r.pinned) return true;
    if (!(r.tvl >= hot.minTvl)) return false;
    if (!majorSyms.has(r.symbol) && hot.minMcap > 0) {
      if (r.mcap == null) return false;
      if ((r.mcapHigh == null ? r.mcap : r.mcapHigh) < hot.minMcap) return false;
    }
    return (r.feePctDay1h || 0) >= hot.minPace;
  };
  const withPins = rows => rows.map(r => ({ ...r, pinned: !!pins[r.tokenAddr] }));
  // A cache from before this build has no hotRows. Its ranked rows stand in,
  // and hotCold tells the empty state to ask for a refresh.
  const hotView = data => ({ ...data, rows: withPins(data.hotRows || data.rows).filter(passesHot),
                             hot, hotCold: !data.hotRows });

  const show = (r, refreshing) => {
    if (r.data && r.data.rows) {
      const tv0 = tokenView && tokenView.rows[0];
      const base = tokenView
        ? { ...r.data, rows: tokenView.rows, tokenView: tokenView.symbol,
            tokenAddr: tv0 && tv0.tokenAddr, pinned: !!(tv0 && pins[tv0.tokenAddr]) }
        : hot.on ? hotView(r.data)
        : { ...r.data, rows: withPins(r.data.rows) };
      const sorted = { ...base, rows: sortRows(base.rows) };
      fillTable(t, sorted, r.stale, r.dataTime, refreshing, r.error, refresh,
                sortCol, sortMode, handleSort, ZAP_READY ? onZap : null, busy, progress, onHot);
    }
    else fillEmptyTable(t, r.message || "Nothing loaded", refreshing, refresh);
    if (shown) t.reload(); else shown = t.present(false);
  };

  // The 🔥 button: on a search result it pins or unpins that token; otherwise
  // it opens the view's settings, which is also where it is turned off.
  async function onHot(action) {
    if (busy) return;
    if (action === "__pin__") {
      const r0 = tokenView && tokenView.rows[0];
      if (!r0 || !r0.tokenAddr) return;
      const addr = r0.tokenAddr;
      if (pins[addr]) {
        delete pins[addr];
      } else {
        pins[addr] = { symbol: r0.symbol, t: Date.now() };
        // The search already has this token's rows, so they join the 🔥 view
        // now; the next refresh fetches them along with everything else.
        if (current && current.data && current.data.rows) {
          const had = current.data.hotRows || current.data.rows;
          const ids = new Set(had.map(r => r.id));
          current = { ...current, data: { ...current.data,
            hotRows: had.concat(tokenView.rows.filter(r => !ids.has(r.id))) } };
        }
      }
      savePins(pins);
      return show(current, false);
    }

    const a = new Alert();
    a.title = "\u{1F525} Fee kenceng";
    a.message = "Pools ranked by the last hour's fee pace (the %/day they would earn "
              + "if the last hour kept up), small ones included.\n\n"
              + "Pinned tokens always show, with no floors: search a token, then tap "
              + "\u2606 Pin.\n\nSmall pools are thin and move fast. Check TVL before depositing.";
    a.addTextField("Min 1h pace, %/day (e.g. 25)", String(hot.minPace));
    a.addTextField("Min market cap (e.g. 100K, 0 = any)", hot.minMcap ? fmtShort(hot.minMcap) : "0");
    a.addTextField("Min TVL (e.g. 1K, 0 = any)", hot.minTvl ? fmtShort(hot.minTvl) : "0");
    a.addAction(hot.on ? "Apply" : "Turn on");
    if (hot.on) a.addDestructiveAction("Turn off");
    a.addCancelAction("Cancel");
    const choice = await a.presentAlert();
    if (choice < 0) return;
    if (choice === 1) {
      hot = { ...hot, on: false };
      sortCol = 2; sortMode = 2;   // back to the ranked default, fees %/day
    } else {
      // A field left empty or unreadable keeps its old value, and "0" means no
      // floor, which parseUsd alone would read as unreadable.
      const usd = (txt, old) => {
        const v = String(txt == null ? "" : txt).trim();
        if (/^\$?0+(\.0*)?$/.test(v)) return 0;
        const n = parseUsd(v);
        return n == null ? old : n;
      };
      const paceTxt = String(a.textFieldValue(0) || "").replace(/[^0-9.]/g, "");
      const pace = paceTxt ? Number(paceTxt) : NaN;
      const wasOn = hot.on;
      hot = {
        on: true,
        minPace: Number.isFinite(pace) ? pace : hot.minPace,
        minMcap: usd(a.textFieldValue(1), hot.minMcap),
        minTvl: usd(a.textFieldValue(2), hot.minTvl),
      };
      if (!wasOn) { sortCol = 3; sortMode = 2; }   // 1h pace, descending
    }
    saveJson("hot", hot);
    return show(current, false);
  }

  // Opening a position spends real money, so it always goes through a
  // confirmation naming the pool, the amount and the range.
  async function onZap(row, preset) {
    if (busy) return;
    // Leave a search result and return to the ranked discovery table.
    if (row === "__clearsearch__") {
      tokenView = null;
      return show(current, false);
    }

    // One search box for every way a pool gets named. What comes back always
    // lands in the table rather than starting a zap, so a result can be read
    // before anything is committed to.
    if (row === "__search__") {
      const ask = new Alert();
      ask.title = "Search";
      ask.message = "A pool id, a token address, a token name, or a DexScreener link.\n\n"
                  + "Every Uniswap V4 USDG pool for it is listed, including thin ones. "
                  + "Check the TVL column before depositing.";
      ask.addTextField("Name, 0x address, pool id, or link", "");
      ask.addAction("Search");
      ask.addCancelAction("Cancel");
      if ((await ask.presentAlert()) !== 0) return;
      const typed = String(ask.textFieldValue(0) || "").trim();
      if (!typed) return;

      const runLookup = async (fn, label) => {
        busy = true;
        progress = { title: "Search", steps: [{ label, state: "run", detail: "" }], running: true, outcome: null };
        show(current, false);
        try {
          const found = await fn();
          tokenView = found;
          progress = null;
        } catch (e) {
          progress = { title: "Search", steps: [], running: false,
            outcome: { ok: false, text: String((e && e.message) || e).slice(0, 200) } };
        }
        busy = false;
        return show(current, false);
      };

      // A link carries the id in its path, so pull the longest hex run out and
      // let its length say whether it names a pool (32 bytes) or a token (20).
      const hex = (typed.match(/0x[0-9a-fA-F]{40,64}/) || [])[0] || "";
      if (/^0x[0-9a-fA-F]{64}$/.test(hex)) {
        return runLookup(async () => {
          const r = await rowFromPoolId(hex);
          return { symbol: `${r.symbol} ${r.tier}`, rows: [r] };
        }, "Reading pool");
      }
      if (/^0x[0-9a-fA-F]{40}$/.test(hex)) {
        return runLookup(async () => {
          const f = await rowsForToken(hex);
          return { symbol: f.symbol, rows: f.rows };
        }, "Finding pools");
      }

      // Not an address: search by name, and let a human resolve a symbol that
      // several contracts answer to.
      busy = true;
      progress = { title: "Search", steps: [{ label: "Searching", state: "run", detail: "" }], running: true, outcome: null };
      show(current, false);
      let cands = null;
      try {
        cands = await findTokens(typed);
      } catch (e) {
        busy = false;
        progress = { title: "Search", steps: [], running: false,
          outcome: { ok: false, text: String((e && e.message) || e).slice(0, 200) } };
        return show(current, false);
      }
      busy = false;
      progress = null;
      let addr = cands[0].addr;
      if (cands.length > 1) {
        show(current, false);
        const pick = new Alert();
        pick.title = `${cands.length} tokens match "${typed}"`;
        pick.message = "Same symbol, different contracts. Pick by liquidity and "
                     + "market cap; the address is shown so it can be checked.";
        const top = cands.slice(0, 6);
        for (const c of top) {
          pick.addAction(`${c.symbol} · ${fmtShort(c.tvl)} TVL · mcap ${c.mcap != null ? fmtShort(c.mcap) : "?"} · ${c.addr.slice(0, 8)}…`);
        }
        pick.addCancelAction("Cancel");
        const chosen = await pick.presentAlert();
        if (chosen < 0 || chosen >= top.length) return show(current, false);
        addr = top[chosen].addr;
      }
      return runLookup(async () => {
        const f = await rowsForToken(addr);
        return { symbol: f.symbol, rows: f.rows };
      }, "Finding pools");
    }

    // Hand-entered amount, range and slice count for this pool. Everything after
    // this point is the normal flow, so the confirmation, gas quote and thin
    // pool warning all still apply.
    if (preset === "__ask__") {
      const ask = new Alert();
      ask.title = `Custom zap${row && row.symbol ? " into " + row.symbol : ""}`;
      ask.message = "USDG to deposit, the band below the price to cover, and how "
                  + "many slices to split it into.\n\nTop is where the ladder starts: "
                  + "0 puts it against the market, 10 holds it back a tenth. Bottom is "
                  + "how far down it reaches. A 10 and 60 ladder covers -10% to -60%.";
      ask.addTextField("USDG amount", String(lastCustom.amount));
      ask.addTextField("Top % below price (0 = at the price)", String(lastCustom.top || 0));
      ask.addTextField("Bottom % below price", String(lastCustom.range));
      ask.addTextField("Slices", String(lastCustom.slices));
      ask.addAction("Continue");
      ask.addCancelAction("Cancel");
      if ((await ask.presentAlert()) !== 0) return;

      const amount = Number(String(ask.textFieldValue(0)).replace(/[^0-9.]/g, ""));
      const top    = Number(String(ask.textFieldValue(1)).replace(/[^0-9.]/g, "") || 0);
      const range  = Number(String(ask.textFieldValue(2)).replace(/[^0-9.]/g, ""));
      const slices = Math.round(Number(String(ask.textFieldValue(3)).replace(/[^0-9.]/g, "")));
      // Bounds are the ones the rest of the script can actually honour: a range
      // at or above 100% has no lower price to put liquidity at, and each slice
      // is its own position, so the count drives both gas and the mint size.
      const bad =
          !(amount > 0)            ? "Amount must be greater than zero."
        : !(top >= 0 && top < 99)  ? "Top must be between 0 and 99%."
        : !(range > 0 && range < 100) ? "Bottom must be between 0 and 100%."
        // A band needs somewhere to be: the bottom has to sit below the top,
        // and close together they collapse onto one tick of the grid.
        : !(range > top)           ? "Bottom must be further from the price than top."
        : !(slices >= 1 && slices <= 100) ? "Slices must be between 1 and 100."
        : null;
      if (bad) {
        progress = { title: "Custom zap", steps: [], running: false, outcome: { ok: false, text: bad } };
        return show(current, false);
      }
      lastCustom = { amount, top, range, slices };
      preset = { amount, top, range, slices };
    }

    busy = true;
    _gasLog = [];
    progress = { title: `${preset.amount} USDG into ${row.symbol} ${row.tier}`, steps: [], running: true, outcome: null };
    show(current, false);

    const rep = {
      start(l) { progress.steps.push({ label: l, state: "run", detail: "" }); show(current, false); return progress.steps.length - 1; },
      detail(i, d) { if (progress.steps[i]) { progress.steps[i].detail = d; show(current, false); } },
      ok(i, d) { if (progress.steps[i]) { progress.steps[i].state = "ok"; progress.steps[i].detail = d || ""; show(current, false); } },
      fail(i, d) { if (progress.steps[i]) { progress.steps[i].state = "fail"; progress.steps[i].detail = d || ""; show(current, false); } },
      skip(i, d) { if (progress.steps[i]) { progress.steps[i].state = "skip"; progress.steps[i].detail = d || ""; show(current, false); } },
      note(l, d) { progress.steps.push({ label: l, state: "info", detail: d || "" }); show(current, false); },
    };

    try {
      if (_ethUsd == null) {
        try {
          const rq = new Request("https://api.dexscreener.com/tokens/v1/robinhood/" + WETH_ADDR);
          rq.headers = { Accept: "application/json" };
          rq.timeoutInterval = 6;
          const arr = await rq.loadJSON();
          for (const pr of (Array.isArray(arr) ? arr : (arr && arr.pairs) || [])) {
            const px = Number(pr && pr.priceUsd);
            if (px > 0) { _ethUsd = px; break; }
          }
        } catch (e) {}
      }

      // The table carries a pool id and a fee but not the whole key, so solve it.
      const si = rep.start("Resolve pool");
      const pk = await resolvePoolKey(row.id, row.currency0, row.currency1, row.lpFee);
      if (!pk) {
        // A fee like 4.036% is a dynamic fee, which on V4 can only be set by a
        // hook, and the hook's address is part of the pool key. It cannot be
        // recovered from the id alone, so these pools are refused rather than
        // guessed at. Plain fixed-fee pools resolve fine.
        rep.fail(si, "could not recover this pool's key, from either the tick grid "
                   + "or its Initialize event");
        throw new Error("pool key not resolvable");
      }
      rep.ok(si, `fee ${(pk.fee / 10000).toFixed(3)}% · spacing ${pk.tickSpacing}`
        + (/^0x0+$/.test(pk.hooks) ? "" : " · has a hook"));

      const ti = rep.start("Read current price");
      // SEL entries are bare selectors; multicall adds the prefix itself, but a
      // direct eth_call needs it here.
      const slot = await zapRpc("eth_call", [{ to: STATE_VIEW, data: "0x" + SEL.getSlot0 + String(row.id).replace(/^0x/, "") }, "latest"]);
      const tick = Number(BigInt.asIntN(24, BigInt("0x" + String(slot).replace(/^0x/, "").slice(64, 128))));
      rep.ok(ti, `tick ${tick}`);

      const depositIsC0 = pk.currency0.toLowerCase() === USDG_ADDR.toLowerCase();
      const raw = BigInt(Math.floor(preset.amount * 1e6));   // USDG has 6 decimals

      const pi = rep.start("Plan the ladder");
      const plan = planZap(pk, tick, raw, depositIsC0, preset);
      if (!plan.slices.length) { rep.fail(pi, "nothing to place"); throw new Error("empty plan"); }
      // Snapping to the tick grid moves the range, so report what it became.
      const realTop = (1 - Math.pow(1.0001, -plan.topTicks)) * 100;
      const realBot = (1 - Math.pow(1.0001, -(plan.topTicks + plan.span))) * 100;
      const realPct = realBot - realTop;
      rep.ok(pi, `${plan.slices.length} slices · ${plan.span} ticks · `
        + (plan.topTicks > 0 ? `-${realTop.toFixed(1)}% to -${realBot.toFixed(1)}% of price`
                             : `${realBot.toFixed(1)}% of price`));

      // Market cap at both edges, so the range reads in the same units as the
      // table. Depositing currency0 means higher ticks are a cheaper token.
      //
      // The near edge used to be printed as row.mcap, the CURRENT market cap,
      // which made every range look like it reached all the way up to today's
      // price no matter what band was asked for: 30% and 15% both printed
      // "2.19M -> 7.31M" on a 7.31M token. The far edge was right; only the
      // top was the wrong number.
      const far = depositIsC0 ? Math.max(...plan.slices.map(x => x.tickUpper))
                              : Math.min(...plan.slices.map(x => x.tickLower));
      const near = depositIsC0 ? Math.min(...plan.slices.map(x => x.tickLower))
                               : Math.max(...plan.slices.map(x => x.tickUpper));
      const mcapAtEdge = (edge) => row.mcap != null
        ? row.mcap * Math.pow(1.0001, depositIsC0 ? -(edge - tick) : (edge - tick)) : null;
      const mcapFar = mcapAtEdge(far);
      const mcapNear = mcapAtEdge(near);

      // Minting pulls USDG through Permit2, so without those approvals the mint
      // reverts and so does any attempt to estimate it. Check first, or the
      // estimate fails with a bare "execution reverted" that explains nothing.
      const ai = rep.start("Check approvals");
      let needsApproval = true;
      try {
        const erc = BigInt(await zapRpc("eth_call", [{ to: USDG_ADDR, data: "0x" + SEL_ALLOWANCE + _a(WALLET) + _a(PERMIT2) }, "latest"]));
        const p2raw = String(await zapRpc("eth_call", [{ to: PERMIT2, data: "0x" + SEL_P2_ALLOWANCE + _a(WALLET) + _a(USDG_ADDR) + _a(POSITION_MANAGER) }, "latest"])).replace(/^0x/, "");
        const p2amt = p2raw.length >= 64 ? BigInt("0x" + p2raw.slice(0, 64)) : 0n;
        const p2exp = p2raw.length >= 128 ? BigInt("0x" + p2raw.slice(64, 128)) : 0n;
        const now = BigInt(Math.floor(Date.now() / 1000));
        const need = raw + openFeeRaw();
        needsApproval = erc < need || p2amt < need || (p2exp !== 0n && p2exp < now);
        rep.ok(ai, needsApproval ? "USDG needs a one-time approval first" : "USDG already approved");
      } catch (e) {
        rep.fail(ai, String((e && e.message) || e).slice(0, 50));
      }

      // Balance is worth checking too, so a shortfall reads as a shortfall.
      const held = BigInt(await zapRpc("eth_call", [{ to: USDG_ADDR, data: "0x" + SEL_BALANCE_OF + _a(WALLET) }, "latest"]).catch(() => "0x0"));
      const openFee = openFeeRaw();
      // The fee is disclosed on the download page only; here it is just part of
      // what the open needs.
      const needText = `${(Number(raw + openFee) / 1e6).toFixed(2)}`;
      if (held < raw + openFee) {
        rep.note("Not enough USDG",
                 `wallet holds ${(Number(held) / 1e6).toFixed(2)}, this needs ${needText}`);
        throw new Error(`wallet holds ${(Number(held) / 1e6).toFixed(2)} USDG, need ${needText}`);
      }

      const deadlinePre = BigInt(Math.floor(Date.now() / 1000) + DEADLINE_MIN * 60);
      const cdPre = buildZapCalldata(pk, plan, depositIsC0, WALLET, deadlinePre);
      const gi = rep.start("Estimate fee");
      let gasText = "";
      try {
        const blk = await zapRpc("eth_getBlockByNumber", ["latest", false]);
        const tipHex = await zapRpc("eth_maxPriorityFeePerGas", []).catch(() => "0x0");
        const base = BigInt(blk.baseFeePerGas || "0x0");
        const price = base + BigInt(tipHex || "0x0");
        const gwei = Number(price) / 1e9;
        // Gas used here is stable; the price is not. One 5-slice zap cost $11.63
        // at 5.7 gwei against a table measured at 0.46. Comparing with recent
        // blocks turns an unfamiliar figure into a wait-or-go decision.
        let spike = null;
        try {
          const h = await zapRpc("eth_feeHistory", ["0x64", "latest", []]);
          const b = (h.baseFeePerGas || []).map(x => Number(x) / 1e9).filter(x => x > 0);
          if (b.length > 4) {
            const med = [...b].sort((x, y) => x - y)[Math.floor(b.length / 2)];
            if (med > 0) spike = (Number(base) / 1e9) / med;
          }
        } catch (e) {}
        const gasNote = ` at ${gwei.toFixed(2)} gwei`
          + (spike == null ? ""
             : spike >= 3   ? `\n\u26a0\ufe0f Gas is ${spike.toFixed(1)}x its recent median. Waiting will be much cheaper.`
             : spike >= 1.5 ? `\nGas is ${spike.toFixed(1)}x its recent median.`
                            : `\nGas is normal right now.`);
        let gas;
        if (needsApproval) {
          // The chain cannot price a mint it would reject, so fall back to the
          // measured curve: about 273k for one slice plus 147k for each extra.
          gas = BigInt(273573 + 147245 * (plan.slices.length - 1));
          const eth = Number(gas * price) / 1e18;
          gasText = `about ${_ethUsd ? fmtUsd(eth * _ethUsd) : eth.toFixed(6) + " ETH"}`
            + ` (${gas} gas, estimated${gasNote}), plus 2 approvals`;
          rep.ok(gi, `estimated ${gasText} (cannot measure until approved)`);
        } else {
          gas = BigInt(await zapRpc("eth_estimateGas", [{ from: WALLET, to: POSITION_MANAGER, data: cdPre, value: "0x0" }]));
          const eth = Number(gas * price) / 1e18;
          gasText = `${_ethUsd ? fmtUsd(eth * _ethUsd) : eth.toFixed(6) + " ETH"}`
            + ` (${gas} gas${gasNote})`;
          rep.ok(gi, gasText);
        }
      } catch (e) {
        rep.fail(gi, String((e && e.message) || e).slice(0, 60));
        gasText = "";
      }

      const lines = plan.slices.map(x => (Number(x.amount) / 1e6).toFixed(0)).join(" / ");

      // A USDG-only deposit cannot straddle the price, so the ladder always
      // opens out of range and earns nothing until price reaches its near edge.
      // How far that is comes down to the pool's tick grid, which can be coarse
      // (a 401 spacing steps 4.1% at a time), so state the distance rather than
      // let it be discovered after the gas is spent.
      const nearEdge = depositIsC0
        ? Math.min(...plan.slices.map(x => x.tickLower))
        : Math.max(...plan.slices.map(x => x.tickUpper));
      const gapTicks = depositIsC0 ? nearEdge - tick : tick - nearEdge;
      // How far the price has to FALL to reach the near edge, which is
      // 1 - 1.0001^-ticks. The old form, 1.0001^ticks - 1, is the rise from
      // that edge back up to today's price, and the two are not the same
      // number: a range whose top sits 30% below the price was reported as
      // needing a 42.87% fall, and one 15% below as needing 17.66%. Both
      // overstated how far away the entry was, on the screen where that
      // distance is the whole decision.
      const gapPct = (1 - Math.pow(1.0001, -Math.max(0, gapTicks))) * 100;
      const stepPct = (Math.pow(1.0001, pk.tickSpacing) - 1) * 100;

      const a = new Alert();
      a.title = `${DRY_RUN ? "Practice: zap" : "Zap"} ${row.symbol} ${row.tier}?`;
      a.message =
          `${preset.amount} USDG into ${plan.slices.length} `
        + `${plan.slices.length > 1 ? "slices, thickest furthest out" : "position"}\n`
        + `${lines} USDG\n\n`
        + (row.tvl > 0 && preset.amount > row.tvl * 0.25
            // A token lookup lists small pools on purpose, so the guard belongs
            // here rather than in the filter: depositing a large share of a
            // pool's own liquidity is a different trade from joining a deep one.
            ? `\u26a0\ufe0f This pool holds only ${fmtUsd(row.tvl)}. `
              + `${preset.amount} USDG is ${(preset.amount / row.tvl * 100).toFixed(0)}% of it.\n\n`
            : "")
        + (plan.topTicks > 0
            ? `Range covers ${realTop.toFixed(1)}% to ${realBot.toFixed(1)}% below the price`
            : `Range covers ${realBot.toFixed(1)}% below the price`)
        + (mcapFar != null && row.mcap != null
            ? `\nmcap ${fmtCompact(mcapFar)} → ${fmtCompact(mcapNear)}` : "")
        + (gasText ? `\n\nFee to open: ${gasText}` : "")
        + (needsApproval ? `\n\nUSDG has not been approved yet, so this sends two `
            + `one-time approvals first. Later zaps skip them.` : "")
        + `\n\nSingle-sided: only USDG goes in, nothing is bought, so there is no `
        + `slippage.\n\n${entryDot(gapPct)} It earns nothing until the price falls ${gapPct.toFixed(2)}% to reach the range.`
        + `\nThis pool's tick grid steps ${stepPct.toFixed(2)}%, which is the closest it can be placed.`
        + (DRY_RUN ? "\n\nPRACTICE MODE: nothing will be sent." : "");
      if (DRY_RUN) a.addAction("Practice run"); else a.addDestructiveAction(`Zap ${preset.amount} USDG`);
      a.addCancelAction("Cancel");

      if ((await a.presentAlert()) !== 0) {
        progress = null;
        busy = false;
        return show(current, false);
      }

      if (DRY_RUN && needsApproval) {
        // A practice run sends nothing, so the approvals never happen, so the
        // mint cannot be simulated: the chain would reject a transfer it has not
        // been permitted to make. Report the plan as sound and stop, rather than
        // showing a revert that only means "not approved yet".
        rep.note("Mint not simulated",
                 "USDG is not approved yet, so the chain cannot price the mint in practice");
        progress.outcome = { ok: true, text:
          `Plan is valid. A real run would send two approvals, then open `
          + `${plan.slices.length} position${plan.slices.length > 1 ? "s" : ""}.` };
      } else {
        if (!DRY_RUN) await ensureZapApprovals(USDG_ADDR, raw + openFee, rep);

        const deadline = BigInt(Math.floor(Date.now() / 1000) + DEADLINE_MIN * 60);
        const cd = buildZapCalldata(pk, plan, depositIsC0, WALLET, deadline);
        const res = await sendTx(POSITION_MANAGER, cd, rep, `Open ${plan.slices.length} position${plan.slices.length > 1 ? "s" : ""}`);

        progress.outcome = res.dryRun
          ? { ok: true, text: "Practice run finished. Nothing was sent." }
          : res.ok ? { ok: true, text: `Opened ${plan.slices.length} position${plan.slices.length > 1 ? "s" : ""} in ${row.symbol} ${row.tier}.` }
          : { ok: false, text: "Reverted on chain. Nothing changed." };
      }
    } catch (e) {
      const m = String((e && e.message) || e);
      progress.outcome = { ok: false, text: /revert|execution/i.test(m)
        ? `Stopped before sending: ${m.slice(0, 100)}` : `Failed: ${m.slice(0, 110)}` };
    }

    if (_gasLog.length && progress.outcome) {
      const total = _gasLog.reduce((x, g) => x + g.wei, 0n);
      const eth = Number(total) / 1e18;
      progress.outcome.gas = `gas ${eth.toFixed(6)} ETH`
        + (_ethUsd ? ` · $${(eth * _ethUsd).toFixed(2)}` : "")
        + (_gasLog.length > 1 ? ` · ${_gasLog.length} transactions` : "");
    }
    progress.running = false;
    busy = false;
    show(current, false);
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
  if (cached && cached.data && cached.data.rows) current = { data: cached.data, stale: false, dataTime: cached.t };

  // Launched with ?pool=... from RH Wallet, to copy what another wallet is
  // farming. The pool is resolved into the same one-pool view the search box
  // produces, so the zap that follows is the ordinary one: same presets, same
  // confirmation, same entry-distance warning. Nothing is placed automatically.
  const wanted = String((args.queryParameters && args.queryParameters.pool) || "").trim();
  if (/^0x[0-9a-fA-F]{64}$/.test(wanted)) {
    busy = true;
    progress = { title: "Copy pool", steps: [{ label: "Reading pool", state: "run", detail: "" }],
                 running: true, outcome: null };
    show(current || { data: null, message: "Loading…" }, true);
    try {
      const r = await rowFromPoolId(wanted);
      tokenView = { symbol: `${r.symbol} ${r.tier}`, rows: [r] };
      progress = null;
    } catch (e) {
      progress = { title: "Copy pool", steps: [], running: false,
        outcome: { ok: false, text: "Could not read that pool: "
          + String((e && e.message) || e).slice(0, 120) } };
    }
    busy = false;
  }

  await refresh();
  await shown;
}
Script.complete();
